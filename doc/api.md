# Public API Reference

This document is the contract for the public surface of
`m8flow_bpmn_core`. Every name documented here is re-exported from
`m8flow_bpmn_core.api` and is part of the supported in-process API.

The library is not an HTTP service. Callers import it directly, pass a
SQLAlchemy `Session` or `Connection`, and own the transaction boundary.

Reads go through `execute_query(...)` or the corresponding read-side
service function. Writes go through `execute_command(...)` or the
corresponding write-side service function. Commands and queries are not
interchangeable: the dispatcher raises `TypeError` when given the wrong
kind of payload.

---

## Dispatchers

```python
from m8flow_bpmn_core import api

api.execute_command(session_or_connection, command)
api.execute_query(session_or_connection, query)
```

- Inputs: either `sqlalchemy.orm.Session` or `sqlalchemy.engine.Connection`.
- Connection behavior: when given a `Connection`, the library opens a
  temporary `Session` bound to it and does not commit or roll back.
- Caller responsibility: the caller owns the transaction boundary.
- Return value: the same ORM model or list that the underlying service
  function returns.
- Dispatcher errors: `execute_command(...)` raises `TypeError` for a
  query or unknown payload. `execute_query(...)` does the same for a
  command or unknown payload.

You can also call the service functions directly, for example
`api.claim_task(...)` or `api.list_process_instances(...)`. The service
functions that correspond to a command or query are documented below
under the dataclass entry rather than repeated twice.

---

## Public Enums

- `ProcessInstanceStatus`
  Current values: `complete`, `error`, `not_started`, `running`,
  `suspended`, `terminated`, `user_input_required`, `waiting`.
- `ProcessInstanceEventType`
  Stable event-type enum used by process-instance event history, for
  example `process_instance_created`, `process_instance_completed`,
  `process_instance_error`, `process_instance_retried`,
  `process_instance_suspended`, `process_instance_terminated`,
  `task_completed`, `task_failed`, and `task_cancelled`.

---

## Service Task Hooks

```python
from m8flow_bpmn_core import api

registry = api.ServiceTaskRegistry()
registry.register_connector(my_connector)
```

The service-task execution layer is a stable extension seam and is wired
into real workflow execution paths.

- `ServiceTaskConnector`
  Protocol with `connector_key`, `list_commands()`, and
  `execute(request)`.
- `ServiceTaskCommandDefinition`
  Stable command metadata for one connector operation.
- `ServiceTaskParameterDefinition`
  Stable parameter metadata for one connector command input.
- `ServiceTaskContext`
  Tenant, process, and task execution context passed to a connector.
- `ServiceTaskRequest`
  One service-task invocation, identified by an operation id such as
  `http/GetRequestV2`.
- `ServiceTaskResult`
  Connector result payload returned to the workflow runtime.
- `ServiceTaskRegistry`
  In-process registry that resolves operation ids to registered
  connectors and can execute a request through the matching connector.
- `ServiceTaskRegistryFactory`
  Callable `() -> ServiceTaskRegistry` used by the default-registry hook
  surface.
- `ConnectorProxyServiceTaskConnector`
  Concrete connector adapter for `m8flow-connector-proxy`.
- `build_service_task_operation_id(...)`
  Builds a stable `<connector_key>/<command_name>` operation id.
- `split_service_task_operation_id(...)`
  Splits and validates a stable operation id.
- `fetch_connector_proxy_command_definitions(...)`
  Fetches the live connector-proxy catalog and normalizes it into stable
  command definitions.
- `build_connector_proxy_service_task_connectors(...)`
  Groups the live proxy catalog into connector objects by connector key.
- `build_connector_proxy_service_task_registry(...)`
  Convenience helper that builds a ready-to-use registry from a
  connector-proxy base URL.
- `service_task_registry_scope(...)`
  Temporary registry override for tests or request-scoped execution.
- `set_default_service_task_registry_factory(...)`
  Process-wide default registry override hook.

This seam is intentionally aligned to the current
`m8flow-connector-proxy` catalog shape, where operators look like
`http/GetRequestV2`, `smtp/SendHTMLEmail`, or `postgres_v2/DoSQL`.

See [service_tasks.md](service_tasks.md) for the connector-proxy
contract and adapter direction.

Synchronous service-task failures surface as
`ServiceTaskExecutionError`.

