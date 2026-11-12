import csv
from datetime import date
import io
import json
from tempfile import TemporaryDirectory
from unittest.mock import patch

import graphene
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.db.models import Value
from django.test import RequestFactory, TestCase, override_settings
from django.test.client import closing_iterator_wrapper
from django.test.utils import CaptureQueriesContext
from graphql_relay import to_global_id
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import ExportableQueryModel
from core.test_helpers import create_test_interactive_user, create_test_role
from core.utils import TimeUtils
from core.views import fetch_export
from individual.models import Individual, Group
from location.apps import LocationConfig
from location.models import Location, UserDistrict
from social_protection.models import BenefitPlan, Beneficiary, GroupBeneficiary
from project_social_protection.gql_queries import (
    ProjectGQLType, ProjectHistoryGQLType,
    ProjectEligibleBeneficiaryFilter, ProjectEligibleGroupBeneficiaryFilter,
)
from project_social_protection.models import (
    Activity, Project, BeneficiaryProjectEnrollment, GroupBeneficiaryProjectEnrollment,
    BeneficiaryProjectTimeEntry, GroupBeneficiaryProjectTimeEntry,
)
from project_social_protection.schema import Query as ProjectQuery


class Query(ProjectQuery, graphene.ObjectType):
    node = graphene.relay.Node.Field()


