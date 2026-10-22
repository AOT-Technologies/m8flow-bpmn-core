# Public API reference

This page is the contract for the public surface of m8flow-core. Every name on
it is exported from `m8flow_core.api`. Anything not on this page is internal
and can change in any release. To use the API, import the module:

```python
from m8flow_core import api
```

The library runs in your process. You pass it a SQLAlchemy `Session` or
`Connection` and own the transaction boundary.
[About the architecture](../explanation/architecture.md) explains the design.

## Conventions

Every part of the API follows these conventions:

- Writes are **commands**, dispatched with `execute_command`. Reads are
  **queries**, dispatched with `execute_query`. The two aren't
  interchangeable.
- Every command and query is a `@dataclass(frozen=True, slots=True)`.
- Fields follow this order:
  1. `tenant_id`, always first and always required.
  2. The primary entity ID, where there is one.
  3. Required business inputs.
  4. Optional inputs, usually defaulting to `None`.
  5. Trailing timestamp fields.
- Every timestamp is a timezone-aware UTC `datetime`, such as
  `datetime.now(UTC)`. There are no epoch-second parameters.
- Return values are SQLAlchemy ORM models from `m8flow_core.models`. Only the
  columns and behavior documented here are stable. Relationships and other
  columns can change without a major version bump.
