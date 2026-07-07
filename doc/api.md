# Public API Reference

This document is the contract for the public surface of `m8flow_bpmn_core`.
Every name listed here is re-exported from `m8flow_bpmn_core.api` and is
guaranteed to keep its name, input fields, return type, and error
semantics. Any change to this surface is a deliberate contract change.

The library is in-process: callers import it directly, own the SQLAlchemy
session/connection, and own the transaction boundary.

Reads go through `execute_query` (or the corresponding service function
directly); writes go through `execute_command`. Commands and queries are
not interchangeable — the dispatcher rejects the wrong kind with a
`TypeError`.

---

## Dispatchers

```python
from m8flow_bpmn_core import api

api.execute_command(session_or_connection, command)
api.execute_query(session_or_connection, query)
```

- **Inputs** — both accept either a `sqlalchemy.orm.Session` or a
  `sqlalchemy.engine.Connection`. When a `Connection` is passed, the
  library opens a temporary session bound to it and does not commit or
  roll back; the caller owns the transaction boundary.
- **`execute_command`** raises `TypeError` if handed a query (or any
  unknown payload). The reverse holds for `execute_query`.
- **Return value** — the same value the underlying service function
  returns (see the per-entry tables below).

You can also call the service functions directly (e.g. `api.claim_task`)
if you prefer to skip the dispatch step. The semantics are identical.

---

## Service Task Hooks

```python
from m8flow_bpmn_core import api

registry = api.ServiceTaskRegistry()
registry.register_connector(my_connector)
```

The service-task execution layer is a stable extension seam and is now wired
into real workflow execution paths.

- `ServiceTaskConnector` - protocol with `connector_key`,
  `list_commands()`, and `execute(request)`.
- `ServiceTaskCommandDefinition` - stable connector command metadata.
- `ServiceTaskParameterDefinition` - stable parameter metadata for one command.
- `ServiceTaskContext` - tenant/process/task execution context passed to a connector.
- `ServiceTaskRequest` - one service-task invocation, identified by an
  operation id such as `http/GetRequestV2`.
- `ServiceTaskResult` - connector result payload returned to the runtime.
- `ServiceTaskRegistry` - in-process registry that resolves operation ids to
  registered connectors.
- `ConnectorProxyServiceTaskConnector` - concrete connector implementation
  that executes commands through `m8flow-connector-proxy`.
- `build_service_task_operation_id(...)` /
  `split_service_task_operation_id(...)` - helper functions for the stable
  `<connector_key>/<command_name>` identifier format.
- `fetch_connector_proxy_command_definitions(...)` - fetches the live proxy
  command catalog and normalizes it into stable command definitions.
- `build_connector_proxy_service_task_connectors(...)` - groups the live proxy
  catalog into one connector object per connector key.
- `build_connector_proxy_service_task_registry(...)` - convenience helper that
  builds a ready-to-use `ServiceTaskRegistry` from a live connector-proxy base URL.
- `service_task_registry_scope(...)` and
  `set_default_service_task_registry_factory(...)` - hooks for request-scoped,
  test-scoped, or process-wide registry overrides.

This seam is intentionally aligned to the current `m8flow-connector-proxy`
catalog shape, where operators look like `http/GetRequestV2`,
`smtp/SendHTMLEmail`, or `postgres_v2/DoSQL`.

See [`service_tasks.md`](service_tasks.md) for the connector-proxy contract and
the intended adapter direction.

During process execution, synchronous service-task failures surface as
`ServiceTaskExecutionError`. The library also persists the failed workflow
snapshot, records `task_failed`, and transitions the process instance to
`error` so the same instance can be retried later.

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

This is the public polling entrypoint for persisted scheduler jobs. It is a
service function rather than a command/query because the caller is driving a
worker loop, not issuing a business command on behalf of an end user.

- **Inputs** - accepts either a `Session` or `Connection`, with the same
  caller-owned transaction semantics as `execute_command(...)` and
  `execute_query(...)`.
- **`now_in_seconds`** - optional due-time override for tests or externally
  controlled scheduling loops.
- **`limit`** - maximum number of due jobs to process in one call. Must be
  greater than zero.
- **`worker_id`** - non-blank identifier recorded in the in-row job lock while
  a job is being processed.
