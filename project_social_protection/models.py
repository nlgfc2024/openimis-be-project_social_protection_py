from django.db import models
from django.utils.translation import gettext as _
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator

from core import models as core_models
from core.models import UUIDModel, ObjectMutation, MutationLog
# BeneficiaryStatus is a value used in enrollment validation; the FK targets
# (BenefitPlan / Beneficiary / GroupBeneficiary) stay in social_protection and are
# referenced lazily by string so there is no import cycle.
from social_protection.models import BeneficiaryStatus


# NOTE ON TABLE NAMES
# This module adopts the tables that used to belong to social_protection. Every model
# pins `db_table` to the original `social_protection_*` name so the app-label change
# moves ownership WITHOUT any DDL. The simple-history tables are pinned the same way at
# the bottom of this file: core's HistoryModel declares `HistoricalRecords(inherit=True)`,
# so redefining `history` on a subclass would raise MultipleRegistrationsError — instead
# we reassign the generated historical model's `db_table` after class definition.


class Activity(core_models.HistoryBusinessModel):
    name = models.CharField(max_length=255, null=False, unique=True)

    class Meta:
        db_table = "social_protection_activity"
        verbose_name = "Activity"
        verbose_name_plural = "Activities"


class ProjectStatus(models.TextChoices):
    INITIATED = "INITIATED", _("INITIATED")
    PREPARATION = "PREPARATION", _("PREPARATION")
    IN_PROGRESS = "IN_PROGRESS", _("IN PROGRESS")
    COMPLETED = "COMPLETED", _("COMPLETED")


class Project(core_models.HistoryBusinessModel):
    benefit_plan = models.ForeignKey('social_protection.BenefitPlan', models.DO_NOTHING, null=False)
    name = models.CharField(max_length=255, null=False)
    code = models.CharField(max_length=32, null=True, blank=True)
    status = models.CharField(
        max_length=100,
        choices=ProjectStatus.choices,
        default=ProjectStatus.PREPARATION,
        null=False
    )
    activity = models.ForeignKey(Activity, models.DO_NOTHING, null=False)
    location = models.ForeignKey('location.Location', models.DO_NOTHING, null=False)
    target_beneficiaries = models.SmallIntegerField(null=False)
    working_days = models.SmallIntegerField(null=False)
    allows_multiple_enrollments = models.BooleanField(default=False)

    # --- Malawi (sprint) fields. All nullable so pre-existing rows stay valid. ---
    # District / Traditional Authority are NOT stored: they are derived from `location`
    # by walking the parent chain (District=type R, TA=type D).
    micro_catchment = models.ForeignKey(
        'location.MicroCatchment', models.DO_NOTHING, null=True, blank=True,
        related_name='projects',
    )
    hotspot = models.ForeignKey(
        'location.Hotspot', models.DO_NOTHING, null=True, blank=True,
        related_name='projects',
    )
    known_place = models.CharField(max_length=255, null=True, blank=True)
    foreman = models.ForeignKey(
        'core.User', models.DO_NOTHING, null=True, blank=True,
        related_name='foreman_projects',
    )
    supervisor = models.ForeignKey(
        'core.User', models.DO_NOTHING, null=True, blank=True,
        related_name='supervisor_projects',
    )

    class Meta:
        db_table = "social_protection_project"
        constraints = [
            # Project names are auto-generated; guarantee uniqueness among live rows
            # per program so a concurrent-create race can't silently duplicate a name.
            models.UniqueConstraint(
                fields=['name', 'benefit_plan'],
                condition=models.Q(is_deleted=False),
                name='uniq_live_project_name_per_plan',
            ),
            # Project code is MIS-generated and must be globally unique.
            models.UniqueConstraint(
                fields=['code'],
                name='uniq_live_project_code',
            ),
        ]


class ProjectMutation(UUIDModel, ObjectMutation):
    project = models.ForeignKey(Project, models.DO_NOTHING, related_name='mutations')
    mutation = models.ForeignKey(MutationLog, models.DO_NOTHING, related_name='project')

    class Meta:
        db_table = "social_protection_projectmutation"


class BeneficiaryProjectEnrollment(core_models.HistoryBusinessModel):
    beneficiary = models.ForeignKey(
        'social_protection.Beneficiary',
        models.DO_NOTHING,
        related_name='project_enrollments',
        null=False,
    )
    project = models.ForeignKey(
        Project,
        models.DO_NOTHING,
        related_name='beneficiary_enrollments',
        null=False,
    )

    class Meta:
        db_table = "social_protection_beneficiaryprojectenrollment"
        unique_together = ('beneficiary', 'project')
        verbose_name = _("Beneficiary Project Enrollment")
        verbose_name_plural = _("Beneficiary Project Enrollments")

    def clean(self):
        if self.beneficiary.status != BeneficiaryStatus.ACTIVE:
            raise ValidationError(_("Only ACTIVE beneficiaries can be enrolled in a project."))
        if self.project.benefit_plan_id != self.beneficiary.benefit_plan_id:
            raise ValidationError(_("Beneficiary and project must belong to the same program."))
        super().clean()

    def __str__(self):
        return f"{self.beneficiary} - {self.project.name}"


