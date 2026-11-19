"""Validated deployment configuration for the Training creation form."""
from copy import deepcopy
from datetime import date
from uuid import UUID

from django.core.exceptions import ValidationError


FIELD_DEFAULTS = {
    'name': {'required': True},
    'sessionStartDate': {}, 'sessionEndDate': {},
    'district': {'required': True}, 'location': {'required': True},
    'microCatchment': {'required': True}, 'hotspot': {'required': True},
    'trainingModules': {'required': True}, 'knownPlace': {},
    'targetBeneficiaries': {'required': True},
    'topicCount': {'readOnly': True},
    'status': {'readOnly': True, 'default': 'PREPARATION'},
    'benefitPlan': {'required': True, 'readOnly': True},
    'foreman': {}, 'supervisor': {},
}
REFERENCE_FIELDS = {'district', 'location', 'microCatchment', 'hotspot', 'foreman', 'supervisor'}


def _normalize_training_fields(raw=None, maximum_target=200):
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - {'fields'}:
        raise ValidationError('training_form must be an object containing fields.')
    configured = raw.get('fields', {})
    if not isinstance(configured, dict) or set(configured) - set(FIELD_DEFAULTS):
        raise ValidationError('training_form.fields contains an unknown field.')
    fields = {}
    for name, defaults in FIELD_DEFAULTS.items():
        rule = configured.get(name, {})
        if not isinstance(rule, dict) or set(rule) - {'visible', 'required', 'readOnly', 'default'}:
            raise ValidationError(f'Invalid training_form rule for {name}.')
        for option in ('visible', 'required', 'readOnly'):
            if option in rule and not isinstance(rule[option], bool):
                raise ValidationError(f'{name}.{option} must be true or false.')
        effective = {'visible': True, 'required': False, 'readOnly': False, **defaults, **deepcopy(rule)}
        # Hiding an optional input alone must not leave an invisible requirement.
        if not effective['visible'] and 'required' not in rule and name not in ('name', 'trainingModules', 'benefitPlan'):
            effective['required'] = False
        if name in ('name', 'trainingModules', 'benefitPlan') and not effective['required']:
            raise ValidationError(f'{name} is required by the Training workflow.')
        if name in ('status', 'benefitPlan', 'topicCount') and not effective['readOnly']:
            raise ValidationError(f'{name} is controlled by the system and must remain read-only.')
        if 'default' in rule:
            value = rule['default']
            if name in ('benefitPlan', 'topicCount'):
                raise ValidationError(f'{name} is derived from the programme or topics and cannot have a default.')
            if name == 'status' and value != 'PREPARATION':
                raise ValidationError('A new Training must start in PREPARATION.')
            if name in ('name', 'knownPlace') and (not isinstance(value, str) or len(value) > 255):
                raise ValidationError(f'{name}.default must be text of at most 255 characters.')
            if name == 'name' and not value.strip():
                raise ValidationError('name.default cannot be blank.')
            if name in ('sessionStartDate', 'sessionEndDate') and value is not None:
                try:
                    date.fromisoformat(value)
                except (ValueError, TypeError):
                    raise ValidationError(f'{name}.default must be an ISO date (YYYY-MM-DD).')
            if name == 'targetBeneficiaries' and value is not None and (
                    not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 2147483647):
                raise ValidationError('targetBeneficiaries.default must be a positive integer or null.')
            if name == 'targetBeneficiaries' and value is not None:
                try:
                    maximum = int(maximum_target)
                except (TypeError, ValueError):
                    maximum = 200
                if value > maximum:
                    raise ValidationError('targetBeneficiaries.default exceeds max_target_beneficiaries.')
            if name in REFERENCE_FIELDS and value is not None and (
                    not isinstance(value, (str, int, dict)) or isinstance(value, bool)
                    or isinstance(value, dict) and not (value.get('uuid') or value.get('id'))):
                raise ValidationError(f'{name}.default must be a reference ID or an object with id/uuid.')
            if name in ('district', 'location', 'foreman', 'supervisor') and value is not None:
                identity = value.get('uuid' if name in ('district', 'location') else 'id', value.get('id')) if isinstance(value, dict) else value
                try:
                    UUID(str(identity))
                except (ValueError, TypeError, AttributeError):
                    raise ValidationError(f'{name}.default must contain a raw UUID.')
            if name == 'trainingModules' and (not isinstance(value, list) or not value
                    or any(not isinstance(item, (str, dict)) or isinstance(item, dict) and not item.get('id') for item in value)):
                raise ValidationError('trainingModules.default must be a nonempty list of module IDs.')
            if name == 'trainingModules':
                for module in value:
                    try:
                        UUID(str(module.get('id') if isinstance(module, dict) else module))
                    except (ValueError, TypeError, AttributeError):
                        raise ValidationError('trainingModules.default must contain raw module UUIDs.')
        if name not in ('district', 'trainingModules', 'status', 'benefitPlan', 'topicCount') and effective['required']:
            if (not effective['visible'] or effective['readOnly']) and effective.get('default') in (None, ''):
                raise ValidationError(f'{name} needs a default when required and hidden/read-only.')
        fields[name] = effective
    if (not fields['district']['visible'] or fields['district']['readOnly']) and fields['location']['visible'] and not (
            fields['district'].get('default') or fields['location'].get('default')):
        raise ValidationError('Hiding district requires a district or location default for the location picker.')
    start, end = fields['sessionStartDate'].get('default'), fields['sessionEndDate'].get('default')
    if start and end and start > end:
        raise ValidationError('Session end date default must not precede the start date default.')
    return {'fields': fields}


def normalize_training_form(raw=None, maximum_target=200, program=None):
    raw = {} if raw is None else raw
    if not isinstance(raw, dict) or set(raw) - {'fields', 'programs'}:
        raise ValidationError('training_form must contain fields and optional programs.')
    shared = raw.get('fields', {})
    effective = _normalize_training_fields({'fields': shared}, maximum_target)
    programs = raw.get('programs', {})
    if not isinstance(programs, dict):
        raise ValidationError('training_form.programs must be an object keyed by programme code.')
    for key, override in programs.items():
        if not isinstance(key, str) or not key.strip() or key != key.strip():
            raise ValidationError('Training programme keys must be nonempty programme codes without surrounding spaces.')
        if not isinstance(override, dict) or set(override) - {'fields'} or not isinstance(override.get('fields'), dict):
            raise ValidationError(f'training_form.programs.{key} must contain fields.')
        merged = deepcopy(shared)
        for field, rule in override['fields'].items():
            if field not in FIELD_DEFAULTS or not isinstance(rule, dict):
                raise ValidationError(f'Invalid Training field override for {key}: {field}.')
            merged[field] = {**merged.get(field, {}), **deepcopy(rule)}
            if rule.get('visible') is False and 'required' not in rule:
                merged[field].pop('required', None)
        # Validate every programme, including those not selected by this request.
        resolved = _normalize_training_fields({'fields': merged}, maximum_target)
        if key == program:
            effective = resolved
    return effective


def get_training_form(benefit_plan=None):
    from project_social_protection.apps import ProjectSocialProtectionConfig
    program = str(benefit_plan.code) if benefit_plan is not None and benefit_plan.code is not None else None
    return normalize_training_form(ProjectSocialProtectionConfig.training_form,
                                   ProjectSocialProtectionConfig.max_target_beneficiaries, program=program)
