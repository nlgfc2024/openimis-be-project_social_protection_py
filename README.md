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

## Assigned-district project mutations

Project services apply the same district policy to create, update, delete and
restore operations. Updates authorize and lock the original project before
checking the effective destination, so supplying an allowed location cannot pull
an inaccessible project into scope. Transfers are permitted when both districts
are assigned. Retained micro-catchment/hotspot links are validated on partial
updates and restore: links must be active, belong to the location's district, and
match each other when both are set. Optional links can be cleared explicitly.
Legacy links that fail these checks must be corrected or cleared before an
update or restore can succeed.

Individual/group enrolment replacements authorize the project and every
beneficiary being added or removed. Beneficiaries retain their narrower TA scope;
omitting an inaccessible existing beneficiary from the replacement list is a
removal and is rejected. Quotas and global assignment conflicts still apply,
but conflicts with inaccessible projects return no project or beneficiary names.

Attendance changes authorize active parent projects and enrolments before any
writes. A supplied time-entry ID must belong to the supplied enrolment and day;
it cannot be used to modify or move a different row. Attendance follows project
district access, including other TAs in the assigned district.

Delete/restore and attendance batches reject mixed-scope input atomically.
Missing and inaccessible parents return the same authorization error. Mutation
services reread assignments, and parent row locks coordinate mutations with
project transfers. Existing GraphQL operation permissions and geography bypasses
remain in effect; anonymous callers cannot use those bypasses to mutate data.

## Benefit Plan access dependency

Project Benefit Plan scoping depends on the shared Track 01 access service from
`openimis-be-social_protection`: `social_protection.benefit_plan_access` and the
`UserBenefitPlanAccess` model/configuration it uses. Deploy or merge a Project
revision that imports this service only after the corresponding Social Protection
revision is available in the same environment. The package dependency in
`setup.py` names `openimis-be-social_protection`, but it is not version-pinned, so
release/merge ordering must provide a revision containing that module.

Benefit Plan assignment scope and assigned-district scope are independent and
are intersected. Disabling geographic `ROW_SECURITY` does not disable Benefit
Plan assignment checks. Disabling the Benefit Plan access-control feature flag
preserves the existing district-only behavior. Superusers and users with the
configured all-plan permission bypass assignment filtering only; GraphQL role
permissions and district rules continue to apply.

The Project Benefit Plan transfer invariant is independent of assignment-access
enforcement: even when the feature flag is disabled, including PWP deployments,
a Project cannot move to another Benefit Plan while it has active individual or
group enrolments. Soft-deleted enrolments do not block a transfer.