- **`tenant_id`** - optional tenant filter when a host application wants to run
  one scheduler loop per tenant.
- **Return value** - `int`, the number of due jobs processed in that call.
- **Raises** - `ValidationError` for invalid `limit` or blank `worker_id`;
  `BpmnCoreError` if one or more claimed jobs fail during execution.

The scheduler loop is batch-continuing, not fail-fast per claimed row:

- if one claimed job fails, the library releases that job's lock
- it still attempts the remaining claimed jobs from that same polling batch
- after the batch finishes:
  - if exactly one job failed, it re-raises that original scheduler error
  - if multiple jobs failed, it raises one summary `BpmnCoreError` that lists
    the failed job keys and error details

Because the caller owns the transaction boundary, a host application that
passes a caller-owned `Session` or `Connection` must still decide whether to
commit the successful jobs from that batch or roll the whole transaction back.

Current V1 coverage includes waiting intermediate catch events, interrupting
timer boundary events, timer start events, and scheduled process retries. The
host application is responsible for wake-up cadence; the library is
responsible for finding due jobs, restoring workflow state, advancing the
workflow, creating timer-started instances, retrying errored instances, and
rescheduling the next timer if the instance remains waiting.

The current implementation is intentionally simple and is best treated as a
single-poller execution path. Multi-worker claim hardening and Celery dispatch
remain follow-on work.

---

## Conventions

All command/query inputs are `@dataclass(frozen=True, slots=True)`. Fields
follow this order:

1. `tenant_id` — always first, always required.
2. Primary entity identifier (e.g. `human_task_id`, `process_instance_id`).
3. Required business inputs.
4. Optional inputs (default `None`).
5. Trailing `*_at_in_seconds` timestamps.

Return values are SQLAlchemy ORM models from `m8flow_bpmn_core.models.*`.
Only the columns documented in this file are part of the stable contract;
internal columns and relationships may change without a major-version
bump.

## Authorization Model

The library now includes a minimal V1 RBAC layer for workflow commands.

- Command authorization is evaluated through stable command keys stored in
  `permission_target.command`.
- The current built-in command keys are `process_definition.import`,
  `process.start`, `task.claim`, `task.complete`, `process.suspend`,
  `process.resume`, `process.retry`, and `process.terminate`.
- Authorization still depends on tenant scoping: user-scoped operations first
  validate tenant membership, then evaluate command permission, then apply
  runtime checks such as task assignment or claimed-task ownership.
- A `permission_target` row is matched by URI plus optional command. A row with
  `command = NULL` behaves like a URI-only target; a row with a command is
  specific to that command key.
- Custom policies can extend or replace the built-in database-backed policy via
  `authorization_policy_scope(...)` or
  `set_default_authorization_policy_factory(...)`.

---

## Commands (write side)

Dispatch with `api.execute_command(...)`.

### `ImportBpmnProcessDefinitionCommand`

Persist BPMN (and optional DMN) XML as a process definition.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `tenant_id` | `str` | yes | |
| `bpmn_identifier` | `str` | yes | Stable identifier for the definition. |
| `user_id` | `int` | yes | Actor who must belong to the tenant and hold `process_definition.import`. |
| `source_bpmn_xml` | `str \| bytes` | yes | |
| `source_dmn_xml` | `str \| bytes \| None` | no | |
| `bpmn_name` | `str \| None` | no | |
| `properties_json` | `dict \| None` | no | Arbitrary metadata (e.g. `lane_owners`). |
| `bpmn_version_control_type` | `str \| None` | no | E.g. `"git"`. |
| `bpmn_version_control_identifier` | `str \| None` | no | E.g. branch or commit. |
| `single_process_hash` | `str \| None` | no | Auto-computed if omitted. |
| `full_process_model_hash` | `str \| None` | no | Auto-computed if omitted; used for idempotent upsert. |
| `created_at_in_seconds` | `int \| None` | no | |
| `updated_at_in_seconds` | `int \| None` | no | |

**Returns**: `BpmnProcessDefinitionModel`.

**Raises**: `ValidationError` when `source_bpmn_xml` (or `source_dmn_xml`,
if supplied) is not parseable or does not contain at least one executable
process, `AuthorizationError` when `user_id` is outside the tenant or lacks
the tenant-scoped `process_definition.import` permission. Validation runs
before anything is persisted, so a rejected import never leaves a partial row
behind.

