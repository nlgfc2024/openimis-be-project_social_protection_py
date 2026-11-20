from unittest.mock import patch

import graphene
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, override_settings

from project_social_protection.apps import ProjectSocialProtectionConfig
from project_social_protection.models import (
    Project, ProjectTopic, BeneficiaryProjectEnrollment, BeneficiaryProjectTimeEntry,
)
from project_social_protection.schema import Query
from project_social_protection.services import ProjectService, ProjectEnrollmentService
from project_social_protection.tests.test_district_scope import ProjectDistrictScopeFixtures
from project_social_protection.training_catalogue import TrainingModuleService
from project_social_protection.training_topics import has_training_attendance, training_module_completion


@override_settings(ROW_SECURITY=False, MODE='dev')
class TrainingTopicsTest(ProjectDistrictScopeFixtures, TestCase):
    def setUp(self):
        super().setUp()
        configuration = patch.object(ProjectSocialProtectionConfig, 'training_form', {'fields': {
            'microCatchment': {'required': False}, 'hotspot': {'required': False},
        }})
        configuration.start()
        self.addCleanup(configuration.stop)
        self.catalogue = TrainingModuleService(self.admin)
        self.a_module = self.catalogue.save('A Entrepreneurship', [self.plan.pk], [
            {'code': 'E01', 'name': 'Business idea'}, {'code': 'E02', 'name': 'Starting a business'}])
        self.b_module = self.catalogue.save('B Management', [self.plan.pk], [
            {'code': 'B01', 'name': 'Business records'}])

    def create_result(self, **data):
        payload = {'name': 'Training', 'benefit_plan': self.plan, 'location': self.ta, 'target_beneficiaries': 10}
        payload.update(data)
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'TRAINING'):
            return ProjectService(self.admin).create(payload)

    def create(self, **data):
        result = self.create_result(**data)
        self.assertTrue(result['success'], result)
        return Project.objects.get(pk=result['data']['id'])

    def enrollment(self, project):
        enrollment = BeneficiaryProjectEnrollment(project=project, beneficiary=self.beneficiaries[0])
        enrollment.save(user=self.admin)
        return enrollment

    def snapshot(self, project):
        return list(project.training_topics.filter(is_deleted=False).values_list(
            'sequence', 'topic__code', 'topic__name', 'topic__activity__name'))

    def test_omitted_modules_default_to_all_and_derive_topic_count(self):
        project = self.create()
        self.assertIsNone(project.activity_id)
        self.assertEqual(project.working_days, 3)
        self.assertEqual([row[1] for row in self.snapshot(project)], ['E01', 'E02', 'B01'])
        self.assertTrue(all(topic.history.exists() for topic in project.training_topics.all()))

    def test_explicit_module_selection_is_rejected_on_create_and_update(self):
        self.assertFalse(self.create_result(module_ids=[self.b_module.pk])['success'])
        project = self.create()
        original = self.snapshot(project)
        self.assertFalse(ProjectService(self.admin).update({'id': project.pk, 'module_ids': [self.a_module.pk]})['success'])
        self.assertEqual(self.snapshot(project), original)
        self.assertEqual(project.working_days, 3)

    def test_empty_duplicate_unlinked_and_deleted_modules_are_rejected(self):
        foreign = self.catalogue.save('Other programme', [self.group_plan.pk], [{'code': 'X01', 'name': 'Other'}])
        for modules in ([], None, [self.a_module.pk, self.a_module.pk], [foreign.pk]):
            with self.subTest(modules=modules):
                self.assertFalse(self.create_result(module_ids=modules)['success'])
        self.catalogue.delete(foreign.pk)
        self.assertFalse(self.create_result(module_ids=[foreign.pk])['success'])

    def test_module_without_live_topics_and_programme_without_modules_are_rejected(self):
        for topic in self.a_module.topics.all():
            topic.is_deleted = True
            topic.save(user=self.admin)
        self.assertFalse(self.create_result()['success'])
        self.assertFalse(self.create_result(benefit_plan=self.group_plan)['success'])

    def test_training_refuses_client_work_fields_and_work_requires_them(self):
        for data in ({'working_days': 3}, {'working_days': None}, {'activity': self.activity}):
            self.assertFalse(self.create_result(**data)['success'])
        payload = {'name': 'Work missing fields', 'benefit_plan': self.plan, 'location': self.ta, 'target_beneficiaries': 10}
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'WORK'):
            self.assertFalse(ProjectService(self.admin).create(payload)['success'])
            payload.update(activity=self.activity, working_days=5)
            self.assertTrue(ProjectService(self.admin).create(payload)['success'])
            self.assertFalse(ProjectService(self.admin).create({**payload, 'name': 'Work modules', 'module_ids': [self.a_module.pk]})['success'])

    def test_catalogue_renames_update_labels_but_membership_and_order_are_fixed(self):
        project = self.create()
        original = list(project.training_topics.values_list('topic_id', 'sequence'))
        topics = list(self.a_module.topics.all())
        self.catalogue.save('Changed module', [self.plan.pk], [
            {'id': topics[1].pk, 'code': 'RENAMED', 'name': 'Changed topic'},
            {'id': topics[0].pk, 'code': 'E01', 'name': topics[0].name},
            {'code': 'E03', 'name': 'New topic'}], self.a_module.pk)
        self.catalogue.save('C New module', [self.plan.pk], [{'code': 'C01', 'name': 'New module topic'}])
        self.assertTrue(ProjectService(self.admin).update({'id': project.pk, 'name': 'Edited training'})['success'])
        self.assertEqual(list(project.training_topics.values_list('topic_id', 'sequence')), original)
        self.assertEqual([row[1] for row in self.snapshot(project)], ['E01', 'RENAMED', 'B01'])
        self.assertEqual(self.snapshot(project)[1][2:], ('Changed topic', 'Changed module'))
        project.refresh_from_db()
        self.assertEqual(project.working_days, 3)
        self.catalogue.delete(self.a_module.pk)
        self.assertTrue(ProjectService(self.admin).update({'id': project.pk, 'name': 'After deletion'})['success'])
        self.assertEqual(list(project.training_topics.values_list('topic_id', 'sequence')), original)

    def test_attendance_locks_modules_including_after_clearing(self):
        project = self.create()
        enrollment = self.enrollment(project)
        service = ProjectEnrollmentService(self.admin, ProjectEnrollmentService.INDIVIDUAL)
        service.bulk_update_time_entries({'time_entries': [
            {'enrollment_id': str(enrollment.pk), 'day_number': 1, 'percent_complete': 100}]})
        self.assertTrue(has_training_attendance(project))
        change = ProjectService(self.admin).update({'id': project.pk, 'module_ids': [self.b_module.pk]})
        self.assertFalse(change['success'])
        self.assertIn('cannot be supplied', str(change))
        self.assertFalse(ProjectService(self.admin).update({'id': project.pk,
            'module_ids': [self.a_module.pk, self.b_module.pk], 'name': 'Renamed'})['success'])
        service.bulk_update_time_entries({'time_entries': [
            {'enrollment_id': str(enrollment.pk), 'day_number': 1, 'clear': True}]})
        self.assertTrue(has_training_attendance(project))
        self.assertFalse(ProjectService(self.admin).update({'id': project.pk, 'module_ids': [self.b_module.pk]})['success'])

    def test_zero_attendance_locks_and_programme_move_is_rejected(self):
        project = self.create()
        enrollment = self.enrollment(project)
        BeneficiaryProjectTimeEntry(enrollment=enrollment, day_number=1, percent_complete=0).save(user=self.admin)
        self.assertTrue(has_training_attendance(project))
        self.assertFalse(ProjectService(self.admin).update({'id': project.pk, 'benefit_plan': self.group_plan})['success'])

    def test_bulk_training_attendance_is_binary_and_all_or_nothing(self):
        project = self.create()
        enrollment = self.enrollment(project)
        service = ProjectEnrollmentService(self.admin, ProjectEnrollmentService.INDIVIDUAL)
        with self.assertRaisesMessage(ValidationError, 'Percent complete is required'):
            service.bulk_update_time_entries({'time_entries': [
                {'enrollment_id': str(enrollment.pk), 'day_number': 1}]})
        with self.assertRaisesMessage(ValidationError, '0 or 100'):
            service.bulk_update_time_entries({'time_entries': [
                {'enrollment_id': str(enrollment.pk), 'day_number': 1, 'percent_complete': 100},
                {'enrollment_id': str(enrollment.pk), 'day_number': 2, 'percent_complete': 50}]})
        self.assertFalse(enrollment.time_entries.exists())
        with self.assertRaises(ValidationError):
            BeneficiaryProjectTimeEntry(enrollment=enrollment, day_number=1, percent_complete=50).save(user=self.admin)

    def test_completion_uses_snapshot_and_counts_each_module_independently(self):
        project = self.create()
        enrollment = self.enrollment(project)
        ProjectEnrollmentService(self.admin, ProjectEnrollmentService.INDIVIDUAL).bulk_update_time_entries({'time_entries': [
            {'enrollment_id': str(enrollment.pk), 'day_number': 1, 'percent_complete': 100},
            {'enrollment_id': str(enrollment.pk), 'day_number': 2, 'percent_complete': 0},
            {'enrollment_id': str(enrollment.pk), 'day_number': 3, 'percent_complete': 100}]})
        result = training_module_completion(enrollment)
        self.assertEqual([(row['ticked_topics'], row['total_topics'], row['percentage']) for row in result],
                         [(1, 2, 50), (1, 1, 100)])
        self.assertEqual(training_module_completion(self.enrolments[0]), [])

    def test_topic_link_failure_rolls_back_project_creation_and_history(self):
        count, history_count = Project.objects.count(), Project.history.count()
        with patch.object(ProjectTopic, 'save', side_effect=RuntimeError('Storage failed')):
            result = self.create_result()
        self.assertFalse(result['success'])
        self.assertEqual(Project.objects.count(), count)
        self.assertEqual(Project.history.count(), history_count)

    def test_graphql_exposes_snapshot_modules_topics_lock_and_completion(self):
        project = self.create()
        enrollment = self.enrollment(project)
        request = RequestFactory().get('/graphql')
        request.user = self.admin
        result = graphene.Schema(query=Query).execute('''query($id: ID!, $enrollment: ID!) {
          project(id: $id) {edges {node {workingDays trainingModulesLocked trainingModules {id name topicCount}
            trainingTopics {sequence code name module {id name}}}}}
          beneficiaryProjectEnrollment(id: $enrollment) {edges {node {moduleCompletion {
            module {id name} tickedTopics totalTopics percentage
          }}}}
        }''', variable_values={'id': str(project.pk), 'enrollment': str(enrollment.pk)}, context_value=request)
        self.assertIsNone(result.errors, result.errors)
        node = result.data['project']['edges'][0]['node']
        self.assertEqual(node['workingDays'], 3)
        self.assertTrue(node['trainingModulesLocked'])
        self.assertEqual([row['code'] for row in node['trainingTopics']], ['E01', 'E02', 'B01'])
        self.assertEqual(len(node['trainingModules']), 2)
        self.assertEqual(len(result.data['beneficiaryProjectEnrollment']['edges'][0]['node']['moduleCompletion']), 2)

    def test_topic_list_is_fixed_before_attendance_and_inputs_have_no_module_list(self):
        from project_social_protection.gql_mutations import CreateProjectInputType, UpdateProjectInputType
        self.assertFalse(hasattr(CreateProjectInputType, 'module_ids'))
        self.assertFalse(hasattr(UpdateProjectInputType, 'module_ids'))
        project = self.create()
        original = list(project.training_topics.values_list('pk', 'topic_id', 'sequence'))
        self.assertFalse(ProjectService(self.admin).update({'id': project.pk, 'benefit_plan': self.group_plan})['success'])
        self.assertEqual(list(project.training_topics.values_list('pk', 'topic_id', 'sequence')), original)

    def test_graphql_reads_current_catalogue_labels_for_existing_training(self):
        project = self.create()
        topic = self.a_module.topics.get(sequence=1)
        rows = [{'id': item.pk, 'code': 'NEW_CODE' if item.pk == topic.pk else item.code,
                 'name': 'Renamed topic' if item.pk == topic.pk else item.name}
                for item in self.a_module.topics.all()]
        self.catalogue.save('Renamed module', [self.plan.pk], rows, self.a_module.pk)
        request = RequestFactory().get('/graphql')
        request.user = self.admin
        result = graphene.Schema(query=Query).execute('''query($id: ID!) {
          project(id: $id) {edges {node {trainingTopics {sequence code name module {name}}}}}
        }''', variable_values={'id': str(project.pk)}, context_value=request)
        self.assertIsNone(result.errors, result.errors)
        first = result.data['project']['edges'][0]['node']['trainingTopics'][0]
        self.assertEqual(first, {'sequence': 1, 'code': 'NEW_CODE', 'name': 'Renamed topic',
                                 'module': {'name': 'Renamed module'}})


    @patch.object(ProjectSocialProtectionConfig, 'project_type', 'TRAINING')
    def test_programme_modules_require_project_permissions_not_catalogue_permissions(self):
        request = RequestFactory().get('/graphql')
        request.user = self.admin
        schema = graphene.Schema(query=Query)
        query = """query($id: UUID!) {
          projectProgrammeModules(benefitPlanId: $id) {id name topicCount}
        }"""
        with patch.object(type(self.admin), 'has_perms',
                          side_effect=lambda perms: list(perms) == list(ProjectSocialProtectionConfig.gql_project_create_perms)):
            result = schema.execute(query, variable_values={'id': str(self.plan.pk)}, context_value=request)
        self.assertIsNone(result.errors, result.errors)
        self.assertEqual([row['topicCount'] for row in result.data['projectProgrammeModules']], [2, 1])
        other = schema.execute(query, variable_values={'id': str(self.group_plan.pk)}, context_value=request)
        self.assertEqual(other.data['projectProgrammeModules'], [])
        with patch.object(type(self.admin), 'has_perms', return_value=False):
            denied = schema.execute(query, variable_values={'id': str(self.plan.pk)}, context_value=request)
        self.assertTrue(denied.errors)
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'WORK'):
            work = schema.execute(query, variable_values={'id': str(self.plan.pk)}, context_value=request)
        self.assertEqual(work.data['projectProgrammeModules'], [])
