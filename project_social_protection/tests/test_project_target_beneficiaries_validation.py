from django.test import SimpleTestCase

from project_social_protection.apps import ProjectSocialProtectionConfig
from project_social_protection.validation import ProjectValidation


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
