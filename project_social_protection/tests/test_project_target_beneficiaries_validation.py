from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from project_social_protection.apps import (
    DEFAULT_MAX_TARGET_BENEFICIARIES,
    ProjectSocialProtectionConfig,
)
from project_social_protection.validation import (
    ProjectValidation,
    get_max_target_beneficiaries,
)


class ProjectTargetBeneficiariesValidationTest(SimpleTestCase):
    def test_rejects_non_positive_target_values(self):
        self.assertTrue(ProjectValidation._validate_target_beneficiaries(0))
        self.assertTrue(ProjectValidation._validate_target_beneficiaries(-1))

    def test_uses_configured_max_target_beneficiaries(self):
        original_max = ProjectSocialProtectionConfig.max_target_beneficiaries
        ProjectSocialProtectionConfig.max_target_beneficiaries = 10
        try:
            self.assertEqual(
                ProjectValidation._validate_target_beneficiaries(10),
                [],
            )
            self.assertTrue(
                ProjectValidation._validate_target_beneficiaries(11)
            )
        finally:
            ProjectSocialProtectionConfig.max_target_beneficiaries = original_max

    def test_uses_shared_default_when_config_value_is_invalid(self):
        original_max = ProjectSocialProtectionConfig.max_target_beneficiaries
        ProjectSocialProtectionConfig.max_target_beneficiaries = "invalid"
        try:
            self.assertEqual(
                get_max_target_beneficiaries(),
                DEFAULT_MAX_TARGET_BENEFICIARIES,
            )
        finally:
            ProjectSocialProtectionConfig.max_target_beneficiaries = original_max

    def test_validate_create_requires_target_beneficiaries(self):
        with self.assertRaises(ValidationError) as cm:
            ProjectValidation.validate_create(
                user=None,
                # Keep name falsy so unique-name validation short-circuits;
                # this test targets the required-target validation only.
                name=None,
                benefit_plan_id='ignored-for-this-test',
                target_beneficiaries=None,
            )

        messages = str(cm.exception)
        self.assertIn('Target beneficiaries is required.', messages)
