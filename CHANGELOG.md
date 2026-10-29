# Changelog

All notable changes to m8flow-core are recorded in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Each release
lists its changes under **Added**, **Changed**, **Deprecated**, **Removed**,
**Fixed**, and **Security**.

## [Unreleased]

## [1.0.0] - 2026-10-22

The first stable release, and the first under the name m8flow-core. Versions
0.1.x to 0.2.1 were untagged development builds named `m8flow-bpmn-core`;
their changes are included here. To move from any of them, follow
[Upgrade to 1.0](docs/how-to/upgrade-to-1.0.md). This release changes the
package name, the public API, and the database schema, so upgrade the library
and m8flow together.

### Added

- Public in-process API in `m8flow_core.api`, with separate commands and
  queries, `execute_command` and `execute_query` dispatchers that accept a
  caller-owned `Session` or `Connection`, and the `BpmnCoreError` error
  hierarchy.
- BPMN and DMN definition import, process instance start, human task
  worklists, claim and completion, process metadata, and event history.
- Lifecycle commands to suspend, resume, error, retry, and terminate process
  instances.
- Tenant-aware command authorization with built-in `user`, `manager`, and
  `admin` roles and pluggable policies.
- Persisted scheduler jobs for timer start events, recurring `timeCycle`
  timers, intermediate and interrupting boundary timers, and scheduled
  retries, run through `run_due_scheduler_jobs`.
- Synchronous BPMN service tasks through pluggable connectors, including an
  adapter for `m8flow-connector-proxy`. A failure on start, timer wake-up, or
  retry is saved for retry even if the caller rolls back.
- The `work_item` table and `WorkItemModel` for human-task claim state, with
  compare-and-set claims that are safe under concurrency.
- `ProcessLifecycleEventType`, `TaskEventType`, and
  `ProcessInstanceEventCategory`, with a non-null `category` on every event.
- `assign_pending_tasks_for_user`, to add potential owners to waiting work
  items after a directory sync. `lane_owners` is now optional.
- `properties_json["initiator_lanes"]`, a list of lanes whose tasks go to the
  user who started the instance. Import validates it and raises
  `ValidationError` for a non-list value, an empty or non-string entry, a lane
  that isn't in the BPMN, or a lane that also has a `lane_owners` key.
- Support for dates beyond 2038.
- A sample host application in `sample_app/` that consumes the library as a
  wheel.
- Runnable examples for conditional approval, parallel review, timers,
  retries, service tasks, and error handling. Most interactive examples also
  run on SQLite with `--sqlite`.
- Apache License 2.0 `LICENSE` and `NOTICE` files, and license metadata in the
  package.
- Contributing guide, code of conduct, security policy,
  support page, issue and pull request templates, and CI for lint, type, and
  security checks, CodeQL, secret scanning, and dependency review.
- A manual workflow that publishes betas and releases to PyPI through trusted
  publishing, with build attestations and a vulnerability scan. A release
  publishes only from the commit its GitHub Release tag points at.
- Documentation organized as tutorials, how-to guides, reference, and
  explanation, with a quick start, an upgrade guide, a database schema
  reference, and diagrams.

### Changed

- **Breaking:** renamed the distribution from `m8flow-bpmn-core` to
  `m8flow-core`, and the import package from `m8flow_bpmn_core` to
  `m8flow_core`.
- **Breaking:** the Alembic history is a single baseline revision,
  `k2l3m4n5o6p7`, that creates the 1.0 schema on an empty database. It
  doesn't convert 0.x databases: bring them to the 0.x head with the 0.x
  migrations, convert them to the 1.0 schema with your own migrations, then
  run `alembic stamp k2l3m4n5o6p7`. See
  [Upgrade to 1.0](docs/how-to/upgrade-to-1.0.md).
- **Breaking:** every timestamp is a timezone-aware UTC `datetime`. Public
  parameters such as `started_at_in_seconds` are now `started_at`, persisted
  epoch columns are now `DateTime` columns such as `created_at`, `run_at`, and
  `occurred_at`, and `run_due_scheduler_jobs` takes `now` instead of
  `now_in_seconds`.
- **Breaking:** `ClaimTaskCommand` and `CompleteTaskCommand` take
  `work_item_id` and return `WorkItemModel`. `GetPendingTasksQuery` returns
  work items.
- **Breaking:** permission targets are exact `(resource_type, resource_id)`
  pairs, with type-wide grants when `resource_id` is `NULL`.
  `AuthorizationRequest` carries `resource_type` and `resource_id`.
