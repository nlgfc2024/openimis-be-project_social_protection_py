"""Creation defaults and requirements shared with the configurable frontend."""
from datetime import date

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from core.models import User
from location.models import Location, MicroCatchment, Hotspot
from project_social_protection.models import Project, ProjectStatus
from project_social_protection.training_form_config import get_training_form


BINDINGS = {
    'name': 'name', 'sessionStartDate': 'session_start_date', 'sessionEndDate': 'session_end_date',
    'location': 'location', 'microCatchment': 'micro_catchment', 'hotspot': 'hotspot',
    'knownPlace': 'known_place', 'targetBeneficiaries': 'target_beneficiaries',
    'foreman': 'foreman', 'supervisor': 'supervisor',
}


def form_for_data(data):
    plan = data.get('benefit_plan')
    if plan is None and data.get('benefit_plan_id'):
        from social_protection.models import BenefitPlan
        plan = BenefitPlan.objects.get(pk=data['benefit_plan_id'])
    return get_training_form(plan)


def reference_id(value, location=False):
    if isinstance(value, dict):
        return value.get('uuid' if location else 'id') or value.get('id')
    return value


def resolve_default(field, value):
    if value is None:
        return None
    model = {'district': Location, 'location': Location, 'microCatchment': MicroCatchment,
             'hotspot': Hotspot, 'foreman': User, 'supervisor': User}.get(field)
    if model:
        key = 'uuid' if model is Location else 'pk'
        obj = model.objects.filter(**{key: reference_id(value, model is Location)}).first()
        if obj is None or getattr(obj, 'is_deleted', False) or getattr(obj, 'validity_to', None):
            raise ValidationError(_('The default for %(field)s does not reference an active record.') % {'field': field})
        if field == 'district' and obj.type != 'R':
            raise ValidationError(_('The district default must reference a District.'))
        return obj
    if field in ('sessionStartDate', 'sessionEndDate') and value:
        return date.fromisoformat(value)
    return value


def apply_training_creation_defaults(data):
    fields = form_for_data(data)['fields']
    district = data.pop('district', None)
    if district is None and not fields['location']['required'] and getattr(data.get('location'), 'type', None) == 'R':
        district = data['location']
    district_id = data.pop('district_id', None)
    if district is None and district_id:
        district = resolve_default('district', district_id)
    if district is None and fields['district'].get('default'):
        district = resolve_default('district', fields['district']['default'])
    for field, attribute in BINDINGS.items():
        rule = fields[field]
        if 'default' not in rule:
            if rule['readOnly'] or not rule['visible']:
                data.pop(attribute, None)
                data.pop(f'{attribute}_id', None)
            continue
        if rule['readOnly'] or not rule['visible'] or attribute not in data and f'{attribute}_id' not in data:
            data.pop(f'{attribute}_id', None)
            data[attribute] = resolve_default(field, rule['default'])
    if not fields['location']['required'] and not (data.get('location') or data.get('location_id')) and district:
        if district.type != 'R':
            raise ValidationError(_('The district must reference a District.'))
        data['location'] = district
    data['status'] = ProjectStatus.PREPARATION
    return data


def validate_training_creation(data, check_district_default=True):
    errors = []
    fields = form_for_data(data)['fields']
    for field, rule in fields.items():
        if not rule['required'] and field != 'district':
            continue
        attribute = BINDINGS.get(field)
        if field in ('trainingModules', 'topicCount'):
            value = data.get('working_days')
        elif field == 'benefitPlan':
            value = data.get('benefit_plan') or data.get('benefit_plan_id')
        elif field == 'status':
            value = data.get('status')
        elif field == 'location':
            value = data.get('location')
            if value is not None and value.type == 'R':
                value = None
        elif field == 'district':
            value = data.get('location')
            seen = set()
            while value is not None and value.type != 'R':
                if value.pk in seen:
                    value = None
                    break
                seen.add(value.pk)
                value = value.parent
        else:
            value = data.get(attribute, data.get(f'{attribute}_id'))
        if rule['required'] and (value is None or value == '' or isinstance(value, str) and not value.strip()):
            errors.append({'message': _('%(field)s is required by the Training form configuration.') % {'field': field}})
        if check_district_default and field == 'district' and value is not None and rule.get('default') is not None and (
                rule['readOnly'] or not rule['visible']):
            expected = resolve_default('district', rule['default'])
            if value.pk != expected.pk:
                errors.append({'message': _('Location must belong to the configured read-only district.')})
    if errors:
        raise ValidationError(errors)


def public_training_form(benefit_plan=None):
    config = get_training_form(benefit_plan)
    for field, rule in config['fields'].items():
        if 'default' not in rule or rule['default'] is None:
            continue
        value = resolve_default(field, rule['default'])
        if field in ('district', 'location'):
            seen = set()
            def location_value(obj):
                if obj.pk in seen:
                    raise ValidationError(_('Invalid location hierarchy in the Training default.'))
                seen.add(obj.pk)
                return {'uuid': str(obj.uuid), 'id': str(obj.pk), 'name': obj.name, 'code': obj.code,
                        'type': obj.type, 'parent': location_value(obj.parent) if obj.parent else None}
            rule['default'] = location_value(value)
        elif field in ('microCatchment', 'hotspot'):
            rule['default'] = {'id': str(value.pk), 'uuid': str(value.uuid) if getattr(value, 'uuid', None) else None,
                               'name': value.name, 'code': value.code}
        elif field in ('foreman', 'supervisor'):
            rule['default'] = {'id': str(value.pk), 'username': value.username}
        elif isinstance(value, date):
            rule['default'] = value.isoformat()
    return config


def validate_training_update(data, project):
    """Validate effective partial updates without reapplying creation defaults."""
    fields = get_training_form(project.benefit_plan)['fields']
    bindings = {**BINDINGS, 'status': 'status', 'benefitPlan': 'benefit_plan'}
    effective = {'working_days': project.working_days}
    errors = []
    for field, attribute in bindings.items():
        previous = getattr(project, attribute)
        proposed = previous
        if attribute in data:
            proposed = data[attribute]
        elif f'{attribute}_id' in data:
            identity = data[f'{attribute}_id']
            model_field = Project._meta.get_field(attribute)
            try:
                proposed = model_field.related_model.objects.get(pk=identity) if identity is not None else None
            except (model_field.related_model.DoesNotExist, ValidationError, ValueError, TypeError) as error:
                raise ValidationError(_('%(field)s must reference an existing record.') % {'field': field}) from error
        effective[attribute] = proposed
        rule = fields[field]
        old_value, new_value = getattr(previous, 'pk', previous), getattr(proposed, 'pk', proposed)
        # Status is read-only on the create form (a new Training starts in PREPARATION),
        # but it still progresses afterwards, so attendance and completion can happen.
        if field != 'status' and (rule['readOnly'] or not rule['visible']) and old_value != new_value:
            errors.append(_('%(field)s is read-only in the Training form configuration.') % {'field': field})
    # A read-only district protects the ancestry even when location itself is editable.
    def district(location):
        seen = set()
        while location is not None and location.pk not in seen:
            if location.type == 'R':
                return location.pk
            seen.add(location.pk)
            location = location.parent
        return None
    rule = fields['district']
    if (rule['readOnly'] or not rule['visible']) and district(project.location) != district(effective['location']):
        errors.append(_('district is read-only in the Training form configuration.'))
    if errors:
        raise ValidationError(errors)
    validate_training_creation(effective, check_district_default=False)
