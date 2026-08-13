import uuid as uuid_lib

from django.test import TestCase

from core.test_helpers import LogInHelper
from social_protection.services import BeneficiaryService, GroupBeneficiaryService
from social_protection.tests.test_helpers import (
    create_benefit_plan,
    create_individual,
    add_individual_to_benefit_plan,
    add_group_to_benefit_plan,
    add_individual_to_group,
    create_group_with_individual,
)
from project_social_protection.models import (
    BeneficiaryProjectEnrollment,
    GroupBeneficiaryProjectEnrollment,
)
from project_social_protection.services import ProjectEnrollmentService
from project_social_protection.tests.test_helpers import create_project


class ProjectEnrollmentTargetBeneficiariesTest(TestCase):
    """Individual enrollment respects Project.target_beneficiaries."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = LogInHelper().get_or_create_user_api()
        cls.benefit_plan = create_benefit_plan(
            cls.user.username,
            payload_override={
                'code': 'TGTCAP',
                'type': 'INDIVIDUAL',
                'max_beneficiaries': None,
            }
        )
        beneficiary_service = BeneficiaryService(cls.user)
        individuals = [
            create_individual(cls.user.username, {'first_name': f'Ind{i}'})
            for i in range(3)
        ]
        cls.beneficiary_uuids = [
            add_individual_to_benefit_plan(
                beneficiary_service,
                individual,
                cls.benefit_plan,
                {'status': 'ACTIVE'},
            )
            for individual in individuals
        ]

    def _make_project(self, target_beneficiaries, **kwargs):
        return create_project(
            f'Cap test project {uuid_lib.uuid4()}',
            self.benefit_plan,
            self.user.username,
            target_beneficiaries=target_beneficiaries,
            **kwargs,
        )

    def test_enroll_exactly_at_target_succeeds(self):
        project = self._make_project(target_beneficiaries=2)
        service = ProjectEnrollmentService(
            self.user, ProjectEnrollmentService.INDIVIDUAL
        )
        service.enroll_project({
            'ids': self.beneficiary_uuids[:2],
            'project_id': str(project.id),
        })
        enrollments = BeneficiaryProjectEnrollment.objects.filter(
            project_id=project.id, is_deleted=False
        )
        self.assertEqual(enrollments.count(), 2)

    def test_enroll_one_over_target_raises_and_writes_nothing(self):
        project = self._make_project(target_beneficiaries=2)
        service = ProjectEnrollmentService(
            self.user, ProjectEnrollmentService.INDIVIDUAL
        )
        with self.assertRaises(ValueError):
            service.enroll_project({
                'ids': self.beneficiary_uuids[:3],
                'project_id': str(project.id),
            })
        enrollments = BeneficiaryProjectEnrollment.objects.filter(
            project_id=project.id, is_deleted=False
        )
        self.assertEqual(enrollments.count(), 0)

    def test_unenroll_only_never_trips_cap(self):
        project = self._make_project(target_beneficiaries=2)
        service = ProjectEnrollmentService(
            self.user, ProjectEnrollmentService.INDIVIDUAL
        )
        service.enroll_project({
            'ids': self.beneficiary_uuids[:2],
            'project_id': str(project.id),
        })
        service.enroll_project({
            'ids': [],
            'project_id': str(project.id),
        })
        enrollments = BeneficiaryProjectEnrollment.objects.filter(
            project_id=project.id, is_deleted=False
        )
        self.assertEqual(enrollments.count(), 0)

    def test_mixed_enroll_unenroll_nets_under_cap_succeeds(self):
        project = self._make_project(target_beneficiaries=2)
        service = ProjectEnrollmentService(
            self.user, ProjectEnrollmentService.INDIVIDUAL
        )
        service.enroll_project({
            'ids': self.beneficiary_uuids[:2],
            'project_id': str(project.id),
        })
        # Swap one enrolled beneficiary for another - net submitted count
        # stays 2, so it must not trip the cap even though a beneficiary
        # not previously enrolled is being added.
        service.enroll_project({
            'ids': [self.beneficiary_uuids[0], self.beneficiary_uuids[2]],
            'project_id': str(project.id),
        })
        enrollments = BeneficiaryProjectEnrollment.objects.filter(
            project_id=project.id, is_deleted=False
        )
        self.assertEqual(enrollments.count(), 2)
        enrolled_ids = {str(e.beneficiary_id) for e in enrollments}
        self.assertEqual(
            enrolled_ids,
            {self.beneficiary_uuids[0], self.beneficiary_uuids[2]},
        )

    def test_additional_enroll_over_existing_cap_raises(self):
        project = self._make_project(target_beneficiaries=2)
        service = ProjectEnrollmentService(
            self.user, ProjectEnrollmentService.INDIVIDUAL
        )
        service.enroll_project({
            'ids': self.beneficiary_uuids[:2],
            'project_id': str(project.id),
        })

        with self.assertRaises(ValueError):
            service.enroll_project({
                'ids': self.beneficiary_uuids[:3],
                'project_id': str(project.id),
            })

        enrollments = BeneficiaryProjectEnrollment.objects.filter(
            project_id=project.id,
            is_deleted=False,
        )
        self.assertEqual(enrollments.count(), 2)

    def test_unenroll_allowed_when_project_is_already_over_target(self):
        project = self._make_project(target_beneficiaries=3)
        service = ProjectEnrollmentService(
            self.user, ProjectEnrollmentService.INDIVIDUAL
        )

        service.enroll_project({
            'ids': self.beneficiary_uuids[:3],
            'project_id': str(project.id),
        })

        # Simulate an admin lowering the target below current assigned count.
        project.target_beneficiaries = 1
        project.save(update_fields=['target_beneficiaries'])

        # Keep one beneficiary assigned: this reduces total from 3 -> 1 and
        # must succeed even though the project was temporarily over target.
        service.enroll_project({
            'ids': [self.beneficiary_uuids[0]],
            'project_id': str(project.id),
        })

        enrollments = BeneficiaryProjectEnrollment.objects.filter(
            project_id=project.id,
            is_deleted=False,
        )
        self.assertEqual(enrollments.count(), 1)
        self.assertEqual(str(enrollments.first().beneficiary_id), self.beneficiary_uuids[0])


class ProjectGroupEnrollmentTargetBeneficiariesTest(TestCase):
    """Group enrollment counts enrolled rows, not member headcount."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = LogInHelper().get_or_create_user_api()
        cls.benefit_plan = create_benefit_plan(
            cls.user.username,
            payload_override={
                'code': 'TGTCAPG',
                'type': 'GROUP',
                'max_beneficiaries': None,
            }
        )
        group_beneficiary_service = GroupBeneficiaryService(cls.user)

        # Group 1 has two members, group 2 has one - three individuals in
        # total across two group rows.
        individual1, group1, _ = create_group_with_individual(
            cls.user.username, {'code': 'GRP1'}
        )
        extra_individual = create_individual(
            cls.user.username, {'first_name': 'Extra'}
        )
        add_individual_to_group(
            cls.user.username, extra_individual, group1, is_head=False
        )
        _, group2, _ = create_group_with_individual(
            cls.user.username, {'code': 'GRP2'}
        )

        cls.group_beneficiary_uuids = [
            add_group_to_benefit_plan(
                group_beneficiary_service, group, cls.benefit_plan,
                {'status': 'ACTIVE'},
            )
            for group in (group1, group2)
        ]

    def test_group_enrollment_cap_counts_rows_not_members(self):
        project = create_project(
            'Group cap test project',
            self.benefit_plan,
            self.user.username,
            target_beneficiaries=2,
        )
        service = ProjectEnrollmentService(
            self.user, ProjectEnrollmentService.GROUP
        )
        # Two group rows (three individuals total) enrolled against a
        # target of 2 - must succeed, since the cap counts rows.
        service.enroll_project({
            'ids': self.group_beneficiary_uuids,
            'project_id': str(project.id),
        })
        enrollments = GroupBeneficiaryProjectEnrollment.objects.filter(
            project_id=project.id, is_deleted=False
        )
        self.assertEqual(enrollments.count(), 2)

    def test_group_enrollment_over_target_row_count_raises(self):
        project = create_project(
            'Group cap test project 2',
            self.benefit_plan,
            self.user.username,
            target_beneficiaries=1,
        )
        service = ProjectEnrollmentService(
            self.user, ProjectEnrollmentService.GROUP
        )
        with self.assertRaises(ValueError):
            service.enroll_project({
                'ids': self.group_beneficiary_uuids,
                'project_id': str(project.id),
            })
        enrollments = GroupBeneficiaryProjectEnrollment.objects.filter(
            project_id=project.id, is_deleted=False
        )
        self.assertEqual(enrollments.count(), 0)