@override_settings(ROW_SECURITY=True, MODE="dev")
class ProjectDistrictScopeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = create_test_interactive_user(username="project-scope-admin")
        role = create_test_role(perm_names=["gql_project_search_perms"], name="Project scope reader")
        cls.reader = create_test_interactive_user(username="project-scope-reader", roles=[role.id])
        cls.other_reader = create_test_interactive_user(username="project-scope-other", roles=[role.id])
        cls.a = cls.location("R", "PS-A")
        cls.b = cls.location("R", "PS-B")
        cls.ta = cls.location("D", "PS-A1", cls.a)
        cls.other_ta = cls.location("D", "PS-A2", cls.a)
        cls.b_ta = cls.location("D", "PS-B1", cls.b)
        cls.villages = [cls.village(ta) for ta in [cls.ta, cls.other_ta, cls.b_ta]]
        cls.assignment = UserDistrict.objects.create(user=cls.reader.i_user, location=cls.ta, audit_user_id=-1)
        UserDistrict.objects.create(user=cls.other_reader.i_user, location=cls.b_ta, audit_user_id=-1)
        cls.plan = BenefitPlan(code="PS-IND", name="Project scope individual", type="INDIVIDUAL")
        cls.plan.save(username=cls.admin.username)
        cls.group_plan = BenefitPlan(code="PS-GRP", name="Project scope group", type="GROUP")
        cls.group_plan.save(username=cls.admin.username)
        cls.activity = Activity(name="Project scope activity")
        cls.activity.save(username=cls.admin.username)
        cls.projects = [cls.project(f"PS-I{n}", village) for n, village in enumerate(cls.villages)]
        cls.group_projects = [
            cls.project(f"PS-G{n}", village, cls.group_plan) for n, village in enumerate(cls.villages)
        ]
        cls.beneficiaries, cls.group_beneficiaries = [], []
        cls.enrolments, cls.group_enrolments, cls.entries, cls.group_entries = [], [], [], []
        for n, village in enumerate(cls.villages):
            individual = Individual(first_name=f"Person{n}", last_name="Scope", dob=date(2000, 1, 1), location=village)
            individual.save(username=cls.admin.username)
            beneficiary = Beneficiary(individual=individual, benefit_plan=cls.plan, status="ACTIVE")
            beneficiary.save(username=cls.admin.username)
            group = Group(code=f"PS-HH{n}", location=village)
            group.save(username=cls.admin.username)
            group_beneficiary = GroupBeneficiary(group=group, benefit_plan=cls.group_plan, status="ACTIVE")
            group_beneficiary.save(username=cls.admin.username)
            enrolment = BeneficiaryProjectEnrollment(beneficiary=beneficiary, project=cls.projects[n])
            enrolment.save(username=cls.admin.username)
            group_enrolment = GroupBeneficiaryProjectEnrollment(
                group_beneficiary=group_beneficiary, project=cls.group_projects[n],
            )
            group_enrolment.save(username=cls.admin.username)
            entry = BeneficiaryProjectTimeEntry(enrollment=enrolment, day_number=1, percent_complete=50)
            entry.save(username=cls.admin.username)
            group_entry = GroupBeneficiaryProjectTimeEntry(
                enrollment=group_enrolment, day_number=1, percent_complete=75,
            )
            group_entry.save(username=cls.admin.username)
            cls.beneficiaries.append(beneficiary)
            cls.group_beneficiaries.append(group_beneficiary)
            cls.enrolments.append(enrolment)
            cls.group_enrolments.append(group_enrolment)
            cls.entries.append(entry)
            cls.group_entries.append(group_entry)

    @classmethod
    def location(cls, kind, code, parent=None):
        return Location.objects.create(type=kind, code=code, name=code, parent=parent, audit_user_id=-1)

    @classmethod
    def village(cls, ta):
        gvh = cls.location("W", ta.code + "-W", ta)
        return cls.location("V", ta.code + "-V", gvh)

    @classmethod
    def project(cls, code, village, plan=None):
        obj = Project(
            code=code, name=code, location=village, benefit_plan=plan or cls.plan,
            activity=cls.activity, target_beneficiaries=10, working_days=5,
            allows_multiple_enrollments=True,
        )
        obj.save(username=cls.admin.username)
        return obj

    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(LocationConfig, "no_location_check", False).start()
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.media = override_settings(MEDIA_ROOT=temp.name)
        self.media.enable()
        self.addCleanup(self.media.disable)

    def request(self, user=None):
        req = RequestFactory().get("/graphql")
        req.user = user or self.reader
        return req

    def query(self, selection, user=None, variables=None, request=None):
        result = graphene.Schema(query=Query).execute(
            selection, context_value=request or self.request(user), variable_values=variables,
        )
        self.assertIsNone(result.errors, result.errors)
        return result.data

    def codes(self, user=None):
        data = self.query("{ project(first:100) { edges { node { code } } } }", user=user)
        return {edge["node"]["code"] for edge in data["project"]["edges"]}

    def test_ta_assignment_grants_entire_parent_district_without_micro_catchment(self):
        self.assertEqual(self.codes(), {"PS-I0", "PS-I1", "PS-G0", "PS-G1"})
        self.assertTrue(all(project.micro_catchment_id is None for project in self.projects))

    def test_direct_district_and_multiple_assignments(self):
        self.assignment.location = self.a
        self.assignment.save()
        self.assertEqual(len(self.codes()), 4)
        UserDistrict.objects.create(user=self.reader.i_user, location=self.b_ta, audit_user_id=-1)
        self.assertEqual(len(self.codes()), 6)

    def test_unassigned_expired_and_inactive_assignments_grant_no_projects(self):
        self.assignment.validity_to = TimeUtils.now()
        self.assignment.save()
        self.assertEqual(self.codes(), set())
        self.assignment.delete()
        self.assertEqual(self.codes(), set())

    def test_inactive_ta_or_parent_district_grants_no_projects(self):
        for location in [self.ta, self.a]:
            with self.subTest(location=location.code):
                location.validity_to = TimeUtils.now()
                location.save()
                self.assertEqual(self.codes(), set())
                location.validity_to = None
                location.save()

    def test_anonymous_model_scope_is_empty(self):
        self.assertFalse(Project.get_queryset(None, AnonymousUser()).exists())

    def test_unrestricted_modes_and_admin_remain_supported(self):
        self.assertEqual(len(self.codes(self.admin)), 6)
        with override_settings(ROW_SECURITY=False):
            self.assertEqual(len(self.codes()), 6)
        with patch.object(LocationConfig, "no_location_check", True):
            self.assertEqual(len(self.codes()), 6)

    def test_scope_preserves_supplied_queryset_and_annotations(self):
        qs = Project.objects.filter(benefit_plan=self.plan).annotate(marker=Value(42))
        scoped = Project.get_queryset(qs, self.reader)
        self.assertCountEqual(scoped.values_list("code", flat=True), ["PS-I0", "PS-I1"])
        self.assertEqual(scoped.first().marker, 42)

    def test_explicit_ids_and_location_filters_cannot_bypass_scope(self):
        for args in [f'id:"{self.projects[2].pk}"', f'parentLocation:"{self.b.uuid}"']:
            with self.subTest(args=args):
                data = self.query(f'{{ project({args}) {{ totalCount }} }}')
                self.assertEqual(data["project"]["totalCount"], 0)

    def test_project_enrolment_and_time_entry_nodes_are_scoped(self):
        for type_name, objects in [
            ("ProjectGQLType", self.projects),
            ("BeneficiaryProjectEnrollmentGQLType", self.enrolments),
            ("GroupBeneficiaryProjectEnrollmentGQLType", self.group_enrolments),
            ("BeneficiaryProjectTimeEntryGQLType", self.entries),
            ("GroupBeneficiaryProjectTimeEntryGQLType", self.group_entries),
        ]:
            for index, visible in [(0, True), (2, False)]:
                with self.subTest(type_name=type_name, visible=visible):
                    node_id = to_global_id(type_name, objects[index].pk)
                    data = self.query(f'{{ node(id:"{node_id}") {{ id }} }}')
                    self.assertEqual(data["node"] is not None, visible)

    def test_enrolment_roots_and_nested_project_time_entries_are_scoped(self):
        for field in ["beneficiaryProjectEnrollment", "groupBeneficiaryProjectEnrollment"]:
            with self.subTest(field=field):
                data = self.query(f"{{ {field} {{ edges {{ node {{ project {{ code }} timeEntries {{ dayNumber }} }} }} }} }}")
                rows = data[field]["edges"]
                self.assertEqual(len(rows), 2)
                self.assertTrue(all(row["node"]["project"]["code"][-1] in ["0", "1"] for row in rows))
                self.assertTrue(all(row["node"]["timeEntries"] == [{"dayNumber": 1}] for row in rows))

    def test_history_follows_current_project_after_transfer(self):
        project = self.projects[0]
        project.location = self.villages[2]
        project.save(username=self.admin.username)
        self.assertFalse(Project.get_history_queryset(None, self.reader).filter(id=project.pk).exists())
        history = Project.get_history_queryset(None, self.other_reader).filter(id=project.pk)
        self.assertEqual(history.count(), 2)
        self.assertCountEqual(history.values_list("location_id", flat=True), [self.villages[0].pk, self.villages[2].pk])
        old_version = history.order_by("history_date").first()
        node_id = to_global_id("ProjectHistoryGQLType", old_version.pk)
        self.assertIsNone(self.query(f'{{ node(id:"{node_id}") {{ id }} }}')["node"])
        self.assertIsNotNone(self.query(f'{{ node(id:"{node_id}") {{ id }} }}', user=self.other_reader)["node"])

    def test_history_retains_authorized_soft_deleted_parent(self):
        project = self.projects[0]
        project.is_deleted = True
        project.save(username=self.admin.username)
        data = self.query(f'{{ projectHistory(id:"{project.pk}") {{ totalCount }} }}')
        self.assertEqual(data["projectHistory"]["totalCount"], 2)
        visible = self.query('{ project(isDeleted:false) { edges { node { code } } } }')
        self.assertNotIn(project.code, {row["node"]["code"] for row in visible["project"]["edges"]})

    def export(self, field, user=None, args=""):
        data = self.query(
            f'query($columns:GenericScalar) {{ {field}(fields:["code"], fieldsColumns:$columns {args}) }}',
            user=user, variables={"columns": json.dumps({"code": "Code"})},
        )
        export = ExportableQueryModel.objects.get(name=data[field])
        with export.content.open("rb") as content:
            rows = list(csv.DictReader(io.StringIO(content.read().decode())))
        return export, rows

    def test_actual_project_export_is_scoped_without_type_hook(self):
        with patch.object(ProjectGQLType, "get_queryset", side_effect=AssertionError("type hook used")):
            export, rows = self.export("projectExport")
        self.assertEqual({row["Code"] for row in rows}, {"PS-I0", "PS-I1", "PS-G0", "PS-G1"})
        self.assertEqual(export.user, self.reader)

    def test_history_export_uses_current_parent_not_snapshot_location(self):
        project = self.projects[0]
        project.location = self.villages[2]
        project.save(username=self.admin.username)
        with patch.object(ProjectHistoryGQLType, "get_queryset", side_effect=AssertionError("type hook used")):
            _, rows = self.export("projectHistoryExport", user=self.other_reader, args=f', id:"{project.pk}"')
        self.assertEqual([row["Code"] for row in rows], [project.code, project.code])
        _, hidden_rows = self.export("projectHistoryExport")
        self.assertNotIn(project.code, [row["Code"] for row in hidden_rows])

    def test_existing_export_download_is_owner_only(self):
        export, _ = self.export("projectExport")
        factory = APIRequestFactory()
        for user, status in [(self.reader, 200), (self.other_reader, 403)]:
            request = factory.get("/api/core/fetch_export", {"export": export.name})
            force_authenticate(request, user=user)
            response = fetch_export(request)
            self.assertEqual(response.status_code, status)
            # Match Django's test client: closing a streamed response must not
            # close the database connection enclosing this TestCase transaction.
            list(closing_iterator_wrapper(
                response.streaming_content if response.streaming else [], response.close,
            ))

    def test_both_eligibility_filters_authorize_project_ids(self):
        for filter_class, model, projects in [
            (ProjectEligibleBeneficiaryFilter, Beneficiary, self.projects),
            (ProjectEligibleGroupBeneficiaryFilter, GroupBeneficiary, self.group_projects),
        ]:
            for field in ["eligible_for_project", "enrolled_in_project"]:
                with self.subTest(filter=filter_class.__name__, field=field):
                    filtered = filter_class(
                        data={field: str(projects[2].pk)}, queryset=model.objects.all(), request=self.request(),
                    )
                    self.assertFalse(filtered.qs.exists())

    def test_candidates_retain_narrower_individual_and_group_ta_scope(self):
        for field, project, beneficiary in [
            ("projectEligibleBeneficiaries", self.projects[1], self.beneficiaries[0]),
            ("projectEligibleGroupBeneficiaries", self.group_projects[1], self.group_beneficiaries[0]),
        ]:
            with self.subTest(field=field):
                data = self.query(f'{{ {field}(eligibleForProject:"{project.pk}") {{ edges {{ node {{ uuid }} }} }} }}')
                self.assertEqual(data[field]["edges"], [{"node": {"uuid": str(beneficiary.pk)}}])

    def test_unassigned_reader_cannot_query_unlocated_candidates_without_project_filter(self):
        individual = Individual(first_name="Unlocated", last_name="Person", dob=date(2000, 1, 1))
        individual.save(username=self.admin.username)
        beneficiary = Beneficiary(individual=individual, benefit_plan=self.plan, status="ACTIVE")
        beneficiary.save(username=self.admin.username)
        group = Group(code="PS-UNLOCATED")
        group.save(username=self.admin.username)
        group_beneficiary = GroupBeneficiary(group=group, benefit_plan=self.group_plan, status="ACTIVE")
        group_beneficiary.save(username=self.admin.username)
        self.assignment.delete()
        data = self.query("""{
            projectEligibleBeneficiaries { totalCount }
            projectEligibleGroupBeneficiaries { totalCount }
        }""")
        self.assertEqual(data["projectEligibleBeneficiaries"]["totalCount"], 0)
        self.assertEqual(data["projectEligibleGroupBeneficiaries"]["totalCount"], 0)

    def test_hidden_project_still_participates_in_global_assignment_conflicts(self):
        hidden = self.projects[2]
        hidden.allows_multiple_enrollments = False
        hidden.save(username=self.admin.username)
        enrollment = BeneficiaryProjectEnrollment(project=hidden, beneficiary=self.beneficiaries[0])
        enrollment.save(username=self.admin.username)
        filtered = ProjectEligibleBeneficiaryFilter(
            data={"eligible_for_project": str(self.projects[1].pk)},
            queryset=Beneficiary.objects.filter(pk=self.beneficiaries[0].pk), request=self.request(),
        )
        self.assertFalse(filtered.qs.exists())

    def test_name_validity_read_does_not_reveal_hidden_project(self):
        data = self.query(f'''{{ projectNameValidity(
            projectName:"{self.projects[2].name}", benefitPlanId:"{self.plan.pk}"
        ) {{ isValid }} }}''')
        self.assertTrue(data["projectNameValidity"]["isValid"])

    def test_request_reuses_one_assignment_lookup_across_read_paths(self):
        selection = """{
            project { edges { node { code } } }
            projectHistory { totalCount }
            beneficiaryProjectEnrollment { edges { node { project { code } timeEntries { dayNumber } } } }
            groupBeneficiaryProjectEnrollment { edges { node { project { code } timeEntries { dayNumber } } } }
        }"""
        with CaptureQueriesContext(connection) as queries:
            self.query(selection)
        assignment_queries = [q for q in queries if '"tblUsersDistricts"' in q["sql"]]
        self.assertEqual(len(assignment_queries), 1)

    def test_next_request_and_raw_user_scope_see_assignment_changes(self):
        self.codes()
        self.assignment.location = self.b_ta
        self.assignment.save()
        self.assertCountEqual(Project.get_queryset(None, self.reader).values_list("code", flat=True), ["PS-I2", "PS-G2"])
        self.assertEqual(self.codes(), {"PS-I2", "PS-G2"})

    def test_empty_scope_is_cached_without_per_field_assignment_queries(self):
        self.assignment.delete()
        with CaptureQueriesContext(connection) as queries:
            data = self.query("{ first:project { totalCount } second:projectHistory { totalCount } }")
        self.assertEqual(data, {"first": {"totalCount": 0}, "second": {"totalCount": 0}})
        self.assertEqual(sum('"tblUsersDistricts"' in q["sql"] for q in queries), 1)

    def test_context_cache_is_separate_for_users_and_security_modes(self):
        request = self.request()
        selection = "{ project { totalCount } }"
        self.assertEqual(self.query(selection, request=request)["project"]["totalCount"], 4)
        request.user = self.other_reader
        self.assertEqual(self.query(selection, request=request)["project"]["totalCount"], 2)
        with patch.object(LocationConfig, "no_location_check", True):
            self.assertEqual(self.query(selection, request=request)["project"]["totalCount"], 6)
        self.assertEqual(self.query(selection, request=request)["project"]["totalCount"], 2)

    def test_read_permission_is_required_for_direct_node(self):
        node_id = to_global_id("ProjectGQLType", self.projects[0].pk)
        with patch.object(type(self.reader), "has_perms", return_value=False):
            result = graphene.Schema(query=Query).execute(
                f'{{ node(id:"{node_id}") {{ id }} }}', context_value=self.request(),
            )
        self.assertTrue(result.errors)
        self.assertIsNone(result.data["node"])
