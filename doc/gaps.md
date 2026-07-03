# Current Gaps

This repository is an initial BPMN core library, not a full M8Flow or
SpiffArena clone. It covers process definition import, process instantiation,
human task worklists, task claim/complete, metadata, and event persistence.

The items below are intentionally not covered yet.

## Authorization Gaps Beyond V1 RBAC

M8Flow and SpiffArena model permissions through URIs that a user can access.
This library now provides a minimal V1 permission layer on top of the BPMN API
for the highest-risk write-side workflow commands.

Supported today:

- stable command keys for `process_definition.import`, `process.start`,
  `task.claim`, `task.complete`, `process.suspend`, `process.resume`,
  `process.retry`, and `process.terminate`
- persistence of those command keys through `permission_target.command`
- tenant-scoped role-to-command grants
- runtime enforcement of tenant membership plus command permission checks for
  those covered workflow actions

Planned direction beyond V1:

- keep the existing URI-based RBAC model
- extend command and function assignments beyond the current V1 workflow actions
- make more API calls check both URI access and workflow-command permissions
- support tenant-aware authorization rules for worklist reads, task actions,
  workflow administration, and read-side access

Examples of missing permission features:

- no command-level authorization coverage for the full API surface
  Remaining uncovered examples include `process.create`,
  `process.initialize_workflow`, `process.metadata.upsert`,
  `process.event.record`, `process.error`, and the read/query surface.
- no workflow admin / tenant admin separation
  V1 `admin` is still a coarse workflow operator role. A future split would
  separate process-definition/process-instance administration from tenant
  identity and policy administration.
- no built-in policy engine for lane-specific or process-specific access rules
  That means there is still no first-class way to express rules such as "only
  Finance lane members may complete this task" or "only admins for process
  model X may terminate its instances" beyond coarse role grants or custom
  policy hooks.

## Service Task Integrations

The library now includes the service-task execution seam and synchronous
runtime integration for BPMN `ServiceTask` nodes.

Available today:

- stable operation ids in the `<connector_key>/<command_name>` format
- a public connector protocol and in-process registry
- synchronous BPMN `ServiceTask` execution for real process-instance runtime paths
- process-wide and scoped hooks for installing connector registries
- documentation of the current connector-proxy catalog and execution contract
- the HTTP adapter for `m8flow-connector-proxy`
- retry integration for synchronous service-task failures
- an end-to-end connector-proxy POC that uses the shared m8flow database/UI path

Missing pieces include:

- delayed backoff policies for repeated service-task failures
- dead-letter or manual intervention handling
- async callback/correlation handling for long-running connector executions

## Timers And Scheduling

The scheduling foundation is now partially implemented.

Available today:

- persisted scheduler jobs through the internal `scheduler_job` table
- detection of waiting intermediate catch and boundary timer events during
  workflow persistence
- storage of the next due time for waiting intermediate and boundary timers
- execution of due intermediate and boundary timer jobs through
  `api.run_due_scheduler_jobs`
- scheduling and execution of basic timer start events through persisted scheduler jobs
- programmatic scheduling and execution of delayed retries for errored process instances
- an architecture that supports either an inline poller or a Celery-backed
  dispatcher/worker integration

Still missing:

- reminder and escalation jobs
- production-grade multi-worker claim/lock orchestration
- Celery dispatcher and worker adapter implementation

## User Management

The library currently validates users against tenant membership, but it does not
yet provide full user management APIs.

Missing pieces include:

- user create, update, delete, and deactivate commands
- group and role management
- user profile synchronization from an identity provider
- account lifecycle hooks
- admin workflows for provisioning and offboarding

## Tenant Management

Tenant membership exists in the schema, but there are no public tenant
management commands yet.

Missing pieces include:

- tenant create and update commands
- tenant suspension and deletion
- tenant-level configuration management
- realm/bootstrap provisioning workflows
- tenant admin APIs for identity mapping and policy assignment

## Messaging And Correlation

The library does not yet cover BPMN messaging features.

Missing pieces include:

- message start events
- intermediate catch/throw message events
- signal events
- correlation keys and message subscriptions
- inbound message routing to running process instances
- outbound notifications or event fan-out

## Advanced BPMN Coverage

Some BPMN features are not yet exercised in the library tests or examples.

Missing or incomplete areas may include:

- call activities and reusable subprocess orchestration
- event subprocesses
- compensation behavior
- boundary error and escalation handling
- multi-instance activities
- non-interrupting intermediate event behavior
- BPMN message correlation against running instances

## Operations And Observability

The library does not yet expose a full operational control plane.

Missing pieces include:

- workflow history search and filtering APIs
- audit trails for admin actions
- metrics and tracing hooks
- job and process dashboards
- operational replay or recovery commands

## What Is In Scope Today

Current support focuses on:

- importing BPMN and DMN definitions
- starting process instances from stored definitions
- materializing human tasks
- querying pending tasks
- claiming and completing tasks
- persisting task payloads as process metadata
- reading process metadata and event history
- caller-owned transaction support through `Session` or `Connection`
