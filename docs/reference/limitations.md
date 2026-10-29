# Limitations

m8flow-core is a workflow core, not a full m8flow or SpiffArena
replacement. This page lists what it doesn't do yet, so you can plan around
it. Each section names what's covered and what's missing.

## What's covered

The library covers the following:

- Importing BPMN and DMN definitions
- Starting process instances from stored definitions
- Building human task worklists, and claiming and completing tasks
- Storing task payloads as process metadata, and reading metadata and event
  history
- Suspending, resuming, erroring, retrying, and terminating process instances
- Timers and scheduled retries, through a scheduler loop you run
- Synchronous service tasks through pluggable connectors
- Tenant membership checks and command permissions for the main workflow
  commands
- Caller-owned transactions through a `Session` or `Connection`

## Authorization

The library checks tenant membership and command permissions for
`process_definition.import`, `process.start`, `task.claim`, `task.complete`,
`process.suspend`, `process.resume`, `process.retry`, and
`process.terminate`. Grants target exact resources, such as one process model,
or a whole resource type or tenant.

Missing:

- **Permission checks on the rest of the API.** Creating a process instance,
  initializing a workflow, upserting metadata, recording events, erroring an
  instance, and every query have no command permission check. Command keys for
  the first five (`process.create`, `process.initialize_workflow`,
  `process.metadata.upsert`, `process.event.record`, and `process.error`)
  exist in the authorization module, but no command enforces them yet. Calls
  that take a `user_id` still check tenant membership.
- **Separate workflow and tenant administration.** The `admin` role is one
  coarse workflow operator role. It doesn't separate process administration
  from tenant identity and policy administration.
- **Rules that span resources.** A grant names one resource, so there's no
  built-in way to say "only Finance lane members may complete this task" or
  "only admins of process model X may terminate its instances". Terminate
  checks the instance, not its model. You can write these as a
  [custom policy](../how-to/customize-authorization.md#add-a-custom-policy).

## Service tasks

Service tasks run synchronously through registered connectors, including the
`m8flow-connector-proxy` adapter. A failure on start, timer wake-up, or retry
is saved durably and can be retried.

Missing:

- **Durable failures after task completion.** A service task that fails after
  `CompleteTaskCommand` shares your transaction, so a rollback undoes the
  failure state too.
- **Backoff policies.** A failed call can be retried, but there's no built-in
  policy such as "retry in 30 seconds, then 2 minutes, then 10 minutes".
- **Dead-letter handling.** There's no operator flow that stops automatic
  retries after too many failures and sends the instance to a manual recovery
  queue.
- **Asynchronous connectors.** A connector must finish within the call. There's
  no way for it to start external work, return a correlation ID, and resume
  the workflow when a callback arrives.

## Timers and scheduling

Timer start events, recurring `timeCycle` timer starts, intermediate timers,
interrupting boundary timers, and scheduled retries are stored as scheduler
jobs. Your application runs them with `run_due_scheduler_jobs`, inline or
from Celery.

Missing:

- **Multi-worker coordination.** Run one poller per database or per tenant.
  Several workers claiming the same jobs isn't safe yet.
- **A library-owned Celery dispatcher.** The repository has a Celery example,
  not a supported Celery integration.
- **Reminder and escalation jobs.** Boundary-timer timeout paths work, but
  there's no general reminder or escalation job type.

## User management

The library checks users against tenant membership but doesn't manage them.

Missing:

- Commands to create, update, delete, and deactivate users
- Group and role management, beyond `ensure_v1_role`
- Profile synchronization from an identity provider
- Account lifecycle hooks and provisioning or offboarding workflows

## Tenant management

Tenants exist in the schema, but there are no commands to manage them.

Missing:

- Commands to create, update, suspend, and delete tenants
- Tenant-level configuration
- Realm and bootstrap provisioning
- Tenant administration for identity mapping and policy assignment

## Messaging and correlation

The library doesn't support BPMN messaging.

Missing:

- **Message start events**, which start a process from an inbound message such
  as `invoice.received` instead of an explicit start command.
- **Intermediate message events**, such as sending `payment.requested` and
  waiting for `payment.confirmed` before continuing.
- **Signal events**, which broadcast to every listening instance at once.
- **Correlation keys and subscriptions**, which match an inbound payload such
  as `order_id=123` to the instance waiting for it.
- **Inbound message routing**, an entry point that turns a webhook or event-bus
  message into "resume this instance at this catch event".
- **Outbound delivery** of thrown messages and signals to email, Kafka, or
  other subscribers.

## Advanced BPMN coverage

The following BPMN features aren't tested in the library:

- **Call activities**, where one process runs another reusable process as a
  child and waits for it.
- **Event subprocesses**, triggered by events inside a running parent process,
  often for cancellation or exception handling.
- **Compensation**, BPMN's way of undoing completed work, such as cancelling
  hotel and flight bookings when a later step fails.
- **Error and escalation boundary events**, for typed business errors such as
  a service task raising `INSUFFICIENT_FUNDS`. Timer boundary events are
  covered; these aren't.
- **Multi-instance activities**, which run a task once per item in a
  collection, in sequence or in parallel.
- **Non-interrupting events**, which start side work, such as a reminder task,
  without cancelling the current path.

## Operations and observability

Missing:

- Search and filter APIs for workflow history
- Audit trails for administrative actions
- Metrics and tracing hooks
- Job and process dashboards
- Replay and recovery commands

## Sample app gaps

The [sample app](../explanation/sample-app-integration.md#integration-findings)
found two read operations that the public API doesn't offer, so it reads the
ORM models directly:

- Listing a tenant's stored process definitions
- Loading one work item by ID

## Related pages

- [BPMN support](bpmn-support.md)
- [Public API reference](api.md)
