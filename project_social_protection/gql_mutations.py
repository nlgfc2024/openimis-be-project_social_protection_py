import graphene as graphene
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils.translation import gettext as _

from core.gql.gql_mutations.base_mutation import (
    BaseHistoryModelCreateMutationMixin, BaseMutation,
    BaseHistoryModelUpdateMutationMixin, BaseHistoryModelDeleteMutationMixin
)
from core.schema import OpenIMISMutation
from location.models import Location
from social_protection.models import BenefitPlan, Beneficiary, GroupBeneficiary

from project_social_protection.apps import ProjectSocialProtectionConfig
from project_social_protection.models import (
    Project, ProjectMutation, Activity,
    BeneficiaryProjectTimeEntry, GroupBeneficiaryProjectTimeEntry,
)
from project_social_protection.services import (
    ProjectService, ProjectEnrollmentService,
)

_MUTATION_MODULE = "project_social_protection"


class CreateProjectInputType(OpenIMISMutation.Input):
    benefit_plan_id = graphene.ID(required=True)
    name = graphene.String(required=True)
    status = graphene.String(required=False)
    activity_id = graphene.ID(required=True)
    location_id = graphene.ID(required=True)
    target_beneficiaries = graphene.Int(required=True)
    working_days = graphene.Int(required=True)
    allows_multiple_enrollments = graphene.Boolean(required=False)


class CreateProjectMutation(
    BaseHistoryModelCreateMutationMixin, BaseMutation
):
    _mutation_class = "CreateProjectMutation"
    _mutation_module = _MUTATION_MODULE
    _model = Project

    @classmethod
    def _validate_mutation(cls, user, **data):
        if isinstance(user, AnonymousUser) or not user.has_perms(
            ProjectSocialProtectionConfig.gql_project_create_perms
        ):
            raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            client_mutation_id = data.pop('client_mutation_id', None)
        if "client_mutation_label" in data:
            data.pop("client_mutation_label")

        data["benefit_plan"] = BenefitPlan.objects.get(
            id=data.pop("benefit_plan_id")
        )
        data["activity"] = Activity.objects.get(id=data.pop("activity_id"))
        data["location"] = Location.objects.get(uuid=data.pop("location_id"))
        data.setdefault(
            "status", Project._meta.get_field("status").get_default()
        )

        service = ProjectService(user)
        res = service.create(data)

        if client_mutation_id and res['success']:
            project = Project.objects.get(id=res['data']['id'])
            ProjectMutation.object_mutated(
                user, client_mutation_id=client_mutation_id, project=project
            )

        return res if not res['success'] else None

    class Input(CreateProjectInputType):
        pass


class UpdateProjectInputType(OpenIMISMutation.Input):
    id = graphene.UUID(required=True)
    benefit_plan_id = graphene.ID(required=False)
    name = graphene.String(required=False)
    status = graphene.String(required=False)
    activity_id = graphene.ID(required=False)
    location_id = graphene.ID(required=False)
    target_beneficiaries = graphene.Int(required=False)
    working_days = graphene.Int(required=False)
    allows_multiple_enrollments = graphene.Boolean(required=False)


class UpdateProjectMutation(
    BaseHistoryModelUpdateMutationMixin, BaseMutation
):
    _mutation_class = "UpdateProjectMutation"
    _mutation_module = _MUTATION_MODULE
    _model = Project

    @classmethod
    def _validate_mutation(cls, user, **data):
        if isinstance(user, AnonymousUser) or not user.has_perms(
            ProjectSocialProtectionConfig.gql_project_update_perms
        ):
            raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop("client_mutation_id")
        if "client_mutation_label" in data:
            data.pop("client_mutation_label")

        if 'benefit_plan_id' in data:
            data["benefit_plan"] = BenefitPlan.objects.get(
                id=data.pop("benefit_plan_id")
            )
        if 'activity_id' in data:
            data["activity"] = Activity.objects.get(id=data.pop("activity_id"))
        if 'location_id' in data:
            data["location"] = Location.objects.get(
                uuid=data.pop("location_id")
            )

        service = ProjectService(user)
        res = service.update(data)

        return res if not res['success'] else None

    class Input(UpdateProjectInputType):
        pass


class DeleteProjectMutation(
    BaseHistoryModelDeleteMutationMixin, BaseMutation
):
    _mutation_class = "DeleteProjectMutation"
    _mutation_module = _MUTATION_MODULE
    _model = Project

    @classmethod
    def _validate_mutation(cls, user, **data):
        if isinstance(user, AnonymousUser) or not user.has_perms(
            ProjectSocialProtectionConfig.gql_project_delete_perms
        ):
            raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop("client_mutation_id")
        if "client_mutation_label" in data:
            data.pop("client_mutation_label")

        service = ProjectService(user)
        ids = data.get("ids")
        if not ids:
            return {
                "success": False, "message": "No IDs to delete", "details": ""
            }

        with transaction.atomic():
            for obj_id in ids:
                res = service.delete({"id": obj_id})
                if not res["success"]:
                    return res

    class Input(OpenIMISMutation.Input):
        ids = graphene.List(graphene.UUID)


