# Tenant-safe schema migration inventory

This document records the tenant-safe JSON and timestamp schema after the
breaking migration work. Historical column names are listed only to explain
the upgrade path; they are not part of the current ORM or public API.

## JSON payload references

`json_data` is currently represented by `JsonDataModel` with the composite ORM
identity `(m8f_tenant_id, hash)`. The hash columns below remain application-level
references; the schema does not enforce a foreign key to the composite JSON key.

| Referencing table | Column | Tenant source | Current write/read path |
| --- | --- | --- | --- |
| `bpmn_process` | `json_data_hash` | `bpmn_process.m8f_tenant_id` | BPMN process persistence and workflow runtime |
| `task` | `json_data_hash` | `task.m8f_tenant_id` | Task materialization and task runtime |
| `task` | `python_env_data_hash` | `task.m8f_tenant_id` | Task materialization and task runtime |

The process-instance metadata reader resolves JSON using the tenant-qualified
identity `(process tenant, bpmn process hash)`. The workflow runtime creates or
updates process and task payloads through
`JsonDataModel.create_or_update_from_payload(session, tenant_id, payload)`.

The upgrade migration `e5f6a7b8c9d0_phase4_compatibility` discovers references
from all three columns above, including task-only references. Part 3 now
validates those references before re-keying: missing hashes, null tenant/hash
pairs, missing tenant rows, and unreferenced legacy payloads fail the upgrade
without payload re-keying. A hash explicitly referenced by multiple tenants is
duplicated deterministically, preserving tenant-local copies.

## JSON schema and migration history

- Initial schema: `json_data.hash` is the primary key and `json_data` has no
  tenant ownership.
- Phase 4 compatibility migration: adds `m8f_tenant_id`, derives ownership
  from process and task references, then changes the primary key to
  `(m8f_tenant_id, hash)` and adds a tenant foreign key.
- Current model: `m8f_tenant_id` and `hash` are both required primary-key
  columns; `data` is required JSON.
- Current compatibility documentation already states that equal hashes may
  exist independently for different tenants.

Tenant identity is mandatory at every JSON read and write boundary. The
breaking migration validates legacy ownership before re-keying and fails
without changing payload rows when references are ambiguous, orphaned, or
missing.

## Timestamp inventory

All persisted model timestamps now use timezone-aware `DateTime` columns. The
following table records the historical epoch columns and their current native
datetime replacements:

| Model/table | Legacy fields | Current compatibility fields |
| --- | --- | --- |
| `UserModel` / `user` | `created_at_in_seconds`, `updated_at_in_seconds` | `created_at`, `updated_at` |
| `TenantModel` / `m8flow_tenant` | `created_at_in_seconds`, `updated_at_in_seconds` | `created_at`, `updated_at` |
| `BpmnProcessDefinitionModel` / `bpmn_process_definition` | `created_at_in_seconds`, `updated_at_in_seconds` | `created_at`, `updated_at` |
| `BpmnProcessModel` / `bpmn_process` | `start_in_seconds`, `end_in_seconds` | `started_at`, `ended_at` |
| `TaskDefinitionModel` / `task_definition` | `created_at_in_seconds`, `updated_at_in_seconds` | `created_at`, `updated_at` |
| `TaskModel` / `task` | `start_in_seconds`, `end_in_seconds` | `started_at`, `ended_at` |
| `ProcessInstanceModel` / `process_instance` | `start_in_seconds`, `end_in_seconds`, `task_updated_at_in_seconds`, `created_at_in_seconds`, `updated_at_in_seconds` | `started_at`, `ended_at`, `task_updated_at`, `created_at`, `updated_at` |
| `WorkItemModel` / `work_item` | `human_task.created_at_in_seconds`, `human_task.updated_at_in_seconds` | `created_at`, `updated_at` |
| `FutureTaskModel` / `future_task` | `run_at_in_seconds`, `queued_to_run_at_in_seconds`, `updated_at_in_seconds` | `run_at`, `queued_to_run_at`, `updated_at` |
| `ProcessInstanceMetadataModel` / `process_instance_metadata` | `created_at_in_seconds`, `updated_at_in_seconds` | `created_at`, `updated_at` |
| `ProcessModelBpmnVersionModel` / `process_model_bpmn_version` | `created_at_in_seconds` | `created_at` |
| `SchedulerJobModel` / `scheduler_job` | `locked_at_in_seconds`, `run_at_in_seconds`, `created_at_in_seconds`, `updated_at_in_seconds` | `locked_at`, `run_at`, `created_at`, `updated_at` |

`ProcessInstanceEventModel` is a related timestamp case: its historical
`timestamp` column was replaced by the timezone-aware `occurred_at` column.

The destructive migration removes the legacy persisted epoch columns after
validation and backfill. Epoch values that remain in timer payloads are
workflow serialization data, not database timestamp columns; their scope is
documented in `doc/scheduling.md`.

## Historical implementation plan (completed)

The following section is retained as migration history. Its legacy column names
describe the pre-breaking schema and must not be interpreted as current ORM
models or active compatibility fields.

1. **Part 2 — tenant-qualified JSON model and access paths:** verify every
   lookup, insert, update, and delete requires the tenant/hash pair.
2. **Part 3 — safe ownership backfill:** inspect all three reference columns,
   report missing hashes, reject ambiguous tenant ownership, and retain all
   task-only payloads.
3. **Part 4 - migration implementation:** completed with a validated staging
   table and populated legacy-database upgrade coverage. The migration changes
   the primary key only after the complete tenant-qualified dataset is staged.
4. **Part 5 - timestamp conversion:** completed with timezone-aware fields as
   canonical values, BIGINT/epoch compatibility synchronization, and post-2038
   coverage.
5. **Part 6 — query/API compatibility:** keep callers and examples working
   while changing internal persistence reads and writes.
6. **Part 7 - rollback and operations:** completed in
   [`migration_runbook.md`](migration_runbook.md), including backups,
   validation, failed-upgrade recovery, and downgrade limits.

## Historical Part 1 conclusion

The highest-risk dependencies were the three application-level JSON hash
references and the split timestamp representation. Those implementation parts
are now complete; production rollout requires the coordinated downstream
M8Flow migration described in `doc/release_migration_strategy.md`.
