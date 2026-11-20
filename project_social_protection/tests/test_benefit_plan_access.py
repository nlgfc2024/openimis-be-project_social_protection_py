import csv
from datetime import date
import io
import json
from unittest.mock import patch

import graphene

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory, TestCase, override_settings
from graphql_relay import to_global_id

from core.models import ExportableQueryModel, MutationLog
from individual.models import Group, Individual
from social_protection.apps import SocialProtectionConfig
from social_protection.models import (
    Beneficiary,
    BenefitPlan,
    GroupBeneficiary,
    UserBenefitPlanAccess,
)

from project_social_protection.models import Project
from project_social_protection.schema import Mutation, Query as ProjectQuery
from project_social_protection.services import ProjectEnrollmentService, ProjectService
from project_social_protection.tests.test_district_scope import (
    ProjectDistrictScopeFixtures,
)


class Query(ProjectQuery, graphene.ObjectType):
    node = graphene.relay.Node.Field()


@override_settings(ROW_SECURITY=True, MODE="dev")
class ProjectBenefitPlanAccessTest(ProjectDistrictScopeFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.plan_access = UserBenefitPlanAccess(
            user=cls.reader,
            benefit_plan=cls.plan,
        )
        cls.plan_access.save(username=cls.admin.username)

    def setUp(self):
        super().setUp()
        original_enabled = SocialProtectionConfig.enable_benefit_plan_access_control
        original_all = SocialProtectionConfig.gql_benefit_plan_all_access_perms
        SocialProtectionConfig.enable_benefit_plan_access_control = True
        SocialProtectionConfig.gql_benefit_plan_all_access_perms = ["160006"]
        self.addCleanup(
            setattr,
            SocialProtectionConfig,
            "enable_benefit_plan_access_control",
            original_enabled,
        )
        self.addCleanup(
            setattr,
            SocialProtectionConfig,
            "gql_benefit_plan_all_access_perms",
            original_all,
        )

    def request(self, user=None):
        request = RequestFactory().get("/graphql")
        request.user = user or self.reader
        return request

    def query(self, selection, user=None, variables=None):
        result = graphene.Schema(query=Query).execute(
            selection,
            context_value=self.request(user),
            variable_values=variables,
        )
        self.assertIsNone(result.errors, result.errors)
        return result.data

    def export(self, field, args="", relation=False):
        fields = ["code", "benefitPlan.name"] if relation else ["code"]
        columns = (
            {"code": "Code", "benefit_plan__name": "Programme"}
            if relation else {"code": "Code"}
        )
        data = self.query(
            f'query($columns:GenericScalar) {{ {field}('
            f'fields:{json.dumps(fields)}, fieldsColumns:$columns {args}) }}',
            variables={"columns": json.dumps(columns)},
        )
        export = ExportableQueryModel.objects.get(name=data[field])
        with export.content.open("rb") as content:
            return list(csv.DictReader(io.StringIO(content.read().decode())))

    def graphql_mutation(self, field, input_type, data):
        schema = graphene.Schema(query=Query, mutation=Mutation)
        request = RequestFactory().post("/graphql")
        request.user = self.reader
        all_plan_perms = SocialProtectionConfig.gql_benefit_plan_all_access_perms
        with patch.object(
            type(self.reader),
            "has_perms",
            side_effect=lambda perms: perms != all_plan_perms,
        ):
            result = schema.execute(
                f'mutation($input:{input_type}!) {{ {field}(input:$input) {{ internalId }} }}',
                context_value=request,
                variable_values={"input": data},
            )
        self.assertIsNone(result.errors, result.errors)
        return MutationLog.objects.get(pk=result.data[field]["internalId"])

    def add_group_plan_access(self):
        assignment = UserBenefitPlanAccess(
            user=self.reader,
            benefit_plan=self.group_plan,
        )
        assignment.save(username=self.admin.username)
        return assignment

    def test_queryset_requires_both_plan_and_district_access(self):
        self.assertCountEqual(
            Project.get_queryset(None, self.reader).values_list("code", flat=True),
            ["PS-I0", "PS-I1"],
        )

        self.add_group_plan_access()
        self.assertCountEqual(
            Project.get_queryset(None, self.reader).values_list("code", flat=True),
            ["PS-I0", "PS-I1", "PS-G0", "PS-G1"],
        )

    def test_rmep_only_user_cannot_access_upg_projects(self):
        self.plan_access.is_deleted = True
        self.plan_access.save(username=self.admin.username)
        self.add_group_plan_access()
        self.assertCountEqual(
            Project.get_queryset(None, self.reader).values_list("code", flat=True),
            ["PS-G0", "PS-G1"],
        )

    def test_plan_scope_remains_active_when_row_security_is_disabled(self):
        with override_settings(ROW_SECURITY=False):
            self.assertCountEqual(
                Project.get_queryset(None, self.reader).values_list("code", flat=True),
                ["PS-I0", "PS-I1", "PS-I2"],
            )

    def test_no_assignments_grant_no_projects(self):
        UserBenefitPlanAccess.objects.filter(user=self.reader).update(is_deleted=True)
        self.assertFalse(Project.get_queryset(None, self.reader).exists())

    def test_feature_disabled_preserves_district_only_behavior(self):
        SocialProtectionConfig.enable_benefit_plan_access_control = False
        self.assertCountEqual(
            Project.get_queryset(None, self.reader).values_list("code", flat=True),
            ["PS-I0", "PS-I1", "PS-G0", "PS-G1"],
        )

    def test_all_plan_permission_bypasses_only_assignment_scope(self):
        with patch.object(type(self.reader), "has_perms", return_value=True):
            self.assertCountEqual(
                Project.get_queryset(None, self.reader).values_list("code", flat=True),
                ["PS-I0", "PS-I1", "PS-G0", "PS-G1"],
            )

    def test_superuser_uses_shared_policy(self):
        self.assertTrue(self.admin.is_superuser)
        expected = {p.pk for p in self.projects + self.group_projects}
        visible = set(
            Project.get_queryset(None, self.admin).filter(
                pk__in=expected,
            ).values_list("pk", flat=True)
        )
        self.assertEqual(visible, expected)

    def test_graphql_lists_direct_ids_history_and_nodes_are_scoped(self):
        hidden_project = self.group_projects[0]
        data = self.query("""{
            project(first: 100) { edges { node { code } } }
            projectHistory { edges { node { code } } }
            beneficiaryProjectEnrollment {
                totalCount
                edges { node { project { code } timeEntries { dayNumber } } }
            }
            groupBeneficiaryProjectEnrollment { totalCount }
        }""")
        self.assertEqual(
            {row["node"]["code"] for row in data["project"]["edges"]},
            {"PS-I0", "PS-I1"},
        )
        self.assertTrue(all(
            row["node"]["code"].startswith("PS-I")
            for row in data["projectHistory"]["edges"]
        ))
        self.assertEqual(data["beneficiaryProjectEnrollment"]["totalCount"], 2)
        self.assertTrue(all(
            row["node"]["project"]["code"].startswith("PS-I")
            and row["node"]["timeEntries"] == [{"dayNumber": 1}]
            for row in data["beneficiaryProjectEnrollment"]["edges"]
        ))
        self.assertEqual(data["groupBeneficiaryProjectEnrollment"]["totalCount"], 0)

        direct = self.query(
            f'{{ project(id:"{hidden_project.pk}") {{ totalCount }} '
            f'projectHistory(id:"{hidden_project.pk}") {{ totalCount }} }}'
        )
        self.assertEqual(direct["project"]["totalCount"], 0)
        self.assertEqual(direct["projectHistory"]["totalCount"], 0)
        visible_node_id = to_global_id("ProjectGQLType", self.projects[0].pk)
        self.assertIsNotNone(
            self.query(f'{{ node(id:"{visible_node_id}") {{ id }} }}')["node"]
        )
        for type_name, obj in [
            ("ProjectGQLType", hidden_project),
            ("GroupBeneficiaryProjectEnrollmentGQLType", self.group_enrolments[0]),
            ("GroupBeneficiaryProjectTimeEntryGQLType", self.group_entries[0]),
        ]:
            node_id = to_global_id(type_name, obj.pk)
            self.assertIsNone(
                self.query(f'{{ node(id:"{node_id}") {{ id }} }}')["node"]
            )

    def test_project_and_history_exports_are_plan_scoped(self):
        project_rows = self.export("projectExport", relation=True)
        self.assertEqual({row["Code"] for row in project_rows}, {"PS-I0", "PS-I1"})
        self.assertEqual({row["Programme"] for row in project_rows}, {self.plan.name})

        history_rows = self.export("projectHistoryExport")
        self.assertTrue(history_rows)
        self.assertTrue(all(row["Code"].startswith("PS-I") for row in history_rows))

    def test_eligible_group_query_rejects_unauthorized_project_id(self):
        data = self.query(
            f'{{ projectEligibleGroupBeneficiaries('
            f'eligibleForProject:"{self.group_projects[0].pk}") {{ totalCount }} }}'
        )
        self.assertEqual(data["projectEligibleGroupBeneficiaries"]["totalCount"], 0)

    def test_mutations_reject_unauthorized_original_and_destination_plans(self):
        service = ProjectService(self.reader)
        with self.assertRaises(PermissionDenied):
            service.create({
                "name": "Unauthorized plan project",
                "benefit_plan": self.group_plan,
                "activity": self.activity,
                "location": self.villages[0],
                "target_beneficiaries": 5,
                "working_days": 5,
            })
        with self.assertRaises(PermissionDenied):
            service.update({
                "id": self.projects[0].pk,
                "benefit_plan": self.group_plan,
            })
        with self.assertRaises(PermissionDenied):
            service.update({"id": self.group_projects[0].pk, "working_days": 6})
        with self.assertRaises(PermissionDenied):
            service.delete({"id": self.group_projects[0].pk})
        self.group_projects[0].is_deleted = True
        self.group_projects[0].save(username=self.admin.username)
        with self.assertRaises(PermissionDenied):
            service.undo_delete({"id": self.group_projects[0].pk})

    def test_graphql_mutations_reject_unauthorized_plans(self):
        hidden = self.group_projects[0]
        mutation_inputs = [
            (
                "createProject",
                "CreateProjectMutationInput",
                {
                    "benefitPlanId": str(self.group_plan.pk),
                    "activityId": str(self.activity.pk),
                    "locationId": str(self.villages[0].uuid),
                    "targetBeneficiaries": 5,
                    "workingDays": 5,
                },
            ),
            (
                "updateProject",
                "UpdateProjectMutationInput",
                {"id": str(self.projects[0].pk), "benefitPlanId": str(self.group_plan.pk)},
            ),
            (
                "deleteProject",
                "DeleteProjectMutationInput",
                {"ids": [str(hidden.pk)]},
            ),
            (
                "enrollGroupProject",
                "ProjectGroupEnrollmentMutationInput",
                {"projectId": str(hidden.pk), "ids": []},
            ),
            (
                "bulkUpdateGroupBeneficiaryTimeEntries",
                "BulkUpdateGroupBeneficiaryTimeEntriesMutationInput",
                {
                    "timeEntries": [{
                        "enrollmentId": str(self.group_enrolments[0].pk),
                        "dayNumber": 1,
                        "percentComplete": 80,
                    }],
                },
            ),
        ]
        project_count = Project.objects.count()
        for field, input_type, data in mutation_inputs:
            with self.subTest(field=field):
                log = self.graphql_mutation(field, input_type, data)
                self.assertEqual(log.status, MutationLog.ERROR)
                self.assertTrue(
                    "access" in log.error.lower() or "unauthorized" in log.error.lower(),
                    log.error,
                )

        hidden.is_deleted = True
        hidden.save(username=self.admin.username)
        log = self.graphql_mutation(
            "undoDeleteProject",
            "UndoDeleteProjectMutationInput",
            {"ids": [str(hidden.pk)]},
        )
        self.assertEqual(log.status, MutationLog.ERROR)
        hidden.refresh_from_db()
        self.assertTrue(hidden.is_deleted)
        self.assertEqual(Project.objects.count(), project_count)
        self.group_entries[0].refresh_from_db()
        self.assertEqual(self.group_entries[0].percent_complete, 75)

    def test_projects_with_individual_or_group_enrolments_cannot_change_plan(self):
        self.add_group_plan_access()
        service = ProjectService(self.reader)

        for project, destination_plan in [
            (self.projects[0], self.group_plan),
            (self.group_projects[0], self.plan),
        ]:
            with self.subTest(project=project.code):
                with self.assertRaisesRegex(
                    ValidationError,
                    "with individual or group enrolments cannot be moved",
                ):
                    service.update({
                        "id": project.pk,
                        "benefit_plan": destination_plan,
                    })
                project.refresh_from_db()
                self.assertNotEqual(project.benefit_plan_id, destination_plan.pk)

    def test_graphql_rejects_plan_change_for_project_with_enrolments(self):
        self.add_group_plan_access()

        log = self.graphql_mutation(
            "updateProject",
            "UpdateProjectMutationInput",
            {
                "id": str(self.projects[0].pk),
                "benefitPlanId": str(self.group_plan.pk),
            },
        )

        self.assertEqual(log.status, MutationLog.ERROR)
        self.assertIn("with individual or group enrolments", log.error)
        self.projects[0].refresh_from_db()
        self.assertEqual(self.projects[0].benefit_plan_id, self.plan.pk)

    def test_active_enrolments_block_transfer_when_access_control_is_disabled(self):
        SocialProtectionConfig.enable_benefit_plan_access_control = False

        with self.assertRaisesRegex(
            ValidationError,
            "with individual or group enrolments cannot be moved",
        ):
            ProjectService(self.reader).update({
                "id": self.projects[0].pk,
                "benefit_plan": self.group_plan,
            })

        self.projects[0].refresh_from_db()
        self.assertEqual(self.projects[0].benefit_plan_id, self.plan.pk)

    def test_soft_deleted_enrolments_do_not_block_plan_transfer(self):
        self.add_group_plan_access()
        service = ProjectService(self.reader)
        transfers = [
            (self.enrolments[0], self.projects[0], self.group_plan),
            (self.group_enrolments[0], self.group_projects[0], self.plan),
        ]

        for enrolment, project, destination_plan in transfers:
            with self.subTest(project=project.code):
                enrolment.is_deleted = True
                enrolment.save(username=self.admin.username)

                result = service.update({
                    "id": project.pk,
                    "benefit_plan": destination_plan,
                })

                self.assertTrue(result["success"])
                project.refresh_from_db()
                self.assertEqual(project.benefit_plan_id, destination_plan.pk)

    def test_project_without_enrolments_can_change_plan_with_both_accesses(self):
        self.add_group_plan_access()
        project = self.project("PS-NO-ENROL", self.villages[0])

        result = ProjectService(self.reader).update({
            "id": project.pk,
            "benefit_plan": self.group_plan,
        })

        self.assertTrue(result["success"])
        project.refresh_from_db()
        self.assertEqual(project.benefit_plan_id, self.group_plan.pk)

    def test_normal_project_update_remains_allowed_with_enrolments(self):
        project = self.projects[0]

        result = ProjectService(self.reader).update({
            "id": project.pk,
            "working_days": 6,
        })

        self.assertTrue(result["success"])
        project.refresh_from_db()
        self.assertEqual(project.working_days, 6)

    def test_enrollment_and_bulk_time_entries_reject_unauthorized_plan(self):
        group_service = ProjectEnrollmentService(
            self.reader,
            ProjectEnrollmentService.GROUP,
        )
        with self.assertRaises(PermissionDenied):
            group_service.enroll_project({
                "project_id": self.group_projects[0].pk,
                "ids": [],
            })
        with self.assertRaises(PermissionDenied):
            group_service.bulk_update_time_entries({
                "time_entries": [{
                    "enrollment_id": self.group_enrolments[0].pk,
                    "day_number": 1,
                    "percent_complete": 80,
                }],
            })
        self.group_entries[0].refresh_from_db()
        self.assertEqual(self.group_entries[0].percent_complete, 75)

    def test_enrollments_reject_incompatible_programmes(self):
        other_individual_plan = BenefitPlan(
            code="PSI-OTH",
            name="Other individual programme",
            type="INDIVIDUAL",
        )
        other_individual_plan.save(username=self.admin.username)
        UserBenefitPlanAccess(
            user=self.reader,
            benefit_plan=other_individual_plan,
        ).save(username=self.admin.username)
        individual = Individual(
            first_name="Other",
            last_name="Programme",
            dob=date(2000, 1, 1),
            location=self.villages[0],
        )
        individual.save(username=self.admin.username)
        beneficiary = Beneficiary(
            individual=individual,
            benefit_plan=other_individual_plan,
            status="ACTIVE",
        )
        beneficiary.save(username=self.admin.username)

        with self.assertRaisesRegex(ValueError, "project's program"):
            ProjectEnrollmentService(
                self.reader,
                ProjectEnrollmentService.INDIVIDUAL,
            ).enroll_project({
                "project_id": self.projects[0].pk,
                "ids": [beneficiary.pk],
            })

        other_group_plan = BenefitPlan(
            code="PSG-OTH",
            name="Other group programme",
            type="GROUP",
        )
        other_group_plan.save(username=self.admin.username)
        UserBenefitPlanAccess(
            user=self.reader,
            benefit_plan=other_group_plan,
        ).save(username=self.admin.username)
        group = Group(code="PS-HH-OTHER", location=self.villages[0])
        group.save(username=self.admin.username)
        group_beneficiary = GroupBeneficiary(
            group=group,
            benefit_plan=other_group_plan,
            status="ACTIVE",
        )
        group_beneficiary.save(username=self.admin.username)
        self.add_group_plan_access()

        with self.assertRaisesRegex(ValueError, "project's program"):
            ProjectEnrollmentService(
                self.reader,
                ProjectEnrollmentService.GROUP,
            ).enroll_project({
                "project_id": self.group_projects[0].pk,
                "ids": [group_beneficiary.pk],
            })

    def test_project_name_validity_does_not_probe_unauthorized_plan(self):
        result = graphene.Schema(query=Query).execute(
            f'{{ projectNameValidity(projectName:"{self.group_projects[0].name}", '
            f'benefitPlanId:"{self.group_plan.pk}") {{ isValid }} }}',
            context_value=self.request(),
        )
        self.assertTrue(result.errors)
        self.assertIn("Benefit Plan access is required", str(result.errors[0]))