### `InitializeProcessInstanceFromDefinitionCommand`

Create a process instance from a stored definition and start the workflow.

| Field | Type | Required |
| --- | --- | --- |
| `tenant_id` | `str` | yes |
| `bpmn_process_definition_id` | `int` | yes |
| `process_initiator_id` | `int` | yes |
| `submission_metadata` | `dict[str, str] \| None` | no |
| `summary` | `str \| None` | no |
| `process_version` | `int` | no (default `1`) |
| `started_at_in_seconds` | `int \| None` | no |
| `bpmn_process_id` | `str \| None` | no — required only when the definition contains multiple processes |

**Returns**: `ProcessInstanceModel` (already advanced to its first wait state).

**Raises**: `ValidationError` (definition missing source XML, ambiguous
process id), `NotFoundError` (definition or initiator missing),
`AuthorizationError` (initiator not in tenant or lacks the
tenant-scoped `process.start` permission).

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

**Returns**: `ProcessInstanceModel`.

**Raises**: `InvalidStateError` (terminal instance or already initialized),
`ValidationError`.

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

**Returns**: `ProcessInstanceModel`. **Raises**: `AuthorizationError`.

### `ClaimTaskCommand`

Claim a pending human task for a user.

| Field | Type | Required |
| --- | --- | --- |
| `tenant_id` | `str` | yes |
| `human_task_id` | `int` | yes |
| `user_id` | `int` | yes |
| `added_by` | `str` | no (default `"manual"`) |

**Returns**: `HumanTaskModel`.

**Raises**: `NotFoundError` (task missing), `AuthorizationError`
(user not in tenant, lacks the tenant-scoped `task.claim` permission,
is not assigned to the task, or tries to claim a task already owned by
another user), `InvalidStateError` (task already completed).

### `CompleteTaskCommand`

Complete a claimed task and advance the workflow.

| Field | Type | Required |
| --- | --- | --- |
| `tenant_id` | `str` | yes |
| `human_task_id` | `int` | yes |
| `user_id` | `int` | yes |
| `completed_at_in_seconds` | `int \| None` | no |
| `task_payload` | `dict[str, str] \| None` | no — persisted as process metadata |

**Returns**: `HumanTaskModel`.

**Raises**: `NotFoundError`, `AuthorizationError`
(user not in tenant, lacks the tenant-scoped `task.complete`
permission, is not assigned to the task, or does not own the claimed
task), `InvalidStateError` (task already completed or has not been
claimed yet).

### `UpsertProcessInstanceMetadataCommand`

Create or update a single metadata key/value for a process instance.

| Field | Type | Required |
| --- | --- | --- |
| `tenant_id` | `str` | yes |
| `process_instance_id` | `int` | yes |
| `key` | `str` | yes |
| `value` | `str` | yes |
| `updated_at_in_seconds` | `int` | yes |
| `created_at_in_seconds` | `int \| None` | no |

**Returns**: `ProcessInstanceMetadataModel`. **Raises**: `NotFoundError`.

### `RecordProcessInstanceEventCommand`

Append an event to the process instance event history.

| Field | Type | Required |
| --- | --- | --- |
| `tenant_id` | `str` | yes |
| `process_instance_id` | `int` | yes |
| `event_type` | `ProcessInstanceEventType \| str` | yes |
| `task_guid` | `str \| None` | no |
| `user_id` | `int \| None` | no — when provided, tenant membership is enforced |
| `timestamp` | `float \| None` | no |

**Returns**: `ProcessInstanceEventModel`.

**Raises**: `NotFoundError`, `AuthorizationError` (if `user_id` is set
and does not belong to the tenant).

### Lifecycle commands

`SuspendProcessInstanceCommand`, `ResumeProcessInstanceCommand`,
`RetryProcessInstanceCommand`, and `TerminateProcessInstanceCommand` share the
same input shape:

| Field | Type | Required |
| --- | --- | --- |
| `tenant_id` | `str` | yes |
| `process_instance_id` | `int` | yes |
| `user_id` | `int` | yes |
| `<verb>_at_in_seconds` | `int \| None` | no |

