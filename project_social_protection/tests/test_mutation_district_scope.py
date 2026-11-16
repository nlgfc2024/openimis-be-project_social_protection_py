import uuid

import graphene
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory, TestCase, override_settings

from location.apps import LocationConfig
from location.models import MicroCatchment, Hotspot, UserDistrict
from core.models import MutationLog
from project_social_protection.gql_mutations import (
    CreateProjectMutation, UpdateProjectMutation, DeleteProjectMutation, UndoDeleteProjectMutation,
    ProjectEnrollmentMutation, ProjectGroupEnrollmentMutation,
    BulkUpdateBeneficiaryTimeEntriesMutation, BulkUpdateGroupBeneficiaryTimeEntriesMutation,
)
from project_social_protection.models import Project
from project_social_protection.services import ProjectService, ProjectEnrollmentService
from project_social_protection.tests.test_district_scope import ProjectDistrictScopeFixtures, Query
from project_social_protection.schema import Mutation


@override_settings(ROW_SECURITY=True, MODE='dev')
class ProjectMutationDistrictScopeTest(ProjectDistrictScopeFixtures, TestCase):
    def payload(self, **overrides):
        data = dict(name=f'Mutation {uuid.uuid4()}', benefit_plan=self.plan, activity=self.activity,
                    location=self.villages[0], target_beneficiaries=5, working_days=5)
        data.update(overrides)
        return data

    def service(self, group=False, user=None):
        return ProjectEnrollmentService(user or self.reader,
                                        ProjectEnrollmentService.GROUP if group else ProjectEnrollmentService.INDIVIDUAL)

    def test_create_accepts_other_ta_in_same_district(self):
        result = ProjectService(self.reader).create(self.payload(location=self.villages[1]))
        self.assertTrue(result['success'], result)
        self.assertEqual(Project.objects.get(pk=result['data']['id']).location_id, self.villages[1].pk)

    def test_create_rejects_other_district_and_unassigned_user(self):
        before = Project.objects.count()
        with self.assertRaises(PermissionDenied):
            ProjectService(self.reader).create(self.payload(location=self.villages[2]))
        self.assignment.delete()
        with self.assertRaises(PermissionDenied):
            ProjectService(self.reader).create(self.payload())
        self.assertEqual(Project.objects.count(), before)

    def test_create_rejects_inactive_destination(self):
        from core.utils import TimeUtils
        self.villages[0].validity_to = TimeUtils.now()
        self.villages[0].save()
        with self.assertRaises(ValidationError):
            ProjectService(self.reader).create(self.payload())

    def test_anonymous_is_denied_even_with_row_security_disabled(self):
        with override_settings(ROW_SECURITY=False):
            with self.assertRaises(PermissionDenied):
                ProjectService(AnonymousUser()).create(self.payload())
            with self.assertRaises(PermissionDenied):
                self.service(user=AnonymousUser()).enroll_project({'project_id': self.projects[0].pk, 'ids': []})

    def test_unrestricted_geographic_modes_still_work(self):
        for user, mode in [(self.admin, 'admin'), (self.reader, 'row'), (self.reader, 'location')]:
            with self.subTest(mode=mode):
                context = override_settings(ROW_SECURITY=False) if mode == 'row' else patch.object(
                    LocationConfig, 'no_location_check', mode == 'location')
                with context:
                    result = ProjectService(user).create(self.payload(location=self.villages[2]))
                self.assertTrue(result['success'], result)

    def test_update_cannot_pull_hidden_project_into_scope(self):
        hidden = self.projects[2]
        history = hidden.history.count()
        with self.assertRaises(PermissionDenied):
            ProjectService(self.reader).update({'id': hidden.pk, 'location': self.villages[0]})
        hidden.refresh_from_db()
        self.assertEqual(hidden.location_id, self.villages[2].pk)
        self.assertEqual(hidden.history.count(), history)

    def test_update_cannot_push_project_outside_scope(self):
        with self.assertRaises(PermissionDenied):
            ProjectService(self.reader).update({'id': self.projects[0].pk, 'location_id': self.villages[2].pk})
        self.projects[0].refresh_from_db()
        self.assertEqual(self.projects[0].location_id, self.villages[0].pk)

    def test_update_accepts_authorized_transfer_and_non_location_patch(self):
        service = ProjectService(self.reader)
        for data in [{'location_id': self.villages[1].pk}, {'working_days': 7}]:
            result = service.update({'id': self.projects[0].pk, **data})
            self.assertTrue(result['success'], result)
        self.projects[0].refresh_from_db()
        self.assertEqual(self.projects[0].location_id, self.villages[1].pk)
        self.assertEqual(self.projects[0].working_days, 7)

    def test_conflicting_fk_forms_cannot_bypass_destination_check(self):
        with self.assertRaises(ValidationError):
            ProjectService(self.reader).create(self.payload(location_id=self.villages[2].pk))
        with self.assertRaises(ValidationError):
            ProjectService(self.reader).update({
                'id': self.projects[0].pk, 'location': self.villages[0], 'location_id': self.villages[2].pk,
            })

    def test_missing_and_hidden_projects_return_same_error(self):
        errors = []
        for pk in [self.projects[2].pk, uuid.uuid4()]:
            with self.assertRaises(PermissionDenied) as error:
                ProjectService(self.reader).update({'id': pk, 'working_days': 7})
            errors.append(str(error.exception))
        self.assertEqual(*errors)
        self.assertNotIn(self.projects[2].name, errors[0])

    def test_delete_and_restore_authorized_project(self):
        service = ProjectService(self.reader)
        self.assertTrue(service.delete({'id': self.projects[0].pk})['success'])
        self.projects[0].refresh_from_db()
        self.assertTrue(self.projects[0].is_deleted)
        self.assertTrue(service.undo_delete({'id': self.projects[0].pk})['success'])
        self.projects[0].refresh_from_db()
        self.assertFalse(self.projects[0].is_deleted)

    def test_delete_and_restore_hidden_project_are_denied(self):
        service = ProjectService(self.reader)
        with self.assertRaises(PermissionDenied):
            service.delete({'id': self.projects[2].pk})
        self.projects[2].is_deleted = True
        self.projects[2].save(username=self.admin.username)
        with self.assertRaises(PermissionDenied):
            service.undo_delete({'id': self.projects[2].pk})
        self.projects[2].refresh_from_db()
        self.assertTrue(self.projects[2].is_deleted)

    def test_mixed_scope_delete_and_restore_batches_are_atomic(self):
        for restore, mutation in [(False, DeleteProjectMutation), (True, UndoDeleteProjectMutation)]:
            with self.subTest(restore=restore):
                Project.objects.filter(pk__in=[p.pk for p in self.projects]).update(is_deleted=restore)
                with self.assertRaises(PermissionDenied):
                    mutation._mutate(self.reader, ids=[self.projects[0].pk, self.projects[2].pk])
                self.assertEqual(set(Project.objects.filter(pk__in=[p.pk for p in self.projects]).values_list(
                    'is_deleted', flat=True)), {restore})

    def test_failed_delete_result_rolls_back_previous_deletion(self):
        original = ProjectService.delete

        def fail_second(service, data):
            if data['id'] == sorted([self.projects[0].pk, self.projects[1].pk], key=str)[1]:
                return {'success': False, 'message': 'failure'}
            return original(service, data)

        with patch.object(ProjectService, 'delete', fail_second):
            result = ProjectService(self.reader).change_deleted([self.projects[0].pk, self.projects[1].pk])
        self.assertFalse(result['success'])
        self.assertFalse(Project.objects.filter(pk__in=[p.pk for p in self.projects], is_deleted=True).exists())

    def catchment(self, district, suffix):
        return MicroCatchment.objects.create(code=f'MUT-{suffix}', name=f'Mutation {suffix}',
                                             district=district, audit_user_id=-1)

    def test_optional_relations_must_match_district_and_each_other(self):
        first = self.catchment(self.a, 'A')
        second = self.catchment(self.a, 'A2')
        foreign = self.catchment(self.b, 'B')
        hotspot = Hotspot.objects.create(code='MUT-H', name='Mutation H', micro_catchment=first, audit_user_id=-1)
        result = ProjectService(self.reader).create(self.payload(micro_catchment=first, hotspot=hotspot))
        self.assertTrue(result['success'], result)
        for data in [dict(micro_catchment=foreign), dict(micro_catchment=second, hotspot=hotspot)]:
            with self.subTest(data=data), self.assertRaises(ValidationError):
                ProjectService(self.reader).create(self.payload(**data))
        # Retained relationships are checked when only location or catchment changes.
        with self.assertRaises(ValidationError):
            ProjectService(self.admin).update({'id': result['data']['id'], 'location': self.villages[2]})
        with self.assertRaises(ValidationError):
            ProjectService(self.reader).update({'id': result['data']['id'], 'micro_catchment': second})

    def test_invalid_optional_relation_ids_are_not_silently_cleared(self):
        for field in ['micro_catchment_id', 'hotspot_id']:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                ProjectService(self.reader).create(self.payload(**{field: 2147483647}))
        with self.assertRaises(ValidationError):
            CreateProjectMutation._mutate(self.reader, benefit_plan_id=self.plan.pk, activity_id=self.activity.pk,
                                          location_id=self.villages[0].uuid, micro_catchment_id=2147483647,
                                          target_beneficiaries=5, working_days=5)

    def test_deleted_parent_cannot_be_enrolled_or_receive_attendance(self):
        self.projects[0].is_deleted = True
        self.projects[0].save(username=self.admin.username)
        for method, payload in [('enroll_project', {'project_id': self.projects[0].pk, 'ids': []}),
                                ('bulk_update_time_entries', {'time_entries': [self.entry_payload()]})]:
            with self.subTest(method=method), self.assertRaises(PermissionDenied):
                getattr(self.service(), method)(payload)

    def test_individual_and_group_enrollment_reject_hidden_projects(self):
        for group, projects in [(False, self.projects), (True, self.group_projects)]:
            with self.subTest(group=group), self.assertRaises(PermissionDenied):
                self.service(group).enroll_project({'project_id': projects[2].pk, 'ids': []})

    def test_assign_and_remove_preserve_narrower_beneficiary_scope(self):
        for group, projects, beneficiaries, enrollments in [
            (False, self.projects, self.beneficiaries, self.enrolments),
            (True, self.group_projects, self.group_beneficiaries, self.group_enrolments),
        ]:
            with self.subTest(group=group):
                service = self.service(group)
                # Project in another TA is visible, but its existing beneficiary is not.
                with self.assertRaises(PermissionDenied):
                    service.enroll_project({'project_id': projects[1].pk, 'ids': []})
                with self.assertRaises(PermissionDenied):
                    service.enroll_project({'project_id': projects[0].pk,
                                            'ids': [beneficiaries[0].pk, beneficiaries[1].pk]})
                service.enroll_project({'project_id': projects[0].pk, 'ids': []})
                enrollments[0].refresh_from_db()
                self.assertTrue(enrollments[0].is_deleted)
                service.enroll_project({'project_id': projects[0].pk, 'ids': [beneficiaries[0].pk]})
                enrollments[0].refresh_from_db()
                self.assertFalse(enrollments[0].is_deleted)

    def test_hidden_global_conflicts_block_assignment_without_leaking_details(self):
        from project_social_protection.models import BeneficiaryProjectEnrollment, GroupBeneficiaryProjectEnrollment
        for group, projects, beneficiaries, model in [
            (False, self.projects, self.beneficiaries, BeneficiaryProjectEnrollment),
            (True, self.group_projects, self.group_beneficiaries, GroupBeneficiaryProjectEnrollment),
        ]:
            with self.subTest(group=group):
                projects[0].allows_multiple_enrollments = False
                projects[0].save(username=self.admin.username)
                model.objects.filter(project=projects[0]).update(is_deleted=True)
                model(project=projects[2], **{'group_beneficiary' if group else 'beneficiary': beneficiaries[0]}).save(
                    username=self.admin.username)
                with self.assertRaises(ValueError) as error:
                    self.service(group).enroll_project({'project_id': projects[0].pk, 'ids': [beneficiaries[0].pk]})
                self.assertNotIn(projects[2].name, str(error.exception))
                self.assertNotIn(str(projects[2].pk), str(error.exception))

    def entry_payload(self, index=0, group=False, **changes):
        enrollments = self.group_enrolments if group else self.enrolments
        return dict(enrollment_id=str(enrollments[index].pk), day_number=1, percent_complete=75, **changes)

    def test_attendance_accepts_authorized_enrollment_and_rejects_hidden_batch(self):
        for group, entries in [(False, self.entries), (True, self.group_entries)]:
            with self.subTest(group=group):
                service = self.service(group)
                service.bulk_update_time_entries({'time_entries': [self.entry_payload(group=group)]})
                entries[0].refresh_from_db()
                self.assertEqual(entries[0].percent_complete, 75)
                with self.assertRaises(PermissionDenied):
                    service.bulk_update_time_entries({'time_entries': [
                        self.entry_payload(group=group, clear=True), self.entry_payload(2, group=group),
                    ]})
                entries[0].refresh_from_db()
                self.assertFalse(entries[0].is_deleted)

    def test_attendance_ids_cannot_reparent_or_change_other_rows(self):
        for group, entries in [(False, self.entries), (True, self.group_entries)]:
            for entry in [entries[1], entries[2]]:
                with self.subTest(group=group, entry=entry.pk), self.assertRaises(PermissionDenied):
                    self.service(group).bulk_update_time_entries({'time_entries': [
                        self.entry_payload(group=group, id=str(entry.pk)),
                    ]})
                entry.refresh_from_db()
                self.assertEqual(entry.percent_complete, 50 if not group else 75)

    def test_missing_enrollment_and_entry_ids_are_rejected(self):
        for payload in [dict(enrollment_id=str(uuid.uuid4()), day_number=1, percent_complete=80),
                        self.entry_payload(id=str(uuid.uuid4()))]:
            with self.subTest(payload=payload), self.assertRaises(PermissionDenied):
                self.service().bulk_update_time_entries({'time_entries': [payload]})

    def test_graphql_update_denies_original_before_name_rebuild(self):
        with patch('project_social_protection.gql_mutations.generate_project_name') as generate:
            result = UpdateProjectMutation.async_mutate(self.reader, id=self.projects[2].pk,
                                                        location_id=self.villages[0].uuid, known_place='Changed')
        # Existing operation permission checks are retained (grant for this test below).
        self.assertTrue(result)
        generate.assert_not_called()
        with patch.object(type(self.reader), 'has_perms', return_value=True):
            result = UpdateProjectMutation.async_mutate(self.reader, id=self.projects[2].pk,
                                                        location_id=self.villages[0].uuid, known_place='Changed')
        self.assertIn('unauthorized', str(result))
        self.projects[2].refresh_from_db()
        self.assertEqual(self.projects[2].location_id, self.villages[2].pk)

    def test_operation_permissions_are_still_required(self):
        for mutation in [CreateProjectMutation, UpdateProjectMutation, DeleteProjectMutation, UndoDeleteProjectMutation,
                         ProjectEnrollmentMutation, ProjectGroupEnrollmentMutation,
                         BulkUpdateBeneficiaryTimeEntriesMutation, BulkUpdateGroupBeneficiaryTimeEntriesMutation]:
            with self.subTest(mutation=mutation.__name__), patch.object(type(self.reader), 'has_perms', return_value=False):
                with self.assertRaises((PermissionDenied, ValidationError)):
                    mutation._validate_mutation(self.reader)

    def test_transfer_between_two_assigned_districts_is_allowed(self):
        UserDistrict.objects.create(user=self.reader.i_user, location=self.b_ta, audit_user_id=-1)
        result = ProjectService(self.reader).update({'id': self.projects[2].pk, 'location': self.villages[0]})
        self.assertTrue(result['success'], result)
        self.projects[2].refresh_from_db()
        self.assertEqual(self.projects[2].location_id, self.villages[0].pk)

    def test_reused_service_sees_assignment_revocation(self):
        service = ProjectService(self.reader)
        self.assertTrue(service.update({'id': self.projects[0].pk, 'working_days': 6})['success'])
        self.assignment.delete()
        with self.assertRaises(PermissionDenied):
            service.update({'id': self.projects[0].pk, 'working_days': 7})
        self.projects[0].refresh_from_db()
        self.assertEqual(self.projects[0].working_days, 6)

    def test_attendance_in_another_ta_of_assigned_district_is_allowed(self):
        for group, entries in [(False, self.entries), (True, self.group_entries)]:
            self.service(group).bulk_update_time_entries({'time_entries': [self.entry_payload(1, group=group)]})
            entries[1].refresh_from_db()
            self.assertEqual(entries[1].percent_complete, 75)

    def test_restore_batch_rolls_back_when_later_relation_validation_fails(self):
        service = ProjectService(self.reader)
        ordered = sorted(self.projects[:2], key=lambda p: str(p.pk))
        for project in ordered:
            project.is_deleted = True
            project.save(username=self.admin.username)
        ordered[1].micro_catchment = self.catchment(self.b, 'INVALID-RESTORE')
        ordered[1].save(username=self.admin.username)
        with self.assertRaises(ValidationError):
            service.change_deleted([p.pk for p in ordered], restore=True)
        for project in ordered:
            project.refresh_from_db()
            self.assertTrue(project.is_deleted)

    def test_graphql_hidden_mutations_fail_without_changing_domain_rows(self):
        schema = graphene.Schema(query=Query, mutation=Mutation)
        request = RequestFactory().post('/graphql')
        request.user = self.reader
        hidden = self.projects[2]
        inputs = [
            ('updateProject', 'UpdateProjectMutationInput',
             {'id': str(hidden.pk), 'locationId': str(self.villages[0].uuid), 'knownPlace': 'Pulled'}),
            ('deleteProject', 'DeleteProjectMutationInput', {'ids': [str(hidden.pk)]}),
            ('enrollProject', 'ProjectEnrollmentMutationInput', {'projectId': str(hidden.pk), 'ids': []}),
            ('enrollGroupProject', 'ProjectGroupEnrollmentMutationInput',
             {'projectId': str(self.group_projects[2].pk), 'ids': []}),
            ('bulkUpdateBeneficiaryTimeEntries', 'BulkUpdateBeneficiaryTimeEntriesMutationInput',
             {'timeEntries': [{'enrollmentId': str(self.enrolments[2].pk), 'dayNumber': 1, 'clear': True}]}),
            ('bulkUpdateGroupBeneficiaryTimeEntries', 'BulkUpdateGroupBeneficiaryTimeEntriesMutationInput',
             {'timeEntries': [{'enrollmentId': str(self.group_enrolments[2].pk), 'dayNumber': 1, 'clear': True}]}),
        ]
        for field, input_type, data in inputs:
            with self.subTest(field=field), patch.object(type(self.reader), 'has_perms', return_value=True):
                result = schema.execute(
                    f'mutation($input:{input_type}!) {{ {field}(input:$input) {{ internalId }} }}',
                    context_value=request, variable_values={'input': data},
                )
                self.assertIsNone(result.errors, result.errors)
                log = MutationLog.objects.get(pk=result.data[field]['internalId'])
                self.assertEqual(log.status, MutationLog.ERROR)
                self.assertIn('unauthorized', log.error)
                self.assertNotIn(hidden.name, log.error)
        hidden.refresh_from_db()
        self.assertFalse(hidden.is_deleted)
        self.assertEqual(hidden.location_id, self.villages[2].pk)
        for entry in [self.entries[2], self.group_entries[2]]:
            entry.refresh_from_db()
            self.assertFalse(entry.is_deleted)

    def test_service_identity_aliases_cannot_overwrite_a_hidden_project(self):
        service = ProjectService(self.reader)
        for key in ['id', 'pk', 'uuid']:
            with self.subTest(create_key=key), self.assertRaises(ValidationError):
                service.create(self.payload(**{key: self.projects[2].pk}))
        for key in ['pk', 'uuid']:
            with self.subTest(update_key=key), self.assertRaises(ValidationError):
                service.update({'id': self.projects[0].pk, key: self.projects[2].pk})
        self.projects[2].refresh_from_db()
        self.assertEqual(self.projects[2].location_id, self.villages[2].pk)

    def test_attendance_accepts_matching_string_ids_without_creating_duplicates(self):
        for group, entries in [(False, self.entries), (True, self.group_entries)]:
            entry = entries[0]
            self.service(group).bulk_update_time_entries({'time_entries': [
                self.entry_payload(group=group, id=str(entry.pk)),
            ]})
            entry.refresh_from_db()
            self.assertEqual(entry.percent_complete, 75)
            self.assertEqual(type(entry).objects.filter(enrollment=entry.enrollment, day_number=1).count(), 1)

    def test_direct_attendance_payload_cannot_override_authorized_enrollment(self):
        for group, enrollments in [(False, self.enrolments), (True, self.group_enrolments)]:
            with self.subTest(group=group), self.assertRaises(ValidationError):
                self.service(group).bulk_update_time_entries({'time_entries': [
                    self.entry_payload(group=group, enrollment=enrollments[2]),
                ]})

    def test_scoped_removal_can_clean_up_soft_deleted_beneficiaries(self):
        for group, beneficiaries, projects, enrollments in [
            (False, self.beneficiaries, self.projects, self.enrolments),
            (True, self.group_beneficiaries, self.group_projects, self.group_enrolments),
        ]:
            with self.subTest(group=group):
                type(beneficiaries[0]).objects.filter(pk=beneficiaries[0].pk).update(is_deleted=True)
                self.service(group).enroll_project({'project_id': projects[0].pk, 'ids': []})
                enrollments[0].refresh_from_db()
                self.assertTrue(enrollments[0].is_deleted)
