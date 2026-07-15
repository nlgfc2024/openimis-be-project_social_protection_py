import logging
import uuid

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from core.services import BaseService
from core.signals import register_service_signal
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
            'fk_field': 'beneficiary_id',
            'error_label': 'Beneficiaries',
        },
        GROUP: {
            'enrollment_model': GroupBeneficiaryProjectEnrollment,
            'time_entry_model': GroupBeneficiaryProjectTimeEntry,
            'fk_field': 'group_beneficiary_id',
            'error_label': 'Group beneficiaries',
        },
    }

    def __init__(self, user, enrollment_type):
        self.user = user
        self.enrollment_type = enrollment_type
        self.config = self.CONFIGS[enrollment_type]

    @register_service_signal('project_enrollment_service.enroll_project')
    def enroll_project(self, obj_data):
        project_id = obj_data['project_id']
        beneficiary_ids = {
            uuid.UUID(str(bid)) for bid in obj_data.get('ids', [])
        }

        project = Project.objects.get(id=project_id)
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

        time_entry_model.bulk_save(
            data_list=time_entries_data,
            user=self.user
        )
