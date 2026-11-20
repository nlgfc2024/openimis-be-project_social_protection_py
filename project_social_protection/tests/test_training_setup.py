import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import graphene
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase, TestCase, override_settings

from project_social_protection.apps import DEFAULT_CONFIG, ProjectSocialProtectionConfig
from project_social_protection.gql_mutations import (
    CreateProjectInputType, UpdateProjectInputType, CreateProjectMutation, UpdateProjectMutation,
)
from project_social_protection.models import Project
from project_social_protection.schema import Query
from project_social_protection.services import ProjectService
from project_social_protection.training_catalogue import TrainingModuleService
from project_social_protection.tests.test_district_scope import ProjectDistrictScopeFixtures


class TrainingConfigurationTest(SimpleTestCase):
    def test_default_and_read_only_input(self):
        self.assertEqual(DEFAULT_CONFIG['project_type'], 'WORK')
        for input_type in (CreateProjectInputType, UpdateProjectInputType):
            self.assertFalse(hasattr(input_type, 'project_type'))
            self.assertTrue(hasattr(input_type, 'session_start_date'))
            self.assertTrue(hasattr(input_type, 'session_end_date'))

    def test_invalid_startup_configuration_is_rejected(self):
        with self.assertRaises(ValidationError):
            ProjectSocialProtectionConfig._ProjectSocialProtectionConfig__load_config({'project_type': 'OTHER'})

    def test_invalid_reload_preserves_all_previous_settings(self):
        config = ProjectSocialProtectionConfig('project_social_protection', __import__('project_social_protection'))
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'TRAINING'), patch.object(
                ProjectSocialProtectionConfig, 'max_target_beneficiaries', 42):
            config._reload_module_config(SimpleNamespace(config=json.dumps({
                'project_type': 'INVALID', 'max_target_beneficiaries': 99})))
            self.assertEqual(ProjectSocialProtectionConfig.project_type, 'TRAINING')
            self.assertEqual(ProjectSocialProtectionConfig.max_target_beneficiaries, 42)

    def test_deployment_query_requires_sign_in_not_project_rights(self):
        schema = graphene.Schema(query=Query)
        anonymous = schema.execute('{projectDeploymentType}', context_value=SimpleNamespace(user=AnonymousUser()))
        self.assertTrue(anonymous.errors)
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'TRAINING'):
            result = schema.execute('{projectDeploymentType}', context_value=SimpleNamespace(
                user=SimpleNamespace(is_authenticated=True)))
            self.assertIsNone(result.errors)
            self.assertEqual(result.data['projectDeploymentType'], 'TRAINING')


@override_settings(ROW_SECURITY=False)
class TrainingSetupTest(ProjectDistrictScopeFixtures, TestCase):
    def setUp(self):
        super().setUp()
        TrainingModuleService(self.admin).save('Setup module', [self.plan.pk],
                                             [{'code': 'SETUP01', 'name': 'Setup topic'}])

    def payload(self, **extra):
        return dict(name='Training example', benefit_plan=self.plan,
                    location=self.ta, target_beneficiaries=10, **extra)

    def create_training(self, **extra):
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'TRAINING'):
            result = ProjectService(self.admin).create(self.payload(**extra))
        self.assertTrue(result['success'], result)
        return Project.objects.get(pk=result['data']['id'])

    def test_service_stamps_type_and_preserves_it_after_deployment_change(self):
        project = self.create_training(project_type='WORK')
        self.assertEqual(project.project_type, 'TRAINING')
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'WORK'):
            result = ProjectService(self.admin).update({'id': project.pk, 'name': 'Updated training', 'project_type': 'WORK'})
        self.assertTrue(result['success'], result)
        project.refresh_from_db()
        self.assertEqual(project.project_type, 'TRAINING')
        self.assertEqual(project.name, 'Updated training')
        self.assertEqual(project.history.first().project_type, 'TRAINING')

    def test_training_requires_nonblank_name_and_valid_length(self):
        for name in (None, '', '   ', 'x' * 256):
            data = self.payload()
            data['name'] = name
            with self.subTest(name=name), patch.object(ProjectSocialProtectionConfig, 'project_type', 'TRAINING'):
                result = ProjectService(self.admin).create(data)
                self.assertFalse(result['success'], result)

    def test_duplicate_name_and_programme_move_return_validation_errors(self):
        project = self.create_training()
        other = self.project(project.name, self.ta, self.group_plan)
        other.project_type = 'TRAINING'
        other.save(username=self.admin.username)
        result = ProjectService(self.admin).update({'id': other.pk, 'benefit_plan': self.plan})
        self.assertFalse(result['success'], result)
        self.assertIn('already exists', str(result))
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'TRAINING'):
            duplicate = ProjectService(self.admin).create(self.payload())
        self.assertFalse(duplicate['success'], duplicate)
        self.assertIn('already exists', str(duplicate))

    def test_optional_dates_and_partial_update_date_validation(self):
        project = self.create_training(session_start_date=date(2026, 12, 2), session_end_date=date(2026, 12, 4))
        service = ProjectService(self.admin)
        invalid = service.update({'id': project.pk, 'session_end_date': date(2026, 12, 1)})
        self.assertFalse(invalid['success'], invalid)
        self.assertTrue(service.update({'id': project.pk, 'session_start_date': None})['success'])
        project.refresh_from_db()
        self.assertIsNone(project.session_start_date)

    def test_reversed_dates_rejected_on_create(self):
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'TRAINING'):
            result = ProjectService(self.admin).create(self.payload(
                session_start_date=date(2026, 12, 4), session_end_date=date(2026, 12, 2)))
        self.assertFalse(result['success'], result)

    def test_location_cannot_be_cleared(self):
        project = self.create_training()
        with self.assertRaises(PermissionDenied) as error:
            ProjectService(self.admin).update({'id': project.pk, 'location': None})
        self.assertIn('unauthorized', str(error.exception))

    def test_mutations_preserve_training_manual_name_and_work_generation(self):
        data = dict(benefit_plan_id=self.plan.pk, activity_id=self.activity.pk, location_id=self.ta.uuid,
                    name='Manual training', target_beneficiaries=10, working_days=5)
        with patch.object(ProjectSocialProtectionConfig, 'project_code_enabled', False), patch.object(
                ProjectSocialProtectionConfig, 'project_type', 'TRAINING'):
            training_data = {key: value for key, value in data.items() if key not in ('activity_id', 'working_days')}
            self.assertIsNone(CreateProjectMutation._mutate(self.admin, **training_data))
        training = Project.objects.get(name='Manual training')
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'WORK'):
            self.assertIsNone(UpdateProjectMutation._mutate(self.admin, id=training.pk, name='Renamed training'))
        training.refresh_from_db()
        self.assertEqual(training.name, 'Renamed training')
        with patch.object(ProjectSocialProtectionConfig, 'project_code_enabled', False), patch.object(
                ProjectSocialProtectionConfig, 'project_type', 'WORK'):
            self.assertIsNone(CreateProjectMutation._mutate(self.admin, **data))
        work = Project.objects.get(name=f'{self.activity.name}-{self.plan.name}')
        self.assertEqual(work.project_type, 'WORK')
        self.assertIsNone(UpdateProjectMutation._mutate(self.admin, id=work.pk, name='Ignored'))
        work.refresh_from_db()
        self.assertNotEqual(work.name, 'Ignored')
