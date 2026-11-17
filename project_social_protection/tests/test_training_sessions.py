import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from core.services import BaseService
from project_social_protection.apps import ProjectSocialProtectionConfig
from project_social_protection.gql_mutations import CreateProjectMutation, UpdateProjectMutation
from project_social_protection.services import ProjectService
from project_social_protection.validation import ProjectValidation


class TrainingSessionTests(SimpleTestCase):
    def test_deployment_type_query_requires_login_and_returns_backend_setting(self):
        from django.contrib.auth.models import AnonymousUser
        from django.core.exceptions import PermissionDenied
        from project_social_protection.schema import Query
        with self.assertRaises(PermissionDenied):
            Query.resolve_project_deployment_type(None, SimpleNamespace(context=SimpleNamespace(user=AnonymousUser())))
        info = SimpleNamespace(context=SimpleNamespace(user=SimpleNamespace(id='user')))
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'TRAINING'):
            self.assertEqual(Query.resolve_project_deployment_type(None, info), 'TRAINING')

    def test_config_rejects_invalid_type_without_changing_previous_value(self):
        app = ProjectSocialProtectionConfig('project_social_protection', __import__('project_social_protection'))
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'TRAINING'):
            app._reload_module_config(SimpleNamespace(config=json.dumps({'project_type': 'INVALID'})))
            self.assertEqual(ProjectSocialProtectionConfig.project_type, 'TRAINING')
            with self.assertRaises(ValidationError):
                app._ProjectSocialProtectionConfig__load_config({'project_type': 'INVALID'})

    def test_type_is_not_a_mutation_input(self):
        from project_social_protection.gql_queries import ProjectGQLType
        self.assertIn('project_type', ProjectGQLType._meta.fields)
        self.assertIn('session_start_date', ProjectGQLType._meta.fields)
        self.assertIn('session_end_date', ProjectGQLType._meta.fields)
        for input_type in (CreateProjectMutation.Input, UpdateProjectMutation.Input):
            self.assertNotIn('project_type', input_type._meta.fields)
            self.assertIn('session_start_date', input_type._meta.fields)
            self.assertIn('session_end_date', input_type._meta.fields)

    def test_service_stamps_type_and_duration_but_preserves_explicit_duration(self):
        activity = SimpleNamespace(json_ext={'default_working_days': 4})
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'TRAINING'), \
                patch.object(BaseService, 'create', side_effect=lambda data: data):
            data = ProjectService(None).create({'activity': activity, 'project_type': 'WORK'})
            self.assertEqual(data['project_type'], 'TRAINING')
            self.assertTrue(data['allows_multiple_enrollments'])
            self.assertEqual(data['working_days'], 4)
            self.assertEqual(ProjectService(None).create({'activity': activity, 'working_days': 7})['working_days'], 7)

    def test_work_remains_default_and_does_not_enable_multiple_enrollment(self):
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'WORK'), \
                patch.object(BaseService, 'create', side_effect=lambda data: data):
            data = ProjectService(None).create({'working_days': 10})
            self.assertEqual(data['project_type'], 'WORK')
            self.assertNotIn('allows_multiple_enrollments', data)

    def test_training_requires_name_location_and_positive_duration(self):
        valid = {'project_type': 'TRAINING', 'name': 'Session one', 'location': object(), 'working_days': 2}
        ProjectValidation.validate_session(valid)
        for change in ({'name': ' '}, {'name': 'a' * 256}, {'location': None}, {'working_days': 0}, {'working_days': True}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                ProjectValidation.validate_session({**valid, **change})

    def test_partial_update_validates_dates_against_stored_values(self):
        existing = SimpleNamespace(project_type='TRAINING', name='Session', location=object(), working_days=2,
                                   session_start_date=date(2026, 5, 2), session_end_date=date(2026, 5, 4))
        with self.assertRaises(ValidationError):
            ProjectValidation.validate_session({'session_end_date': date(2026, 5, 1)}, existing)
        ProjectValidation.validate_session({'session_end_date': None}, existing)

    def test_update_keeps_stored_type_and_training_enrollment_policy(self):
        with patch('project_social_protection.services.Project.objects') as objects, \
                patch.object(BaseService, 'update', side_effect=lambda data: data):
            objects.get.return_value = SimpleNamespace(project_type='TRAINING')
            data = ProjectService(None).update({'id': 'id', 'project_type': 'WORK', 'allows_multiple_enrollments': False})
            self.assertNotIn('project_type', data)
            self.assertTrue(data['allows_multiple_enrollments'])


class TrainingDatabaseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from core.test_helpers import create_test_interactive_user
        from project_social_protection.tests.test_helpers import create_benefit_plan, create_project
        from social_protection.models import GroupBeneficiary, BeneficiaryStatus
        from social_protection.tests.test_helpers import create_group_with_individual
        cls.user = create_test_interactive_user(username='training_test_admin')
        cls.plan = create_benefit_plan(username=cls.user.username)
        cls.plan.type = 'GROUP'
        cls.plan.save(username=cls.user.username)
        cls.project = create_project('Training session', cls.plan, cls.user.username,
                                     project_type='TRAINING', allows_multiple_enrollments=True)
        cls.member, cls.group, _ = create_group_with_individual(cls.user.username)
        cls.group_beneficiary = GroupBeneficiary(group=cls.group, benefit_plan=cls.plan,
                                                 status=BeneficiaryStatus.ACTIVE)
        # A group Phase supplies eligibility; individual participant records are
        # materialized by the Training assignment service.
        cls.group_beneficiary.save(username=cls.user.username)

    def test_training_mutations_preserve_manual_name_and_use_activity_duration(self):
        from project_social_protection.models import Project
        activity = self.project.activity
        activity.json_ext = {'default_working_days': 3}
        activity.save(username=self.user.username)
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'TRAINING'), \
                patch.object(ProjectSocialProtectionConfig, 'project_code_enabled', False):
            result = CreateProjectMutation._mutate(
                self.user, benefit_plan_id=self.plan.id, activity_id=activity.id,
                location_id=self.project.location.uuid, target_beneficiaries=10,
                name='Manually entered session', session_start_date=date(2026, 5, 2),
                session_end_date=date(2026, 5, 3),
            )
        self.assertIsNone(result)
        created = Project.objects.get(name='Manually entered session')
        self.assertEqual(created.project_type, 'TRAINING')
        self.assertEqual(created.working_days, 3)
        self.assertTrue(created.allows_multiple_enrollments)
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'WORK'):
            self.assertIsNone(UpdateProjectMutation._mutate(self.user, id=created.id, name='Renamed session', working_days=5))
        created.refresh_from_db()
        self.assertEqual(created.name, 'Renamed session')
        self.assertEqual(created.project_type, 'TRAINING')
        self.assertEqual(created.working_days, 5)

    def test_training_rejects_duplicate_manual_name(self):
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'TRAINING'):
            result = ProjectService(self.user).create({
                'name': self.project.name, 'benefit_plan': self.plan,
                'activity': self.project.activity, 'location': self.project.location,
                'target_beneficiaries': 10, 'working_days': 2,
            })
        self.assertFalse(result['success'])

    def test_training_can_be_created_without_target_and_enrolled_without_cap(self):
        from project_social_protection.models import Project, BeneficiaryProjectEnrollment
        from project_social_protection.services import ProjectEnrollmentService
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'TRAINING'):
            result = ProjectService(self.user).create({
                'name': 'No target session', 'benefit_plan': self.plan,
                'activity': self.project.activity, 'location': self.project.location,
                'working_days': 2,
            })
        self.assertTrue(result['success'], result)
        session = Project.objects.get(name='No target session')
        self.assertIsNone(session.target_beneficiaries)
        ProjectEnrollmentService(self.user, 'GROUP').enroll_project({
            'project_id': session.id, 'ids': [self.group_beneficiary.id],
        })
        self.assertEqual(BeneficiaryProjectEnrollment.objects.filter(project=session, is_deleted=False).count(), 1)

    def test_work_mutation_keeps_generated_names(self):
        from project_social_protection.gql_mutations import generate_project_name
        from project_social_protection.models import Project
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'WORK'), \
                patch.object(ProjectSocialProtectionConfig, 'project_code_enabled', False):
            result = CreateProjectMutation._mutate(
                self.user, benefit_plan_id=self.plan.id, activity_id=self.project.activity_id,
                location_id=self.project.location.uuid, target_beneficiaries=10,
                working_days=5, name='Ignored client name',
            )
        self.assertIsNone(result)
        generated_name = generate_project_name(None, self.project.activity, self.plan, None)
        created = Project.objects.get(name=generated_name)
        self.assertEqual(created.project_type, 'WORK')
        self.assertFalse(created.allows_multiple_enrollments)

    def test_group_assignment_creates_individual_records_and_allows_second_session(self):
        from project_social_protection.services import ProjectEnrollmentService
        from project_social_protection.models import BeneficiaryProjectEnrollment, GroupBeneficiaryProjectEnrollment
        from social_protection.models import Beneficiary
        from project_social_protection.tests.test_helpers import create_project
        service = ProjectEnrollmentService(self.user, 'GROUP')
        service.enroll_project({'project_id': self.project.id, 'ids': [self.group_beneficiary.id]})
        beneficiary = Beneficiary.objects.get(individual=self.member, benefit_plan=self.plan, is_deleted=False)
        self.assertTrue(BeneficiaryProjectEnrollment.objects.filter(project=self.project, beneficiary=beneficiary, is_deleted=False).exists())
        self.assertFalse(GroupBeneficiaryProjectEnrollment.objects.filter(project=self.project).exists())
        second = create_project('Second session', self.plan, self.user.username, project_type='TRAINING')
        service.enroll_project({'project_id': second.id, 'ids': [self.group_beneficiary.id]})
        self.assertEqual(Beneficiary.objects.filter(individual=self.member, benefit_plan=self.plan).count(), 1)
        self.assertTrue(BeneficiaryProjectEnrollment.objects.filter(project=second, beneficiary=beneficiary, is_deleted=False).exists())

    def test_training_group_assignment_can_exceed_target(self):
        from project_social_protection.services import ProjectEnrollmentService
        from social_protection.models import Beneficiary
        from project_social_protection.models import Project
        Project.objects.filter(id=self.project.id).update(target_beneficiaries=0)
        ProjectEnrollmentService(self.user, 'GROUP').enroll_project({
            'project_id': self.project.id, 'ids': [self.group_beneficiary.id],
        })
        self.assertTrue(Beneficiary.objects.filter(individual=self.member, benefit_plan=self.plan).exists())

    def test_training_target_can_exceed_work_maximum(self):
        from project_social_protection.models import Project
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'TRAINING'):
            result = ProjectService(self.user).create({
                'name': 'Large training target', 'benefit_plan': self.plan,
                'activity': self.project.activity, 'location': self.project.location,
                'working_days': 2, 'target_beneficiaries': 100000,
            })
        self.assertTrue(result['success'], result)
        self.assertEqual(Project.objects.get(name='Large training target').target_beneficiaries, 100000)
