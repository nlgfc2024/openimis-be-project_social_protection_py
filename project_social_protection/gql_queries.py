import graphene
import graphene_django_optimizer as gql_optimizer
import django_filters
from graphene_django import DjangoObjectType

from core import ExtendedConnection
from social_protection.models import Beneficiary, GroupBeneficiary
from social_protection.gql_queries import BenefitPlanGQLType

from project_social_protection.models import (
    Activity,
    Project,
    BeneficiaryProjectTimeEntry,
    GroupBeneficiaryProjectTimeEntry,
    BeneficiaryProjectEnrollment,
    GroupBeneficiaryProjectEnrollment,
)


class BeneficiaryProjectTimeEntryGQLType(DjangoObjectType):
    class Meta:
        model = BeneficiaryProjectTimeEntry
        fields = ("id", "day_number", "percent_complete")
        interfaces = (graphene.relay.Node,)


class GroupBeneficiaryProjectTimeEntryGQLType(DjangoObjectType):
    class Meta:
        model = GroupBeneficiaryProjectTimeEntry
        fields = ("id", "day_number", "percent_complete")
        interfaces = (graphene.relay.Node,)


class BeneficiaryProjectEnrollmentGQLType(DjangoObjectType):
    uuid = graphene.String(source='uuid')
    time_entries = graphene.List(BeneficiaryProjectTimeEntryGQLType)

    class Meta:
        model = BeneficiaryProjectEnrollment
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "project__id": ["exact"],
            "beneficiary__id": ["exact"],
            "is_deleted": ["exact"],
        }
        connection_class = ExtendedConnection

    def resolve_time_entries(self, info, **kwargs):
        qs = BeneficiaryProjectTimeEntry.objects.filter(
            enrollment=self, is_deleted=False)
        return gql_optimizer.query(qs, info)


class GroupBeneficiaryProjectEnrollmentGQLType(DjangoObjectType):
    uuid = graphene.String(source='uuid')
    time_entries = graphene.List(GroupBeneficiaryProjectTimeEntryGQLType)

    class Meta:
        model = GroupBeneficiaryProjectEnrollment
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "project__id": ["exact"],
            "group_beneficiary__id": ["exact"],
            "is_deleted": ["exact"],
        }
        connection_class = ExtendedConnection

    def resolve_time_entries(self, info, **kwargs):
        qs = GroupBeneficiaryProjectTimeEntry.objects.filter(
            enrollment=self, is_deleted=False)
        return gql_optimizer.query(qs, info)


class ActivityFilter(django_filters.FilterSet):
    class Meta:
        model = Activity
        fields = {
            "id": ["exact"],
            "name": ["exact", "iexact", "startswith", "istartswith",
                     "contains", "icontains"],
            "date_created": ["exact", "lt", "lte", "gt", "gte"],
            "date_updated": ["exact", "lt", "lte", "gt", "gte"],
            "is_deleted": ["exact"],
            "version": ["exact"],
        }


class ActivityGQLType(DjangoObjectType):
    uuid = graphene.String(source='uuid')

    class Meta:
        model = Activity
        interfaces = (graphene.relay.Node,)
        filterset_class = ActivityFilter
        connection_class = ExtendedConnection


class ProjectFilter(django_filters.FilterSet):
    class Meta:
        model = Project
        fields = {
            "id": ["exact"],
            "name": ["exact", "iexact", "startswith", "istartswith",
                     "contains", "icontains"],
            'status': ['exact', 'icontains'],
            'benefit_plan__id': ['exact'],
            'activity__id': ['exact'],
            'location__id': ['exact'],
            'target_beneficiaries': ['exact', 'gte', 'lte'],
            'working_days': ['exact', 'gte', 'lte'],
            "date_created": ["exact", "lt", "lte", "gt", "gte"],
            "date_updated": ["exact", "lt", "lte", "gt", "gte"],
            "is_deleted": ["exact"],
            "version": ["exact"],
        }


class ProjectGQLType(DjangoObjectType):
    uuid = graphene.String(source='uuid')

    class Meta:
        model = Project
        interfaces = (graphene.relay.Node,)
        filterset_class = ProjectFilter
        connection_class = ExtendedConnection


