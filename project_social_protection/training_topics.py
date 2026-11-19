"""Training-specific topic snapshots; attendance columns refer to snapshot sequence."""
from collections import OrderedDict

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from project_social_protection.models import (
    Activity, ActivityTopic, ProjectTopic, BeneficiaryProjectEnrollment,
    GroupBeneficiaryProjectEnrollment, BeneficiaryProjectTimeEntry, GroupBeneficiaryProjectTimeEntry,
)


def has_training_attendance(project):
    # History deliberately includes cleared/deleted entries and old enrolments:
    # removing attendance must never unlock an already-used column mapping.
    for enrollment, entry in ((BeneficiaryProjectEnrollment, BeneficiaryProjectTimeEntry),
                              (GroupBeneficiaryProjectEnrollment, GroupBeneficiaryProjectTimeEntry)):
        ids = enrollment.objects.filter(project=project).values('pk')
        if entry.objects.filter(enrollment_id__in=ids).exists() or entry.history.filter(enrollment_id__in=ids).exists():
            return True
    return False


def prepare_training_topics(data, project=None):
    """Consume module_ids and set derived fields; None means retain the snapshot."""
    project_type = project.project_type if project else data['project_type']
    if project_type != 'TRAINING':
        if 'module_ids' in data:
            raise ValidationError(_('Modules can only be selected for a training.'))
        return None
    if 'working_days' in data:
        raise ValidationError(_('Training working days is derived from its topics and cannot be supplied.'))
    if data.get('activity') is not None or data.get('activity_id') is not None:
        raise ValidationError(_('A training uses modules instead of a work Activity.'))
    data['activity'] = None
    data.pop('activity_id', None)
    plan_id = getattr(data.get('benefit_plan'), 'pk', data.get('benefit_plan_id'))
    plan_id = plan_id if plan_id is not None else getattr(project, 'benefit_plan_id', None)
    saved = list(project.training_topics.filter(is_deleted=False)) if project else []
    saved_ids = list(dict.fromkeys(str(row.module_id) for row in saved))
    supplied = 'module_ids' in data
    selected = data.pop('module_ids', None)
    if supplied and (not isinstance(selected, (list, tuple)) or not selected):
        raise ValidationError(_('Select at least one training module.'))
    selected_ids = list(map(str, selected)) if supplied else saved_ids
    if len(selected_ids) != len(set(selected_ids)):
        raise ValidationError(_('A training module cannot be selected more than once.'))
    moved = project is not None and str(plan_id) != str(project.benefit_plan_id)
    if project and saved and (not supplied or selected_ids == saved_ids) and not moved:
        return None
    if project and has_training_attendance(project):
        raise ValidationError(_('Training modules and programme cannot change after attendance has been recorded.'))
    if not supplied and not saved:
        selected_ids = [str(pk) for pk in Activity.objects.filter(
            is_deleted=False, benefit_plans__pk=plan_id).order_by('name', 'pk').values_list('pk', flat=True)]
    if not selected_ids:
        raise ValidationError(_('The programme has no training modules. Configure its modules first.'))
    # Lock modules in a stable order, shared with catalogue edits, before topics.
    modules = {str(module.pk): module for module in Activity.objects.select_for_update().filter(
        pk__in=selected_ids, is_deleted=False, benefit_plans__pk=plan_id).order_by('pk')}
    if len(modules) != len(selected_ids):
        raise ValidationError(_('Every training module must be active and linked to the selected programme.'))
    # Moving programmes without changing modules must keep the existing snapshot.
    if saved and selected_ids == saved_ids:
        if any(not module.topics.filter(is_deleted=False).exists() for module in modules.values()):
            raise ValidationError(_('Every training module must contain at least one topic.'))
        return None
    rows = []
    for module_id in selected_ids:
        module = modules[module_id]
        topics = list(ActivityTopic.objects.select_for_update().filter(
            activity=module, is_deleted=False).order_by('sequence', 'pk'))
        if not topics:
            raise ValidationError(_('Every training module must contain at least one topic.'))
        for topic in topics:
            rows.append(dict(topic=topic, module=module, code=topic.code, name=topic.name, module_name=module.name))
    if len(rows) > 32767:
        raise ValidationError(_('A training cannot contain more than 32767 topics.'))
    data['working_days'] = len(rows)
    return rows


def save_training_topics(project, rows, user):
    if rows is None:
        return
    for previous in project.training_topics.filter(is_deleted=False):
        previous.is_deleted = True
        previous.save(user=user)
    for sequence, row in enumerate(rows, start=1):
        ProjectTopic(project=project, sequence=sequence, **row).save(user=user)


def training_modules(project):
    modules = OrderedDict()
    for topic in project.training_topics.filter(is_deleted=False):
        module = modules.setdefault(topic.module_id, {
            'id': topic.module_id, 'name': topic.module_name, 'topic_count': 0,
        })
        module['topic_count'] += 1
    return list(modules.values())


def training_module_completion(enrollment):
    """Shared read/calculation-rule helper, independent of payment thresholds."""
    if enrollment.project.project_type != 'TRAINING':
        return []
    attended = set(enrollment.time_entries.filter(is_deleted=False, percent_complete=100).values_list('day_number', flat=True))
    modules = OrderedDict()
    for topic in enrollment.project.training_topics.filter(is_deleted=False):
        module = modules.setdefault(topic.module_id, dict(module={'id': topic.module_id, 'name': topic.module_name},
                                                         ticked_topics=0, total_topics=0))
        module['total_topics'] += 1
        module['ticked_topics'] += topic.sequence in attended
    return [{**module, 'percentage': 100 * module['ticked_topics'] / module['total_topics']}
            for module in modules.values()]
