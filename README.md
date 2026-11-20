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

## Configurable Training creation form

In Django admin, edit the ModuleConfiguration for module
`project_social_protection`, layer **backend** (stored as `be`), version **1.0.0**.
Merge `training_form` into its existing JSON; retain other deployment settings.
The complete pasteable example is [training-form-config.example.json](training-form-config.example.json).
It hides micro-catchment/hotspot/foreman/supervisor and makes target beneficiaries
optional; it is an example and is not automatically installed.

Every Training create field accepts `visible`, `required`, `readOnly`, and,
where applicable, `default`. Unspecified settings retain the existing form.
Hiding an optional field makes it optional unless `required` is explicitly set;
a hidden/read-only required input needs a valid default. Unknown fields/options,
non-boolean flags and incompatible rules are rejected when the configuration is
saved. Invalid reloads retain the previous settings.

Field keys: `name`, `sessionStartDate`, `sessionEndDate`, `district`, `location`
(TA), `microCatchment`, `hotspot`, `trainingModules`, `knownPlace`,
`targetBeneficiaries`, `topicCount`, `status`, `benefitPlan`, `foreman`, `supervisor`.
Name remains required; it may be hidden/read-only with a default.
TA (`location`) is required only when its configured `required` value is true.
When TA is optional and omitted, the selected District is saved as the Training's
geographic scope. Keep District available or supply its default UUID. District-only
Training codes use zeroes in the TA segment; geographic access checks still apply.
Programme comes from the route, status starts in PREPARATION and topic count is
derived, so those three stay read-only. Hidden/read-only modules without a default
use all programme modules. The generated code is not a creation input.

Text/numeric defaults use JSON strings/numbers. Dates use `YYYY-MM-DD` or null.
Location/district defaults use raw location UUIDs; foreman/supervisor use raw User
UUIDs; micro-catchment/hotspot use their database IDs. Reference objects with
`id`/`uuid` are also accepted, but labels are resolved from actual records.
`trainingModules.default` is an ordered array of raw module UUIDs. Location scope
and module/programme membership are still validated on save.

`projectTrainingFormConfig` exposes only the effective form settings and resolved
default labels to signed-in clients. The create page fetches it from the backend,
so a Django-admin configuration change needs a page reload, not a frontend build.
Defaults and required/read-only/hidden-input handling are also enforced by the
create service; WORK behavior is unchanged. Optional Training targets are stored
as null (migration `0010_configurable_training_form`) and do not impose an
enrolment cap. Normal target limits still apply when a target is supplied.

These settings control creation. Existing records do not have their historical
values overwritten when a deployment default changes.

## Training topic snapshots

Training create/update accepts ordered `moduleIds` and does not accept work
`activityId` or manually entered `workingDays`. Omission on create selects all
live modules of the programme, ordered by name. Every selected module must have
live topics and belong to the programme. WORK still requires its Activity and
positive working days.

Migration `0009_training_topic_snapshots` adds audited ProjectTopic snapshots.
The service copies topic codes/names, module names, and topic order and derives
working days from the count. Catalogue edits and deletions do not change existing
snapshots. Module changes atomically replace the snapshot until attendance is
first recorded; cleared/deleted attendance remains in history and keeps the
mapping locked. Existing trainings with attendance but no snapshot require a
separate reviewed legacy mapping; an edit never silently reinterprets old columns.

Project GraphQL exposes `trainingModules`, `trainingTopics`, and
`trainingModulesLocked`. Enrollment `moduleCompletion` uses the shared
`training_module_completion` helper to return ticked/total topics and percentage
per module. TRAINING time entries accept only 0 or 100; WORK percentages remain
unchanged. The topic attendance grid is the next UI step.

## Training module catalogue

Administration > Training modules manages shared Activity modules, programme links,
and ordered topics. Module searches use `gql_activity_search_perms` (208001);
create/update/delete use `gql_activity_create_perms` (208002),
`gql_activity_update_perms` (208003), and `gql_activity_delete_perms` (208004).
All mutations enforce their right in the service as well as the UI.

`trainingModule(benefitPlans_Id: "<programme UUID>")` returns live catalogue modules
with `programmes` and ordered `topics`. `createTrainingModule`,
`updateTrainingModule`, and `deleteTrainingModule` expose atomic changes and clear
validation errors. A save includes the complete ordered topic list; retain topic
IDs to edit/reorder, and omit removed topics to soft-delete them. Topic codes are
unique among all live topics. Legacy WORK Activities do not require programme
links or topics and continue to use the existing Activity query.

Topic changes have their own history. Each module save also records programme IDs
and the ordered topics under `json_ext.training_catalogue` so its existing history
captures M2M association changes. Deleting modules/topics retains the rows and
their historical records. Project attendance snapshots are a separate feature.

## Training setup

Configure module `project_social_protection`, layer `backend`, using the module's
existing version. Add `"project_type": "TRAINING"` to its configuration to create
trainings, or `"project_type": "WORK"` for the existing work deployment (default).
Only these two values are valid. Invalid startup configuration fails; an invalid
reload is logged and retains the previous configuration.

Migration `0007_training_sessions` marks existing projects and their history as
WORK. New projects receive the deployment type in the service; it is read-only
and remains unchanged when the deployment configuration changes. The signed-in
`projectDeploymentType` GraphQL query supplies the create form's type; editing
uses the stored `projectType` instead. There is no type selector.

Trainings require a manually entered name (maximum 255 characters), unique among
live projects in their programme, and a location. Optional `sessionStartDate`
and `sessionEndDate` support clearing and partial updates; an end date before the
start date is rejected. WORK name generation is unchanged. This setup step does
not implement the module/topic catalogue, attendance sheets, or the revised
training enrolment restrictions; those are subsequent steps.

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

Programme-specific Training fields use `training_form.programs.202646268.fields` for
RMEP and `training_form.programs.<UPG_PROGRAM_CODE>.fields` for UPG. Keys match the selected
BenefitPlan's `code` value. Replace `<UPG_PROGRAM_CODE>` with the actual UPG code.
Each programme overrides shared `training_form.fields` property by property;
unmatched programmes retain shared rules. Set `visible: false` to remove an
optional field. Visibility, required, readOnly and default are all supported.
The example JSON includes both programmes; adjust its sample rules as needed.
The API resolves the selected programme using `projectTrainingFormConfig(benefitPlanId: UUID)`;
creation defaults and validation use the same programme rules. Save the backend
module configuration and reload the creation page; JSON-only changes need no UI build.
WORK/TRAINING remains a deployment setting.
