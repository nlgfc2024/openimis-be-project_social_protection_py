import graphene
import graphene_django_optimizer as gql_optimizer
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
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
    ProjectTrainingModuleGQLType,
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
from project_social_protection.catalogue_mutations import (
    CreateTrainingModule, UpdateTrainingModule, DeleteTrainingModule,
)


class Query(ExportableProjectQueryMixin, graphene.ObjectType):
    training_module = OrderedDjangoFilterConnectionField(
        ActivityGQLType, orderBy=graphene.List(graphene.String),
    )

    def resolve_training_module(self, info, **kwargs):
        from project_social_protection.training_catalogue import require_catalogue_permission
        require_catalogue_permission(info.context.user, 'search')
        from project_social_protection.training_catalogue import scoped_training_modules
        return scoped_training_modules(info.context.user).filter(
            topics__is_deleted=False).distinct().prefetch_related('benefit_plans', 'topics')

    project_programme_modules = graphene.List(
        ProjectTrainingModuleGQLType, benefit_plan_id=graphene.UUID(required=True),
    )

    def resolve_project_programme_modules(self, info, benefit_plan_id):
        user = info.context.user
        config = ProjectSocialProtectionConfig
        if not user.is_authenticated or not any(user.has_perms(perms) for perms in (
                config.gql_project_search_perms, config.gql_project_create_perms,
                config.gql_project_update_perms)):
            raise PermissionDenied(_("unauthorized"))
        if config.project_type != 'TRAINING':
            return []
        from social_protection.models import BenefitPlan
        from social_protection.benefit_plan_access import require_benefit_plan_access
        require_benefit_plan_access(user, benefit_plan_id)
        if not BenefitPlan.objects.filter(pk=benefit_plan_id, is_deleted=False).exists():
            raise ValidationError(_('Programme does not exist.'))
        from django.db.models import Count
        return list(Activity.objects.filter(is_deleted=False, benefit_plans__pk=benefit_plan_id)
                    .order_by('name', 'pk').annotate(topic_count=Count(
                        'topics', filter=Q(topics__is_deleted=False)))
                    .values('id', 'name', 'topic_count'))

    project_deployment_type = graphene.String()
    project_training_form_config = graphene.JSONString(benefit_plan_id=graphene.UUID())

    def resolve_project_training_form_config(self, info, benefit_plan_id=None):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        from project_social_protection.training_form import public_training_form
        plan = None
        if benefit_plan_id is not None:
            from social_protection.models import BenefitPlan
            plan = BenefitPlan.objects.filter(pk=benefit_plan_id, is_deleted=False).first()
            if plan is None:
                raise ValidationError(_('Programme does not exist.'))
        if plan is not None:
            from social_protection.benefit_plan_access import require_benefit_plan_access
            require_benefit_plan_access(info.context.user, plan.pk)
        return public_training_form(plan)

    def resolve_project_deployment_type(self, info):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        return ProjectSocialProtectionConfig.project_type

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
            Project.get_queryset(None, info).filter(*filters)
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

        query = Project.get_history_queryset(None, info).filter(*filters)

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
            BeneficiaryProjectEnrollmentGQLType._meta.model.get_queryset(None, info).filter(
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
            GroupBeneficiaryProjectEnrollmentGQLType._meta.model.get_queryset(None, info).filter(
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
            BeneficiaryGQLType._meta.model.get_queryset(None, info.context.user).filter(
                is_deleted=False,
                benefit_plan_id__in=Project.get_queryset(None, info).values("benefit_plan_id"),
            ),
            info,
        )

    def resolve_project_eligible_group_beneficiaries(self, info, **kwargs):
        Query._check_permissions(
            info.context.user,
            ProjectSocialProtectionConfig.gql_project_search_perms
        )
        return gql_optimizer.query(
            GroupBeneficiaryGQLType._meta.model.get_queryset(None, info.context.user).filter(
                is_deleted=False,
                benefit_plan_id__in=Project.get_queryset(None, info).values("benefit_plan_id"),
            ),
            info,
        )


class Mutation(graphene.ObjectType):
    create_training_module = CreateTrainingModule.Field()
    update_training_module = UpdateTrainingModule.Field()
    delete_training_module = DeleteTrainingModule.Field()
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