For initial start, timer-start, waiting-workflow refresh, and retry reruns,
the service-task contract is stronger than a normal in-transaction failure:
the same process instance remains retryable afterwards even if the host
application lets the exception escape an outer transaction helper that rolls
back the main unit of work. To support that, the library uses a limited
autonomous persistence step for the failure snapshot, failure events, and
final `error` status. That safeguard is for workflow recovery state only; it
must not be treated as a commit of arbitrary caller-side changes.

Workflow advancement after a user task is completed still shares the caller
transaction boundary in V1.

---

## Scheduler Service Function

```python
from m8flow_bpmn_core import api

api.run_due_scheduler_jobs(
    session_or_connection,
    *,
    now_in_seconds=None,
    limit=100,
    worker_id="inline",
    tenant_id=None,
)
```

This is the public polling entrypoint for persisted scheduler jobs. It
is a service function rather than a command/query because the caller is
driving a worker loop, not issuing a business command on behalf of an
end user.

- Inputs: accepts either a `Session` or `Connection`, with the same
  caller-owned transaction semantics as `execute_command(...)` and
  `execute_query(...)`.
- `now_in_seconds`: optional due-time override for tests or externally
  controlled scheduling loops.
- `limit`: maximum number of due jobs to process in one call. Must be
  greater than zero.
- `worker_id`: non-blank identifier recorded in the in-row lock while a
  job is claimed for execution.
- `tenant_id`: optional tenant filter when a host application wants one
  scheduler loop per tenant.
- Return value: `int`, the number of due jobs processed in that call.
- Raises: `ValidationError` for invalid `limit` or blank `worker_id`;
  `BpmnCoreError` if one or more claimed jobs fail during execution.

The scheduler loop is batch-continuing, not fail-fast per claimed row:

- if one claimed job fails, the library releases that job's lock
- it still attempts the remaining claimed jobs from that same batch
- after the batch finishes:
  - if exactly one job failed, it re-raises that original scheduler
    error
  - if multiple jobs failed, it raises one summary `BpmnCoreError`
    listing the failed job keys and error details

Because the caller owns the transaction boundary, a host application
that passes a caller-owned `Session` or `Connection` must still decide
whether to commit the successful jobs from that batch or roll the whole
transaction back.

Current V1 coverage includes:

- waiting intermediate catch events
- interrupting timer boundary events
- timer start events
- recurring finite `timeCycle` timer-start rescheduling
- scheduled process retries

The host application is responsible for wake-up cadence. The library is
responsible for finding due jobs, restoring workflow state, advancing
the workflow, creating timer-started instances, retrying errored
instances, and rescheduling the next timer if the workflow remains
waiting.

The current implementation is intentionally simple and is best treated
as a single-poller execution path by default. The repository also
includes an example-level Celery beat/worker poller that calls this same
entrypoint. Multi-worker claim hardening and a first-class
library-owned Celery dispatcher remain follow-on work.

---

## Additional Runtime Helpers

Two public helpers are re-exported for callers that need lower-level
workflow runtime access:

- `advance_process_instance_workflow(...)`
  Restores an initialized workflow, completes the supplied runtime task
  guid, advances the process, and persists the refreshed state.
- `resolve_lane_assignment_id(lane_name)`
  Converts a lane name into a stable positive integer that matches
  m8flow's lane/group id expectations.

---

## Conventions

All command/query inputs are `@dataclass(frozen=True, slots=True)`.
Fields follow this order:

1. `tenant_id` - always first, always required.
2. Primary entity identifier, when applicable.
3. Required business inputs.
4. Optional inputs, usually defaulting to `None`.
5. Trailing `*_at_in_seconds` timestamps.

Return values are SQLAlchemy ORM models from `m8flow_bpmn_core.models.*`.
Only the columns and semantics documented in this file are part of the
stable contract. Internal relationships and implementation-only columns
may change without a major-version bump.

---

## Authorization Model

The library includes a minimal V1 RBAC layer for workflow commands.

- Command authorization is evaluated through stable command keys stored
  in `permission_target.command`.
- The built-in command keys are:
  `process_definition.import`, `process.start`, `task.claim`,
  `task.complete`, `process.suspend`, `process.resume`,
  `process.retry`, and `process.terminate`.
- User-scoped operations first validate tenant membership, then
  evaluate command permission, then apply runtime checks such as task
  assignment or claimed-task ownership.
- A `permission_target` row is matched by URI plus optional command. A
  row with `command = NULL` behaves like a URI-only target; a row with a
  command is specific to that command key.
