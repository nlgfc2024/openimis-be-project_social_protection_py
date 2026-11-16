"""Shared geographic checks for project mutation services and object loaders."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils.translation import gettext as _

from location.models import Location, MicroCatchment, Hotspot
from project_social_protection.models import Project
from project_social_protection.scope import allowed_project_location_ids


def require_authenticated(user):
    if user is None or not user.is_authenticated:
        raise PermissionDenied(_("unauthorized"))


def authorized_project(user, project_id, deleted=False):
    """Load and lock the original parent before resolving a proposed destination.

    Call inside transaction.atomic. Missing and inaccessible objects share an error.
    """
    require_authenticated(user)
    project = Project.get_queryset(Project.objects.select_for_update(), user).filter(
        pk=project_id, is_deleted=deleted,
    ).first()
    if project is None:
        raise PermissionDenied(_("unauthorized"))
    return project


def _related(data, field, model, project=None):
    if field in data:
        value = data[field]
        pk = getattr(value, 'pk', value)
        if f'{field}_id' in data and str(pk) != str(data[f'{field}_id']):
            raise ValidationError(_("Conflicting project location inputs."))
    elif f'{field}_id' in data:
        pk = data[f'{field}_id']
    else:
        pk = getattr(project, f'{field}_id', None)
    if pk is None:
        return None
    value = model.objects.filter(pk=pk, validity_to__isnull=True).first()
    if value is None:
        raise ValidationError(_("Invalid project location, micro-catchment or hotspot."))
    return value


def validate_destination(user, data, project=None):
    """Check the effective state, including retained relations on partial updates."""
    require_authenticated(user)
    if 'pk' in data or 'uuid' in data or (project is None and 'id' in data):
        raise ValidationError(_("Project identity cannot be supplied or changed."))
    location = _related(data, 'location', Location, project)
    allowed = allowed_project_location_ids(user)
    if location is None or (allowed is not None and location.pk not in allowed):
        raise PermissionDenied(_("unauthorized"))

    micro_catchment = _related(data, 'micro_catchment', MicroCatchment, project)
    hotspot = _related(data, 'hotspot', Hotspot, project)
    district = location
    visited = set()
    while district is not None and district.type != 'R':
        if district.pk in visited or district.validity_to is not None:
            raise ValidationError(_("Invalid project location hierarchy."))
        visited.add(district.pk)
        district = district.parent
    if micro_catchment or hotspot:
        if district is None or district.validity_to is not None:
            raise ValidationError(_("Invalid project location hierarchy."))
        if micro_catchment and micro_catchment.district_id != district.pk:
            raise ValidationError(_("Micro-catchment must belong to the project's district."))
        if hotspot:
            catchment = MicroCatchment.objects.filter(
                pk=hotspot.micro_catchment_id, validity_to__isnull=True, district=district,
            ).first()
            if catchment is None or (micro_catchment and catchment.pk != micro_catchment.pk):
                raise ValidationError(_("Hotspot must match the project's district and micro-catchment."))
