"""Disqualifying participants when a training of a disqualifying programme completes.

A programme that sets ``BenefitPlan.json_ext.training_disqualification_percentage``
(UMEP: 90) runs non-continuous trainings. When such a training is marked COMPLETED,
each ACTIVE participant below that percentage in any module of the training is
suspended through the programme's beneficiary service, with ``disqualified_on`` and
``disqualification_reason`` recorded on the beneficiary. A value exactly at the
percentage is not disqualified. Per-module completion comes from the shared
training_module_completion_for helper, as on the attendance grid and in the payroll.
"""
import datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from project_social_protection.models import BeneficiaryProjectEnrollment, GroupBeneficiaryProjectEnrollment
from project_social_protection.training_topics import training_module_completion_for

DISQUALIFICATION_PERCENTAGE_KEY = "training_disqualification_percentage"


def disqualification_percentage(benefit_plan):
    """The programme's disqualification percentage, or None when it disqualifies nobody."""
    json_ext = benefit_plan.json_ext if isinstance(getattr(benefit_plan, "json_ext", None), dict) else {}
    value = json_ext.get(DISQUALIFICATION_PERCENTAGE_KEY)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        number = None
    if isinstance(value, bool) or number is None or not number.is_finite() or not 0 < number <= 100:
        raise ValidationError(_("The programme's %s must be a number above 0 and at most 100.")
                              % DISQUALIFICATION_PERCENTAGE_KEY)
    return number


def is_disqualifying_training(project):
    return project.project_type == "TRAINING" and disqualification_percentage(project.benefit_plan) is not None


def failing_modules(rows, percentage):
    """The modules below ``percentage``; whole numbers, so the boundary is exact."""
    return [row for row in rows if row["total_topics"] and row["ticked_topics"] * 100 < percentage * row["total_topics"]]


def disqualification_reason(project, failing, percentage):
    return _("Training '%(training)s': %(modules)s; below the programme's %(percentage)s%%.") % {
        "training": project.name,
        "modules": ", ".join("module '%s' %s%%" % (row["module"]["name"], _format(row["percentage"]))
                             for row in failing),
        "percentage": _format(percentage),
    }


def disqualify_participants(project, user):
    """Suspend the training's ACTIVE participants below the programme's percentage.

    Returns how many were suspended. Raises ValidationError when one cannot be saved,
    so the caller's transaction (the training's completion) is rolled back.
    """
    from social_protection.models import BeneficiaryStatus
    from social_protection.services import BeneficiaryService, GroupBeneficiaryService

    percentage = disqualification_percentage(project.benefit_plan)
    if percentage is None:
        return 0
    today = datetime.date.today().isoformat()
    suspended = 0
    for enrollment_model, beneficiary_field, service_class, owner_field in (
            (BeneficiaryProjectEnrollment, "beneficiary", BeneficiaryService, "individual_id"),
            (GroupBeneficiaryProjectEnrollment, "group_beneficiary", GroupBeneficiaryService, "group_id")):
        enrollments = list(enrollment_model.objects.filter(project=project, is_deleted=False).select_related(
            "project", beneficiary_field))
        completion = training_module_completion_for(enrollments)
        for enrollment in enrollments:
            beneficiary = getattr(enrollment, beneficiary_field)
            if beneficiary is None or beneficiary.is_deleted or beneficiary.status != BeneficiaryStatus.ACTIVE:
                continue
            failing = failing_modules(completion.get(enrollment.pk, []), percentage)
            if not failing:
                continue
            result = service_class(user).update({
                "id": beneficiary.id,
                "benefit_plan_id": beneficiary.benefit_plan_id,
                owner_field: getattr(beneficiary, owner_field),
                "status": BeneficiaryStatus.SUSPENDED,
                "json_ext": {
                    **(beneficiary.json_ext if isinstance(beneficiary.json_ext, dict) else {}),
                    "disqualified_on": today,
                    "disqualification_reason": disqualification_reason(project, failing, percentage),
                },
            })
            if not result or not result.get("success"):
                raise ValidationError(_("Participant %(beneficiary)s could not be suspended: %(error)s") % {
                    "beneficiary": beneficiary.id,
                    "error": (result or {}).get("detail") or (result or {}).get("message"),
                })
            suspended += 1
    return suspended


def _format(value):
    number = Decimal(str(value)).quantize(Decimal("0.1"))
    return f"{number.normalize():f}"
