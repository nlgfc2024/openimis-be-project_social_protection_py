from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from core.validation import BaseModelValidation, ObjectExistsValidationMixin
from project_social_protection.models import Project

MAX_TARGET_BENEFICIARIES = 200


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
    def _validate_target_beneficiaries(target_beneficiaries):
        if target_beneficiaries is None:
            return []
        if target_beneficiaries > MAX_TARGET_BENEFICIARIES:
            return [{"message": _(
                "Target beneficiaries cannot exceed %(max)s."
            ) % {'max': MAX_TARGET_BENEFICIARIES}}]
        return []

    @classmethod
    def validate_create(cls, user, **data):
        bp = data.get('benefit_plan')
        errors = validate_project_unique_name(
            data.get('name'), bp.id if bp else data.get('benefit_plan_id')
        )
        errors.extend(cls._validate_target_beneficiaries(data.get('target_beneficiaries')))
        if errors:
            raise ValidationError(errors)

    @classmethod
    def validate_update(cls, user, **data):
        # name is derived and not sent on update, but guard defensively.
        bp = data.get('benefit_plan')
        errors = validate_project_unique_name(
            data.get('name'),
            bp.id if bp else data.get('benefit_plan_id'),
            data.get('id'),
        )
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
