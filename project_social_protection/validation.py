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
    def validate_session(data, existing=None):
        def value(key):
            return data[key] if key in data else getattr(existing, key, None)
        start, end = value('session_start_date'), value('session_end_date')
        if start and end and start > end:
            raise ValidationError("Session end date must not precede its start date.")
        if value('project_type') == 'TRAINING':
            name = value('name')
            if not isinstance(name, str) or not name.strip():
                raise ValidationError("Training name is required.")
            if len(name) > Project._meta.get_field('name').max_length:
                raise ValidationError("Training name must not exceed 255 characters.")
            if not value('location'):
                raise ValidationError("Training location is required.")
        days = value('working_days')
        if value('project_type') == 'TRAINING' and (
            isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 32767
        ):
            raise ValidationError("Working days must be an integer between 1 and 32767.")
        if value('project_type') != 'TRAINING' and 'working_days' in data and days is None:
            raise ValidationError("Working days is required.")

    @staticmethod
    def _validate_required_target_beneficiaries(target_beneficiaries):
        if target_beneficiaries is None:
            return [{"message": _("Target beneficiaries is required.")}]
        return []

    @staticmethod
    def _validate_target_beneficiaries(target_beneficiaries, training=False):
        if target_beneficiaries is None:
            return []

        max_target_beneficiaries = get_max_target_beneficiaries()

        if target_beneficiaries < MIN_TARGET_BENEFICIARIES:
            return [{"message": _(
                "Target beneficiaries must be at least %(min)s."
            ) % {'min': MIN_TARGET_BENEFICIARIES}}]

        if not training and target_beneficiaries > max_target_beneficiaries:
            return [{"message": _(
                "Target beneficiaries cannot exceed %(max)s."
            ) % {'max': max_target_beneficiaries}}]
        return []

    @classmethod
    def validate_create(cls, user, **data):
        cls.validate_session(data)
        bp = data.get('benefit_plan')
        errors = validate_project_unique_name(
            data.get('name'), bp.id if bp else data.get('benefit_plan_id')
        )
        if data.get('project_type', 'WORK') != 'TRAINING':
            errors.extend(cls._validate_required_target_beneficiaries(data.get('target_beneficiaries')))
        errors.extend(cls._validate_target_beneficiaries(data.get('target_beneficiaries'), data.get('project_type') == 'TRAINING'))
        if errors:
            raise ValidationError(errors)

    @classmethod
    def validate_update(cls, user, **data):
        existing = Project.objects.get(id=data['id'])
        cls.validate_session(data, existing)
        if existing.project_type != 'TRAINING' and 'target_beneficiaries' in data and data['target_beneficiaries'] is None:
            raise ValidationError("Target beneficiaries is required.")
        # name is derived and not sent on update, but guard defensively.
        bp = data.get('benefit_plan')
        errors = validate_project_unique_name(
            data.get('name'),
            bp.id if bp else data.get('benefit_plan_id', existing.benefit_plan_id),
            data.get('id'),
        )
        errors.extend(cls._validate_target_beneficiaries(data.get('target_beneficiaries'), existing.project_type == 'TRAINING'))
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
