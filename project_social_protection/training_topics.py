"""Fixed Training topic membership/order with labels read from the catalogue."""
from collections import OrderedDict

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from project_social_protection.models import (
    Activity, ActivityTopic, ProjectTopic, BeneficiaryProjectEnrollment,
    GroupBeneficiaryProjectEnrollment, BeneficiaryProjectTimeEntry, GroupBeneficiaryProjectTimeEntry,
)


def has_training_attendance(project):
    for enrollment, entry in ((BeneficiaryProjectEnrollment, BeneficiaryProjectTimeEntry),
                              (GroupBeneficiaryProjectEnrollment, GroupBeneficiaryProjectTimeEntry)):
        ids = enrollment.objects.filter(project=project).values('pk')
        if entry.objects.filter(enrollment_id__in=ids).exists() or entry.history.filter(enrollment_id__in=ids).exists():
            return True
    return False


def prepare_training_topics(data, project=None):
    """Select all programme topics on create; never rebuild an existing list."""
    if 'module_ids' in data:
        raise ValidationError(_('Training modules are determined by the programme and cannot be supplied.'))
    project_type = project.project_type if project else data['project_type']
    if project_type != 'TRAINING':
        return None
    if 'working_days' in data:
        raise ValidationError(_('Training working days is derived from its topics and cannot be supplied.'))
    if data.get('activity') is not None or data.get('activity_id') is not None:
        raise ValidationError(_('A training uses modules instead of a work Activity.'))
    data['activity'] = None
    data.pop('activity_id', None)
    plan_id = getattr(data.get('benefit_plan'), 'pk', data.get('benefit_plan_id'))
    plan_id = plan_id if plan_id is not None else getattr(project, 'benefit_plan_id', None)
    if project is not None:
        if str(plan_id) != str(project.benefit_plan_id):
            raise ValidationError(_('The programme cannot change after the Training topic list is created.'))
        return None

    # Lock modules in the same stable order as catalogue writes, then apply the
    # catalogue display order when assigning the Training's immutable sequence.
    modules = list(Activity.objects.select_for_update().filter(
        is_deleted=False, benefit_plans__pk=plan_id).order_by('pk'))
    if not modules:
        raise ValidationError(_('The programme has no training modules. Configure its modules first.'))
    modules.sort(key=lambda module: (module.name, str(module.pk)))
    rows = []
    for module in modules:
        topics = list(ActivityTopic.objects.select_for_update().filter(
            activity=module, is_deleted=False).order_by('sequence', 'pk'))
        if not topics:
            raise ValidationError(_('Every training module must contain at least one topic.'))
        rows.extend(dict(topic=topic) for topic in topics)
    if len(rows) > 32767:
        raise ValidationError(_('A training cannot contain more than 32767 topics.'))
    data['working_days'] = len(rows)
    return rows


def save_training_topics(project, rows, user):
    if rows is None:
        return
    if project.training_topics.exists():
        raise ValidationError(_('A Training topic list cannot be replaced.'))
    for sequence, row in enumerate(rows, start=1):
        ProjectTopic(project=project, sequence=sequence, **row).save(user=user)


def training_modules(project):
    modules = OrderedDict()
    for link in project.training_topics.filter(is_deleted=False).select_related('topic__activity'):
        activity = link.topic.activity
        module = modules.setdefault(activity.pk, {'id': activity.pk, 'name': activity.name, 'topic_count': 0})
        module['topic_count'] += 1
    return list(modules.values())


def training_module_completion(enrollment):
    """Shared calculation-rule helper; no thresholds are applied by the grid."""
    if enrollment.project.project_type != 'TRAINING':
        return []
    attended = set(enrollment.time_entries.filter(is_deleted=False, percent_complete=100).values_list('day_number', flat=True))
    modules = OrderedDict()
    for link in enrollment.project.training_topics.filter(is_deleted=False).select_related('topic__activity'):
        activity = link.topic.activity
        module = modules.setdefault(activity.pk, dict(module={'id': activity.pk, 'name': activity.name},
                                                      ticked_topics=0, total_topics=0))
        module['total_topics'] += 1
        module['ticked_topics'] += link.sequence in attended
    return [{**module, 'percentage': 100 * module['ticked_topics'] / module['total_topics']}
            for module in modules.values()]