Each returns `ProcessInstanceModel`, requires tenant membership, and enforces
the matching tenant-scoped command permission before checking the state
machine:

| Command | Allowed transitions | Raises `InvalidStateError` for… |
| --- | --- | --- |
| `SuspendProcessInstanceCommand` | non-terminal → `suspended` | terminal instances |
| `ResumeProcessInstanceCommand` | `suspended` → `running` | non-suspended, terminal |
| `RetryProcessInstanceCommand` | `error` → `running` | non-errored |
| `TerminateProcessInstanceCommand` | non-terminal → `terminated` | `complete`, `error` |

Suspending an already-suspended instance (and similar idempotent calls
like terminating a terminated one or erroring an errored one) is a
no-op — the existing instance is returned unchanged.

`ErrorProcessInstanceCommand` remains separate:

| Field | Type | Required |
| --- | --- | --- |
| `tenant_id` | `str` | yes |
| `process_instance_id` | `int` | yes |
| `user_id` | `int \| None` | no — tenant-checked when supplied |
| `errored_at_in_seconds` | `int \| None` | no |

It returns `ProcessInstanceModel` and allows non-terminal → `error`. It
raises `InvalidStateError` for `complete` and `terminated`. Unlike the covered
admin lifecycle commands above, `process.error` is not yet part of the built-in
V1 command-permission set.

### `ScheduleProcessInstanceRetryCommand`

Persist a delayed retry job for an errored process instance.

| Field | Type | Required |
| --- | --- | --- |
| `tenant_id` | `str` | yes |
| `process_instance_id` | `int` | yes |
| `user_id` | `int` | yes |
| `retry_at_in_seconds` | `int` | yes |
| `scheduled_at_in_seconds` | `int \| None` | no |

**Returns**: `SchedulerJobModel`.

**Raises**: `NotFoundError` (process instance or user missing),
`AuthorizationError` (user not in tenant or lacks the tenant-scoped
`process.retry` permission), `InvalidStateError` (instance is not currently in
`error` status).

The scheduled job is deduplicated per process instance. Scheduling a second
retry for the same errored instance updates the existing row rather than
creating a duplicate job.

When the due row is later picked up by `api.run_due_scheduler_jobs(...)`, the
library retries that same process instance through the normal
`process.retry` lifecycle. That means the instance returns from `error` to
`running`, `end_in_seconds` is cleared, terminated runtime tasks are reopened,
terminated human tasks are reset back to `READY`, and the consumed scheduler
row is deleted. If the errored instance failed on a synchronous service task,
the retry path also restores the persisted workflow snapshot, resets the
errored service-task branch, and reruns it immediately. If the instance is no
longer in `error` when the worker
reaches the due row, the stale scheduler row is deleted without forcing a
retry.

---

## Queries (read side)

Dispatch with `api.execute_query(...)`. All queries are read-only and do
not mutate state.

### `GetProcessInstanceQuery`

| Field | Type |
| --- | --- |
| `tenant_id` | `str` |
| `process_instance_id` | `int` |

**Returns**: `ProcessInstanceModel`. **Raises**: `NotFoundError`.

### `GetPendingTasksQuery`

| Field | Type | Notes |
| --- | --- | --- |
| `tenant_id` | `str` | |
| `user_id` | `int \| None` | When set, scopes to tasks assigned to that user. |

**Returns**: `list[HumanTaskModel]`, ordered by id.
**Raises**: `AuthorizationError` if `user_id` is set and does not
belong to `tenant_id`.

### `GetProcessInstanceEventsQuery`

| Field | Type |
| --- | --- |
| `tenant_id` | `str` |
| `process_instance_id` | `int` |

**Returns**: `list[ProcessInstanceEventModel]`, ordered by timestamp.
**Raises**: `NotFoundError`.

### `GetProcessInstanceMetadataQuery`

| Field | Type |
| --- | --- |
| `tenant_id` | `str` |
| `process_instance_id` | `int` |

**Returns**: `list[ProcessInstanceMetadataModel]`, ordered by key.
**Raises**: `NotFoundError`.

### `ListProcessInstancesQuery`

| Field | Type | Notes |
| --- | --- | --- |
| `tenant_id` | `str` | |
| `status` | `ProcessInstanceStatus \| str \| None` | Filter by status. |

