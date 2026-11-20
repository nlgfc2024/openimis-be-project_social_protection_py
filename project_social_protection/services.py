import logging
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext as _

from core.services import BaseService
from core.signals import register_service_signal
from social_protection.models import (
    Beneficiary,
    GroupBeneficiary,
    BeneficiaryStatus,
)
from project_social_protection.models import (
    Project,
    BeneficiaryProjectTimeEntry,
    GroupBeneficiaryProjectTimeEntry,
    BeneficiaryProjectEnrollment,
    GroupBeneficiaryProjectEnrollment,
)
from project_social_protection.validation import ProjectValidation
from project_social_protection.mutation_scope import (
    authorized_project, require_authenticated, validate_destination,
)

logger = logging.getLogger(__name__)


class ProjectService(BaseService):
    OBJECT_TYPE = Project

    def __init__(self, user, validation_class=ProjectValidation):
        super().__init__(user, validation_class)

    @register_service_signal("project_service.create")
    @transaction.atomic
    def create(self, obj_data):
        validate_destination(self.user, obj_data)
        return super().create(obj_data)

    @register_service_signal("project_service.update")
    @transaction.atomic
    def update(self, obj_data):
        project = authorized_project(self.user, obj_data['id'])
        validate_destination(self.user, obj_data, project)

        destination_plan = obj_data.get('benefit_plan')
        destination_plan_id = getattr(
            destination_plan,
            'pk',
            obj_data.get('benefit_plan_id'),
        )
        is_plan_transfer = (
            destination_plan_id is not None
            and str(destination_plan_id) != str(project.benefit_plan_id)
        )
        if is_plan_transfer and (
            BeneficiaryProjectEnrollment.objects.filter(project=project).exists()
            or GroupBeneficiaryProjectEnrollment.objects.filter(project=project).exists()
        ):
            raise ValidationError(_(
                "A Project with individual or group enrolments cannot be moved "
                "to another Benefit Plan."
            ))

        return super().update(obj_data)

    @register_service_signal("project_service.delete")
    @transaction.atomic
    def delete(self, obj_data):
        authorized_project(self.user, obj_data['id'])
        return super().delete(obj_data)

    @register_service_signal('project_service.undo_delete')
    @transaction.atomic
    def undo_delete(self, obj_data):
        obj_ = authorized_project(self.user, obj_data['id'], deleted=True)
        validate_destination(self.user, {}, obj_)
        self.validation_class.validate_undo_delete(obj_data)
        obj_.is_deleted = False
        obj_.save(user=self.user)
        return {
            "success": True,
            "message": "Ok",
            "detail": "Undo Delete",
        }

    @transaction.atomic
    def change_deleted(self, ids, restore=False):
        # Preflight the entire batch before the first write. Lock in stable order.
        ids = sorted(set(ids), key=str)
        for project_id in ids:
            authorized_project(self.user, project_id, deleted=restore)
        for project_id in ids:
            result = (self.undo_delete if restore else self.delete)({'id': project_id})
            if not result['success']:
                # BaseService reports validation/save failures rather than raising.
                transaction.set_rollback(True)
                return result