- Every public failure is a subclass of `BpmnCoreError`. See [Errors](#errors).
- Every command or query that accepts a `user_id` checks that the user
  belongs to the tenant before it does anything else.

## Dispatchers

The two dispatchers have the following signatures:

```python
api.execute_command(session_or_connection, command)
api.execute_query(session_or_connection, query)
```

They behave as follows:

| Aspect | Behavior |
|---|---|
| First argument | A `sqlalchemy.orm.Session` or a `sqlalchemy.engine.Connection` |
| With a `Connection` | The library opens a temporary `Session` bound to it and never commits or rolls back |
| Return value | The ORM model or list that the matching service function returns |
| Wrong payload kind | `execute_command` raises `TypeError` for a query or an unknown object; `execute_query` raises `TypeError` for a command or an unknown object |

Each command and query also has a service function you can call directly,
with the same arguments, such as `api.claim_task(...)` or
`api.list_process_instances(...)`.

## Commands

### `ImportBpmnProcessDefinitionCommand`

Stores BPMN XML, and optional DMN XML, as a process definition. Validation
runs before anything is stored.

It takes the following fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `tenant_id` | `str` | Yes | |
| `bpmn_identifier` | `str` | Yes | Stable identifier for the process model |
| `user_id` | `int` | Yes | Needs the `process_definition.import` permission |
| `source_bpmn_xml` | `str \| bytes` | Yes | |
| `source_dmn_xml` | `str \| bytes \| None` | No | |
| `bpmn_name` | `str \| None` | No | |
| `properties_json` | `dict[str, Any] \| None` | No | Arbitrary metadata, such as `lane_owners` |
| `bpmn_version_control_type` | `str \| None` | No | For example, `"git"` |
| `bpmn_version_control_identifier` | `str \| None` | No | For example, a branch or commit |
| `process_xml_digest` | `str \| None` | No | SHA-256 digest of the BPMN source; computed if omitted |
| `created_at` | `datetime \| None` | No | |
| `updated_at` | `datetime \| None` | No | |

Returns `BpmnProcessDefinitionModel`. Importing a definition with a timer start
event also creates or refreshes its scheduler jobs.

Raises:

- `ValidationError` if the BPMN or DMN is malformed, or the BPMN doesn't
  contain exactly one executable process.
- `NotFoundError` if the user doesn't exist.
- `AuthorizationError` if the user is outside the tenant or lacks the
  permission.

### `InitializeProcessInstanceFromDefinitionCommand`

Creates a process instance from a stored definition and starts the workflow.

It takes the following fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `tenant_id` | `str` | Yes | |
| `bpmn_process_definition_id` | `int` | Yes | |
| `process_initiator_id` | `int` | Yes | Needs the `process.start` permission |
| `submission_metadata` | `dict[str, str] \| None` | No | Metadata stored at start |
| `summary` | `str \| None` | No | |
| `process_version` | `int` | No | Default `1` |
| `started_at` | `datetime \| None` | No | |
| `bpmn_process_id` | `str \| None` | No | Required only when the definition has several executable processes |

Returns `ProcessInstanceModel`, already advanced to its first wait state or to
completion.

Raises:

- `NotFoundError` if the definition or initiator doesn't exist.
- `AuthorizationError` if the initiator is outside the tenant or lacks the
  permission.
- `ValidationError` if the stored definition is unusable or the process ID is
  ambiguous.
- `ServiceTaskExecutionError` if the workflow reaches a service task and the
  connector fails.

### `InitializeProcessInstanceWorkflowCommand`

Starts the workflow runtime for a process instance that already exists.

It takes the following fields:

| Field | Type | Required |
|---|---|---|
| `tenant_id` | `str` | Yes |
| `process_instance_id` | `int` | Yes |
| `bpmn_xml` | `str \| bytes` | Yes |
| `bpmn_process_id` | `str \| None` | No |
| `started_at` | `datetime \| None` | No |
| `dmn_xml` | `str \| bytes \| None` | No |

Returns `ProcessInstanceModel`.

Raises:

- `NotFoundError` if the process instance doesn't exist.
- `InvalidStateError` if the instance is terminal or already initialized.
- `ValidationError` if the BPMN or DMN fails to parse or validate.
- `ServiceTaskExecutionError` if the workflow reaches a service task and the
  connector fails.

### `CreateProcessInstanceCommand`

Creates a process instance row without starting the workflow.

It takes the following fields:

| Field | Type | Required |
|---|---|---|
| `tenant_id` | `str` | Yes |
| `process_model_identifier` | `str` | Yes |
| `process_model_display_name` | `str` | Yes |
| `process_initiator_id` | `int` | Yes |
| `bpmn_process_definition_id` | `int` | Yes |
| `bpmn_process_id` | `int` | Yes |
| `summary` | `str \| None` | No |
| `process_version` | `int` | No (default `1`) |
| `created_at` | `datetime \| None` | No |
| `updated_at` | `datetime \| None` | No |

Returns `ProcessInstanceModel`.

Raises:

- `NotFoundError` if the initiator doesn't exist.
- `AuthorizationError` if the initiator isn't in the tenant.

### `ClaimTaskCommand`

Claims a pending work item for a user.

It takes the following fields:

| Field | Type | Required |
|---|---|---|
| `tenant_id` | `str` | Yes |
| `work_item_id` | `int` | Yes |
| `user_id` | `int` | Yes |
| `added_by` | `str` | No (default `"manual"`) |

Returns `WorkItemModel`.

Raises:

- `NotFoundError` if the user or work item doesn't exist.
- `AuthorizationError` if the user is outside the tenant, lacks the
  `task.claim` permission, isn't a potential owner of the work item, or the
  work item is already claimed by another user.
- `InvalidStateError` if the work item is already completed, or if another
  claim on the same work item wins a concurrent update.

### `CompleteTaskCommand`

Completes a claimed work item and advances the workflow.

It takes the following fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `tenant_id` | `str` | Yes | |
| `work_item_id` | `int` | Yes | |
| `user_id` | `int` | Yes | |
| `completed_at` | `datetime \| None` | No | |
| `task_payload` | `dict[str, str] \| None` | No | Stored as process metadata, one row per key, with values as strings |

Returns `WorkItemModel`.

Raises:

- `NotFoundError` if the user or work item doesn't exist.
- `AuthorizationError` if the user is outside the tenant, lacks the
  `task.complete` permission, isn't a potential owner, or doesn't own the
  claim.
- `InvalidStateError` if the work item is already completed or isn't claimed
  yet.
- `ServiceTaskExecutionError` if the workflow advances into a service task and
  the connector fails.

### `UpsertProcessInstanceMetadataCommand`

Creates or updates one metadata key and value on a process instance.

It takes the following fields:

| Field | Type | Required |
|---|---|---|
| `tenant_id` | `str` | Yes |
| `process_instance_id` | `int` | Yes |
| `key` | `str` | Yes |
| `value` | `str` | Yes |
| `updated_at` | `datetime` | Yes |
| `created_at` | `datetime \| None` | No |

Returns `ProcessInstanceMetadataModel`.

Raises `NotFoundError` if the process instance doesn't exist.

### `RecordProcessInstanceEventCommand`

Appends an event to a process instance's history.

It takes the following fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `tenant_id` | `str` | Yes | |
| `process_instance_id` | `int` | Yes | |
| `event_type` | `ProcessLifecycleEventType \| TaskEventType \| str` | Yes | Determines the event's category |
| `task_guid` | `str \| None` | No | |
| `user_id` | `int \| None` | No | Tenant membership is checked when set |
| `occurred_at` | `datetime \| None` | No | Defaults to the current time, to the microsecond |

Returns `ProcessInstanceEventModel`.

Raises:

- `NotFoundError` if the process instance doesn't exist, or `user_id` is set
  and the user doesn't exist.
- `AuthorizationError` if `user_id` is set and the user isn't in the tenant.
- `ValidationError` if `event_type` isn't a valid event type.

### Lifecycle commands

`SuspendProcessInstanceCommand`, `ResumeProcessInstanceCommand`,
`RetryProcessInstanceCommand`, and `TerminateProcessInstanceCommand` share
one shape:

| Field | Type | Required |
|---|---|---|
| `tenant_id` | `str` | Yes |
| `process_instance_id` | `int` | Yes |
| `user_id` | `int` | Yes |
| `suspended_at`, `resumed_at`, `retried_at`, or `terminated_at` | `datetime \| None` | No |

Each returns `ProcessInstanceModel`. Each checks tenant membership and the
matching command permission before it checks the state transition:

| Command | Permission | Allowed transition | Raises `InvalidStateError` for |
|---|---|---|---|
| `SuspendProcessInstanceCommand` | `process.suspend` | Non-terminal → `suspended` | Terminal instances |
| `ResumeProcessInstanceCommand` | `process.resume` | `suspended` → `running` | Terminal or non-suspended instances |
| `RetryProcessInstanceCommand` | `process.retry` | `error` → `running` | Instances not in `error` |
| `TerminateProcessInstanceCommand` | `process.terminate` | Non-terminal → `terminated` | `complete` and `error` instances |

All four also raise:

- `NotFoundError` if the process instance or user doesn't exist.
- `AuthorizationError` if the user is outside the tenant or lacks the
  permission.

`RetryProcessInstanceCommand` can also raise `ServiceTaskExecutionError` if
the retry reruns a failed service task and it fails again.

These commands are idempotent: suspending a suspended instance, resuming a
running one, or terminating a terminated one returns the instance unchanged.

### `ErrorProcessInstanceCommand`

Marks a process instance as `error`.

It takes the following fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `tenant_id` | `str` | Yes | |
| `process_instance_id` | `int` | Yes | |
| `user_id` | `int \| None` | No | Tenant membership is checked when set |
| `errored_at` | `datetime \| None` | No | |

Returns `ProcessInstanceModel`. Erroring an instance already in `error`
returns it unchanged. This command isn't permission-checked.

Raises:

- `NotFoundError` if the process instance doesn't exist, or `user_id` is set
  and the user doesn't exist.
- `AuthorizationError` if `user_id` is set and the user isn't in the tenant.
- `InvalidStateError` for `complete` and `terminated` instances.

### `ScheduleProcessInstanceRetryCommand`

Stores a delayed retry job for a process instance in `error`.

It takes the following fields:

| Field | Type | Required |
|---|---|---|
| `tenant_id` | `str` | Yes |
| `process_instance_id` | `int` | Yes |
| `user_id` | `int` | Yes |
| `retry_at` | `datetime` | Yes |
| `scheduled_at` | `datetime \| None` | No |

Returns `SchedulerJobModel`. There's at most one retry job per instance:
scheduling another updates the existing job.

Raises:

- `NotFoundError` if the process instance or user doesn't exist.
- `AuthorizationError` if the user is outside the tenant or lacks the
  `process.retry` permission.
- `InvalidStateError` if the instance isn't in `error`.

When `run_due_scheduler_jobs` reaches the job, it runs the normal retry on the
same instance, as described in
[About scheduling](../explanation/scheduling.md#scheduled-retry-lifecycle).

## Queries

All queries are read-only. The following table lists each one:

| Query | Fields | Returns | Raises |
|---|---|---|---|
| `GetProcessInstanceQuery` | `tenant_id`, `process_instance_id` | `ProcessInstanceModel` | `NotFoundError` if the instance doesn't exist |
| `GetPendingTasksQuery` | `tenant_id`, `user_id: int \| None` | `list[WorkItemModel]`, ordered by ID; only the user's work items when `user_id` is set | `NotFoundError` or `AuthorizationError` if `user_id` is set and the user doesn't exist or isn't in the tenant |
| `GetProcessInstanceEventsQuery` | `tenant_id`, `process_instance_id` | `list[ProcessInstanceEventModel]`, ordered by `occurred_at` and ID | `NotFoundError` if the instance doesn't exist |
| `GetProcessInstanceMetadataQuery` | `tenant_id`, `process_instance_id` | `list[ProcessInstanceMetadataModel]`, ordered by key and ID | `NotFoundError` if the instance doesn't exist |
| `ListProcessInstancesQuery` | `tenant_id`, `status: ProcessInstanceStatus \| str \| None` | `list[ProcessInstanceModel]`, ordered by ID | None |
| `ListErrorProcessInstancesQuery` | `tenant_id` | `list[ProcessInstanceModel]` in `error` | None |
| `ListSuspendedProcessInstancesQuery` | `tenant_id` | `list[ProcessInstanceModel]` in `suspended` | None |
| `ListTerminatedProcessInstancesQuery` | `tenant_id` | `list[ProcessInstanceModel]` in `terminated` | None |

There's no cross-tenant read path.

A `WorkItemModel` holds the claim state of one human task: `id`,
`process_instance_id`, `task_guid`, `lane_assignment_id`, `actual_owner_id`,
`completed_by_user_id`, `task_status`, and `completed`. It reads display
details from the underlying task through properties, such as `task_name`,
`task_title`, and `lane_name`.
[About the workflow data model](../explanation/workflow-data-model.md)
explains the split.

## Scheduler

The scheduler entry point has the following signature:

```python
api.run_due_scheduler_jobs(
    session_or_connection,
    *,
    now=None,
    limit=100,
    worker_id="inline",
    tenant_id=None,
)
```

Finds due scheduler jobs and runs them: timer starts, intermediate and
boundary timers, recurring timer starts, and scheduled retries. It's a service
function, not a command, because your application calls it from a worker loop,
not on behalf of a user. It takes the following parameters:

| Parameter | Description |
|---|---|
| `session_or_connection` | A `Session` or `Connection`, with the same transaction rules as the dispatchers |
| `now` | A timezone-aware `datetime` that overrides the current time when deciding what's due, for tests or external clocks |
| `limit` | The most jobs to run in one call. Must be greater than zero. |
| `worker_id` | A non-blank ID stored on each job while this call has it claimed |
| `tenant_id` | Runs only this tenant's jobs |

Returns an `int`: the number of due jobs processed.

Raises:

- `ValidationError` if `limit` isn't positive or `worker_id` is blank.
- `BpmnCoreError` if one or more jobs fail. The call runs the whole batch
  first. If one job failed, it raises that job's error; if several failed, it
  raises one error that lists each failed job key.

[Run timers and scheduled retries](../how-to/run-timers-and-retries.md) shows
how to call it.

## Service task hooks

The following names make up the service task extension point:

| Name | Kind | Description |
|---|---|---|
| `ServiceTaskConnector` | Protocol | `connector_key`, `list_commands()`, and `execute(request) -> ServiceTaskResult` |
| `ServiceTaskCommandDefinition` | Dataclass | One connector command: `connector_key`, `command_name`, `display_name`, `description`, `parameters`, `metadata` |
| `ServiceTaskParameterDefinition` | Dataclass | One command parameter: `name`, `parameter_type`, `required`, `description`, `metadata` |
| `ServiceTaskContext` | Dataclass | Where the call comes from: `tenant_id`, `process_instance_id`, `process_definition_id`, `task_guid`, `task_name`, `task_type`, `metadata` |
| `ServiceTaskRequest` | Dataclass | One invocation: `operation_id`, `parameters`, `context`, `task_data`, `callback_url`, `metadata` |
| `ServiceTaskResult` | Dataclass | The connector's answer: `payload`, which must be JSON-serializable, and `metadata` |
| `ServiceTaskRegistry` | Class | Maps connector keys to connectors: `register_connector`, `unregister_connector`, `get_connector`, `list_connectors`, `list_commands`, `get_command`, and `execute(request)` |
| `ServiceTaskRegistryFactory` | Type | A callable `() -> ServiceTaskRegistry` |
| `ConnectorProxyServiceTaskConnector` | Class | A connector that calls one connector key of `m8flow-connector-proxy` |
| `build_service_task_operation_id(connector_key, command_name)` | Function | Returns `<connector_key>/<command_name>` |
| `split_service_task_operation_id(operation_id)` | Function | Validates an operation ID and returns `(connector_key, command_name)` |
| `fetch_connector_proxy_command_definitions(...)` | Function | Reads the proxy's `/v1/commands` catalog as command definitions |
| `build_connector_proxy_service_task_connectors(...)` | Function | Groups the proxy catalog into one connector per connector key |
| `build_connector_proxy_service_task_registry(base_url, *, timeout_seconds=...)` | Function | Builds a registry from a proxy base URL |
| `service_task_registry_scope(registry_or_factory)` | Context manager | Installs a registry for a block of calls |
| `set_default_service_task_registry_factory(factory)` | Function | Installs a registry factory for the whole process |

A connector failure reaches the caller as `ServiceTaskExecutionError`.
[About service task execution](../explanation/service-task-execution.md)
explains when a failure is saved even if the caller rolls back.

## Authorization hooks

The following names make up the authorization extension point:

| Name | Kind | Description |
|---|---|---|
| `AuthorizationPolicy` | Protocol | `authorize(session, request) -> AuthorizationDecision` |
| `AuthorizationPolicyFactory` | Type | A callable that returns an `AuthorizationPolicy` |
| `AuthorizationRequest` | Dataclass | `tenant_id`, `actor_user_id`, `command_key`, `permission`, `resource_type`, `resource_id`, `metadata` |
| `AuthorizationDecision` | Dataclass | `allowed: bool`, `reason: str \| None` |
| `DatabaseAuthorizationPolicy` | Class | The default policy, based on role grants stored in the database |
| `authorization_policy_scope(policy_or_factory)` | Context manager | Installs a policy for a block of calls |
| `set_default_authorization_policy_factory(factory)` | Function | Installs a policy factory for the whole process |

The command key constants, and the resource each command is checked against,
are the following:

| Constant | Value | Resource type | Resource ID |
|---|---|---|---|
| `PROCESS_DEFINITION_IMPORT_COMMAND` | `"process_definition.import"` | `process_definition` | The `bpmn_identifier` |
| `PROCESS_START_COMMAND` | `"process.start"` | `process_model` | The process model identifier |
| `PROCESS_SUSPEND_COMMAND` | `"process.suspend"` | `process_instance` | The instance ID |
| `PROCESS_RESUME_COMMAND` | `"process.resume"` | `process_instance` | The instance ID |
| `PROCESS_RETRY_COMMAND` | `"process.retry"` | `process_instance` | The instance ID |
| `PROCESS_TERMINATE_COMMAND` | `"process.terminate"` | `process_instance` | The instance ID |
| `TASK_CLAIM_COMMAND` | `"task.claim"` | `task` | The task GUID |
| `TASK_COMPLETE_COMMAND` | `"task.complete"` | `task` | The task GUID |

For `process.start`, `task.claim`, and `task.complete`, the request also
carries `metadata`, such as the work item's lane and owner or the process
definition identifiers.

## Runtime helpers

Three lower-level helpers are exported for callers that need direct access to
the workflow runtime:

| Function | Description |
|---|---|
| `advance_process_instance_workflow(session, *, tenant_id, process_instance_id, completed_task_guid, completed_at=None)` | Restores an initialized workflow, completes the given runtime task GUID, advances the process, and saves the new state |
| `resolve_lane_assignment_id(lane_name, tenant_id=None)` | Converts a lane name into the stable positive integer that m8flow uses as a lane group ID. With `tenant_id`, the group is qualified as `<tenant_id>:<lane_name>`, so equal lane names in different tenants never share a group. |
| `assign_pending_tasks_for_user(session, *, tenant_id, user_id)` | Adds the user as a potential owner of incomplete work items in their lanes, for example after you sync directory membership. It doesn't claim them. Returns `list[WorkItemModel]`. |

## Enums

`ProcessInstanceStatus` has the following values: `complete`, `error`,
`not_started`, `running`, `suspended`, `terminated`, `user_input_required`,
and `waiting`.

`ProcessLifecycleEventType` has the following values:

- `process_instance_created`
- `process_instance_completed`
- `process_instance_error`
- `process_instance_force_run`
- `process_instance_migrated`
- `process_instance_resumed`
- `process_instance_retried`
- `process_instance_rewound_to_task`
- `process_instance_suspended`
- `process_instance_suspended_for_error`
- `process_instance_terminated`

`TaskEventType` has the following values: `task_cancelled`, `task_completed`,
`task_data_edited`, `task_executed_manually`, `task_failed`, and
`task_skipped`.

`ProcessInstanceEventCategory` has the values `process` and `task`. Every
stored event has one, derived from its type.

## Errors

Every public error is a subclass of `BpmnCoreError`. Each one also subclasses
a built-in exception, so you can catch either:

```text
BpmnCoreError
|- ValidationError            (also ValueError)
|  `- InvalidStateError       (also ValueError)
|- AuthorizationError         (also PermissionError)
|- NotFoundError              (also LookupError)
`- ServiceTaskExecutionError  (also RuntimeError)
```

The following table shows when each error is raised:

| Error | Raised when | Raised by |
|---|---|---|
| `ValidationError` | Input is malformed or contradictory, such as an ambiguous `bpmn_process_id`, an invalid event type, or invalid scheduler arguments | Import, initialize, event, and scheduler calls |
| `InvalidStateError` | The entity's state doesn't allow the operation, such as claiming a completed work item or suspending a terminated instance | Task and lifecycle commands |
| `AuthorizationError` | The user isn't in the tenant, lacks the command permission, isn't a potential owner, or doesn't own the claim | Any call that takes a `user_id` |
| `NotFoundError` | The entity doesn't exist in the tenant | Any call that loads a user, work item, lane owner, instance, or definition |
| `ServiceTaskExecutionError` | A BPMN service task failed in its connector | Initialize, complete-task, retry, and scheduler calls |

`examples/errors_demo.py` raises each error for real. See
[Run the examples](../how-to/run-the-examples.md#run-the-sqlite-examples).