**Returns**: `list[ProcessInstanceModel]`.

### Status-shortcut list queries

`ListErrorProcessInstancesQuery`, `ListSuspendedProcessInstancesQuery`,
`ListTerminatedProcessInstancesQuery` — each takes a single `tenant_id`
field and returns `list[ProcessInstanceModel]` filtered to the matching
status.

---

## Errors

All public errors are subclasses of `BpmnCoreError`. Each leaf class
additionally subclasses the matching builtin exception so callers can
catch either the domain class or the builtin.

```
BpmnCoreError
├── ValidationError                 (also: ValueError)
│     └── InvalidStateError          # bad state transition
├── AuthorizationError              (also: PermissionError)
├── NotFoundError                   (also: LookupError)
└── ServiceTaskExecutionError       (also: RuntimeError)
```

| Error | When | Public sources |
| --- | --- | --- |
| `ValidationError` | Inputs are malformed (e.g. ambiguous `bpmn_process_id`, missing definition XML) | `Initialize*`, `Import*` commands |
| `InvalidStateError` | The target entity is in a state that does not permit the operation (e.g. suspending a terminal instance, claiming a completed task) | Task and lifecycle commands |
| `AuthorizationError` | The supplied user does not belong to the tenant, lacks a required command permission, is not assigned to the target task, or does not own the target task | Any command/query that accepts `user_id` |
| `NotFoundError` | The requested entity (tenant, user, task, process instance, definition, lane owner) does not exist for the supplied tenant | All commands/queries |
| `ServiceTaskExecutionError` | A registered service task connector or proxy adapter fails while executing a BPMN service task | Service-task runtime execution paths |

Catch by either the domain class or the matching builtin — both work.

See [`examples.md`](examples.md#errors-demo) for a runnable walkthrough
(`examples/errors_demo.py`) that triggers each class through the public
API and asserts the contract above.

---

## Authorization Hooks

Custom policy engines can plug into command authorization through the public
hook surface:

- `AuthorizationPolicy` — protocol with
  `authorize(session, request) -> AuthorizationDecision`
- `AuthorizationPolicyFactory` — callable returning an `AuthorizationPolicy`
- `AuthorizationRequest` — carries `tenant_id`, `actor_user_id`,
  `command_key`, `permission`, `target_uri`, `target_id`, and optional
  `metadata`
- `AuthorizationDecision` — `{allowed: bool, reason: str | None}`
- `DatabaseAuthorizationPolicy` — the built-in role/grant policy used by default
- `authorization_policy_scope(policy_or_factory)` — temporary override, useful
  for tests or request-scoped policy composition
- `set_default_authorization_policy_factory(factory)` — process-wide default
  policy override

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
metadata so future policy engines can make richer decisions without changing
the command shape. Examples include task lane/owner context and process
definition identifiers.

---

## Tenant and identity model

- `tenant_id` is the first argument of every command and query.
- Users are bound to a tenant through their `service` (Keycloak realm
  URL or analogous identifier) and `service_id` fields. A user belongs
  to a tenant when `service_realm(user.service)` matches one of the
  tenant's identifiers (either `id` or `slug`).
- Every command that accepts a `user_id` calls
  `ensure_user_belongs_to_tenant(...)` before doing any work.
- Process-instance queries filter on `m8f_tenant_id`. There is no
  cross-tenant read path in the public API.
- Timer-started process instances do not have an external caller. The library
  creates them with an internal tenant-scoped system user and stores that user
  id in `process_initiator_id`.

---

## Payload handling

- `CompleteTaskCommand.task_payload` is the recommended channel for
  values produced by a UI form. They are persisted as
  `ProcessInstanceMetadataModel` rows keyed by the payload keys.
- `InitializeProcessInstanceFromDefinitionCommand.submission_metadata`
  is still supported for seeding metadata at start time, but examples
  now prefer task-completion payloads.

---

## BPMN and lane owners

- The runtime imports BPMN (and optional DMN) source XML from the
  stored definition.
- Lane ownership is resolved from `properties_json["lane_owners"]` on
  the definition (a mapping of lane name → list of user identifiers).
- The conditional-approval example demonstrates requester, manager,
  reviewer, and finance lanes.
