import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import graphene
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from project_social_protection.apps import ProjectSocialProtectionConfig
from project_social_protection.models import Project
from project_social_protection.schema import Query
from project_social_protection.services import ProjectService, ProjectEnrollmentService
from project_social_protection.training_catalogue import TrainingModuleService
from project_social_protection.training_form import public_training_form
from project_social_protection.training_form_config import get_training_form, normalize_training_form
from project_social_protection.tests.test_district_scope import ProjectDistrictScopeFixtures


class TrainingFormConfigurationTest(SimpleTestCase):
    def test_location_required_is_configurable(self):
        fields = normalize_training_form({'fields': {'location': {'visible': False, 'required': False}}})['fields']
        self.assertFalse(fields['location']['required'])
        self.assertFalse(fields['location']['visible'])
    def test_matching_uses_code_even_when_name_or_source_matches_another_rule(self):
        config = {'programs': {
            '202646268': {'fields': {'knownPlace': {'default': 'Code match'}}},
            'pwp': {'fields': {'knownPlace': {'default': 'Source match'}}},
            'RMEP': {'fields': {'knownPlace': {'default': 'Name match'}}},
        }}
        with patch.object(ProjectSocialProtectionConfig, 'training_form', config):
            plan = SimpleNamespace(code='202646268', name='RMEP', json_ext={'data_source': 'pwp'})
            self.assertEqual(get_training_form(plan)['fields']['knownPlace']['default'], 'Code match')
            plan.code = 'UNCONFIGURED'
            self.assertNotIn('default', get_training_form(plan)['fields']['knownPlace'])

    def test_program_rules_inherit_shared_fields_without_leaking(self):
        raw = {'fields': {'knownPlace': {'default': 'Shared venue'},
                          'hotspot': {'visible': True, 'required': True}},
               'programs': {'pwp': {'fields': {'knownPlace': {'readOnly': True}}},
                            'sctp': {'fields': {'hotspot': {'visible': False}}}}}
        rmep = normalize_training_form(raw, program='pwp')['fields']
        upg = normalize_training_form(raw, program='sctp')['fields']
        self.assertTrue(rmep['knownPlace']['readOnly'])
        self.assertEqual(rmep['knownPlace']['default'], 'Shared venue')
        self.assertTrue(rmep['hotspot']['required'])
        self.assertFalse(upg['hotspot']['visible'])
        self.assertFalse(upg['hotspot']['required'])
        self.assertFalse(upg['knownPlace']['readOnly'])
        self.assertEqual(normalize_training_form(raw, program='other'), normalize_training_form(raw))
        self.assertTrue(raw['fields']['hotspot']['required'])

    def test_invalid_unselected_program_is_rejected(self):
        for programs in ([], {'sctp': {'fields': {'unknown': {}}}},
                         {'pwp': {'fields': {'name': {'required': False}}}}):
            with self.subTest(programs=programs), self.assertRaises(ValidationError):
                normalize_training_form({'programs': programs}, program='other')

    def test_documented_example_is_valid(self):
        example = Path(__file__).resolve().parents[2] / 'training-form-config.example.json'
        ProjectSocialProtectionConfig.validate_configuration(SimpleNamespace(config=example.read_text()))

    def test_defaults_and_visibility_of_optional_fields(self):
        config = normalize_training_form({'fields': {'hotspot': {'visible': False}}})
        self.assertTrue(config['fields']['name']['required'])
        self.assertTrue(config['fields']['topicCount']['readOnly'])
        self.assertFalse(config['fields']['hotspot']['visible'])
        self.assertFalse(config['fields']['hotspot']['required'])

    def test_invalid_rules_are_rejected(self):
        cases = [[], {'fields': []}, {'fields': {'unknown': {}}}, {'fields': {'name': {'required': False}}},
                 {'fields': {'name': {'visible': False}}}, {'fields': {'name': {'visible': 'false'}}},
                 {'fields': {'name': {'readOnly': True}}}, {'fields': {'topicCount': {'readOnly': False}}},
                 {'fields': {'targetBeneficiaries': {'default': -1}}}, {'fields': {'location': {'default': 'bad'}}},
                 {'fields': {'sessionStartDate': {'default': 'yesterday'}}},
                 {'fields': {'sessionStartDate': {'default': '2026-12-03'}, 'sessionEndDate': {'default': '2026-12-01'}}},
                 {'fields': {'trainingModules': {'default': []}}}]
        for config in cases:
            with self.subTest(config=config), self.assertRaises(ValidationError):
                normalize_training_form(config)

    def test_hidden_required_defaults_and_system_fields(self):
        config = normalize_training_form({'fields': {'name': {'visible': False, 'default': 'Default name'}}})
        self.assertFalse(config['fields']['name']['visible'])
        self.assertTrue(config['fields']['name']['required'])
        for field in ('benefitPlan', 'topicCount', 'trainingModules'):
            with self.assertRaises(ValidationError):
                normalize_training_form({'fields': {field: {'default': 'value'}}})

    def test_target_default_respects_the_configured_limit(self):
        with self.assertRaises(ValidationError):
            normalize_training_form({'fields': {'targetBeneficiaries': {'default': 201}}})
        self.assertEqual(normalize_training_form({'fields': {
            'targetBeneficiaries': {'default': 201}}}, maximum_target=500)['fields']['targetBeneficiaries']['default'], 201)

    def test_django_admin_validator_rejects_invalid_configuration(self):
        with self.assertRaises(ValidationError):
            ProjectSocialProtectionConfig.validate_configuration(SimpleNamespace(config=json.dumps({
                'training_form': {'fields': {'hotspot': {'readOnly': 'yes'}}}})))

    def test_invalid_reload_preserves_previous_settings(self):
        instance = ProjectSocialProtectionConfig('project_social_protection', __import__('project_social_protection'))
        previous = {'fields': {'knownPlace': {'default': 'Existing'}}}
        with patch.object(ProjectSocialProtectionConfig, 'training_form', previous):
            instance._reload_module_config(SimpleNamespace(config=json.dumps({'training_form': {
                'fields': {'name': {'required': False}}}})))
            self.assertEqual(ProjectSocialProtectionConfig.training_form, previous)


