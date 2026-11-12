# Changelog

All notable changes to m8flow-core are recorded in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Each release
lists its changes under **Added**, **Changed**, **Deprecated**, **Removed**,
**Fixed**, and **Security**.

## [Unreleased]

### Added

- BPMN Transaction cancellation stops active work and timers, runs recorded
  compensation handlers, and follows the attached Cancel Boundary Event once.
  `validate_compensation_bpmn` lets hosts check supported forms before saving.

- BPMN call activities through the tenant-aware `CallActivityResolver` API.
  Called child workflows are stored inside the parent workflow snapshot and
  their human tasks use the existing parent process instance worklist.
- BPMN compensation boundary events, associations, task/subprocess handlers,
  intermediate throws and end events, targeted compensation, both wait modes,
  and durable reverse completion ordering with nested scope isolation.
- Tenant-scoped compensation journals and progress query, compensation lifecycle
  events, connector idempotency context, and retry of failed undo handlers.
- Additive Alembic revision `l3m4n5o6p7q8` and a restartable compensation example.
  Compensation checkpoints use caller-owned transactions; callers must commit
  captured failures to retain them for retry. Legacy compensation snapshots
  without completion journals cannot continue compensation.

### Changed

- Resolved CodeQL code-scanning alerts without changing the public API.
  `resume_process_instance` and `retry_process_instance` moved from
  `services/process_instances.py` to the new
  `services/process_instance_reactivation.py`, which breaks the import cycle
  with `services/workflow_runtime.py`. Both are still exported from
  `m8flow_core.api` and `m8flow_core.services`. The lazily loaded names in the
  `m8flow_core.services` and `m8flow_core.application` packages are now also
  imported under `TYPE_CHECKING`, so static analysis can see them.
- SpiffWorkflow 3.2.0 is now the minimum supported version, up from 3.1.2.
  Earlier versions never set up event subprocesses.

### Fixed

- Added the compensation journal tables to the sample app's migration chain,
  so tenant realignment can run against databases migrated by the app.
- Removed import cycles in compensation persistence and process termination.
- A timer start event inside an event subprocess is now scheduled as a timer
  of the running instance. Before, importing the definition scheduled it as a
  process timer start, which started extra instances, and the running instance
  fired it only when another timer woke it. Needs SpiffWorkflow 3.2.0 or later.

### Security

- Updated locked dependencies to resolve open Dependabot advisories:
  `filelock` 4.2.4, `virtualenv` 21.15.1, `Mako` 1.4.3, `lxml` 6.1.3, and
  `pydantic-settings` 2.16.2 in `uv.lock`; `Werkzeug` 3.1.9 and `Mako` 1.4.3
  in `sample_app/uv.lock`. The declared dependency ranges are unchanged.

## [1.0.0] - 2026-10-22
## [1.0.0] - 2026-11-11

The first stable release, and the first under the name m8flow-core. Versions
0.1.x to 0.2.1 were untagged development builds named `m8flow-bpmn-core`.
This release changes the package name, the public API, and the database
schema, so upgrade the library and m8flow together. To move from a 0.x build,
follow [Upgrade to 1.0](docs/how-to/upgrade-to-1.0.md), which lists every
renamed and removed name.

### Added

- Public in-process API in `m8flow_core.api`: separate commands and queries,
  `execute_command` and `execute_query` dispatchers that accept a
  caller-owned `Session` or `Connection`, and the `BpmnCoreError` error
  hierarchy.
- BPMN and DMN import, process instances, human task worklists with claim and
  completion, process metadata, event history, and lifecycle commands to
  suspend, resume, error, retry, and terminate.
- Tenant-aware command authorization with built-in `user`, `manager`, and
  `admin` roles and pluggable policies.
- Persisted scheduler jobs for timer start events, `timeCycle` timers,
  intermediate and boundary timers, and scheduled retries, run through
  `run_due_scheduler_jobs`.
- Synchronous BPMN service tasks through pluggable connectors, including an
  adapter for `m8flow-connector-proxy`.
- BPMN call activities through the tenant-aware `CallActivityResolver` API.
- The `work_item` table and `WorkItemModel` for human-task claim state, with
  claims that are safe under concurrency.
- `properties_json["initiator_lanes"]`, to route a lane's tasks to the user
  who started the instance, and `assign_pending_tasks_for_user`, to add
  potential owners after a directory sync.
- Apache License 2.0, a sample host application in `sample_app/`, runnable
  examples, and PyPI releases with build attestations.

### Changed

- **Breaking:** renamed the distribution from `m8flow-bpmn-core` to
  `m8flow-core`, and the import package from `m8flow_bpmn_core` to
  `m8flow_core`.
- **Breaking:** the Alembic history starts again at a single baseline,
  `k2l3m4n5o6p7`, which creates the 1.0 schema on an empty database. Convert
  0.x databases yourself, then run `alembic stamp k2l3m4n5o6p7`.
- **Breaking:** every timestamp is a timezone-aware UTC `datetime`, in public
  parameters and in database columns. `*_in_seconds` names are gone.
- **Breaking:** `ClaimTaskCommand` and `CompleteTaskCommand` take
  `work_item_id` and return `WorkItemModel`, and `GetPendingTasksQuery`
  returns work items.
- **Breaking:** permission targets are exact `(resource_type, resource_id)`
  pairs instead of URI patterns.
- **Breaking:** user identity, group, serializer-version, and digest columns
  were renamed, and constraints and indexes use the `m8f_*` namespace.
- **Breaking:** a lane goes to the process initiator only when listed in
  `initiator_lanes`, not because its name looks like "process initiator".
- Lane owners that match no user in the tenant are skipped with a warning
  instead of failing the process.
- The minimum supported SpiffWorkflow version is 3.2.0.

### Removed

- **Breaking:** `HumanTaskModel` and the `human_task` tables, replaced by
  `work_item`.
- **Breaking:** `ProcessInstanceEventType`, replaced by
  `ProcessLifecycleEventType`, `TaskEventType`, and
  `ProcessInstanceEventCategory`.
- **Breaking:** several `ProcessInstanceModel` status helpers,
  `FutureTaskModel.insert_or_update`, the `added_by` argument for claims, and
  event types and `WorkItemUserAddedBy` labels the library never records.

### Fixed

- A timer start event inside an event subprocess fires within the running
  instance instead of starting extra instances.
- Race conditions when two requests create the same permission rows at once.

### Security

- Process and task payloads are isolated per tenant. Before, two tenants with
  equal payload content shared one `json_data` row.
- BPMN source XML is parsed with `defusedxml`.

[Unreleased]: https://github.com/AOT-Technologies/m8flow-core/compare/1.0.0...HEAD
[1.0.0]: https://github.com/AOT-Technologies/m8flow-core/releases/tag/1.0.0