class UndoDeleteProjectMutation(
    BaseHistoryModelDeleteMutationMixin, BaseMutation
):
    _mutation_class = "UndoDeleteProjectMutation"
    _mutation_module = _MUTATION_MODULE
    _model = Project

    @classmethod
    def _validate_mutation(cls, user, **data):
        if isinstance(user, AnonymousUser) or not user.has_perms(
            ProjectSocialProtectionConfig.gql_project_delete_perms
        ):
            raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop("client_mutation_id")
        if "client_mutation_label" in data:
            data.pop("client_mutation_label")

        service = ProjectService(user)
        ids = data.get("ids")
        if not ids:
            return {
                "success": False,
                "message": "No IDs to undo delete",
                "details": ""
            }

        with transaction.atomic():
            for obj_id in ids:
                res = service.undo_delete({"id": obj_id})
                if not res["success"]:
                    return res

    class Input(OpenIMISMutation.Input):
        ids = graphene.List(graphene.UUID)


class ProjectEnrollmentMutation(
    BaseHistoryModelDeleteMutationMixin, BaseMutation
):
    _mutation_class = "ProjectEnrollmentMutation"
    _mutation_module = _MUTATION_MODULE
    _model = Beneficiary

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(
                ProjectSocialProtectionConfig.gql_project_beneficiary_enroll_perms):
            raise PermissionDenied(_("unauthorized"))

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        return ProjectEnrollmentService(
            user, ProjectEnrollmentService.INDIVIDUAL
        ).enroll_project(data)

    class Input(OpenIMISMutation.Input):
        ids = graphene.List(graphene.UUID)
        project_id = graphene.UUID(required=True)


class ProjectGroupEnrollmentMutation(
    BaseHistoryModelDeleteMutationMixin, BaseMutation
):
    _mutation_class = "ProjectGroupEnrollmentMutation"
    _mutation_module = _MUTATION_MODULE
    _model = GroupBeneficiary

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(
                ProjectSocialProtectionConfig.gql_project_beneficiary_enroll_perms):
            raise PermissionDenied(_("unauthorized"))

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        return ProjectEnrollmentService(
            user, ProjectEnrollmentService.GROUP
        ).enroll_project(data)

    class Input(OpenIMISMutation.Input):
        ids = graphene.List(graphene.UUID)
        project_id = graphene.UUID(required=True)


class TimeEntryInputType(graphene.InputObjectType):
    id = graphene.UUID(required=False)
    enrollment_id = graphene.UUID(required=True)
    day_number = graphene.Int(required=True)
    percent_complete = graphene.Int(required=True)


class BulkUpdateBeneficiaryTimeEntriesMutation(BaseMutation):
    _mutation_class = "BulkUpdateBeneficiaryTimeEntriesMutation"
    _mutation_module = _MUTATION_MODULE
    _model = BeneficiaryProjectTimeEntry

    @classmethod
    def _validate_mutation(cls, user, **data):
        if isinstance(user, AnonymousUser):
            raise ValidationError("mutation.authentication_required")
        perms = ProjectSocialProtectionConfig.gql_project_beneficiary_time_entry_perms
        if not user.has_perms(perms):
            raise ValidationError("mutation.unauthorized")

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = ProjectEnrollmentService(
            user, ProjectEnrollmentService.INDIVIDUAL
        )
        service.bulk_update_time_entries(data)

    class Input(OpenIMISMutation.Input):
        time_entries = graphene.List(TimeEntryInputType, required=True)


class BulkUpdateGroupBeneficiaryTimeEntriesMutation(BaseMutation):
    _mutation_class = "BulkUpdateGroupBeneficiaryTimeEntriesMutation"
    _mutation_module = _MUTATION_MODULE
    _model = GroupBeneficiaryProjectTimeEntry

    @classmethod
    def _validate_mutation(cls, user, **data):
        if isinstance(user, AnonymousUser):
            raise ValidationError("mutation.authentication_required")
        perms = ProjectSocialProtectionConfig.gql_project_beneficiary_time_entry_perms
        if not user.has_perms(perms):
            raise ValidationError("mutation.unauthorized")

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = ProjectEnrollmentService(
            user, ProjectEnrollmentService.GROUP
        )
        service.bulk_update_time_entries(data)

    class Input(OpenIMISMutation.Input):
        time_entries = graphene.List(TimeEntryInputType, required=True)