- Custom policies can extend or replace the built-in
  database-backed policy through `authorization_policy_scope(...)` or
  `set_default_authorization_policy_factory(...)`.

---

## Commands (write side)

Dispatch with `api.execute_command(...)`.

### `ImportBpmnProcessDefinitionCommand`

Persist BPMN XML, and optional DMN XML, as a process definition.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `tenant_id` | `str` | yes | |
| `bpmn_identifier` | `str` | yes | Stable identifier for the process model. |
| `user_id` | `int` | yes | Actor who must belong to the tenant and hold `process_definition.import`. |
| `source_bpmn_xml` | `str \| bytes` | yes | |
| `source_dmn_xml` | `str \| bytes \| None` | no | |
| `bpmn_name` | `str \| None` | no | |
| `properties_json` | `dict[str, Any] \| None` | no | Arbitrary metadata such as `lane_owners`. |
| `bpmn_version_control_type` | `str \| None` | no | Example: `"git"`. |
| `bpmn_version_control_identifier` | `str \| None` | no | Example: branch or commit. |
| `single_process_hash` | `str \| None` | no | Auto-computed if omitted. |
| `full_process_model_hash` | `str \| None` | no | Auto-computed if omitted; used for idempotent upsert. |
| `created_at_in_seconds` | `int \| None` | no | |
| `updated_at_in_seconds` | `int \| None` | no | |

Returns: `BpmnProcessDefinitionModel`.

Raises:

- `ValidationError` if the BPMN or DMN source is malformed, or the BPMN
  source does not contain exactly one executable process
- `NotFoundError` if `user_id` does not exist
- `AuthorizationError` if the user is outside the tenant or lacks the
  tenant-scoped `process_definition.import` permission

Notes:

- validation runs before anything is persisted
- importing a timer-start definition may also create or refresh
  definition-scoped scheduler rows

### `InitializeProcessInstanceFromDefinitionCommand`

Create a process instance from a stored definition and start the
workflow.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `tenant_id` | `str` | yes | |
| `bpmn_process_definition_id` | `int` | yes | |
| `process_initiator_id` | `int` | yes | |
| `submission_metadata` | `dict[str, str] \| None` | no | Seed metadata persisted at start time. |
| `summary` | `str \| None` | no | |
| `process_version` | `int` | no | Default `1`. |
| `started_at_in_seconds` | `int \| None` | no | |
| `bpmn_process_id` | `str \| None` | no | Required only when the definition contains multiple executable BPMN processes. |

Returns: `ProcessInstanceModel`, already advanced to its first wait
state or completion point.

Raises:

- `NotFoundError` if the definition or initiator does not exist
- `AuthorizationError` if the initiator is outside the tenant or lacks
  the tenant-scoped `process.start` permission
- `ValidationError` if the stored definition is unusable or the BPMN
  process id selection is ambiguous
- `ServiceTaskExecutionError` if startup reaches a BPMN service task and
  the registered connector fails

### `InitializeProcessInstanceWorkflowCommand`

Start the workflow runtime for an already-created process instance.

| Field | Type | Required |
| --- | --- | --- |
| `tenant_id` | `str` | yes |
| `process_instance_id` | `int` | yes |
| `bpmn_xml` | `str \| bytes` | yes |
| `bpmn_process_id` | `str \| None` | no |
| `started_at_in_seconds` | `int \| None` | no |
| `dmn_xml` | `str \| bytes \| None` | no |

Returns: `ProcessInstanceModel`.

Raises:

- `NotFoundError` if the process instance does not exist
- `InvalidStateError` if the instance is terminal or already initialized
- `ValidationError` for BPMN/DMN parsing or runtime validation failures
- `ServiceTaskExecutionError` if initial execution reaches a BPMN
  service task and the connector fails

### `CreateProcessInstanceCommand`

Create a process instance row without starting the workflow.

| Field | Type | Required |
| --- | --- | --- |
| `tenant_id` | `str` | yes |
| `process_model_identifier` | `str` | yes |
| `process_model_display_name` | `str` | yes |
| `process_initiator_id` | `int` | yes |
| `bpmn_process_definition_id` | `int` | yes |
| `bpmn_process_id` | `int` | yes |
| `summary` | `str \| None` | no |
| `process_version` | `int` | no (default `1`) |
| `created_at_in_seconds` | `int \| None` | no |
| `updated_at_in_seconds` | `int \| None` | no |

Returns: `ProcessInstanceModel`.

