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
from project_social_protection.creation import generate_project_code, _resolve_malawi_fields
from project_social_protection.mutation_scope import (
    authorized_project, invalid_hotspot_error, validate_destination,
)
from project_social_protection.models import (
    Project, ProjectMutation, Activity, ProjectStatus,
    BeneficiaryProjectTimeEntry, GroupBeneficiaryProjectTimeEntry,
)
from project_social_protection.services import (
    ProjectService, ProjectEnrollmentService,
)

_MUTATION_MODULE = "project_social_protection"


def generate_project_name(hotspot, activity, benefit_plan, known_place):
    """Return ``Hotspot-Activity-Program[- Known place]``."""
    parts = [p for p in (
        getattr(hotspot, 'name', None),
        getattr(activity, 'name', None),
        getattr(benefit_plan, 'name', None),
    ) if p]
    name = '-'.join(parts)
    if known_place:
        name = f"{name} - {known_place}"
    return name


class CreateProjectInputType(OpenIMISMutation.Input):
    # Shared inputs are nullable for TRAINING; ProjectValidation still requires
    # WORK activity, positive working days, location and target beneficiaries.
    district_id = graphene.UUID(required=False)
    session_start_date = graphene.Date(required=False)
    session_end_date = graphene.Date(required=False)
    benefit_plan_id = graphene.ID(required=True)
    name = graphene.String(required=False)
    activity_id = graphene.ID(required=False)
    location_id = graphene.ID(required=False)
    target_beneficiaries = graphene.Int(required=False)
    working_days = graphene.Int(required=False)
    allows_multiple_enrollments = graphene.Boolean(required=False)
    micro_catchment_id = graphene.ID(required=False)
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
        client_mutation_id = None
        if "client_mutation_id" in data:
            client_mutation_id = data.pop('client_mutation_id', None)
        if "client_mutation_label" in data:
            data.pop("client_mutation_label")

        if ProjectSocialProtectionConfig.project_type == 'TRAINING':
            res = ProjectService(user).create(data)
        else:
            data["benefit_plan"] = BenefitPlan.objects.get(
                id=data.pop("benefit_plan_id")
            )
            if data.get('activity_id'):
                data['activity'] = Activity.objects.get(id=data.pop('activity_id'))
            if data.get('location_id'):
                data['location'] = Location.objects.get(uuid=data.pop('location_id'))
            else:
                data.pop('location_id', None)
            if not data.get('location'):
                raise ValidationError(_('Location is required.'))
            validate_destination(user, {"location": data["location"]})
            # Creation always starts in preparation, regardless of a client-provided
            # status. Progression is allowed only after the project exists.
            data["status"] = ProjectStatus.PREPARATION
            _resolve_malawi_fields(data)
            if ProjectSocialProtectionConfig.project_type == 'WORK':
                data["name"] = generate_project_name(
                    data.get("hotspot"), data.get("activity"), data["benefit_plan"],
                    data.get("known_place"),
                )

            service = ProjectService(user)
            if ProjectSocialProtectionConfig.project_code_enabled:
                max_retries = 5
                for _attempt in range(max_retries):
                    try:
                        code = generate_project_code(data["location"], allow_district=ProjectSocialProtectionConfig.project_type == 'TRAINING')
                    except ValidationError as exc:
                        return {
                            "success": False,
                            "message": exc.messages[0],
                            "details": "",
                        }
                    if code is None:
                        return {
                            "success": False,
                            "message": _("Project code sequence limit reached for this location."),
                            "details": "",
                        }
                    data["code"] = code
                    res = service.create(data)
                    if res['success']:
                        break

                    detail = str(res.get('detail', ''))
                    if 'uniq_project_code' in detail:
                        continue
                    if 'uniq_live_project_name_per_plan' in detail:
                        return {
                            "success": False,
                            "message": _("A project with this name already exists for the selected program."),
                            "details": "",
                        }
                    return res
                else:
                    return {
                        "success": False,
                        "message": _("Failed to generate a unique project code. Please retry."),
                        "details": "",
                    }
            else:
                res = service.create(data)
                if not res['success'] and 'uniq_live_project_name_per_plan' in str(res.get('detail', '')):
                    return {
                        "success": False,
                        "message": _("A project with this name already exists for the selected program."),
                        "details": "",
                    }

        if client_mutation_id and res['success']:
            project = Project.objects.get(id=res['data']['id'])
            ProjectMutation.object_mutated(
                user, client_mutation_id=client_mutation_id, project=project
            )

        return res if not res['success'] else None

    class Input(CreateProjectInputType):
        pass


class UpdateProjectInputType(OpenIMISMutation.Input):
    session_start_date = graphene.Date(required=False)
    session_end_date = graphene.Date(required=False)
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
    micro_catchment_id = graphene.ID(required=False)
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
    @transaction.atomic
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop("client_mutation_id")
        if "client_mutation_label" in data:
            data.pop("client_mutation_label")

        name_driving_fields = {
            "benefit_plan_id", "activity_id", "hotspot_id", "known_place",
        }
        rebuild_name = bool(name_driving_fields.intersection(data))

        project = authorized_project(user, data["id"])
        if project.project_type == 'WORK':
            data.pop('name', None)

        if 'benefit_plan_id' in data:
            data["benefit_plan"] = BenefitPlan.objects.get(
                id=data.pop("benefit_plan_id")
            )
        if 'activity_id' in data:
            activity_id = data.pop('activity_id')
            data['activity'] = Activity.objects.get(id=activity_id) if activity_id else None
        if 'location_id' in data:
            data["location"] = Location.objects.get(
                uuid=data.pop("location_id")
            )
        # Rebuild the generated name when any name-driving field changes.  Values that
        # are not in the patch continue to come from the stored project.
        _resolve_malawi_fields(data)
        if rebuild_name and project.project_type == 'WORK':
            data["name"] = generate_project_name(
                data.get("hotspot", project.hotspot),
                data.get("activity", project.activity),
                data.get("benefit_plan", project.benefit_plan),
                data.get("known_place", project.known_place),
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

        return service.change_deleted(ids)

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

        return service.change_deleted(ids, restore=True)

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
    percent_complete = graphene.Int(required=False)
    clear = graphene.Boolean(required=False)


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
