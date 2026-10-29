from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from project_social_protection.services import ProjectEnrollmentService


class ProjectEnrollmentErrorLabelsTests(SimpleTestCase):
    @patch('project_social_protection.services.GroupBeneficiary.objects.filter')
    def test_group_enrollment_error_uses_household_code(self, group_filter):
        group_filter.return_value.select_related.return_value = [
            SimpleNamespace(
                id='group-beneficiary-id',
                group=SimpleNamespace(code='40688803'),
            ),
        ]
        service = ProjectEnrollmentService(None, ProjectEnrollmentService.GROUP)

        labels = service._beneficiary_labels({'group-beneficiary-id'})

        self.assertEqual(labels, ['Household 40688803'])

    @patch('project_social_protection.services.Beneficiary.objects.filter')
    def test_individual_enrollment_error_uses_name_and_form_number(self, beneficiary_filter):
        beneficiary_filter.return_value.select_related.return_value = [
            SimpleNamespace(
                id='beneficiary-id',
                individual=SimpleNamespace(
                    first_name='Patricia',
                    last_name='Sichali',
                    json_ext={'form_number': '44655740'},
                ),
            ),
        ]
        service = ProjectEnrollmentService(None, ProjectEnrollmentService.INDIVIDUAL)

        labels = service._beneficiary_labels({'beneficiary-id'})

        self.assertEqual(labels, ['Patricia Sichali (Form Number: 44655740)'])