Raises:

- `NotFoundError` if the initiator does not exist
- `AuthorizationError` if the initiator does not belong to the tenant

### `ClaimTaskCommand`

Claim a pending human task for a user.

| Field | Type | Required |
| --- | --- | --- |
| `tenant_id` | `str` | yes |
| `human_task_id` | `int` | yes |
| `user_id` | `int` | yes |
| `added_by` | `str` | no (default `"manual"`) |

Returns: `HumanTaskModel`.

Raises:

- `NotFoundError` if the user or task does not exist
- `AuthorizationError` if the user is outside the tenant, lacks the
  tenant-scoped `task.claim` permission, is not assigned to the task,
  or tries to claim a task already owned by another user
- `InvalidStateError` if the task is already completed

### `CompleteTaskCommand`

Complete a claimed task and advance the workflow.

| Field | Type | Required |
| --- | --- | --- |
| `tenant_id` | `str` | yes |
| `human_task_id` | `int` | yes |
| `user_id` | `int` | yes |
| `completed_at_in_seconds` | `int \| None` | no |
| `task_payload` | `dict[str, str] \| None` | no - persisted as process metadata |

Returns: `HumanTaskModel`.

Raises:

- `NotFoundError` if the user or task does not exist
- `AuthorizationError` if the user is outside the tenant, lacks the
  tenant-scoped `task.complete` permission, is not assigned to the
  task, or does not own the claimed task
- `InvalidStateError` if the task is already completed or has not been
  claimed yet
- `ServiceTaskExecutionError` if downstream workflow advancement reaches
  a BPMN service task and the connector fails

Notes:

- task payload keys and values are persisted as process metadata
- payload values are stored as strings

### `UpsertProcessInstanceMetadataCommand`

Create or update one metadata key/value for a process instance.

| Field | Type | Required |
| --- | --- | --- |
| `tenant_id` | `str` | yes |
| `process_instance_id` | `int` | yes |
| `key` | `str` | yes |
| `value` | `str` | yes |
| `updated_at_in_seconds` | `int` | yes |
| `created_at_in_seconds` | `int \| None` | no |

Returns: `ProcessInstanceMetadataModel`.

Raises:

- `NotFoundError` if the process instance does not exist

### `RecordProcessInstanceEventCommand`

Append an event to the process-instance event history.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `tenant_id` | `str` | yes | |
| `process_instance_id` | `int` | yes | |
| `event_type` | `ProcessInstanceEventType \| str` | yes | |
| `task_guid` | `str \| None` | no | |
| `user_id` | `int \| None` | no | When provided, tenant membership is enforced. |
| `timestamp` | `float \| None` | no | Defaults to current time with microsecond precision. |

Returns: `ProcessInstanceEventModel`.

Raises:

- `NotFoundError` if the process instance does not exist, or if `user_id`
  is supplied and the user does not exist
- `AuthorizationError` if `user_id` is supplied and does not belong to
  the tenant

### Lifecycle commands

`SuspendProcessInstanceCommand`, `ResumeProcessInstanceCommand`,
`RetryProcessInstanceCommand`, and `TerminateProcessInstanceCommand`
share the same input shape:

| Field | Type | Required |
| --- | --- | --- |
| `tenant_id` | `str` | yes |
| `process_instance_id` | `int` | yes |
| `user_id` | `int` | yes |
| `<verb>_at_in_seconds` | `int \| None` | no |

Each returns `ProcessInstanceModel`, requires tenant membership, and
enforces the matching tenant-scoped command permission before checking
the state machine.

| Command | Allowed transitions | Raises `InvalidStateError` for... |
| --- | --- | --- |
| `SuspendProcessInstanceCommand` | non-terminal -> `suspended` | terminal instances |
| `ResumeProcessInstanceCommand` | `suspended` -> `running` | non-suspended or terminal instances |
| `RetryProcessInstanceCommand` | `error` -> `running` | non-errored instances |
| `TerminateProcessInstanceCommand` | non-terminal -> `terminated` | `complete` and `error` instances |

Common raises:

- `NotFoundError` if the process instance or user does not exist
- `AuthorizationError` if the user is outside the tenant or lacks the
  matching command permission

Additional note for `RetryProcessInstanceCommand`:

- `ServiceTaskExecutionError` may be raised if retrying an errored
  service-task workflow reruns the connector and it fails again

Idempotent behavior:

- suspending an already-suspended instance returns the existing instance
- resuming an already-running instance returns the existing instance
- erroring an already-errored instance returns the existing instance
- terminating an already-terminated instance returns the existing
  instance

### `ErrorProcessInstanceCommand`

Mark a process instance as `error`.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `tenant_id` | `str` | yes | |
| `process_instance_id` | `int` | yes | |
| `user_id` | `int \| None` | no | Tenant membership is enforced when supplied. |
| `errored_at_in_seconds` | `int \| None` | no | |

Returns: `ProcessInstanceModel`.

Raises:

- `NotFoundError` if the process instance does not exist, or if `user_id`
  is supplied and the user does not exist
- `AuthorizationError` if `user_id` is supplied and the user does not
  belong to the tenant
- `InvalidStateError` for `complete` and `terminated` instances

Unlike the covered admin lifecycle commands above, `process.error` is
not yet part of the built-in V1 command-permission set.

### `ScheduleProcessInstanceRetryCommand`

Persist a delayed retry job for an errored process instance.

| Field | Type | Required |
| --- | --- | --- |
| `tenant_id` | `str` | yes |
| `process_instance_id` | `int` | yes |
| `user_id` | `int` | yes |
| `retry_at_in_seconds` | `int` | yes |
| `scheduled_at_in_seconds` | `int \| None` | no |

Returns: `SchedulerJobModel`.

Raises:

- `NotFoundError` if the process instance or user does not exist
- `AuthorizationError` if the user is outside the tenant or lacks the
  tenant-scoped `process.retry` permission
- `InvalidStateError` if the instance is not currently in `error`

The scheduled job is deduplicated per process instance. Scheduling a
second retry for the same errored instance updates the existing row
rather than creating a duplicate job.

When the due row is later picked up by `api.run_due_scheduler_jobs(...)`,
the library retries that same process instance through the normal
`process.retry` lifecycle. That means the instance returns from `error`
to `running`, `end_in_seconds` is cleared, terminated runtime tasks are
reopened, terminated human tasks are reset back to `READY`, and the
consumed scheduler row is deleted. If the errored instance failed on a
synchronous service task, the retry path also restores the persisted
workflow snapshot, resets the errored service-task branch, and reruns it
immediately. If the instance is no longer in `error` when the worker
reaches the due row, the stale scheduler row is deleted without forcing
a retry.

---

## Queries (read side)

Dispatch with `api.execute_query(...)`. All queries are read-only.

### `GetProcessInstanceQuery`

| Field | Type |
| --- | --- |
| `tenant_id` | `str` |
| `process_instance_id` | `int` |

Returns: `ProcessInstanceModel`.

Raises:

- `NotFoundError` if the process instance does not exist

### `GetPendingTasksQuery`

| Field | Type | Notes |
| --- | --- | --- |
| `tenant_id` | `str` | |
| `user_id` | `int \| None` | When set, scopes to tasks assigned to that user. |

Returns: `list[HumanTaskModel]`, ordered by id.

Raises:

- `NotFoundError` if `user_id` is supplied and the user does not exist
- `AuthorizationError` if `user_id` is supplied and the user does not
  belong to the tenant

### `GetProcessInstanceEventsQuery`

| Field | Type |
| --- | --- |
| `tenant_id` | `str` |
| `process_instance_id` | `int` |

Returns: `list[ProcessInstanceEventModel]`, ordered by timestamp and id.

Raises:

- `NotFoundError` if the process instance does not exist

### `GetProcessInstanceMetadataQuery`

| Field | Type |
| --- | --- |
| `tenant_id` | `str` |
| `process_instance_id` | `int` |

Returns: `list[ProcessInstanceMetadataModel]`, ordered by key and id.

Raises:

- `NotFoundError` if the process instance does not exist

### `ListProcessInstancesQuery`

| Field | Type | Notes |
| --- | --- | --- |
| `tenant_id` | `str` | |
| `status` | `ProcessInstanceStatus \| str \| None` | Optional status filter. |

Returns: `list[ProcessInstanceModel]`, ordered by id.

### Status-shortcut list queries

`ListErrorProcessInstancesQuery`, `ListSuspendedProcessInstancesQuery`,
and `ListTerminatedProcessInstancesQuery` each take a single
`tenant_id` field and return `list[ProcessInstanceModel]` filtered to
the matching status.

---

## Errors

All public errors are subclasses of `BpmnCoreError`. Each leaf class
also subclasses the matching builtin exception so callers can catch
either the domain class or the builtin.

