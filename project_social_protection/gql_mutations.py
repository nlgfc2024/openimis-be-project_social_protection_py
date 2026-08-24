import graphene as graphene
import re
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
from location.models import Location, Hotspot, MicroCatchment
from social_protection.models import BenefitPlan, Beneficiary, GroupBeneficiary

from project_social_protection.apps import ProjectSocialProtectionConfig
from project_social_protection.models import (
    Project, ProjectMutation, Activity, ProjectStatus,
    BeneficiaryProjectTimeEntry, GroupBeneficiaryProjectTimeEntry,
)
from project_social_protection.services import (
    ProjectService, ProjectEnrollmentService,
)

_MUTATION_MODULE = "project_social_protection"


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


def generate_project_code(location):
    district_digits = int(ProjectSocialProtectionConfig.project_code_district_digits)
    ta_digits = int(ProjectSocialProtectionConfig.project_code_ta_digits)
    sequence_digits = int(ProjectSocialProtectionConfig.project_code_sequence_digits)

    district = _find_location_by_type(location, 'R')
    ta = _find_location_by_type(location, 'D')
    if district is None or ta is None:
        raise ValidationError(
            _("A project location must belong to a District and Traditional Authority.")
        )

    district_code = _normalize_numeric_code(getattr(district, 'code', None), district_digits)
    ta_code = _normalize_numeric_code(getattr(ta, 'code', None), ta_digits)
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
        data['micro_catchment'] = MicroCatchment.objects.filter(id=micro_catchment_id).first() if micro_catchment_id else None
    if 'hotspot_id' in data:
        hotspot_id = data.pop('hotspot_id')
        hotspot = Hotspot.objects.filter(id=hotspot_id).first() if hotspot_id else None
        if hotspot_id and hotspot is None:
            raise ValidationError(_("Hotspot %(id)s does not exist.") % {'id': hotspot_id})
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
    benefit_plan_id = graphene.ID(required=True)
    name = graphene.String(required=False)
    activity_id = graphene.ID(required=True)
    location_id = graphene.ID(required=True)
    target_beneficiaries = graphene.Int(required=True)
    working_days = graphene.Int(required=True)
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

        data["benefit_plan"] = BenefitPlan.objects.get(
            id=data.pop("benefit_plan_id")
        )
        data["activity"] = Activity.objects.get(id=data.pop("activity_id"))
        data["location"] = Location.objects.get(uuid=data.pop("location_id"))
        # Creation always starts in preparation, regardless of a client-provided
        # status. Progression is allowed only after the project exists.
        data["status"] = ProjectStatus.PREPARATION
        _resolve_malawi_fields(data)
        # Name is derived, not client-supplied.
        data["name"] = generate_project_name(
            data.get("hotspot"), data["activity"], data["benefit_plan"],
            data.get("known_place"),
        )

        service = ProjectService(user)
        if ProjectSocialProtectionConfig.project_code_enabled:
            max_retries = 5
            for _ in range(max_retries):
                try:
                    code = generate_project_code(data["location"])
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
                if 'uniq_live_project_code' in detail:
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
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop("client_mutation_id")
        if "client_mutation_label" in data:
            data.pop("client_mutation_label")
        # Name is MIS-derived; never let a client overwrite it on update.
        data.pop("name", None)

        name_driving_fields = {
            "benefit_plan_id", "activity_id", "hotspot_id", "known_place",
        }
        rebuild_name = bool(name_driving_fields.intersection(data))

        project = Project.objects.get(id=data["id"])

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
        # Rebuild the generated name when any name-driving field changes.  Values that
        # are not in the patch continue to come from the stored project.
        _resolve_malawi_fields(data)
        if rebuild_name:
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
