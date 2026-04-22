# API Overview

`m8flow_bpmn_core` is designed to be imported directly from Python code.
The public entry points are in `m8flow_bpmn_core.api`.

## Core Entry Points

- `execute_command(session_or_connection, command)` mutates state.
- `execute_query(session_or_connection, query)` reads state.

Both functions accept either a SQLAlchemy `Session` or a SQLAlchemy
`Connection`.

If you pass a `Connection`, the caller owns the transaction boundary. The
library will use a temporary session bound to that connection and will not
commit or roll back for you.

## Commands

The command layer is the public way to change state.

| Command | Purpose |
| --- | --- |
| `ImportBpmnProcessDefinitionCommand` | Store BPMN and optional DMN XML, hashes, and properties. |
| `InitializeProcessInstanceFromDefinitionCommand` | Create a process instance from a stored definition and start the workflow runtime. |
| `InitializeProcessInstanceWorkflowCommand` | Start the workflow runtime when you already have the process instance and BPMN XML. |
| `CreateProcessInstanceCommand` | Create a process instance row directly. |
| `ClaimTaskCommand` | Claim a pending task for a user. |
| `CompleteTaskCommand` | Complete a task. Optional `task_payload` values are persisted as process metadata at completion time. |
| `UpsertProcessInstanceMetadataCommand` | Create or update a process instance metadata row. |
| `RecordProcessInstanceEventCommand` | Append an event to the process instance event history. |
| `SuspendProcessInstanceCommand` | Suspend a process instance. |
| `ResumeProcessInstanceCommand` | Resume a suspended process instance. |
| `RetryProcessInstanceCommand` | Retry a failed process instance. |
| `ErrorProcessInstanceCommand` | Move a process instance into the error state. |
| `TerminateProcessInstanceCommand` | Terminate a running process instance. |
| `GetPendingTasksCommand` | Command-form worklist read for a tenant or user. |
| `GetProcessInstanceCommand` | Read a process instance snapshot. |
| `GetProcessInstanceMetadataCommand` | Read persisted process metadata rows. |
| `GetProcessInstanceEventsCommand` | Read the process event history. |

## Queries

The query layer exposes the same read operations in query form.

Common query classes include:

- `GetPendingTasksQuery`
- `GetProcessInstanceQuery`
- `GetProcessInstanceMetadataQuery`
- `GetProcessInstanceEventsQuery`
- `ListProcessInstancesQuery`
- `ListErrorProcessInstancesQuery`
- `ListSuspendedProcessInstancesQuery`
- `ListTerminatedProcessInstancesQuery`

## Tenant And Identity Model

- `tenant_id` scopes every command and query.
- Users are validated against the tenant by their `service` and `service_id`
  fields.
- In the example app, `service` is the Keycloak realm URL and `service_id` is
  the external identity provider user id.
- Commands that act on a user or task check that the user belongs to the target
  tenant before they proceed.

## Payload Handling

- Use `CompleteTaskCommand.task_payload` when a UI form should be submitted as
  part of task completion.
- The example workflows attach the submit form payload, manager decision, and
  finance decision through task completion so the flow matches the UI more
  closely.
- `InitializeProcessInstanceFromDefinitionCommand` still accepts
  `submission_metadata` for callers that want to seed initial metadata, but the
  current examples prefer task completion payloads.

## BPMN And Lane Owners

- The runtime imports BPMN and optional DMN source XML from the stored
  definition.
- Lane assignment and lane owner resolution are driven by the BPMN lane
  metadata and the `lane_owners` mapping stored in the process definition
  properties.
- The conditional approval example demonstrates requestor, manager, reviewer,
  and finance users across two lanes.
