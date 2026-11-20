"""Atomic, permission-checked administration of programme training modules."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from uuid import uuid4
from django.utils.translation import gettext as _

from social_protection.models import BenefitPlan
from project_social_protection.apps import ProjectSocialProtectionConfig
from project_social_protection.models import Activity, ActivityTopic, Project


def require_catalogue_permission(user, operation):
    perms = getattr(ProjectSocialProtectionConfig, f'gql_activity_{operation}_perms')
    if not user or not user.is_authenticated or not user.has_perms(perms):
        raise PermissionDenied(_('unauthorized'))


def _text(value, label, max_length):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise ValidationError(_('%(field)s is required and must not exceed %(length)s characters.') % {
            'field': label, 'length': max_length})
    return value.strip()


class TrainingModuleService:
    def __init__(self, user):
        self.user = user

    def save(self, name, programme_ids, topics, module_id=None):
        require_catalogue_permission(self.user, 'update' if module_id else 'create')
        try:
            with transaction.atomic():
                return self._save(name, programme_ids, topics, module_id)
        except IntegrityError as error:
            # Concurrent requests can pass preflight uniqueness checks together.
            raise ValidationError(_('A module name or topic code already exists. Reload and try again.')) from error

    def _save(self, name, programme_ids, topics, module_id):
        name = _text(name, _('Module name'), 255)
        module = None
        if module_id:
            module = Activity.objects.select_for_update().filter(pk=module_id, is_deleted=False).first()
            if not module or not module.topics.filter(is_deleted=False).exists() or not module.benefit_plans.filter(is_deleted=False).exists():
                raise ValidationError(_('Training module does not exist.'))
        if Activity.objects.filter(name=name).exclude(pk=module_id).exists():
            raise ValidationError(_('A module with this name already exists.'))
        if not programme_ids:
            raise ValidationError(_('Select at least one programme.'))
        programme_ids = set(map(str, programme_ids))
        programmes = list(BenefitPlan.objects.filter(pk__in=programme_ids, is_deleted=False))
        if {str(plan.pk) for plan in programmes} != programme_ids:
            raise ValidationError(_('One or more programmes do not exist.'))
        if not topics:
            raise ValidationError(_('A training module must contain at least one topic.'))
        existing = {str(topic.pk): topic for topic in ActivityTopic.objects.select_for_update().filter(
            activity=module, is_deleted=False)} if module else {}
        prepared, codes, ids = [], set(), set()
        for row in topics:
            code = _text(row.get('code'), _('Topic code'), 64)
            topic_name = _text(row.get('name'), _('Topic name'), 255)
            topic_id = str(row['id']) if row.get('id') else None
            if code in codes:
                raise ValidationError(_('Topic codes must be unique.'))
            if topic_id and (topic_id not in existing or topic_id in ids):
                raise ValidationError(_('Invalid or repeated topic ID for this module.'))
            codes.add(code)
            if topic_id:
                ids.add(topic_id)
            prepared.append((existing.get(topic_id), code, topic_name))
        if ActivityTopic.objects.filter(code__in=codes, is_deleted=False).exclude(pk__in=existing).exists():
            raise ValidationError(_('A topic code already exists in another module.'))

        if module is None:
            module = Activity(name=name)
        module.name = name
        module.save(user=self.user)
        module.benefit_plans.set(programmes)

        # Vacate all live positions before reordering, retaining IDs and history.
        maximum = max((topic.sequence for topic in existing.values()), default=0)
        for offset, topic in enumerate(existing.values(), start=maximum + 1):
            if str(topic.pk) not in ids:
                topic.is_deleted = True
            else:
                topic.sequence = offset
            topic.save(user=self.user)
        # Vacate old codes too, permitting atomic code swaps between retained topics.
        # The saved historical row keeps the original code and the final save records
        # the new one. Temporary codes are private to this transaction.
        for topic, code, _name in prepared:
            if topic and topic.code != code:
                ActivityTopic.objects.filter(pk=topic.pk).update(code=f'__reorder_{uuid4().hex}')
        for sequence, (topic, code, topic_name) in enumerate(prepared, start=1):
            topic = topic or ActivityTopic(activity=module)
            topic.code, topic.name, topic.sequence = code, topic_name, sequence
            topic.save(user=self.user)
        # HistoryBusinessModel's inherited history does not track plain M2M links.
        # Keep an audited snapshot so programme links and full list edits are visible.
        module.json_ext = {**(module.json_ext or {}), 'training_catalogue': {
            'programme_ids': sorted(programme_ids),
            'topics': list(module.topics.filter(is_deleted=False).values('code', 'name', 'sequence')),
        }}
        module.save(user=self.user)
        return module

    @transaction.atomic
    def delete(self, module_id):
        require_catalogue_permission(self.user, 'delete')
        module = Activity.objects.select_for_update().filter(pk=module_id, is_deleted=False).first()
        if not module:
            raise ValidationError(_('Training module does not exist.'))
        if Project.objects.filter(activity=module, is_deleted=False).exists():
            raise ValidationError(_('Cannot delete an Activity used by a live project.'))
        if not module.topics.filter(is_deleted=False).exists() or not module.benefit_plans.filter(is_deleted=False).exists():
            raise ValidationError(_('Training module does not exist.'))
        for topic in module.topics.select_for_update().filter(is_deleted=False):
            topic.is_deleted = True
            topic.save(user=self.user)
        module.is_deleted = True
        module.save(user=self.user)
        return module