class ProjectEnrollmentService:
    INDIVIDUAL = 'INDIVIDUAL'
    GROUP = 'GROUP'

    CONFIGS = {
        INDIVIDUAL: {
            'enrollment_model': BeneficiaryProjectEnrollment,
            'time_entry_model': BeneficiaryProjectTimeEntry,
            'beneficiary_model': Beneficiary,
            'fk_field': 'beneficiary_id',
            'error_label': 'Beneficiaries',
            'select_related': ('beneficiary__individual',),
        },
        GROUP: {
            'enrollment_model': GroupBeneficiaryProjectEnrollment,
            'time_entry_model': GroupBeneficiaryProjectTimeEntry,
            'beneficiary_model': GroupBeneficiary,
            'fk_field': 'group_beneficiary_id',
            'error_label': 'Group beneficiaries',
            'select_related': ('group_beneficiary__group',),
        },
    }

    def _validate_enrollable(self, project, beneficiary_ids):
        """Enforce the invariants that BeneficiaryProjectEnrollment.clean() would —
        clean() is bypassed because enroll persists via bulk_save. A beneficiary must
        exist, be ACTIVE, and belong to the same benefit plan (program) as the project."""
        if not beneficiary_ids:
            return
        beneficiary_model = self.config['beneficiary_model']
        rows = {
            b['id']: b
            for b in beneficiary_model.objects.filter(
                id__in=beneficiary_ids, is_deleted=False
            ).values('id', 'status', 'benefit_plan_id')
        }
        bad = []
        for bid in beneficiary_ids:
            b = rows.get(bid)
            if (b is None
                    or b['status'] != BeneficiaryStatus.ACTIVE
                    or b['benefit_plan_id'] != project.benefit_plan_id):
                bad.append(str(bid))
        if bad:
            raise ValueError(
                _("%(label)s %(ids)s cannot be enrolled: they must exist, be "
                  "ACTIVE and belong to the project's program.")
                % {'label': self.config['error_label'], 'ids': ', '.join(bad)}
            )

    def __init__(self, user, enrollment_type):
        self.user = user
        self.enrollment_type = enrollment_type
        self.config = self.CONFIGS[enrollment_type]

    def _describe_enrollment_conflict(self, enrollment):
        """Return a user-facing description without exposing internal UUIDs."""
        if self.enrollment_type == self.INDIVIDUAL:
            individual = enrollment.beneficiary.individual
            beneficiary_name = ' '.join(
                part for part in (individual.first_name, individual.last_name) if part
            ) or _('Unnamed beneficiary')
        else:
            beneficiary_name = enrollment.group_beneficiary.group.code or _(
                'Unnamed group beneficiary'
            )

        return _('%(beneficiary)s (project: %(project)s)') % {
            'beneficiary': beneficiary_name,
            'project': enrollment.project.name,
        }

    @register_service_signal('project_enrollment_service.enroll_project')
    @transaction.atomic
    def enroll_project(self, obj_data):
        project_id = obj_data['project_id']
        beneficiary_ids = {
            uuid.UUID(str(bid)) for bid in obj_data.get('ids', [])
        }

        # Lock the project row for the duration so concurrent enrolls of the same
        # project serialize (avoids the read-then-write race on the exclusive-enroll check).
        project = authorized_project(self.user, project_id)
        enrollment_model = self.config['enrollment_model']
        fk_field = self.config['fk_field']

        # including deleted
        all_enrollments = {
            getattr(e, fk_field): e
            for e in enrollment_model.objects.filter(project_id=project_id)
        }

        currently_enrolled = {
            bid for bid, e in all_enrollments.items() if not e.is_deleted
        }

        to_enroll = beneficiary_ids - currently_enrolled
        to_unenroll = currently_enrolled - beneficiary_ids

        changed_ids = to_enroll | to_unenroll
        visible_ids = set(self.config['beneficiary_model'].get_queryset(None, self.user).filter(
            pk__in=changed_ids,
        ).values_list('pk', flat=True))
        if changed_ids - visible_ids:
            raise PermissionDenied(_("unauthorized"))

        # clean() is skipped under bulk_save; enforce ACTIVE + same-program here.
        self._validate_enrollable(project, to_enroll)

        active_individual_count = BeneficiaryProjectEnrollment.objects.filter(
            project_id=project_id,
            is_deleted=False,
        ).count()
        active_group_count = GroupBeneficiaryProjectEnrollment.objects.filter(
            project_id=project_id,
            is_deleted=False,
        ).count()

        # Compute total assigned count after applying this mutation's delta.
        # This keeps server-side cap enforcement independent from client behavior.
        total_assigned_before_change = active_individual_count + active_group_count
        current_type_delta = len(to_enroll) - len(to_unenroll)
        total_assigned_after_change = (
            total_assigned_before_change + current_type_delta
        )

        # If a project is already over target, allow mutations that reduce
        # assigned beneficiaries so users can recover back under the cap.
        if (
            total_assigned_after_change > project.target_beneficiaries
            and total_assigned_after_change > total_assigned_before_change
        ):
            msg = _(
                "This change would bring the project to %(count)s assigned "
                "beneficiaries, exceeding the target of %(target)s."
            ) % {
                'count': total_assigned_after_change,
                'target': project.target_beneficiaries,
            }
            raise ValueError(msg)

        if not project.allows_multiple_enrollments and to_enroll:
            already_enrolled_elsewhere = enrollment_model.objects.filter(
                **{f'{fk_field}__in': to_enroll},
                is_deleted=False,
            ).exclude(
                project_id=project_id
            ).select_related(
                'project', *self.config['select_related']
            ).order_by('project__name')
            if already_enrolled_elsewhere:
                visible_projects = set(Project.get_queryset(None, self.user).filter(
                    pk__in=already_enrolled_elsewhere.values('project_id'),
                ).values_list('pk', flat=True))
                if any(row.project_id not in visible_projects for row in already_enrolled_elsewhere):
                    raise ValueError(_("Cannot assign beneficiaries: an existing project assignment conflicts."))
                msg = _(
                    "Cannot assign %(label)s because they are already attached "
                    "to another project in this phase: %(conflicts)s."
                ) % {
                    'label': self.config['error_label'],
                    'conflicts': '; '.join(
                        self._describe_enrollment_conflict(enrollment)
                        for enrollment in already_enrolled_elsewhere
                    ),
                }
                raise ValueError(msg)

        data_list = []

        for beneficiary_id in to_unenroll:
            enrollment = all_enrollments[beneficiary_id]
            data_list.append({'id': enrollment.id, 'is_deleted': True})

        for beneficiary_id in to_enroll:
            if beneficiary_id in all_enrollments:
                enrollment = all_enrollments[beneficiary_id]
                data_list.append({'id': enrollment.id, 'is_deleted': False})
            else:
                data_list.append(
                    {fk_field: beneficiary_id, 'project_id': project_id}
                )

        enrollment_model.bulk_save(
            data_list, self.user, include_deleted=True
        )

    @register_service_signal(
        'project_enrollment_service.bulk_update_time_entries'
    )
    @transaction.atomic
    def bulk_update_time_entries(self, obj_data):
        require_authenticated(self.user)
        time_entries_data = [dict(entry) for entry in obj_data.get('time_entries', [])]
        fields = {'id', 'enrollment_id', 'day_number', 'percent_complete', 'clear'}
        if any(set(entry) - fields for entry in time_entries_data):
            raise ValidationError(_("Invalid time-entry fields."))

        if not time_entries_data:
            return

        enrollment_model = self.config['enrollment_model']
        time_entry_model = self.config['time_entry_model']

        enrollment_ids = {str(e['enrollment_id']) for e in time_entries_data}
        project_ids = set(enrollment_model.objects.filter(
            pk__in=enrollment_ids, is_deleted=False,
        ).values_list('project_id', flat=True))
        locked_projects = set(Project.get_queryset(Project.objects.select_for_update(), self.user).filter(
            pk__in=project_ids, is_deleted=False,
        ).order_by('pk').values_list('pk', flat=True))
        if project_ids - locked_projects:
            raise PermissionDenied(_("unauthorized"))
        enrollments = enrollment_model.objects.select_for_update().filter(
            id__in=enrollment_ids, is_deleted=False, project_id__in=locked_projects,
        ).select_related('project')

        enrollment_map = {str(e.id): e for e in enrollments}
        valid_enrollment_ids = set(enrollment_map.keys())

        if enrollment_ids - valid_enrollment_ids:
            raise PermissionDenied(_("unauthorized"))

        supplied_ids = {entry['id'] for entry in time_entries_data if entry.get('id')}
        existing_entries = {
            str(row.pk): row for row in time_entry_model.objects.select_for_update().filter(pk__in=supplied_ids)
        }
        for entry in time_entries_data:
            if entry.get('id'):
                stored = existing_entries.get(str(entry['id']))
                if (stored is None or str(stored.enrollment_id) != str(entry['enrollment_id'])
                        or stored.day_number != entry['day_number']):
                    raise PermissionDenied(_("unauthorized"))
                entry['id'] = stored.pk

        # Reject duplicate (enrollment, day) pairs within the batch — they would
        # otherwise fight over the same unique row.
        seen = set()
        for entry in time_entries_data:
            key = (str(entry['enrollment_id']), entry['day_number'])
            if key in seen:
                raise ValidationError(
                    _('Duplicate time entry for enrollment %(e)s day %(d)s.')
                    % {'e': entry['enrollment_id'], 'd': entry['day_number']}
                )
            seen.add(key)

        for entry in time_entries_data:
            enrollment = enrollment_map[str(entry['enrollment_id'])]
            project = enrollment.project

            day = entry['day_number']
            if not 1 <= day <= project.working_days:
                raise ValidationError(
                    _('Day number must be between 1 and %(working_days)s.')
                    % {'working_days': project.working_days}
                )

            if entry.get('clear'):
                continue

            percent = entry.get('percent_complete')
            if percent is None:
                raise ValidationError(
                    _('Percent complete is required unless clearing an attendance entry.')
                )
            if not 0 <= percent <= 100:
                raise ValidationError(
                    _('Percent complete must be between 0 and 100.')
                )

        # `clear` is an API-only instruction, not a time-entry model field. Split
        # it out before bulk_save so `clear: false` cannot be passed to the ORM.
        clear_entries = []
        entries_to_save = []
        for entry in time_entries_data:
            if entry.pop('clear', False):
                clear_entries.append(entry)
            else:
                entries_to_save.append(entry)

        # A cleared cell returns to "not recorded". Remove its active time entry
        # so the absence/presence value is not retained in the database.
        for entry in clear_entries:
            existing_entry = time_entry_model.objects.filter(
                enrollment_id=entry['enrollment_id'],
                day_number=entry['day_number'],
                is_deleted=False,
            ).first()
            if existing_entry:
                existing_entry.delete(user=self.user)

        time_entries_data = entries_to_save
        if not time_entries_data:
            return

        # Upsert: include soft-deleted rows in the lookup. A cleared day retains
        # its unique (enrollment, day_number) row, so recording that day again must
        # revive the old row rather than attempting a duplicate insert.
        entries_without_id = [e for e in time_entries_data if not e.get('id')]
        if entries_without_id:
            lookup = Q()
            for e in entries_without_id:
                lookup |= Q(
                    enrollment_id=e['enrollment_id'],
                    day_number=e['day_number'],
                )
            existing = {
                (str(te.enrollment_id), te.day_number): te
                for te in time_entry_model.objects.filter(lookup)
            }
            for e in entries_without_id:
                match = existing.get((str(e['enrollment_id']), e['day_number']))
                if match:
                    e['id'] = match.id
                    if match.is_deleted:
                        e['is_deleted'] = False

        time_entry_model.bulk_save(
            data_list=time_entries_data,
            user=self.user,
            include_deleted=True,
        )
