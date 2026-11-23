import datetime
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from core.models import MutationLog
from social_protection.models import Beneficiary
from social_protection.services import BeneficiaryService

from project_social_protection.apps import ProjectSocialProtectionConfig
from project_social_protection.gql_mutations import UpdateProjectMutation
from project_social_protection.models import BeneficiaryProjectEnrollment, Project
from project_social_protection.services import ProjectEnrollmentService, ProjectService
from project_social_protection.tests.test_district_scope import ProjectDistrictScopeFixtures
from project_social_protection.training_catalogue import TrainingModuleService


@override_settings(ROW_SECURITY=False, MODE='dev')
class TrainingDisqualificationTest(ProjectDistrictScopeFixtures, TestCase):
    """Module A has two topics (columns 1-2) and module B one (column 3)."""

    def setUp(self):
        super().setUp()
        configuration = patch.object(ProjectSocialProtectionConfig, 'training_form', {'fields': {
            'microCatchment': {'required': False}, 'hotspot': {'required': False},
        }})
        configuration.start()
        self.addCleanup(configuration.stop)
        catalogue = TrainingModuleService(self.admin)
        catalogue.save('A Entrepreneurship', [self.plan.pk], [
            {'code': 'DQ01', 'name': 'Business idea'}, {'code': 'DQ02', 'name': 'Starting a business'}])
        catalogue.save('B Management', [self.plan.pk], [{'code': 'DQ03', 'name': 'Business records'}])
        self.set_percentage(90)

    def set_percentage(self, value):
        self.plan.json_ext = {**(self.plan.json_ext or {}), 'training_disqualification_percentage': value}
        self.plan.save(username=self.admin.username)

    def training(self, ticks):
        """An IN_PROGRESS training with beneficiaries 0 and 1 enrolled and ``ticks``
        (columns per beneficiary) recorded."""
        payload = {'name': f'Training {len(Project.objects.all())}', 'benefit_plan': self.plan,
                   'location': self.ta, 'target_beneficiaries': 10}
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'TRAINING'):
            result = ProjectService(self.admin).create(payload)
        self.assertTrue(result['success'], result)
        project = Project.objects.get(pk=result['data']['id'])
        project.status = 'IN_PROGRESS'
        project.save(user=self.admin)
        entries = []
        for beneficiary, columns in zip(self.beneficiaries, ticks):
            enrollment = BeneficiaryProjectEnrollment(project=project, beneficiary=beneficiary)
            enrollment.save(user=self.admin)
            entries.extend({'enrollment_id': str(enrollment.pk), 'day_number': day, 'percent_complete': 100}
                           for day in columns)
        ProjectEnrollmentService(self.admin, ProjectEnrollmentService.INDIVIDUAL).bulk_update_time_entries(
            {'time_entries': entries})
        return project

    def complete(self, project):
        return ProjectService(self.admin).update({'id': project.id, 'status': 'COMPLETED'})

    def test_completing_suspends_participants_below_the_percentage_in_any_module(self):
        project = self.training([[1, 2, 3], [1, 3]])
        result = self.complete(project)
        self.assertTrue(result['success'], result)
        self.assertEqual(result['data']['suspended_participants'], 1)
        passed, failed = (Beneficiary.objects.get(pk=b.pk) for b in self.beneficiaries[:2])
        self.assertEqual(passed.status, 'ACTIVE')
        self.assertNotIn('disqualified_on', passed.json_ext or {})
        self.assertEqual(failed.status, 'SUSPENDED')
        self.assertEqual(failed.json_ext['disqualified_on'], datetime.date.today().isoformat())
        self.assertEqual(failed.json_ext['disqualification_reason'],
                         f"Training '{project.name}': module 'A Entrepreneurship' 50%; below the programme's 90%.")

    def test_a_participant_exactly_at_the_percentage_stays(self):
        self.set_percentage(50)
        result = self.complete(self.training([[1, 3], [3]]))
        self.assertEqual(result['data']['suspended_participants'], 1)
        self.assertEqual(Beneficiary.objects.get(pk=self.beneficiaries[0].pk).status, 'ACTIVE')
        self.assertEqual(Beneficiary.objects.get(pk=self.beneficiaries[1].pk).status, 'SUSPENDED')

    def test_a_programme_without_the_percentage_completes_as_before(self):
        self.set_percentage(None)
        project = self.training([[1], []])
        result = self.complete(project)
        self.assertTrue(result['success'], result)
        self.assertNotIn('suspended_participants', result['data'])
        self.assertEqual({Beneficiary.objects.get(pk=b.pk).status for b in self.beneficiaries[:2]}, {'ACTIVE'})
        # Its trainings can still be reopened.
        self.assertTrue(ProjectService(self.admin).update({'id': project.id, 'status': 'IN_PROGRESS'})['success'])

    def test_a_completed_training_is_locked(self):
        project = self.training([[1, 2, 3], [1, 2, 3]])
        self.complete(project)
        with self.assertRaisesMessage(ValidationError, 'cannot be reopened'):
            ProjectService(self.admin).update({'id': project.id, 'status': 'IN_PROGRESS'})
        enrollment = BeneficiaryProjectEnrollment.objects.filter(project=project).first()
        with self.assertRaisesMessage(ValidationError, 'only be recorded while IN_PROGRESS'):
            ProjectEnrollmentService(self.admin, ProjectEnrollmentService.INDIVIDUAL).bulk_update_time_entries(
                {'time_entries': [{'enrollment_id': str(enrollment.pk), 'day_number': 1, 'percent_complete': 0}]})

    def test_a_failed_suspension_leaves_the_training_and_everyone_unchanged(self):
        project = self.training([[1], [1]])
        with patch.object(BeneficiaryService, 'update', side_effect=[{'success': False, 'detail': 'refused'}]):
            result = self.complete(project)
        self.assertFalse(result['success'])
        self.assertIn('could not be suspended', str(result))
        project.refresh_from_db()
        self.assertEqual(project.status, 'IN_PROGRESS')
        self.assertEqual({Beneficiary.objects.get(pk=b.pk).status for b in self.beneficiaries[:2]}, {'ACTIVE'})

    def test_an_invalid_percentage_stops_the_completion_but_not_other_edits(self):
        project = self.training([[1], [1]])
        self.set_percentage('ninety')
        with self.assertRaisesMessage(ValidationError, 'training_disqualification_percentage must be a number'):
            self.complete(project)
        self.assertTrue(ProjectService(self.admin).update({'id': project.id, 'name': 'Renamed training'})['success'])

    def test_a_training_progresses_through_its_statuses(self):
        payload = {'name': 'Progressing', 'benefit_plan': self.plan, 'location': self.ta, 'target_beneficiaries': 10}
        with patch.object(ProjectSocialProtectionConfig, 'project_type', 'TRAINING'):
            project = Project.objects.get(pk=ProjectService(self.admin).create(payload)['data']['id'])
        self.assertEqual(project.status, 'PREPARATION')
        self.assertTrue(ProjectService(self.admin).update({'id': project.id, 'status': 'IN_PROGRESS'})['success'])
        project.refresh_from_db()
        self.assertEqual(project.status, 'IN_PROGRESS')

    def test_the_mutation_log_reports_how_many_were_suspended(self):
        project = self.training([[1, 2, 3], [2]])
        log = MutationLog(json_content='{}', user=self.admin, client_mutation_id='dq-complete')
        log.save()
        self.assertIsNone(UpdateProjectMutation._mutate(
            self.admin, id=project.id, status='COMPLETED', client_mutation_id='dq-complete'))
        log.refresh_from_db()
        self.assertEqual(log.json_ext['suspended_participants'], 1)
