import graphene
import graphene_django_optimizer as gql_optimizer
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils.translation import gettext as _

from core.gql_queries import ValidationMessageGQLType
from core.schema import OrderedDjangoFilterConnectionField
from core.services import wait_for_mutation
from core.utils import append_validity_filter
from location.models import extend_allowed_locations, Location
from social_protection.gql_queries import (
    BeneficiaryGQLType, GroupBeneficiaryGQLType,
)

from project_social_protection.apps import ProjectSocialProtectionConfig
from project_social_protection.gql_mutations import (
    CreateProjectMutation,
    UpdateProjectMutation,
    DeleteProjectMutation,
    UndoDeleteProjectMutation,
    ProjectEnrollmentMutation,
    ProjectGroupEnrollmentMutation,
    BulkUpdateBeneficiaryTimeEntriesMutation,
    BulkUpdateGroupBeneficiaryTimeEntriesMutation,
)
from project_social_protection.gql_queries import (
    ActivityGQLType,
    ProjectGQLType,
    ProjectHistoryGQLType,
    BeneficiaryProjectEnrollmentGQLType,
    GroupBeneficiaryProjectEnrollmentGQLType,
    ProjectEligibleBeneficiaryFilter,
    ProjectEligibleGroupBeneficiaryFilter,
)
from project_social_protection.models import Activity, Project
from project_social_protection.export_mixin import ExportableProjectQueryMixin
from project_social_protection.validation import validate_project_unique_name


