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

Both export base resolvers apply scope because Core's export mixin calls them
directly. Core builds these files synchronously and restricts downloads to the
requesting user; no Core or Location change is required.

This is the read phase of issue #14. Applying the same scope to mutation services
and proposed destination locations is a separate follow-up. This read-scope change
does not claim to enforce the mutation boundary.
