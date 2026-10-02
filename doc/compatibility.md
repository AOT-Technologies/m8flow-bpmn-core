# Compatibility Baseline

This document records the Phase 0 compatibility surface for consumers such as
`m8flow`. The `f7a8b9c0d1e2` migration is an explicit breaking transition
away from the legacy epoch persistence columns; downstream reconciliation is
required before applying that revision.

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

- `user`, `group`, `principal`, `user_group_assignment`
- `permission_target`, `permission_assignment`
- `bpmn_process_definition`, `bpmn_process`, `process_model_bpmn_version`
- `process_instance`, `task`, `task_definition`, `human_task`,
  `human_task_user`, `future_task`
- `json_data`, `process_instance_event`, `process_instance_metadata`,
  `scheduler_job`
- timezone-aware attributes such as `created_at`, `updated_at`, `started_at`,
  `ended_at`, and `occurred_at`
- `ProcessInstanceEventType` and `ProcessInstanceStatus` values
- `ProcessInstanceModel.spiff_serializer_version`

## Phase 0 rules

Until a consumer migration is complete:

1. Preserve `m8flow_bpmn_core.api` exports and signatures.
2. Preserve command/query field names and ordering.
3. Preserve returned model types and existing model attributes.
4. Preserve persisted table, column, foreign-key, and enum string values.
5. Treat additive columns, compatibility aliases, and internal adapters as the
   preferred mechanism for remediation.
6. Treat table splits, model renames, timestamp replacement, and enum removal
   as breaking changes requiring a separately versioned migration.

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

## Part 6 public API compatibility

The public command and query dataclasses accept timezone-aware datetime inputs.
Dispatch behavior, field ordering, tenant-first validation, and returned ORM
model types remain unchanged apart from timestamp field names.

Returned models now expose additive timezone-aware DateTime attributes such as
`created_at`, `updated_at`, `started_at`, `ended_at`, `run_at`, and
`occurred_at`. Callers must migrate reads and writes to these native UTC-aware
attributes before the destructive schema migration is applied.

## Phase 3 work-item compatibility layer

Phase 3 introduces internal work-item state transitions in
`services/work_items.py` over the existing `human_task` row. Claim,
completion, termination, reopening, and ready-state transitions are centralized
there while `HumanTaskModel`, the `human_task` table, task IDs, and returned
attributes remain unchanged. The additive `work_item` table is backfilled and
runtime transitions dual-write both rows.

## Phase 4 tenant and event compatibility layer

Phase 4 keeps the existing process/task JSON hash fields and event-type values
while strengthening their storage boundaries:

- `json_data` is keyed by `(m8f_tenant_id, hash)`, so equal payload hashes in
  different tenants no longer resolve to shared data. Runtime lookups and
  writes always include the tenant identifier.
- `ProcessInstanceEventType` remains the compatibility enum. The additive
  `ProcessLifecycleEventType`, `TaskEventType`, and
  `ProcessInstanceEventCategory` enums expose the cleaner split, while the
  event table stores a nullable `category` for old rows and populates it for
  new events.
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

## Phase 6 authorization target compatibility layer

Permission targets now have additive `resource_type` and `resource_id` fields.
When both are present, authorization uses exact pair matching; URI-based
targets continue to use the existing compatibility matcher. Existing grants
and URI helper signatures remain valid, so consumers can migrate target by
target.

## Workflow model and event-state redesign contract

The workflow model redesign uses an additive transition for compatibility with
existing `m8flow` consumers. The new `work_item` table has been added and
backfilled using the legacy human-task ID, while existing `human_task` rows and
query-facing attributes remain available. Runtime claim-state transitions now
dual-write the legacy human-task row and its normalized work-item companion.
The work-item table does not duplicate task,
process, lane, form, or JSON metadata.

`ProcessLifecycleEventType` and `TaskEventType` are the preferred internal
event vocabularies. The combined `ProcessInstanceEventType` enum and existing
event string values remain supported. The additive event `category` column is
backfilled and legacy null categories remain readable.

Runtime task-state logic uses SpiffWorkflow's `TaskState`, while persisted
state names remain compatible strings such as `READY`, `COMPLETED`, `ERROR`,
and `CANCELLED`.

The detailed field-ownership and migration contract is documented in
`doc/workflow_model_event_state_redesign.md`.