- **Breaking:** `json_data` is keyed by `(m8f_tenant_id, hash)`, so payloads
  are never shared across tenants. Every read and write includes the tenant.
- **Breaking:** user identity fields are `realm_identifier`,
  `external_org_id`, and `external_user_id`; the group table is `m8f_group`;
  `spiff_serializer_version` is `workflow_engine_version`; and process digests
  are one `process_xml_digest`.
- **Breaking:** the Celery example reads `M8FLOW_CORE_CELERY_*` variables
  instead of `M8FLOW_BPMN_CORE_CELERY_*`, and the examples publish to the
  `m8flow-core-examples` process group.
- **Breaking:** the example scripts dropped their `_poc` suffix, for example
  `examples/conditional_approval.py` instead of
  `examples/conditional_approval_poc.py`.
- **Breaking:** initiator lanes are declared with
  `properties_json["initiator_lanes"]`. A lane is no longer routed to the
  process initiator because its name looks like "process initiator"; tasks
  with no lane, or in an unnamed lane, still go to the initiator.
- **Breaking:** `WorkItemUserAddedBy` has three members, `process_initiator`,
  `lane_owner`, and `lane_assignment`. Writing any other `added_by` value
  raises `ValidationError`; `None` is allowed. Rows that already hold other
  labels load and work unchanged.
- Lane owners are matched by username, email, or `service_id`, with lane
  names compared after trimming outer whitespace, ignoring case. Owners that
  match no user in the tenant are skipped with a logged warning instead of
  failing the process. Named owners are added to the tenant's lane group, a
  user appears at most once per work item, and resuming or retrying an
  instance adds users who now qualify without removing existing potential
  owners.
- `resolve_lane_assignment_id` takes an optional `tenant_id`, so equal lane
  names in different tenants never share a group.
- Constraints and indexes use the `m8f_*` namespace.
- The model, authorization, event, and lane-assignment code was redesigned
  independently of spiff-arena.
  [About the clean-room review](docs/explanation/clean-room-review.md)
  records the review.
- `NOTICE` states SpiffWorkflow's license as LGPL-3.0-or-later, notes the
  native libraries bundled by psycopg-binary, and describes the obligations of
  applications that redistribute m8flow-core with its dependencies.
- The source distribution includes only the library, its tests, docs,
  examples, and migrations.

### Removed

- **Breaking:** `ProcessInstanceEventType`, replaced by the split event enums.
- **Breaking:** the `human_task` and `human_task_user` tables and
  `HumanTaskModel`, replaced by `work_item` and `work_item_user`.
- **Breaking:** URI permission targets and URI matching.
- **Breaking:** legacy `*_in_seconds` columns. The 1.0 schema has no epoch
  columns.
- **Breaking:** the `ProcessInstanceModel` status helpers `can_submit_task`,
  `allowed_to_run`, `can_receive_message`, `is_immediately_runnable`,
  `terminal_statuses`, `non_terminal_statuses` and
  `immediately_runnable_statuses`. `has_terminal_status()` and
  `active_statuses()` remain.
- **Breaking:** `ClaimTaskCommand.added_by` and the `added_by` parameter of
  `claim_task`. Claiming never adds or relabels potential owners.
- **Breaking:** the `guest` and `manual` members of `WorkItemUserAddedBy`.
- **Breaking:** `FutureTaskModel.insert_or_update`. Create or change
  `future_task` rows with ordinary ORM operations.
- **Breaking:** the event type values `process_instance_force_run`,
  `process_instance_migrated`, `process_instance_rewound_to_task`,
  `process_instance_suspended_for_error`, `task_data_edited`,
  `task_executed_manually` and `task_skipped`, and their
  `ProcessLifecycleEventType` and `TaskEventType` members. Recording one of
  them raises `ValidationError`. Existing event rows that hold them are
  unaffected and read back as stored.

### Fixed

- The interactive examples crashed when printing a task, because they read
  its lazy-loaded name after the session had closed.
- Authorization race conditions when two requests create the same permission
  rows at once.
- The sample app's lockfile, which was missing the `defusedxml` dependency.

### Security

- Process and task payloads are isolated per tenant. Before, two tenants with
  equal payload content shared one `json_data` row.
- The library reads BPMN source XML with `defusedxml`.

[Unreleased]: https://github.com/AOT-Technologies/m8flow-core/compare/1.0.0...HEAD
[1.0.0]: https://github.com/AOT-Technologies/m8flow-core/releases/tag/1.0.0