class Query(ExportableProjectQueryMixin, graphene.ObjectType):
    activity = OrderedDjangoFilterConnectionField(
        ActivityGQLType,
        orderBy=graphene.List(of_type=graphene.String),
        applyDefaultValidityFilter=graphene.Boolean(),
        client_mutation_id=graphene.String(),
    )

    project = OrderedDjangoFilterConnectionField(
        ProjectGQLType,
        orderBy=graphene.List(of_type=graphene.String),
        applyDefaultValidityFilter=graphene.Boolean(),
        client_mutation_id=graphene.String(),
        parent_location=graphene.String(),
        parent_location_level=graphene.Int(),
    )

    project_name_validity = graphene.Field(
        ValidationMessageGQLType,
        project_name=graphene.String(required=True),
        benefit_plan_id=graphene.String(required=True),
        description="Checks that the specified Project name is valid"
    )

    project_history = OrderedDjangoFilterConnectionField(
        ProjectHistoryGQLType,
        orderBy=graphene.List(of_type=graphene.String),
        client_mutation_id=graphene.String(),
        search=graphene.String(),
        sort_alphabetically=graphene.Boolean(),
    )

    # Enrollment reads (replace the former Beneficiary.projectEnrollments field).
    beneficiary_project_enrollment = OrderedDjangoFilterConnectionField(
        BeneficiaryProjectEnrollmentGQLType,
        orderBy=graphene.List(of_type=graphene.String),
    )
    group_beneficiary_project_enrollment = OrderedDjangoFilterConnectionField(
        GroupBeneficiaryProjectEnrollmentGQLType,
        orderBy=graphene.List(of_type=graphene.String),
    )

    # Project-aware beneficiary lists (replace the former Beneficiary
    # eligibleForProject / enrolledInProject filters).
    project_eligible_beneficiaries = OrderedDjangoFilterConnectionField(
        BeneficiaryGQLType,
        filterset_class=ProjectEligibleBeneficiaryFilter,
        orderBy=graphene.List(of_type=graphene.String),
    )
    project_eligible_group_beneficiaries = OrderedDjangoFilterConnectionField(
        GroupBeneficiaryGQLType,
        filterset_class=ProjectEligibleGroupBeneficiaryFilter,
        orderBy=graphene.List(of_type=graphene.String),
    )

    @staticmethod
    def _check_permissions(user, perms):
        if isinstance(user, AnonymousUser) or not user.id \
                or not user.has_perms(perms):
            raise PermissionDenied(_("unauthorized"))

    def resolve_activity(self, info, **kwargs):
        Query._check_permissions(
            info.context.user,
            ProjectSocialProtectionConfig.gql_activity_search_perms
        )
        filters = append_validity_filter(**kwargs)
        query = Activity.objects.filter(*filters)
        return gql_optimizer.query(query, info)

    def resolve_project(self, info, **kwargs):
        Query._check_permissions(
            info.context.user,
            ProjectSocialProtectionConfig.gql_project_search_perms
        )
        filters = append_validity_filter(**kwargs)

        client_mutation_id = kwargs.get("client_mutation_id", None)
        if client_mutation_id:
            wait_for_mutation(client_mutation_id)
            filters.append(
                Q(mutations__mutation__client_mutation_id=client_mutation_id)
            )

        parent_location = kwargs.get('parent_location')
        if parent_location is not None:
            location = Location.objects.get(uuid=parent_location)
            descendant_ids = extend_allowed_locations([location.pk])
            filters.append(Q(location__id__in=descendant_ids))

        query = ProjectGQLType.with_assigned_beneficiaries_count(
            Project.objects.filter(*filters)
        )
        return gql_optimizer.query(query, info)

    def resolve_project_name_validity(self, info, **kwargs):
        perms = ProjectSocialProtectionConfig.gql_project_search_perms
        if not info.context.user.has_perms(perms):
            raise PermissionDenied(_("unauthorized"))
        errors = validate_project_unique_name(
            kwargs['project_name'], kwargs['benefit_plan_id']
        )
        if errors:
            return ValidationMessageGQLType(
                False, error_message=errors[0]['message']
            )
        return ValidationMessageGQLType(True)

    def resolve_project_history(self, info, **kwargs):
        filters = []

        search = kwargs.get("search", None)
        if search:
            search_terms = search.split(' ')
            search_queries = Q()
            for term in search_terms:
                search_queries |= Q(name__icontains=term)
            filters.append(search_queries)

        client_mutation_id = kwargs.get("client_mutation_id", None)
        if client_mutation_id:
            wait_for_mutation(client_mutation_id)
            filters.append(
                Q(mutations__mutation__client_mutation_id=client_mutation_id)
            )

        Query._check_permissions(
            info.context.user,
            ProjectSocialProtectionConfig.gql_project_search_perms
        )

        query = Project.history.filter(*filters)

        sort_alphabetically = kwargs.get("sort_alphabetically", None)
        if sort_alphabetically:
            query = query.order_by('name')
        return gql_optimizer.query(query, info)

    def resolve_beneficiary_project_enrollment(self, info, **kwargs):
        Query._check_permissions(
            info.context.user,
            ProjectSocialProtectionConfig.gql_project_search_perms
        )
        return gql_optimizer.query(
            BeneficiaryProjectEnrollmentGQLType._meta.model.objects.filter(
                is_deleted=False
            ),
            info,
        )

    def resolve_group_beneficiary_project_enrollment(self, info, **kwargs):
        Query._check_permissions(
            info.context.user,
            ProjectSocialProtectionConfig.gql_project_search_perms
        )
        return gql_optimizer.query(
            GroupBeneficiaryProjectEnrollmentGQLType._meta.model.objects.filter(
                is_deleted=False
            ),
            info,
        )

    def resolve_project_eligible_beneficiaries(self, info, **kwargs):
        Query._check_permissions(
            info.context.user,
            ProjectSocialProtectionConfig.gql_project_search_perms
        )
        return gql_optimizer.query(
            BeneficiaryGQLType._meta.model.objects.filter(is_deleted=False),
            info,
        )

    def resolve_project_eligible_group_beneficiaries(self, info, **kwargs):
        Query._check_permissions(
            info.context.user,
            ProjectSocialProtectionConfig.gql_project_search_perms
        )
        return gql_optimizer.query(
            GroupBeneficiaryGQLType._meta.model.objects.filter(is_deleted=False),
            info,
        )


class Mutation(graphene.ObjectType):
    create_project = CreateProjectMutation.Field()
    update_project = UpdateProjectMutation.Field()
    delete_project = DeleteProjectMutation.Field()
    undo_delete_project = UndoDeleteProjectMutation.Field()
    enroll_project = ProjectEnrollmentMutation.Field()
    enroll_group_project = ProjectGroupEnrollmentMutation.Field()
    bulk_update_beneficiary_time_entries = \
        BulkUpdateBeneficiaryTimeEntriesMutation.Field()
    bulk_update_group_beneficiary_time_entries = \
        BulkUpdateGroupBeneficiaryTimeEntriesMutation.Field()
