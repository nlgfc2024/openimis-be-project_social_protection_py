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
Catalogue writes use the standard OpenIMIS mutation journal and CSRF/error
pipeline. Create/update/delete return `internalId` and `clientMutationId`;
clients wait for the mutation log's final status before reporting success.
Each operation accepts its own Relay input (`CreateTrainingModuleInput`,
`UpdateTrainingModuleInput`, or `DeleteTrainingModuleInput`); update/delete IDs
belong inside `input`. Updates reject ordinary WORK Activities, and deletion
rejects Activities referenced by live projects. Unused modules are soft-deleted.

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