```text
BpmnCoreError
|- ValidationError            (also: ValueError)
|  `- InvalidStateError       # bad state transition
|- AuthorizationError         (also: PermissionError)
|- NotFoundError              (also: LookupError)
`- ServiceTaskExecutionError  (also: RuntimeError)
```

| Error | When | Public sources |
| --- | --- | --- |
| `ValidationError` | Inputs are malformed or contradictory, such as ambiguous `bpmn_process_id`, invalid event type, or invalid scheduler inputs | Import, initialize, event, and scheduler paths |
| `InvalidStateError` | The target entity is in a state that does not permit the operation, such as claiming a completed task or suspending a terminal instance | Task and lifecycle commands |
| `AuthorizationError` | The supplied user does not belong to the tenant, lacks a required command permission, is not assigned to the target task, or does not own the target task | Any command/query that accepts `user_id`, plus covered workflow admin commands |
| `NotFoundError` | The requested entity does not exist for the supplied tenant scope | All commands/queries that load users, tasks, lane owners, instances, or definitions |
| `ServiceTaskExecutionError` | A BPMN service task failed while invoking a registered connector or proxy adapter | Initialize, complete-task, retry, and workflow-runtime execution paths |

Catch by either the domain class or the matching builtin. Both work.

See [examples.md](examples.md#errors-demo) for a runnable walkthrough in
`examples/errors_demo.py`.

---

## Authorization Hooks

Custom policy engines can plug into command authorization through the
public hook surface:

- `AuthorizationPolicy`
  Protocol with `authorize(session, request) -> AuthorizationDecision`.
- `AuthorizationPolicyFactory`
  Callable returning an `AuthorizationPolicy`.
- `AuthorizationRequest`
  Carries `tenant_id`, `actor_user_id`, `command_key`, `permission`,
  `target_uri`, `target_id`, and optional `metadata`.
- `AuthorizationDecision`
  Shape: `{allowed: bool, reason: str | None}`.
- `DatabaseAuthorizationPolicy`
  Built-in role/grant policy used by default.
- `authorization_policy_scope(policy_or_factory)`
  Temporary override, useful for tests or request-scoped policy
  composition.
- `set_default_authorization_policy_factory(factory)`
  Process-wide default policy override.

Stable V1 command keys are re-exported from `m8flow_bpmn_core.api`:

- `PROCESS_DEFINITION_IMPORT_COMMAND == "process_definition.import"`
- `PROCESS_START_COMMAND == "process.start"`
- `PROCESS_SUSPEND_COMMAND == "process.suspend"`
- `PROCESS_RESUME_COMMAND == "process.resume"`
- `PROCESS_RETRY_COMMAND == "process.retry"`
- `PROCESS_TERMINATE_COMMAND == "process.terminate"`
- `TASK_CLAIM_COMMAND == "task.claim"`
- `TASK_COMPLETE_COMMAND == "task.complete"`

The current task and process-start enforcement points also attach request
metadata so future policy engines can make richer decisions without
changing the command shape. Examples include task lane/owner context and
process-definition identifiers.

---

## Tenant and Identity Model

- `tenant_id` is the first field on every command and query.
- Users are bound to a tenant through their `service` value and tenant
  identifier fields.
- A user belongs to a tenant when their resolved identifiers intersect
  with the tenant's identifiers, which may include tenant `id` and
  `slug`.
- Every public command or query that accepts a `user_id` validates
  tenant membership before doing user-scoped work.
- Process-instance queries are tenant-scoped. There is no cross-tenant
  read path in the public API.
- Timer-started process instances do not have an external caller. The
  library creates them with an internal tenant-scoped system user and
  stores that user id in `process_initiator_id`.

---

## Payload Handling

- `CompleteTaskCommand.task_payload` is the recommended channel for
  values produced by a UI form.
- Task payload values are persisted as `ProcessInstanceMetadataModel`
  rows keyed by payload key, with values stored as strings.
- `InitializeProcessInstanceFromDefinitionCommand.submission_metadata`
  is still supported for seeding metadata at start time.

---

## BPMN and Lane Owners

- The runtime imports BPMN and optional DMN source XML from the stored
  process definition.
- Lane ownership is resolved from `properties_json["lane_owners"]` on
  the definition, using a mapping of lane name -> list of user
  identifiers.
- The runtime materializes human-task assignments from that lane-owner
  metadata plus BPMN lane information.

