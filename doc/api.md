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

---

## Commands (write side)

Dispatch with `api.execute_command(...)`.

### `ImportBpmnProcessDefinitionCommand`

Persist BPMN (and optional DMN) XML as a process definition.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `tenant_id` | `str` | yes | |
| `bpmn_identifier` | `str` | yes | Stable identifier for the definition. |
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
`AuthorizationError` (initiator not in tenant).

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
(user not in tenant), `InvalidStateError` (task already completed).

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
(user not in tenant or not assigned to the task), `InvalidStateError`
(task already completed).

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
`ErrorProcessInstanceCommand`, `RetryProcessInstanceCommand`,
`TerminateProcessInstanceCommand` all share the same input shape:

| Field | Type | Required |
| --- | --- | --- |
| `tenant_id` | `str` | yes |
| `process_instance_id` | `int` | yes |
| `user_id` | `int \| None` | no — tenant-checked when supplied |
| `<verb>_at_in_seconds` | `int \| None` | no |

All return `ProcessInstanceModel`. Each enforces a state machine:

| Command | Allowed transitions | Raises `InvalidStateError` for… |
| --- | --- | --- |
| `SuspendProcessInstanceCommand` | non-terminal → `suspended` | terminal instances |
| `ResumeProcessInstanceCommand` | `suspended` → `running` | non-suspended, terminal |
| `ErrorProcessInstanceCommand` | non-terminal → `error` | `complete`, `terminated` |
| `RetryProcessInstanceCommand` | `error` → `running` | non-errored |
| `TerminateProcessInstanceCommand` | non-terminal → `terminated` | `complete`, `error` |

Suspending an already-suspended instance (and similar idempotent calls
like terminating a terminated one or erroring an errored one) is a
no-op — the existing instance is returned unchanged.

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
└── NotFoundError                   (also: LookupError)
```

| Error | When | Public sources |
| --- | --- | --- |
| `ValidationError` | Inputs are malformed (e.g. ambiguous `bpmn_process_id`, missing definition XML) | `Initialize*`, `Import*` commands |
| `InvalidStateError` | The target entity is in a state that does not permit the operation (e.g. suspending a terminal instance, claiming a completed task) | Task and lifecycle commands |
| `AuthorizationError` | The supplied user does not belong to the tenant, or is not assigned to the target task | Any command/query that accepts `user_id` |
| `NotFoundError` | The requested entity (tenant, user, task, process instance, definition, lane owner) does not exist for the supplied tenant | All commands/queries |

Catch by either the domain class or the matching builtin — both work.

See [`examples.md`](examples.md#errors-demo) for a runnable walkthrough
(`examples/errors_demo.py`) that triggers each class through the public
API and asserts the contract above.

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
