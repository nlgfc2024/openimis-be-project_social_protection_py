"""Creation helpers shared by the service and GraphQL WORK mutations."""
import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from core.models import User
from location.models import Hotspot, MicroCatchment
from project_social_protection.apps import ProjectSocialProtectionConfig
from project_social_protection.models import Project
from project_social_protection.mutation_scope import invalid_hotspot_error


def _find_location_by_type(location, location_type):
    current = location
    while current is not None:
        if getattr(current, "type", None) == location_type:
            return current
        current = getattr(current, "parent", None)
    return None


def _normalize_numeric_code(code_value, digits):
    numeric = ''.join(ch for ch in str(code_value or '') if ch.isdigit())
    return (numeric[-digits:] if numeric else '').zfill(digits)


def generate_project_code(location, allow_district=False):
    district_digits = int(ProjectSocialProtectionConfig.project_code_district_digits)
    ta_digits = int(ProjectSocialProtectionConfig.project_code_ta_digits)
    sequence_digits = int(ProjectSocialProtectionConfig.project_code_sequence_digits)

    district = _find_location_by_type(location, 'R')
    ta = _find_location_by_type(location, 'D')
    if district is None or (ta is None and not (allow_district and location.type == 'R')):
        raise ValidationError(
            _("A project location must belong to a District and Traditional Authority.")
        )

    district_code = _normalize_numeric_code(getattr(district, 'code', None), district_digits)
    ta_code = _normalize_numeric_code(getattr(ta, 'code', None), ta_digits) if ta else '0' * ta_digits
    prefix = f"{district_code}{ta_code}"
    pattern = re.compile(rf"^{prefix}(\d{{{sequence_digits}}})$")

    max_seq = 0
    existing_codes = Project.objects.filter(
        code__startswith=prefix,
    ).values_list('code', flat=True)
    for code in existing_codes:
        match = pattern.match(code or '')
        if not match:
            continue
        max_seq = max(max_seq, int(match.group(1)))

    if max_seq >= (10 ** sequence_digits) - 1:
        return None

    next_seq = str(max_seq + 1).zfill(sequence_digits)
    return f"{prefix}{next_seq}"


def _resolve_malawi_fields(data):
    """Resolve the Malawi (sprint) FK inputs on a project mutation payload in place:
    micro_catchment_id -> MicroCatchment, hotspot_id -> Hotspot, foreman_id /
    supervisor_id -> core.User. `known_place` is a plain string and passes through
    untouched."""
    if 'micro_catchment_id' in data:
        micro_catchment_id = data.pop('micro_catchment_id')
        micro_catchment = MicroCatchment.objects.filter(id=micro_catchment_id).first() if micro_catchment_id else None
        if micro_catchment_id and micro_catchment is None:
            raise ValidationError(_("Invalid project micro-catchment."))
        data['micro_catchment'] = micro_catchment
    if 'hotspot_id' in data:
        hotspot_id = data.pop('hotspot_id')
        hotspot = Hotspot.objects.filter(id=hotspot_id).first() if hotspot_id else None
        if hotspot_id and hotspot is None:
            raise invalid_hotspot_error()
        data['hotspot'] = hotspot
    if 'foreman_id' in data:
        foreman_id = data.pop('foreman_id')
        foreman = User.objects.filter(id=foreman_id).first() if foreman_id else None
        if foreman_id and foreman is None:
            raise ValidationError(_("Foreman %(id)s does not exist.") % {'id': foreman_id})
        data['foreman'] = foreman
    if 'supervisor_id' in data:
        supervisor_id = data.pop('supervisor_id')
        supervisor = User.objects.filter(id=supervisor_id).first() if supervisor_id else None
        if supervisor_id and supervisor is None:
            raise ValidationError(_("Supervisor %(id)s does not exist.") % {'id': supervisor_id})
        data['supervisor'] = supervisor
