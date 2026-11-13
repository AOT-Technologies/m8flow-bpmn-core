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

- BPMN Transaction cancellation stops active work and timers, runs recorded
  compensation handlers, and follows the attached cancel boundary event once.
  `validate_compensation_bpmn` lets hosts check supported forms before saving.
  See [Cancel a Transaction](docs/how-to/run-compensation.md#cancel-a-transaction).
- [Publish a release](docs/how-to/publish-a-release.md#withdraw-or-replace-a-release)
  now covers yanking a broken version from PyPI, marking it on GitHub and in
  the changelog, and replacing it with a patch release.
- CI uploads test coverage from `main` to Codecov, and the README shows a
  coverage badge. The PyPI badge now reads the latest version from PyPI.

### Changed

- CI runs the Python 3.13 and 3.14, macOS, and Windows test matrix on pull
  requests as well as on `main`.
- The Codecov upload runs in its own `main`-only job, so the job that runs
  pull request code no longer holds the `id-token: write` permission.

### Security

- Connector proxy calls and the Keycloak provisioning helper no longer follow
  an HTTP redirect to a non-http(s) URL such as `ftp://`. They also no longer
  carry handlers for `ftp:`, `file:`, or `data:` URLs.

## [1.0.0] - 2026-11-12

The first stable release, and the first under the name m8flow-core. Versions
0.1.x to 0.2.1 were untagged development builds named `m8flow-bpmn-core`, and
`1.0.0b1` to `1.0.0b3` were pre-releases on PyPI. This release changes the
package name, the public API, and the database schema, so upgrade the library
and m8flow together. To move from a 0.x build or a beta, follow
[Upgrade to 1.0](docs/how-to/upgrade-to-1.0.md), which lists every renamed and
removed name.

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
- BPMN call activities through the tenant-aware `CallActivityResolver` API,
  and sequential and parallel multi-instance tasks.
- Durable, tenant-scoped BPMN signals. Intermediate and end throw events
  broadcast to waiting intermediate catch events, boundary events, event
  subprocesses, and signal start events, with a subscription index and
  idempotent delivery receipts. New names: `PublishSignalCommand`,
  `RunSignalDeliveriesCommand`, `GetSignalPublicationQuery`,
  `ListSignalDeliveriesQuery`, `run_signal_deliveries`, the matching service
  functions, and the `SIGNAL_PUBLISH_COMMAND` permission, granted to managers
  and admins. Alembic revision `l3m4n5o6p7q8` adds the four signal registry
  tables. See [Publish signals](docs/how-to/publish-signals.md).
- BPMN compensation: compensation boundary events, task and subprocess
  handlers, intermediate throw and end events, targeted compensation, both
  wait modes, and durable reverse completion ordering with nested scope
  isolation. New names: `GetProcessInstanceCompensationsQuery`,
  `get_process_instance_compensations`, `CompensationRecordModel`,
  `CompensationStatus`, and `CompensationExecutionError`. Connectors get a
  stable idempotency key, and failed undo handlers can be retried. Alembic
  revision `m8f_671_compensation` adds the three compensation journal tables.
  See [Run compensation handlers](docs/how-to/run-compensation.md).
- The `work_item` table and `WorkItemModel` for human-task claim state, with
  claims that are safe under concurrency.
- `properties_json["initiator_lanes"]`, to route a lane's tasks to the user
  who started the instance, and `assign_pending_tasks_for_user`, to add
  potential owners after a directory sync.
- Support for Python 3.12, 3.13, and 3.14.
- A sample host application in `sample_app/` and runnable examples, including
  interactive signal broadcast and compensation walkthroughs.
- Apache License 2.0 with [REUSE](https://reuse.software/) 3.3 compliance:
  every file declares its copyright and license, and the wheel and sdist ship
  the license texts.
- A [versioning policy](docs/reference/versioning.md) and a
  [performance baseline](docs/reference/performance-baseline.md).
- PyPI releases with PyPI and GitHub build provenance attestations. See
  [Publish a release](docs/how-to/publish-a-release.md#signing-and-provenance).

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
- The connector proxy and Keycloak HTTP helpers reject URLs that aren't
  absolute `http` or `https` URLs, so a misconfigured base URL can't read
  local files through `file://`.

[Unreleased]: https://github.com/AOT-Technologies/m8flow-core/compare/1.0.0...HEAD
[1.0.0]: https://github.com/AOT-Technologies/m8flow-core/releases/tag/1.0.0
