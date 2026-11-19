from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from core.validation import BaseModelValidation, ObjectExistsValidationMixin
from project_social_protection.apps import (
    DEFAULT_MAX_TARGET_BENEFICIARIES,
    ProjectSocialProtectionConfig,
)
from project_social_protection.models import Project

MIN_TARGET_BENEFICIARIES = 1


def get_max_target_beneficiaries():
    configured = getattr(
        ProjectSocialProtectionConfig,
        'max_target_beneficiaries',
        DEFAULT_MAX_TARGET_BENEFICIARIES,
    )
    try:
        return int(configured)
    except (TypeError, ValueError):
        return DEFAULT_MAX_TARGET_BENEFICIARIES


def validate_project_unique_name(name, benefit_plan_id, uuid=None):
    if not name or not benefit_plan_id:
        return []
    instance = Project.objects.filter(
        name=name, benefit_plan__id=benefit_plan_id, is_deleted=False
    ).exclude(id=uuid).first()
    if instance:
        return [{"message": _(
            "Project name '%(name)s' already exists for this program."
        ) % {'name': name}}]
    return []


class ProjectValidation(BaseModelValidation, ObjectExistsValidationMixin):
    OBJECT_TYPE = Project

    @staticmethod
    def _validate_session(data, project=None):
        def effective(field):
            return data[field] if field in data else getattr(project, field, None)

        errors = []
        start, end = effective('session_start_date'), effective('session_end_date')
        if start and end and start > end:
            errors.append({'message': _('Session end date must not precede session start date.')})
        project_type = project.project_type if project else data.get(
            'project_type', ProjectSocialProtectionConfig.project_type)
        if project_type == 'TRAINING':
            name = effective('name')
            if not isinstance(name, str) or not name.strip():
                errors.append({'message': _('Training name is required.')})
            elif len(name) > Project._meta.get_field('name').max_length:
                errors.append({'message': _('Training name cannot exceed 255 characters.')})
        else:
            if not effective('activity') and not effective('activity_id'):
                errors.append({'message': _('A work project requires an Activity.')})
            days = effective('working_days')
            if not isinstance(days, int) or isinstance(days, bool) or days < 1:
                errors.append({'message': _('A work project requires positive working days.')})
        return errors

    @staticmethod
    def _validate_required_target_beneficiaries(target_beneficiaries):
        if target_beneficiaries is None:
            return [{"message": _("Target beneficiaries is required.")}]
        return []

    @staticmethod
    def _validate_target_beneficiaries(target_beneficiaries):
        if target_beneficiaries is None:
            return []

        max_target_beneficiaries = get_max_target_beneficiaries()

        if target_beneficiaries < MIN_TARGET_BENEFICIARIES:
            return [{"message": _(
                "Target beneficiaries must be at least %(min)s."
            ) % {'min': MIN_TARGET_BENEFICIARIES}}]

        if target_beneficiaries > max_target_beneficiaries:
            return [{"message": _(
                "Target beneficiaries cannot exceed %(max)s."
            ) % {'max': max_target_beneficiaries}}]
        return []

    @classmethod
    def validate_create(cls, user, **data):
        bp = data.get('benefit_plan')
        errors = validate_project_unique_name(
            data.get('name'), bp.id if bp else data.get('benefit_plan_id')
        )
        errors.extend(cls._validate_session(data))
        errors.extend(
            cls._validate_required_target_beneficiaries(
                data.get('target_beneficiaries')
            )
        )
        errors.extend(cls._validate_target_beneficiaries(data.get('target_beneficiaries')))
        if errors:
            raise ValidationError(errors)

    @classmethod
    def validate_update(cls, user, **data):
        project = Project.objects.get(pk=data['id'])
        bp = data.get('benefit_plan')
        errors = validate_project_unique_name(
            data.get('name', project.name),
            bp.id if bp else data.get('benefit_plan_id', project.benefit_plan_id),
            data.get('id'),
        )
        errors.extend(cls._validate_session(data, project))
        errors.extend(cls._validate_target_beneficiaries(data.get('target_beneficiaries')))
        if errors:
            raise ValidationError(errors)

    @classmethod
    def validate_undo_delete(cls, data):
        obj_id = data.get('id')
        cls.validate_object_exists(obj_id)
        obj = Project.objects.get(id=obj_id)
        errors = validate_project_unique_name(
            obj.name, obj.benefit_plan_id, obj_id
        )
        if errors:
            raise ValidationError(errors)