@override_settings(ROW_SECURITY=False, MODE='dev')
class TrainingFormServiceTest(ProjectDistrictScopeFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.module = TrainingModuleService(self.admin).save('Configuration module', [self.plan.pk], [
            {'code': 'CONFIG01', 'name': 'Training topic'}])

    def configuration(self, fields=None):
        return {'fields': {'microCatchment': {'visible': False}, 'hotspot': {'visible': False}, **(fields or {})}}

    def test_optional_hidden_ta_uses_selected_district(self):
        district = self.ta.parent
        while district.type != 'R':
            district = district.parent
        result = self.create({'location': {'visible': False, 'required': False}},
                             location=None, district_id=district.uuid)
        self.assertTrue(result['success'], result)
        self.assertEqual(Project.objects.get(pk=result['data']['id']).location_id, district.pk)

    def test_required_ta_rejects_district_only(self):
        district = self.ta.parent
        while district.type != 'R':
            district = district.parent
        result = self.create(location=district)
        self.assertFalse(result['success'], result)

    def test_program_code_controls_public_rules_defaults_and_validation(self):
        config = self.configuration()
        config['programs'] = {
            '202646268': {'fields': {'knownPlace': {'readOnly': True, 'default': 'RMEP venue'}}},
            '202646269': {'fields': {'knownPlace': {'required': True}}},
        }
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'TRAINING'), patch.object(
                ProjectSocialProtectionConfig, 'training_form', config):
            self.plan.json_ext = {'data_source': 'pwp'}
            self.plan.code = '202646268'
            type(self.plan).objects.filter(pk=self.plan.pk).update(json_ext=self.plan.json_ext, code=self.plan.code)
            public = public_training_form(self.plan)
            self.assertEqual(public['fields']['knownPlace']['default'], 'RMEP venue')
            result = ProjectService(self.admin).create({
                'name': 'RMEP configured', 'benefit_plan': self.plan, 'location': self.ta,
                'target_beneficiaries': 10, 'known_place': 'Ignored input'})
            self.assertTrue(result['success'], result)
            self.assertEqual(Project.objects.get(pk=result['data']['id']).known_place, 'RMEP venue')
            # A different programme sharing the same source must have different rules.
            self.plan.code = '202646269'
            type(self.plan).objects.filter(pk=self.plan.pk).update(code=self.plan.code)
            self.assertTrue(public_training_form(self.plan)['fields']['knownPlace']['required'])
            result = ProjectService(self.admin).create({
                'name': 'UPG missing venue', 'benefit_plan': self.plan, 'location': self.ta,
                'target_beneficiaries': 10})
            self.assertFalse(result['success'], result)

    def create(self, fields=None, **values):
        payload = {'name': 'Configured training', 'benefit_plan': self.plan, 'location': self.ta,
                   'target_beneficiaries': 10, **values}
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'TRAINING'), patch.object(
                ProjectSocialProtectionConfig, 'training_form', self.configuration(fields)):
            return ProjectService(self.admin).create(payload)

    def test_hidden_optional_target_can_be_null(self):
        result = self.create({'targetBeneficiaries': {'visible': False}}, target_beneficiaries=None)
        self.assertTrue(result['success'], result)
        self.assertIsNone(Project.objects.get(pk=result['data']['id']).target_beneficiaries)

    def test_training_without_target_can_enrol_beneficiaries(self):
        result = self.create({'targetBeneficiaries': {'required': False}}, target_beneficiaries=None,
                             allows_multiple_enrollments=True)
        self.assertTrue(result['success'], result)
        ProjectEnrollmentService(self.admin, ProjectEnrollmentService.INDIVIDUAL).enroll_project({
            'project_id': result['data']['id'], 'ids': [str(self.beneficiaries[0].pk)],
        })
        self.assertEqual(Project.objects.get(pk=result['data']['id']).beneficiary_enrollments.count(), 1)

    def test_work_target_cannot_be_cleared(self):
        result = ProjectService(self.admin).update({'id': self.projects[0].pk, 'target_beneficiaries': None})
        self.assertFalse(result['success'])
        self.assertIn('Target beneficiaries is required', str(result))

    def test_required_target_is_enforced_but_optional_target_still_checks_range(self):
        self.assertFalse(self.create(target_beneficiaries=None)['success'])
        self.assertFalse(self.create({'targetBeneficiaries': {'required': False}}, target_beneficiaries=201)['success'])

    def test_read_only_defaults_override_supplied_values_and_hidden_values_are_removed(self):
        result = self.create({'name': {'readOnly': True, 'default': 'Configured name'},
                              'knownPlace': {'readOnly': True, 'default': 'Default place'},
                              'foreman': {'visible': False}},
                             name='Changed by client', known_place='Changed by client', foreman=self.admin)
        self.assertTrue(result['success'], result)
        project = Project.objects.get(pk=result['data']['id'])
        self.assertEqual(project.name, 'Configured name')
        self.assertEqual(project.known_place, 'Default place')
        self.assertIsNone(project.foreman_id)

    def test_optional_field_can_be_required(self):
        result = self.create({'knownPlace': {'required': True}})
        self.assertFalse(result['success'])
        self.assertIn('knownPlace is required', str(result))

    def test_location_defaults_and_automatic_programme_modules_are_resolved(self):
        result = self.create({'location': {'readOnly': True, 'default': str(self.ta.uuid)},
                              'trainingModules': {'readOnly': True}}, location=self.b_ta)
        self.assertTrue(result['success'], result)
        project = Project.objects.get(pk=result['data']['id'])
        self.assertEqual(project.location_id, self.ta.pk)
        self.assertEqual(project.working_days, 1)

    def test_read_only_district_default_matches_the_saved_location(self):
        for required in (True, False):
            result = self.create({'district': {'readOnly': True, 'required': required,
                                               'default': str(self.a.uuid)}}, location=self.b_ta)
            self.assertFalse(result['success'])
            self.assertIn('configured read-only district', str(result))

    def test_hidden_module_selection_uses_all_programme_modules(self):
        result = self.create({'trainingModules': {'visible': False}})
        self.assertTrue(result['success'], result)
        self.assertEqual(Project.objects.get(pk=result['data']['id']).working_days, 1)

    def test_hidden_date_defaults_and_explicit_optional_clear(self):
        result = self.create({'sessionStartDate': {'visible': False, 'default': '2026-12-01'},
                              'knownPlace': {'default': 'Default place'}}, known_place=None)
        self.assertTrue(result['success'], result)
        project = Project.objects.get(pk=result['data']['id'])
        self.assertEqual(project.session_start_date.isoformat(), '2026-12-01')
        self.assertIsNone(project.known_place)

    def test_public_configuration_resolves_labels_and_requires_sign_in(self):
        config = self.configuration({'location': {'default': str(self.ta.uuid)},
                                     'foreman': {'default': str(self.admin.pk)}})
        with patch.object(ProjectSocialProtectionConfig, 'training_form', config):
            public = public_training_form()
            self.assertEqual(public['fields']['location']['default']['name'], self.ta.name)
            request = RequestFactory().post('/graphql')
            request.user = self.admin
            schema = graphene.Schema(query=Query)
            result = schema.execute('{projectTrainingFormConfig}', context_value=request)
            self.assertIsNone(result.errors, result.errors)
            self.assertIn('fields', json.loads(result.data['projectTrainingFormConfig']))
            request.user = AnonymousUser()
            self.assertTrue(schema.execute('{projectTrainingFormConfig}', context_value=request).errors)

    def test_work_keeps_required_target_and_ignores_training_defaults(self):
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'WORK'), patch.object(
                ProjectSocialProtectionConfig, 'training_form', self.configuration({'targetBeneficiaries': {'required': False}})):
            result = ProjectService(self.admin).create({'name': 'Work', 'benefit_plan': self.plan,
                'location': self.ta, 'activity': self.activity, 'working_days': 3})
        self.assertFalse(result['success'])
        self.assertIn('Target beneficiaries is required', str(result))
