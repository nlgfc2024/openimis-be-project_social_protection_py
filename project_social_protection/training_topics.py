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
    locked = {module.pk: module for module in modules}
    modules = [locked[pk] for pk in Activity.objects.filter(pk__in=locked)
               .order_by('name', 'pk').values_list('pk', flat=True)]
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
    return training_module_completion_for([enrollment]).get(enrollment.pk, [])


def training_module_completion_for(enrollments):
    """training_module_completion for many enrollments of one model, in two queries.

    Returns {enrollment pk: per-module rows}; an enrollment on a project that is not a
    Training has no rows. Each enrollment's project must be loaded or loadable.
    """
    enrollments = [enrollment for enrollment in enrollments if enrollment.project.project_type == 'TRAINING']
    if not enrollments:
        return {}
    entry_model = type(enrollments[0]).time_entries.rel.related_model
    attended = {}
    for enrollment_id, day_number in entry_model.objects.filter(
            enrollment_id__in=[enrollment.pk for enrollment in enrollments],
            is_deleted=False, percent_complete=100).values_list('enrollment_id', 'day_number'):
        attended.setdefault(enrollment_id, set()).add(day_number)
    sheets = {}
    for link in ProjectTopic.objects.filter(
            project_id__in={enrollment.project_id for enrollment in enrollments}, is_deleted=False,
    ).select_related('topic__activity').order_by('project_id', 'sequence', 'id'):
        sheets.setdefault(link.project_id, []).append(link)

    completion = {}
    for enrollment in enrollments:
        ticked = attended.get(enrollment.pk, set())
        modules = OrderedDict()
        for link in sheets.get(enrollment.project_id, []):
            activity = link.topic.activity
            module = modules.setdefault(activity.pk, dict(module={'id': activity.pk, 'name': activity.name},
                                                          ticked_topics=0, total_topics=0))
            module['total_topics'] += 1
            module['ticked_topics'] += link.sequence in ticked
        completion[enrollment.pk] = [{**module, 'percentage': 100 * module['ticked_topics'] / module['total_topics']}
                                     for module in modules.values()]
    return completion
