"""Geographic scope shared by project reads and their related records."""

from django.conf import settings
from graphql import ResolveInfo

from location.apps import LocationConfig
from location.models import (
    Location, allowed_micro_catchment_district_ids, extend_allowed_locations,
)


def allowed_project_location_ids(user):
    """Return permitted location IDs, or None for unrestricted geography.

    TA assignments grant their whole parent district, as for micro-catchments.
    Cache only on a GraphQL request; raw-user service calls remain fresh.
    """
    if isinstance(user, ResolveInfo):
        context = user.context
        request_user = context.user
        key = (
            request_user, settings.ROW_SECURITY, LocationConfig.no_location_check,
            getattr(request_user, "is_superuser", False),
        )
        cached = getattr(context, "_project_location_scope", None)
        if cached is None or cached[0] != key:
            cached = (key, allowed_project_location_ids(request_user))
            context._project_location_scope = cached
        return cached[1]

    districts = allowed_micro_catchment_district_ids(user)
    if districts is None:
        return None
    if not districts:
        return frozenset()
    active_districts = list(Location.objects.filter(
        pk__in=districts, type="R", validity_to__isnull=True,
    ).values_list("pk", flat=True))
    if not active_districts:
        return frozenset()
    return frozenset(extend_allowed_locations(
        active_districts, strict=True, loc_types=["R", "D", "W", "V"],
    ))
