from unittest.mock import patch

import graphene
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.test import RequestFactory, TestCase, override_settings
from core.models import MutationLog

from project_social_protection.apps import ProjectSocialProtectionConfig
from project_social_protection.models import Activity, ActivityTopic
from project_social_protection.schema import Query, Mutation
from project_social_protection.tests.test_district_scope import ProjectDistrictScopeFixtures
from project_social_protection.training_catalogue import TrainingModuleService


@override_settings(ROW_SECURITY=False, MODE='dev')
class TrainingCatalogueTest(ProjectDistrictScopeFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.service = TrainingModuleService(self.admin)

    def topics(self, prefix='E'):
        return [{'code': f'{prefix}01', 'name': 'First topic'}, {'code': f'{prefix}02', 'name': 'Second topic'}]

    def create(self, name='Entrepreneurship', prefix='E', plans=None):
        return self.service.save(name, plans or [self.plan.pk], self.topics(prefix))

    def graphql(self, query, variables=None, user=None):
        request = RequestFactory().post('/graphql')
        request.user = user or self.admin
        with patch('core.async_mutations', False):
            return graphene.Schema(query=Query, mutation=Mutation).execute(
                query, variable_values=variables, context_value=request)

    def test_create_shared_programme_links_order_and_audit(self):
        module = self.create(plans=[self.plan.pk, self.group_plan.pk])
        self.assertEqual(module.benefit_plans.count(), 2)
        self.assertEqual(list(module.topics.values_list('code', 'sequence')), [('E01', 1), ('E02', 2)])
        self.assertEqual(module.history.first().json_ext['training_catalogue']['programme_ids'],
                         sorted([str(self.plan.pk), str(self.group_plan.pk)]))
        self.assertTrue(all(topic.history.count() for topic in module.topics.all()))

    def test_edit_reorder_remove_add_and_programme_changes_are_atomic(self):
        module = self.create()
        first, second = list(module.topics.all())
        self.service.save('Renamed module', [self.group_plan.pk], [
            {'id': second.pk, 'code': second.code, 'name': 'Updated topic'},
            {'code': 'E03', 'name': 'New topic'}], module.pk)
        module.refresh_from_db()
        first.refresh_from_db()
        self.assertTrue(first.is_deleted)
        self.assertEqual(list(module.topics.filter(is_deleted=False).values_list('code', 'sequence')),
                         [('E02', 1), ('E03', 2)])
        self.assertEqual(list(module.benefit_plans.values_list('pk', flat=True)), [self.group_plan.pk])
        self.assertEqual(module.name, 'Renamed module')
        self.assertEqual(first.history.first().is_deleted, True)

    def test_reordering_and_swapping_codes_preserves_ids(self):
        module = self.create()
        first, second = list(module.topics.all())
        self.service.save(module.name, [self.plan.pk], [
            {'id': second.pk, 'code': first.code, 'name': second.name},
            {'id': first.pk, 'code': second.code, 'name': first.name}], module.pk)
        self.assertEqual(list(module.topics.filter(is_deleted=False).values_list('pk', 'code', 'sequence')),
                         [(second.pk, 'E01', 1), (first.pk, 'E02', 2)])

    def test_duplicate_module_names_empty_topics_and_missing_programmes(self):
        module = self.create()
        cases = [(module.name, [self.plan.pk], self.topics('B')),
                 ('Empty topics', [self.plan.pk], []), ('No programme', [], self.topics('B')),
                 ('Blank name', [self.plan.pk], [{'code': ' ', 'name': 'Topic'}])]
        for name, plans, topics in cases:
            with self.subTest(name=name), self.assertRaises(ValidationError):
                self.service.save(name, plans, topics)
        self.assertEqual(ActivityTopic.objects.count(), 2)

    def test_duplicate_topic_codes_in_request_or_other_module_are_rejected(self):
        self.create()
        for topics in ([{'code': 'A', 'name': 'One'}, {'code': 'A', 'name': 'Two'}], self.topics()):
            with self.subTest(topics=topics), self.assertRaises(ValidationError):
                self.service.save('Business', [self.plan.pk], topics)

    def test_foreign_and_repeated_topic_ids_are_rejected_without_changes(self):
        module, other = self.create(), self.create('Business', 'B')
        topic = module.topics.first()
        for topics in ([{'id': other.topics.first().pk, 'code': 'X', 'name': 'Foreign'}],
                       [{'id': topic.pk, 'code': 'X', 'name': 'One'}, {'id': topic.pk, 'code': 'Y', 'name': 'Two'}]):
            with self.subTest(topics=topics), self.assertRaises(ValidationError):
                self.service.save('Changed', [self.group_plan.pk], topics, module.pk)
        module.refresh_from_db()
        self.assertEqual(module.name, 'Entrepreneurship')
        self.assertEqual(module.topics.filter(is_deleted=False).count(), 2)

    def test_failure_rolls_back_name_programmes_topics_and_history(self):
        module = self.create()
        before = module.history.count()
        with patch.object(ActivityTopic, 'save', side_effect=RuntimeError('Storage failed')):
            with self.assertRaises(RuntimeError):
                self.service.save('Changed', [self.group_plan.pk], self.topics('N'), module.pk)
        module.refresh_from_db()
        self.assertEqual(module.name, 'Entrepreneurship')
        self.assertEqual(module.history.count(), before)
        self.assertEqual(list(module.benefit_plans.values_list('pk', flat=True)), [self.plan.pk])
        self.assertEqual(list(module.topics.values_list('code', flat=True)), ['E01', 'E02'])

    def test_concurrent_uniqueness_conflicts_return_validation_error(self):
        with patch.object(TrainingModuleService, '_save', side_effect=IntegrityError('unique violation')):
            with self.assertRaisesMessage(ValidationError, 'already exists'):
                self.create()

    def test_deletion_rejects_activities_referenced_by_live_projects(self):
        module = self.create()
        project = self.projects[0]
        project.activity = module
        project.save(user=self.admin)
        with self.assertRaisesMessage(ValidationError, 'live project'):
            self.service.delete(module.pk)
        module.refresh_from_db()
        self.assertFalse(module.is_deleted)
        self.assertEqual(module.topics.filter(is_deleted=False).count(), 2)
        with self.assertRaisesMessage(ValidationError, 'live project'):
            self.service.delete(self.projects[1].activity_id)

    def test_unused_module_deletion_is_soft_and_retains_history(self):
        module = self.create()
        project = self.projects[0]
        project.activity = module
        project.is_deleted = True
        project.save(user=self.admin)
        self.service.delete(module.pk)
        module.refresh_from_db()
        project.refresh_from_db()
        self.assertTrue(module.is_deleted)
        self.assertEqual(module.topics.filter(is_deleted=True).count(), 2)
        self.assertEqual(project.activity_id, module.pk)
        self.assertTrue(module.history.first().is_deleted)
        result = self.graphql('{ trainingModule { totalCount edges { node { name } } } }')
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['trainingModule']['totalCount'], 0)

    def test_work_activity_cannot_be_converted_to_training_module(self):
        activity = self.projects[0].activity
        original_name = activity.name
        with self.assertRaisesMessage(ValidationError, 'does not exist'):
            self.service.save('Converted work', [self.plan.pk], self.topics(), activity.pk)
        activity.refresh_from_db()
        self.assertEqual(activity.name, original_name)
        self.assertFalse(activity.topics.exists())
        self.assertFalse(activity.benefit_plans.exists())

    def test_permissions_enforced_in_service_for_each_operation(self):
        module = self.create()
        for operation, call in [('create', lambda: self.create('Denied', 'X')),
                                ('update', lambda: self.service.save(module.name, [self.plan.pk], self.topics(), module.pk)),
                                ('delete', lambda: self.service.delete(module.pk))]:
            key = f'gql_activity_{operation}_perms'
            expected = getattr(ProjectSocialProtectionConfig, key)
            with self.subTest(operation=operation), patch.object(type(self.admin), 'has_perms', return_value=False) as permission:
                with self.assertRaises(PermissionDenied):
                    call()
                permission.assert_called_with(expected)
        with self.assertRaises(PermissionDenied):
            TrainingModuleService(AnonymousUser()).save('Denied', [self.plan.pk], self.topics('X'))

    def test_graphql_filters_programmes_orders_topics_and_excludes_legacy_work(self):
        self.create()
        self.create('Business', 'B', [self.group_plan.pk])
        result = self.graphql('''query($id: ID!) { trainingModule(benefitPlans_Id: $id) {
          totalCount edges {node {uuid name programmes {name} topics {id code name sequence}}}
        }}''', {'id': str(self.plan.pk)})
        self.assertIsNone(result.errors, result.errors)
        self.assertEqual(result.data['trainingModule']['totalCount'], 1)
        topics = result.data['trainingModule']['edges'][0]['node']['topics']
        self.assertEqual([topic['code'] for topic in topics], ['E01', 'E02'])
        work = self.graphql('{activity {totalCount}}')
        self.assertIsNone(work.errors, work.errors)
        self.assertEqual(work.data['activity']['totalCount'], 3)

    def test_graphql_mutation_contract_and_validation_messages(self):
        result = self.graphql('''mutation($input: CreateTrainingModuleInput!) {
          createTrainingModule(input: $input) {internalId clientMutationId}
        }''', {'input': {'name': 'Business', 'programmeIds': [str(self.plan.pk)], 'topics': self.topics()}})
        self.assertIsNone(result.errors, result.errors)
        created = result.data['createTrainingModule']
        journal = MutationLog.objects.get(pk=created['internalId'])
        self.assertEqual(journal.status, 2, journal.error)
        module = Activity.objects.get(name='Business')
        result = self.graphql('''mutation($input: UpdateTrainingModuleInput!) {
          updateTrainingModule(input: $input) {internalId clientMutationId}
        }''', {'input': {'id': str(module.pk), 'name': 'Business',
                 'programmeIds': [str(self.plan.pk)], 'topics': []}})
        self.assertIsNone(result.errors, result.errors)
        journal = MutationLog.objects.get(pk=result.data['updateTrainingModule']['internalId'])
        self.assertEqual(journal.status, 1)
        self.assertIn('at least one topic', journal.error)
        self.assertEqual(module.topics.filter(is_deleted=False).count(), 2)

    def test_graphql_reads_and_writes_reject_missing_rights(self):
        for user in (AnonymousUser(), self.reader):
            result = self.graphql('{trainingModule {totalCount}}', user=user)
            self.assertTrue(result.errors)
            result = self.graphql('''mutation($input: CreateTrainingModuleInput!) {
              createTrainingModule(input: $input) {internalId}
            }''', {'input': {'name': 'Denied', 'programmeIds': [str(self.plan.pk)], 'topics': self.topics()}}, user=user)
            if not result.errors:
                self.assertEqual(MutationLog.objects.get(pk=result.data['createTrainingModule']['internalId']).status, 1)
        self.assertFalse(Activity.objects.filter(name='Denied').exists())

    def test_journal_tracks_successful_update_and_delete(self):
        module = self.create()
        result = self.graphql('''mutation($input: UpdateTrainingModuleInput!) {
          updateTrainingModule(input: $input) {internalId clientMutationId}
        }''', {'input': {'id': str(module.pk), 'name': 'Renamed',
                        'programmeIds': [str(self.plan.pk)], 'topics': self.topics('N'),
                        'clientMutationId': 'catalogue-update', 'clientMutationLabel': 'Update catalogue'}})
        self.assertIsNone(result.errors, result.errors)
        payload = result.data['updateTrainingModule']
        self.assertEqual(payload['clientMutationId'], 'catalogue-update')
        journal = MutationLog.objects.get(pk=payload['internalId'])
        self.assertEqual(journal.status, 2, journal.error)
        self.assertEqual(journal.client_mutation_label, 'Update catalogue')
        module.refresh_from_db()
        self.assertEqual(module.name, 'Renamed')
        result = self.graphql('''mutation($input: DeleteTrainingModuleInput!) {
          deleteTrainingModule(input: $input) {internalId}
        }''', {'input': {'id': str(module.pk)}})
        self.assertIsNone(result.errors, result.errors)
        journal = MutationLog.objects.get(pk=result.data['deleteTrainingModule']['internalId'])
        self.assertEqual(journal.status, 2, journal.error)
        module.refresh_from_db()
        self.assertTrue(module.is_deleted)

    def test_journal_records_protected_work_activity_failures(self):
        activity = self.projects[0].activity
        for mutation, input_type, data in [
                ('updateTrainingModule', 'UpdateTrainingModuleInput', {
                    'id': str(activity.pk), 'name': 'Converted', 'programmeIds': [str(self.plan.pk)], 'topics': self.topics()}),
                ('deleteTrainingModule', 'DeleteTrainingModuleInput', {'id': str(activity.pk)})]:
            result = self.graphql(f'''mutation($input: {input_type}!) {{
              {mutation}(input: $input) {{internalId}}
            }}''', {'input': data})
            self.assertIsNone(result.errors, result.errors)
            journal = MutationLog.objects.get(pk=result.data[mutation]['internalId'])
            self.assertEqual(journal.status, 1)
            self.assertTrue(journal.error)
        activity.refresh_from_db()
        self.assertFalse(activity.is_deleted)
        self.assertFalse(activity.topics.exists())

    @override_settings(MODE='prod', IS_TESTING=False)
    def test_catalogue_mutation_retains_core_csrf_validation(self):
        request = RequestFactory().post('/graphql', HTTP_X_CSRFTOKEN='incorrect')
        request.user = self.admin
        request.session = {'csrftoken': 'expected'}
        count = MutationLog.objects.count()
        result = graphene.Schema(query=Query, mutation=Mutation).execute('''
          mutation($input: CreateTrainingModuleInput!) {
            createTrainingModule(input: $input) {internalId}
          }''', variable_values={'input': {'name': 'CSRF blocked',
              'programmeIds': [str(self.plan.pk)], 'topics': self.topics()}}, context_value=request)
        self.assertTrue(result.errors)
        self.assertEqual(MutationLog.objects.count(), count)
        self.assertFalse(Activity.objects.filter(name='CSRF blocked').exists())
