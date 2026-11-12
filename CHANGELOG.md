<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Changelog

All notable changes to m8flow-core are recorded in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Each release
lists its changes under **Added**, **Changed**, **Deprecated**, **Removed**,
**Fixed**, and **Security**. Versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html); the
[versioning policy](docs/reference/versioning.md) defines the public contract
and what each version part means.

## [Unreleased]

### Added

- [REUSE](https://reuse.software/) 3.3 compliance. Every file declares its
  copyright and license through an SPDX header, `REUSE.toml`, or a `.license`
  file, and `LICENSES/` holds the license texts. `reuse lint` runs in CI, in
  pre-commit, and as `make license-headers`; `make license-headers-fix` runs
  the new `tools/add_license_headers.py` to add missing headers. Migrations
  released in 1.0.0 are left unchanged.
- The source distribution includes `LICENSES/`, `NOTICE.license`, and
  `REUSE.toml`, and the wheel ships `LICENSES/Apache-2.0.txt` with `LICENSE`
  and `NOTICE`.
- A [versioning policy](docs/reference/versioning.md) that defines the
  public contract, when each part of the version changes, how deprecations
  work, and which Python versions are supported.
- `make format` and `make format-check` run the Ruff formatter. CI and
  pre-commit now fail on unformatted Python, including Python code blocks in
  Markdown.
- After a merge to `main`, CI runs the test suite on Python 3.12, 3.13, and
  3.14, on Linux, macOS, and Windows. Pull requests run Linux with Python
  3.12. The package metadata lists the 3.13 and 3.14 classifiers.
- CI and the publish workflow fail when line coverage of `m8flow_core` drops
  below 80%. Pull requests also fail when less than 80% of their changed
  lines are covered, checked with the new `diff-cover` dev dependency.
- A release smoke test, `tools/smoke_test.py`. CI and the publish workflow
  install the built wheel into a clean environment and run the
  parallel-review workflow to completion.
- `tools/benchmark.py` and a [performance baseline](docs/reference/performance-baseline.md)
  for BPMN import and workflow execution.
- The PostgreSQL CI job runs every integration test in `integration/`, not
  only the compensation test.

### Changed

- The copyright holder is now `AOT Technologies`, matching node-wire, in the
  SPDX headers, `LICENSE`, `NOTICE`, and the package author metadata.
- Durable tenant-scoped BPMN signal publication, subscription indexing, and
  idempotent delivery receipts. Intermediate/end throws broadcast to waiting
  intermediate catches, boundary events, event subprocesses, and Signal Start
  definitions. Local handlers react immediately; hosts run queued deliveries
  with `RunSignalDeliveriesCommand` or `run_signal_deliveries`.
- `PublishSignalCommand`, `RunSignalDeliveriesCommand`,
  `GetSignalPublicationQuery`, `ListSignalDeliveriesQuery`, matching service
  functions, and `SIGNAL_PUBLISH_COMMAND`. Managers and admins can publish with
  the new tenant-scoped permission. An additive Alembic revision
  `l3m4n5o6p7q8` introduces the four signal registry tables.
- An interactive SQLite signal broadcast example and embedding guide.
- Signal-bearing workflows keep instance creation, service-failure recovery,
  publication, and consumption in the caller's transaction. Rollback restores
  them together; commit an error state to retain it.
- Overlapping signal runner calls serialize per tenant. The registry enforces
  same-tenant publication sources and required subscription references.
- BPMN compensation boundary events, associations, task/subprocess handlers,
  intermediate throws and end events, targeted compensation, both wait modes,
  and durable reverse completion ordering with nested scope isolation.
- Tenant-scoped compensation journals and progress query, compensation lifecycle
  events, connector idempotency context, and retry of failed undo handlers.
- Alembic revision `m8f_671_compensation` follows the signal registry revision
  and enforces same-tenant process references in all three journal tables.
- An interactive compensation walkthrough shows committed progress across an
  engine restart and supports `--non-interactive` for scripted runs.
  Compensation checkpoints use caller-owned transactions; callers must commit
  captured failures to retain them for retry. Legacy compensation snapshots
  without completion journals cannot continue compensation.
- A runtime test for sequential and parallel multi-instance script tasks. They
  already ran through SpiffWorkflow; the BPMN support reference, limitations
  page, and README no longer list them, or call activities, as unsupported.

### Fixed

- Added the compensation journal tables to the sample app's migration chain,
  so tenant realignment can run against databases migrated by the app.
- Removed import cycles in compensation persistence and process termination.
- Signal starts can be retried after a caller rollback on SQLite without
  retaining a stale workflow row in the session.
- Removed unused runtime compatibility imports and made the signal service
  facade's exports explicit. Workflow serializer configuration now uses the
  shared version constant, addressing the remaining CodeQL unused-symbol
  alerts without changing the public API or serialized format.
- Removed cyclic imports introduced by signal publication. Engine construction,
  workflow-state persistence, and signal indexing now form lower-level internal
  modules; Spiff signal throws use an injected publisher callback. Public API
  exports and persisted workflow formats are unchanged.
- Added sample-app revision `20261104_0003` for the signal registry and its
  composite parent indexes. The app now upgrades existing databases and adopts
  existing signal tables without changing the host's Alembic version state.
  This fixes sample-app startup and CI failures on missing signal tables.
- Unmatched signal throws are covered by regressions: publication succeeds
  without a catcher and downstream execution continues (BPMN-03). Signal end
  events now publish instead of silently behaving as none end events.
- Non-interrupting signal boundaries re-arm for repeated occurrences;
  interrupting signal event subprocesses cancel competing scope execution.
  Tasks in signal-triggered subprocesses are persisted and materialized.

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
