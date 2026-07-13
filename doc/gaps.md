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

- delayed backoff policies for repeated service-task failures:
  Today a failed synchronous connector call can be retried, but there is no
  built-in policy layer for "retry in 30 seconds, then 2 minutes, then 10
  minutes" style backoff behavior.
- dead-letter or manual intervention handling:
  There is no first-class operator flow for "this connector has failed too many
  times; stop retrying automatically and send it to a manual recovery queue"
  scenarios.
- async callback/correlation handling for long-running connector executions:
  V1 assumes the connector finishes within the current runtime call. A future
  async model would let a connector start external work, return a correlation
  id, and then resume the waiting workflow when a callback arrives later.

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
- a host-owned inline poller execution model
- an example-level Celery beat/worker poller that drives the same public
  `api.run_due_scheduler_jobs(...)` entrypoint used by the inline model

Still missing:

- reminder and escalation job types:
  BPMN timeout paths such as interrupting boundary-timer escalations are now
  supported, but there is still no separate generic reminder/escalation job
  framework.
- production-grade multi-worker claim/lock orchestration
- a first-class library-owned Celery dispatcher/claim adapter beyond the
  current example-level integration

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

- message start events:
  These would allow a process definition to start from an inbound BPMN message
  such as `invoice.received`, instead of requiring an explicit
  `process.start` API call.
- intermediate catch/throw message events:
  These are the BPMN message events used inside a running process, for example
  sending `payment.requested` and later waiting for `payment.confirmed` before
  advancing the workflow.
- signal events:
  Signals are broadcast-style BPMN events rather than point-to-point messages.
  A single signal could wake multiple listening process instances at once.
- correlation keys and message subscriptions:
  The library still needs a durable way to remember which running instance is
  waiting for which message and how to match an inbound payload such as
  `order_id=123` to the correct waiter.
- inbound message routing to running process instances:
  Even with BPMN message support, a host application still needs a library
  entrypoint for translating a webhook or event-bus message into "resume this
  waiting process instance at this message catch event".
- outbound notifications or event fan-out:
  When BPMN throws a message or signal, there is not yet a first-class delivery
  layer for publishing that event to external systems such as email, Kafka, or
  other subscribers.

## Advanced BPMN Coverage

Some BPMN features are not yet exercised in the library tests or examples.

Missing or incomplete areas may include:

- call activities and reusable subprocess orchestration:
  A BPMN call activity lets one process invoke another reusable process as a
  child. For example, a purchase workflow might call a shared vendor-onboarding
  subprocess and wait for it to finish.
- event subprocesses:
  These are subprocesses triggered by events inside a running parent process,
  often used for cancellation, exception, or side-channel behavior without
  modeling everything on the main path.
- compensation behavior:
  Compensation is BPMN's rollback-like mechanism for undoing previously
  completed work. For example, if a workflow books travel and later fails, the
  compensation path may cancel the hotel and flight reservations.
- boundary error and escalation handling:
  Timer boundary events are now covered, but typed BPMN business errors and
  escalations attached to tasks are still separate behavior. A service task
  raising `INSUFFICIENT_FUNDS`, for example, is not the same as a timer firing.
- multi-instance activities:
  BPMN can run one task or subprocess multiple times over a collection, either
  sequentially or in parallel. Typical examples are parallel reviewer tasks or
  looping over invoice line items.
- non-interrupting intermediate event behavior:
  Non-interrupting events create side work without cancelling the current path.
  For example, a reminder timer may create a follow-up task while leaving the
  original approval task open.
- BPMN message correlation against running instances:
  This is the BPMN-runtime side of the messaging gap above: once multiple
  process instances are waiting on the same message type, the library still
  needs a safe way to correlate one inbound message to the correct instance.

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
