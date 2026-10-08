# Breaking release contract

Version `0.2.0` is the coordinated breaking contract for consumers such as
`m8flow`. Apply the migration only after the downstream code has moved to the
canonical names and resource-pair authorization API.

The coordinated release sequence and supported core/M8Flow migration pairs are
documented in [release_migration_strategy.md](release_migration_strategy.md).

## Public API

`m8flow_bpmn_core.api` is the supported public entrypoint. Its exported names,
command/query dataclasses, error hierarchy, and public enum values are treated
as compatibility-sensitive.

Commands and queries are frozen, slotted dataclasses. `tenant_id` is the first
field on every command and query. Timestamp inputs use timezone-aware UTC
`datetime` values.

The public dispatchers return the underlying service results directly. In
practice, callers can receive SQLAlchemy models such as process instances,
human tasks, process events, metadata rows, and process definitions. Their
existing attribute names are therefore response compatibility concerns even
though the library is not an HTTP service.

## Compatibility-sensitive model schema

The following existing table and column names are baseline names. A migration
that renames or removes one requires an explicit compatibility plan and a
coordinated consumer migration:

- `user`, `m8f_group`, `principal`, `user_group_assignment`
- `permission_target`, `permission_assignment`
- `bpmn_process_definition`, `bpmn_process`, `process_model_bpmn_version`
- `process_instance`, `task`, `task_definition`, `work_item`, `future_task`
- `json_data`, `process_instance_event`, `process_instance_metadata`,
  `scheduler_job`
- timezone-aware attributes such as `created_at`, `updated_at`, `started_at`,
  `ended_at`, and `occurred_at`
- `ProcessLifecycleEventType`, `TaskEventType`, and `ProcessInstanceStatus`
  values
- `ProcessInstanceModel.workflow_engine_version`

## Migration rules

Before applying the destructive migration:

1. Deploy the matching M8Flow migration and core version together.
2. Validate and back up the database.
3. Update callers to `work_item`, named identity fields, canonical digest and
   event enums, and explicit authorization resource pairs.
4. Do not run older workers or schedulers after the migration.

The executable baseline for these rules is in
`tests/test_compatibility_contract.py`.

## Timestamp removal transition

The additive migration first adds nullable UTC-aware DateTime columns alongside
the existing epoch columns and backfills them.

The additive migration is
`d2e4f6a8b0c1_add_datetime_compatibility_columns.py`. It backfills the native
columns from existing epoch values and does not remove or rename any existing
column. The follow-up migration
`f7a8b9c0d1e2_remove_legacy_epoch_columns.py` removes the legacy persistence
columns. This release's ORM and service layer use only the native datetime
columns, so the removal migration can be applied without runtime queries that
reference dropped columns. Consumers must migrate command inputs, queries,
serializers, and fixtures before applying it; its downgrade recreates empty
columns and cannot restore removed values.

## Timestamp migration

Public commands and queries accept timezone-aware datetime inputs only.

Returned models now expose additive timezone-aware DateTime attributes such as
`created_at`, `updated_at`, `started_at`, `ended_at`, `run_at`, and
`occurred_at`. Callers must migrate reads and writes to these native UTC-aware
attributes before the destructive schema migration is applied.

## Work-item model

`work_item` is the sole persisted claim-state model. Human-task display and
form metadata remains owned by task and process-definition models.

## Tenant and event model

Phase 4 keeps the existing process/task JSON hash fields and event-type values
while strengthening their storage boundaries:

- `json_data` is keyed by `(m8f_tenant_id, hash)`, so equal payload hashes in
  different tenants no longer resolve to shared data. Runtime lookups and
  writes always include the tenant identifier.
- `ProcessLifecycleEventType`, `TaskEventType`, and
  `ProcessInstanceEventCategory` are canonical, and event `category` is
  non-null after backfill.
- Constraint identifiers are renamed to `m8f_*` names only. Table names,
  columns, legacy event values, and ORM model names remain unchanged.

## Phase 5 task-state compatibility layer

Phase 5 replaces remaining comparisons and assignments for Spiff task states
with `SpiffWorkflow.util.task.TaskState` members. Persisted state names such as
`READY`, `COMPLETED`, `CANCELLED`, and `ERROR` remain unchanged because the
code uses the enum member names when storing them. The internal
`WorkItemState` enum continues to represent M8Flow-specific human-work-item
states such as `CLAIMED` and `TERMINATED`. `TaskModel.state` validates Spiff
state names and preserves the legacy process-operation value `TERMINATED`.

## Authorization target model

Permission targets use required explicit `resource_type` and `resource_id`
fields. URI matching and URI-only helper signatures are removed.

## Workflow model and event-state redesign contract

The workflow model redesign uses `work_item` as the canonical claim-state row.
The work-item table does not duplicate task,
process, lane, form, or JSON metadata.

`ProcessLifecycleEventType` and `TaskEventType` are the canonical event
vocabularies. Historical event string values remain unchanged, and categories
are backfilled before being enforced as non-null.

Runtime task-state logic uses SpiffWorkflow's `TaskState`, while persisted
state names remain compatible strings such as `READY`, `COMPLETED`, `ERROR`,
and `CANCELLED`.

The detailed field-ownership and migration contract is documented in
`doc/workflow_model_event_state_redesign.md`.