class GroupBeneficiaryProjectEnrollment(core_models.HistoryBusinessModel):
    group_beneficiary = models.ForeignKey(
        'social_protection.GroupBeneficiary',
        models.DO_NOTHING,
        related_name='project_enrollments',
        null=False,
    )
    project = models.ForeignKey(
        Project,
        models.DO_NOTHING,
        related_name='group_beneficiary_enrollments',
        null=False,
    )

    class Meta:
        db_table = "social_protection_groupbeneficiaryprojectenrollment"
        unique_together = ('group_beneficiary', 'project')
        verbose_name = _("Group Beneficiary Project Enrollment")
        verbose_name_plural = _("Group Beneficiary Project Enrollments")

    def clean(self):
        if self.group_beneficiary.status != BeneficiaryStatus.ACTIVE:
            raise ValidationError(_("Only ACTIVE group beneficiaries can be enrolled in a project."))
        if self.project.benefit_plan_id != self.group_beneficiary.benefit_plan_id:
            raise ValidationError(_("Group beneficiary and project must belong to the same program."))
        super().clean()

    def __str__(self):
        return f"{self.group_beneficiary.group.code} - {self.project.name}"


class AbstractProjectTimeEntry(core_models.HistoryBusinessModel):
    """
    Base model for recording daily percent completion for enrollments in projects.
    Subclasses must implement `_get_enrollment_instance()`.
    """
    # Sequential workday number within the project (1..working_days).
    day_number = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    # Percentage of work completed for this day (0–100).
    percent_complete = models.IntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    class Meta:
        abstract = True

    def _get_enrollment_instance(self):
        """
        Subclasses must override this to return the enrollment object.
        """
        raise NotImplementedError("_get_enrollment_instance() must be implemented in subclass")

    def clean(self):
        enrollment = self._get_enrollment_instance()
        project = enrollment.project

        if not 1 <= self.day_number <= project.working_days:
            raise ValidationError(
                _("Day number must be between 1 and %(working_days)s.") % {"working_days": project.working_days}
            )

        super().clean()

    def __str__(self):
        enrollment = self._get_enrollment_instance()
        return f"{enrollment} - Day {self.day_number}: {self.percent_complete}%"


class BeneficiaryProjectTimeEntry(AbstractProjectTimeEntry):
    enrollment = models.ForeignKey(
        'BeneficiaryProjectEnrollment',
        models.DO_NOTHING,
        related_name='time_entries',
        null=False,
    )

    class Meta:
        db_table = "social_protection_beneficiaryprojecttimeentry"
        unique_together = ('enrollment', 'day_number')
        verbose_name = _("Beneficiary Project Time Entry")
        verbose_name_plural = _("Beneficiary Project Time Entries")

    def _get_enrollment_instance(self):
        return self.enrollment


class GroupBeneficiaryProjectTimeEntry(AbstractProjectTimeEntry):
    enrollment = models.ForeignKey(
        'GroupBeneficiaryProjectEnrollment',
        models.DO_NOTHING,
        related_name='time_entries',
        null=False,
    )

    class Meta:
        db_table = "social_protection_groupbeneficiaryprojecttimeentry"
        unique_together = ('enrollment', 'day_number')
        verbose_name = _("Group Beneficiary Project Time Entry")
        verbose_name_plural = _("Group Beneficiary Project Time Entries")

    def _get_enrollment_instance(self):
        return self.enrollment


# --- Pin the simple-history tables to their original social_protection_* names. ---
# core.HistoryModel uses HistoricalRecords(inherit=True); overriding `history` on the
# subclass would double-register it, so we reassign the generated historical model's
# db_table here instead. We set BOTH `_meta.db_table` (used by the ORM at runtime) and
# `_meta.original_attrs['db_table']` (read by makemigrations' ModelState.from_model) so
# the migration state matches runtime and no DDL is generated for these tables.
def _pin_history_table(model, table_name):
    hist_meta = model.history.model._meta
    hist_meta.db_table = table_name
    hist_meta.original_attrs['db_table'] = table_name
    # Fail loudly at import if a Django / simple-history upgrade ever makes this
    # reassignment a no-op — otherwise the next makemigrations would silently emit an
    # AlterModelTable renaming a production history table. Pair with a CI job running
    # `manage.py makemigrations --check --dry-run` as a second guard.
    assert hist_meta.db_table == table_name, (
        f"history table pin failed for {model.__name__}: {hist_meta.db_table}"
    )


_pin_history_table(Activity, "social_protection_historicalactivity")
_pin_history_table(Project, "social_protection_historicalproject")
_pin_history_table(
    BeneficiaryProjectEnrollment,
    "social_protection_historicalbeneficiaryprojectenrollment",
)
_pin_history_table(
    GroupBeneficiaryProjectEnrollment,
    "social_protection_historicalgroupbeneficiaryprojectenrollment",
)
_pin_history_table(
    BeneficiaryProjectTimeEntry,
    "social_protection_historicalbeneficiaryprojecttimeentry",
)
_pin_history_table(
    GroupBeneficiaryProjectTimeEntry,
    "social_protection_historicalgroupbeneficiaryprojecttimeentry",
)