class ProjectHistoryGQLType(DjangoObjectType):
    uuid = graphene.String(source='uuid')

    def resolve_user_updated(self, info):
        return self.user_updated

    class Meta:
        model = Project.history.model
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "name": ["exact", "iexact", "startswith", "istartswith",
                     "contains", "icontains"],
            'status': ['exact', 'icontains'],
            'benefit_plan__id': ['exact'],
            'activity__id': ['exact'],
            'location__id': ['exact'],
            'target_beneficiaries': ['exact', 'gte', 'lte'],
            'working_days': ['exact', 'gte', 'lte'],
            "date_created": ["exact", "lt", "lte", "gt", "gte"],
            "date_updated": ["exact", "lt", "lte", "gt", "gte"],
            "is_deleted": ["exact"],
            "version": ["exact"],
        }
        connection_class = ExtendedConnection


# --- Project-aware beneficiary eligibility ------------------------------------
# These filters used to live on social_protection's BeneficiaryFilter. They are
# project concerns, so they move here. They are applied against social_protection's
# Beneficiary / GroupBeneficiary querysets (allowed dependency direction) and used
# by the project module's own beneficiary queries (see schema.py), so
# social_protection no longer references the project domain.

class ProjectEligibilityFilterMixin:
    def filter_eligible_for_project(self, queryset, name, value):
        if not value:
            return queryset

        project = Project.objects.filter(id=value).first()
        if project is None:
            return queryset.none()
        enrollment_model = self._get_enrollment_model()
        beneficiary_field = self._get_beneficiary_field()

        # Keep the "enrolled elsewhere" lookup a lazy queryset so it compiles to a
        # correlated subquery instead of materializing every id into a Python set.
        if project.allows_multiple_enrollments:
            enrolled_in_exclusive = enrollment_model.objects.filter(
                project__allows_multiple_enrollments=False,
                is_deleted=False,
            ).exclude(
                project_id=project.id
            ).values_list(f'{beneficiary_field}_id', flat=True)
            return queryset.exclude(id__in=enrolled_in_exclusive)
        enrolled_elsewhere = enrollment_model.objects.filter(
            is_deleted=False,
        ).exclude(
            project_id=project.id
        ).values_list(f'{beneficiary_field}_id', flat=True)
        return queryset.exclude(id__in=enrolled_elsewhere)

    def filter_enrolled_in_project(self, queryset, name, value):
        if not value:
            return queryset
        enrollment_model = self._get_enrollment_model()
        beneficiary_field = self._get_beneficiary_field()
        enrolled_ids = enrollment_model.objects.filter(
            project_id=value,
            is_deleted=False
        ).values_list(f'{beneficiary_field}_id', flat=True)
        return queryset.filter(id__in=enrolled_ids)

    def _get_enrollment_model(self):
        raise NotImplementedError

    def _get_beneficiary_field(self):
        raise NotImplementedError


class ProjectEligibleBeneficiaryFilter(
    django_filters.FilterSet, ProjectEligibilityFilterMixin
):
    eligible_for_project = django_filters.CharFilter(
        method='filter_eligible_for_project')
    enrolled_in_project = django_filters.CharFilter(
        method='filter_enrolled_in_project')

    class Meta:
        model = Beneficiary
        fields = {
            "id": ["exact"],
            "status": ["exact"],
            **{f"benefit_plan__{k}": v
               for k, v in BenefitPlanGQLType._meta.filter_fields.items()
               if k in ("id",)},
            "is_deleted": ["exact"],
        }

    def _get_enrollment_model(self):
        return BeneficiaryProjectEnrollment

    def _get_beneficiary_field(self):
        return 'beneficiary'


class ProjectEligibleGroupBeneficiaryFilter(
    django_filters.FilterSet, ProjectEligibilityFilterMixin
):
    eligible_for_project = django_filters.CharFilter(
        method='filter_eligible_for_project')
    enrolled_in_project = django_filters.CharFilter(
        method='filter_enrolled_in_project')

    class Meta:
        model = GroupBeneficiary
        fields = {
            "id": ["exact"],
            "status": ["exact"],
            "benefit_plan__id": ["exact"],
            "is_deleted": ["exact"],
        }

    def _get_enrollment_model(self):
        return GroupBeneficiaryProjectEnrollment

    def _get_beneficiary_field(self):
        return 'group_beneficiary'
