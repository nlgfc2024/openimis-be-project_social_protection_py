"""Test helpers for the project domain. Reuses social_protection's benefit-plan /
beneficiary factories (that domain stays there) and adds the project/activity factories
that moved here with the models."""
from social_protection.tests.test_helpers import (  # noqa: F401
    PatchedOpenIMISGraphQLTestCase,
    create_benefit_plan,
    find_or_create_benefit_plan,
    create_individual,
    create_group,
    create_group_with_individual,
    add_individual_to_benefit_plan,
    add_group_to_benefit_plan,
    generate_random_string,
)
from location.test_helpers import create_test_village
from project_social_protection.models import Activity, Project


def find_or_create_activity(name, username):
    activity_found = Activity.objects.filter(name=name)
    if activity_found:
        activity = activity_found.first()
    else:
        activity = Activity(name=name)
        activity.save(username=username)
    return activity


def create_project(name, benefit_plan, username,
                   allows_multiple_enrollments=False, status=None):
    activity = find_or_create_activity("Community Outreach", username)
    location = create_test_village()

    project = Project(
        name=name,
        benefit_plan=benefit_plan,
        activity=activity,
        location=location,
        target_beneficiaries=100,
        working_days=90,
        allows_multiple_enrollments=allows_multiple_enrollments,
    )
    if status is not None:
        project.status = status

    project.save(username=username)
    return project
