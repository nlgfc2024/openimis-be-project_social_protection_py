import logging
import uuid

from django.core.exceptions import ValidationError
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

logger = logging.getLogger(__name__)


class ProjectService(BaseService):
    OBJECT_TYPE = Project

    def __init__(self, user, validation_class=ProjectValidation):
        super().__init__(user, validation_class)

    @register_service_signal("project_service.create")
    def create(self, obj_data):
        return super().create(obj_data)

    @register_service_signal("project_service.update")
    def update(self, obj_data):
        return super().update(obj_data)

    @register_service_signal("project_service.delete")
    def delete(self, obj_data):
        return super().delete(obj_data)

    @register_service_signal('project_service.undo_delete')
    def undo_delete(self, obj_data):
        self.validation_class.validate_undo_delete(obj_data)
        obj_ = self.OBJECT_TYPE.objects.filter(
            id=obj_data['id']
        ).first()
        obj_.is_deleted = False
        obj_.save(user=self.user)
        return {
            "success": True,
            "message": "Ok",
            "detail": "Undo Delete",
        }


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
        },
        GROUP: {
            'enrollment_model': GroupBeneficiaryProjectEnrollment,
            'time_entry_model': GroupBeneficiaryProjectTimeEntry,
            'beneficiary_model': GroupBeneficiary,
            'fk_field': 'group_beneficiary_id',
            'error_label': 'Group beneficiaries',
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

    @register_service_signal('project_enrollment_service.enroll_project')
    @transaction.atomic
    def enroll_project(self, obj_data):
        project_id = obj_data['project_id']
        beneficiary_ids = {
            uuid.UUID(str(bid)) for bid in obj_data.get('ids', [])
        }

        # Lock the project row for the duration so concurrent enrolls of the same
        # project serialize (avoids the read-then-write race on the exclusive-enroll check).
        project = Project.objects.select_for_update().filter(id=project_id).first()
        if project is None:
            raise ValueError(_("Project %(id)s does not exist.") % {'id': project_id})
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

        # clean() is skipped under bulk_save; enforce ACTIVE + same-program here.
        self._validate_enrollable(project, to_enroll)

        if len(beneficiary_ids) > project.target_beneficiaries and \
            len(beneficiary_ids) > len(currently_enrolled):
            msg = _(
                "This change would bring the project to %(count)s enrolled "
                "%(label)s, exceeding the target of %(target)s."
            ) % {
                'count': len(beneficiary_ids),
                'label': self.config['error_label'].lower(),
                'target': project.target_beneficiaries,
            }
            raise ValueError(msg)

        if not project.allows_multiple_enrollments and to_enroll:
            already_enrolled_elsewhere = set(
                enrollment_model.objects.filter(
                    **{f'{fk_field}__in': to_enroll},
                    is_deleted=False
                ).exclude(
                    project_id=project_id
                ).values_list(fk_field, flat=True)
            )
            if already_enrolled_elsewhere:
                msg = _(
                    "%(label)s %(ids)s are already enrolled in another "
                    "project. This project does not allow multiple "
                    "enrollments."
                ) % {
                    'label': self.config['error_label'],
                    'ids': already_enrolled_elsewhere
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
        time_entries_data = obj_data.get('time_entries', [])

        if not time_entries_data:
            return

        enrollment_model = self.config['enrollment_model']
        time_entry_model = self.config['time_entry_model']

        enrollment_ids = {str(e['enrollment_id']) for e in time_entries_data}
        enrollments = enrollment_model.objects.filter(
            id__in=enrollment_ids,
            is_deleted=False
        ).select_related('project')

        enrollment_map = {str(e.id): e for e in enrollments}
        valid_enrollment_ids = set(enrollment_map.keys())

        if invalid_ids := enrollment_ids - valid_enrollment_ids:
            raise ValueError(
                _('Invalid enrollment IDs: %(ids)s') % {'ids': invalid_ids}
            )

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

            percent = entry['percent_complete']
            if not 0 <= percent <= 100:
                raise ValidationError(
                    _('Percent complete must be between 0 and 100.')
                )

        # Upsert: for entries submitted without an id, resolve the existing row by
        # (enrollment, day_number) so a resubmit updates instead of hitting the
        # unique_together constraint. unique_together=('enrollment','day_number').
        entries_without_id = [e for e in time_entries_data if not e.get('id')]
        if entries_without_id:
            lookup = Q()
            for e in entries_without_id:
                lookup |= Q(
                    enrollment_id=e['enrollment_id'],
                    day_number=e['day_number'],
                )
            existing = {
                (str(te.enrollment_id), te.day_number): te.id
                for te in time_entry_model.objects.filter(
                    lookup, is_deleted=False
                )
            }
            for e in entries_without_id:
                match = existing.get((str(e['enrollment_id']), e['day_number']))
                if match:
                    e['id'] = match

        time_entry_model.bulk_save(
            data_list=time_entries_data,
            user=self.user
        )
