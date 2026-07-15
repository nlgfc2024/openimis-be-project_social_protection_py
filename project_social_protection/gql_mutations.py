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
from core.models import User
from location.models import Location, Hotspot
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


def _resolve_malawi_fields(data):
    """Resolve the Malawi (sprint) FK inputs on a project mutation payload in place:
    hotspot_id -> Hotspot, foreman_id / supervisor_id -> core.User. `known_place` is a
    plain string and passes through untouched."""
    if 'hotspot_id' in data:
        hotspot_id = data.pop('hotspot_id')
        data['hotspot'] = Hotspot.objects.get(id=hotspot_id) if hotspot_id else None
    if 'foreman_id' in data:
        foreman_id = data.pop('foreman_id')
        data['foreman'] = User.objects.get(id=foreman_id) if foreman_id else None
    if 'supervisor_id' in data:
        supervisor_id = data.pop('supervisor_id')
        data['supervisor'] = User.objects.get(id=supervisor_id) if supervisor_id else None


def generate_project_name(hotspot, activity, benefit_plan, known_place):
    """Malawi project name: "Hotspot-Sector-Phase #<n> - Known place".
    Sector = Activity, Phase = BenefitPlan. <n> is a per-(hotspot, sector, phase) sequence
    so re-running for the same trio yields Project1, Project2, ... The count includes
    soft-deleted rows so a number is never reused after a delete; the live-rows unique
    constraint on (name, benefit_plan) is the real backstop against a concurrent-create
    race (the loser gets an IntegrityError / validation error and can retry)."""
    seq = Project.objects.filter(
        hotspot=hotspot, activity=activity, benefit_plan=benefit_plan,
    ).count() + 1
    parts = [p for p in (
        getattr(hotspot, 'name', None),
        getattr(activity, 'name', None),
        getattr(benefit_plan, 'name', None),
    ) if p]
    name = f"{'-'.join(parts)} #{seq}"
    if known_place:
        name = f"{name} - {known_place}"
    return name


class CreateProjectInputType(OpenIMISMutation.Input):
    benefit_plan_id = graphene.ID(required=True)
    # name is auto-generated from hotspot/activity/benefit_plan/known_place; kept optional
    # so a client may still send one, but it is overwritten on create.
    name = graphene.String(required=False)
    status = graphene.String(required=False)
    activity_id = graphene.ID(required=True)
    location_id = graphene.ID(required=True)
    target_beneficiaries = graphene.Int(required=True)
    working_days = graphene.Int(required=True)
    allows_multiple_enrollments = graphene.Boolean(required=False)
    # Malawi (sprint) fields
    hotspot_id = graphene.ID(required=False)
    known_place = graphene.String(required=False)
    foreman_id = graphene.ID(required=False)
    supervisor_id = graphene.ID(required=False)


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
        _resolve_malawi_fields(data)
        # Name is derived, not client-supplied.
        data["name"] = generate_project_name(
            data.get("hotspot"), data["activity"], data["benefit_plan"],
            data.get("known_place"),
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
    # Malawi (sprint) fields
    hotspot_id = graphene.ID(required=False)
    known_place = graphene.String(required=False)
    foreman_id = graphene.ID(required=False)
    supervisor_id = graphene.ID(required=False)


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
        # Name is MIS-derived; never let a client overwrite it on update.
        data.pop("name", None)

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
        # Editable Malawi fields; the auto-generated name is left intact on update.
        _resolve_malawi_fields(data)

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
