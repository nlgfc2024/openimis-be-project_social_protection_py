# openIMIS Backend project_social_protection reference module

`openimis-be-project_social_protection_py` owns the **project** slice of the
social-protection domain: projects, activities, beneficiary/group enrollment into
projects, and per-day project time-entries (logsheets).

It was extracted from `openimis-be-social_protection_py`. Programs / benefit plans stay
in `social_protection`; this module depends on them (`BenefitPlan`, `Beneficiary`,
`GroupBeneficiary`).

The database tables keep their original `social_protection_*` names (pinned via
`db_table`) so the split preserves existing data — the module adopts the existing tables
rather than creating new ones.

## Django app

```
project_social_protection
```

## Depends on

- `openimis-be-core`
- `openimis-be-individual`
- `openimis-be-location`
- `openimis-be-social_protection`

## Assigned-district project reads

`Project.get_queryset(queryset, user)` applies the same geographic policy as
micro-catchments to the project's required location. With row security enabled,
an active TA (`D`) assignment grants every PWP project in its parent District
(`R`), including projects under other TAs. Direct District assignments also work.
Restricted users without assignments, including national staff, see no projects.
They need appropriate assignments; there is no implicit national-staff bypass.
Superusers, `ROW_SECURITY=False`, and `no_location_check=True` retain unrestricted
geography. Operation-specific permissions remain required.

Individual and Group beneficiary candidates retain their existing narrower
TA-level scope. An optional micro-catchment or a client-supplied location filter
cannot broaden project access. GraphQL reads cache allowed locations only for the
request; a new request or raw-user model call reads assignments afresh.

Project lists, nodes, history, exports, enrolments and time-entry reads use this
policy. History follows the current parent project: its authorized users can see
the complete trail, including records created before a district transfer and
history for a permitted soft-deleted parent. Historical snapshot locations do not
add another restriction.
# Training sessions

In Django Admin, edit the **backend** ModuleConfiguration for
`project_social_protection` and add `"project_type": "TRAINING"` to its JSON.
The default is `WORK`. Only these two values are accepted; invalid reloads
retain the previous configuration. Keep this backend record unexposed.

Apply migrations `0007_training_sessions` and `0008_optional_training_target`
before enabling this code. Migration 0007 marks
existing projects and historical rows as WORK. New projects receive the configured
type in the service; changing deployment settings never changes existing records.
GraphQL exposes `projectType` read-only, and accepts optional `sessionStartDate`
and `sessionEndDate` in create/update inputs (end must not precede start).

TRAINING requires a manually entered name and a location.
Target beneficiaries is optional for TRAINING; omit it or send null for no enrollment
cap. A supplied Training target is informational and has no configured maximum;
it does not cap enrollment. WORK continues to require a target.
Migration `0008_optional_training_target` enables
nullable IntegerField targets on live and historical projects.

Training names must be nonblank,
at most 255 characters, and unique among live projects in the Phase. WORK keeps
its generated names. Set Activity JSON in Django Admin to, for example,
`{"default_working_days": 5}` to seed a new Training's `workingDays` when omitted.
An explicit duration wins; durations must be integers from 1 through 32767.
Activity GraphQL also exposes `defaultWorkingDays`.

TRAINING always permits participation in multiple sessions, including overlapping
periods: a participant may attend several trainings within the same period.
Training uses individual plans and existing ACTIVE individual beneficiaries;
assignment never creates or approves beneficiary records. Group enrollment is
rejected for Training. The frontend household-code convenience queries existing
active individual beneficiaries through the authorized Project query, then adds
their IDs to the complete individual selection. Missing records stay missing. The individual assignment API accepts the complete desired
participant set, so clients must retain previously selected participants when
adding more. WORK keeps its existing group assignment behavior and exclusive enrollment
checks against other WORK projects.

Partial updates use the stored Phase when checking name uniqueness. This also
fixes WORK updates that omit benefit_plan_id.

The frontend must separately support manual Training names and the new date and
duration fields. No UI or deployment configuration is changed by this module.
