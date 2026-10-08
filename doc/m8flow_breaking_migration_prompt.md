# M8Flow breaking migration implementation prompt

Use this prompt from the `C:\dev\repos\m8flow` repository. It migrates M8Flow
to the final breaking `m8flow-bpmn-core` contract. Do not preserve compatibility
fallbacks for the structures listed below.

## Context

`m8flow-bpmn-core` is moving from its compatibility release to a breaking
schema/API release. M8Flow and the core library must be deployed as one unit.
The target contract is documented in the core repository as
`doc/breaking_schema_contract.md`.

Before changing code, inspect the current M8Flow tree and run a repository-wide
search for these legacy names:

```text
HumanTaskModel
HumanTaskUserModel
human_task
human_task_user
human_task_id
tenant_specific_field_1
tenant_specific_field_2
tenant_specific_field_3
spiff_serializer_version
single_process_hash
full_process_model_hash
ProcessInstanceEventType
permission_target_id
\.uri
_in_seconds
```

Do not modify historical migrations merely to remove their old names. Add new
migrations that transform the existing schema, and update active application
code, new migrations, fixtures, and tests.

## Required M8Flow changes

### Workflow and human tasks

- Use `WorkItemModel` as the only persisted claim-state model.
- Remove imports and queries for `HumanTaskModel` and `HumanTaskUserModel`.
- Replace `human_task_id` parameters with `work_item_id` where the identifier
  represents claimable work.
- Keep task-definition and process display metadata sourced from their proper
  models rather than copying it into `work_item`.
- Update claim, complete, reopen, terminate, retry, reassignment, pending-list,
  review, external-form, and notification flows.
- Remove `coalesce(work_item, human_task)` fallback queries.
- Update route paths, serializers, OpenAPI declarations, fixtures, and UI-facing
  response objects consistently.

### Group table

- Treat `m8f_group` as the canonical group table name.
- Replace every active M8Flow reference to the legacy `group` table in ORM
  models, raw SQL, joins, foreign keys, authorization queries, lane
  assignment queries, fixtures, and tests.
- Do not recreate a compatibility view or alias named `group`.
- Ensure group, principal, user-group assignment, work-item lane, and
  authorization migrations all reference `m8f_group`.

### Tenant identity

Replace all uses of the anonymous core columns with the named fields:

```text
tenant_specific_field_1 -> realm_identifier
tenant_specific_field_2 -> external_org_id
tenant_specific_field_3 -> external_user_id
```

Update identity synchronization, lookup, tenant switching, user creation,
tests, serializers, and documentation. Add a migration validation step that
fails if old and new values disagree before dropping the old columns.

### Process definition identity

- Replace `single_process_hash` and `full_process_model_hash` with the final
  canonical process digest.
- Update import, lookup, idempotent upsert, scheduler, designer, and test code.
- Validate that no two definitions would collapse onto one digest incorrectly.
- Update API payloads and fixtures.

### Workflow engine version

Rename all active uses of `spiff_serializer_version` to
`workflow_engine_version`, including model access, serialization, workflow
restore, migrations, and tests.

### Events

- Import and use `ProcessLifecycleEventType` and `TaskEventType` directly.
- Remove `ProcessInstanceEventType` imports and public references.
- Read and write non-null `ProcessInstanceEventModel.category`.
- Update event queries, API serialization, audit pages, fixtures, and tests.
- Verify event counts and ordering before and after migration.

### Authorization

- Use explicit `(resource_type, resource_id)` targets for all new and existing
  authorization checks.
- Convert every resolvable URI target using an explicit reviewed mapping.
- Fail migration for URI targets that cannot be mapped unambiguously.
- Remove URI matching, URI fallback, and URI-only authorization checks from
  active application code.
- Update permission imports, policy decorators, identity services, fixtures,
  and authorization tests.

### Timestamp usage

- Replace all core-owned persisted `*_in_seconds` reads and writes with
  timezone-aware `datetime` values.
- Change public parameters, filters, sorting, serializers, and tests.
- Do not convert unrelated non-persistence protocol fields automatically;
  document those fields explicitly if they remain epoch-based.
- Ensure all comparisons use UTC-aware datetimes.

## Migration requirements

Add a coordinated M8Flow migration after the compatibility/backfill revisions.
It must be the first M8Flow migration applied after core revision
`k2l3m4n5o6p7`, and that dependency must be recorded in the release notes.
It must:

1. validate work-item, identity, digest, authorization, event, JSON, and
   timestamp data;
2. stop without schema changes when validation finds ambiguity or conflicts;
3. use the already-renamed `m8f_group` table and rebuild/verify every
   dependent foreign key;
4. migrate assignments and remove `human_task` and `human_task_user` only
   after all data is represented by the canonical work-item schema;
5. rename identity and workflow-engine columns;
6. enforce non-null event categories;
7. remove legacy epoch columns and indexes;
8. remove obsolete URI and process-hash columns;
9. provide a downgrade that documents data-loss limitations and requires a
   tested backup for rollback.

Historical Alembic revisions must remain unchanged.

The downstream migration is not complete until its revision exists in the
M8Flow repository and an upgrade test executes it against a database whose
core schema is at `k2l3m4n5o6p7`. This prompt is not a substitute for that
M8Flow migration.

## Verification

Add or update tests for:

- populated legacy-database upgrade;
- validation failure for every ambiguous/orphaned case;
- work-item claiming, completion, reassignment, retry, and termination;
- named identity synchronization;
- canonical process digest lookup and collision detection;
- split event enums and non-null categories;
- explicit resource-pair authorization;
- tenant-isolated JSON reads;
- timezone-aware dates beyond 2038;
- absence of legacy imports, public parameters, ORM mappings, and runtime
  queries.

Run at minimum:

```powershell
uv run --locked pytest
uv run --locked ruff check .
git diff --check
```

Run the M8Flow integration suite against a database upgraded through the final
core and M8Flow revisions. Do not deploy while old workers, schedulers, API
processes, or background jobs can still write the legacy schema.

## Completion criteria

The migration is complete only when:

- M8Flow starts against the final core schema without compatibility aliases;
- no active M8Flow source references the legacy names above;
- all upgrade and integration tests pass;
- the migration runbook records supported core/M8Flow version pairs, backup
  requirements, rollback limitations, and deployment order.
