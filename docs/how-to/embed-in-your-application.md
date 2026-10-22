# Embed the library in your application

This guide shows how to call m8flow-core from your own application code:
how to pass it a database handle, run a workflow, read its state back, and
handle errors. For every command, query, and error, see the
[public API reference](../reference/api.md).

## Before you begin

You need the following:

- The library installed in your application's environment, as described in
  [Build and install the package](build-and-install-the-package.md).
- A database with the library's tables. The library ships its ORM models in
  `m8flow_core.models`. Your application owns the migrations for them;
  [About the sample app integration](../explanation/sample-app-integration.md)
  shows one way to set that up with Alembic.
- Tenants and users in that database. The library checks them but doesn't
  create them.

## Choose a session or a connection

`execute_command` and `execute_query` accept either a SQLAlchemy `Session` or
a `Connection`:

- Pass a **`Session`** to run the library inside an ORM session you already
  manage.
- Pass a **`Connection`** to control exactly when the transaction commits.
  The library opens a temporary session bound to the connection and never
  commits or rolls back. Your application owns the transaction boundary.

## Run a workflow in a caller-owned transaction

The following example imports a BPMN and DMN definition, starts a process
instance, and completes the first task, all inside one transaction that
commits when the `with` block ends:

```python
from sqlalchemy import create_engine

from m8flow_core import api

engine = create_engine(DATABASE_URL)
TENANT_ID = "tenant-conditional-approval"

with engine.begin() as connection:
    definition = api.execute_command(
        connection,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=TENANT_ID,
            bpmn_identifier="m8flow-core-examples/conditional-approval-poc",
            user_id=definition_admin_user_id,
            bpmn_name="Conditional Approval POC",
            source_bpmn_xml=bpmn_xml,
            source_dmn_xml=dmn_xml,
            properties_json={"flow": "conditional_approval"},
        ),
    )

    process_instance = api.execute_command(
        connection,
        api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=TENANT_ID,
            bpmn_process_definition_id=definition.id,
            process_initiator_id=requester_user_id,
            summary="Expense claim submission",
            bpmn_process_id="Process_conditional_approval_8qpy9gh",
        ),
    )

    pending_tasks = api.execute_query(
        connection,
        api.GetPendingTasksQuery(tenant_id=TENANT_ID, user_id=requester_user_id),
    )
    submit_task = pending_tasks[0]

    api.execute_command(
        connection,
        api.ClaimTaskCommand(
            tenant_id=TENANT_ID,
            work_item_id=submit_task.id,
            user_id=requester_user_id,
        ),
    )

    api.execute_command(
        connection,
        api.CompleteTaskCommand(
            tenant_id=TENANT_ID,
            work_item_id=submit_task.id,
            user_id=requester_user_id,
            task_payload={
                "expense_date": "2026-04-01",
                "expense_type": "Travel",
                "amount": "1500",
                "description": "Trip to LA",
            },
        ),
    )
```

Replace the following:

- `DATABASE_URL`: a SQLAlchemy URL, such as
  `postgresql+psycopg://user:password@localhost:5432/workflows`.
- `bpmn_xml` and `dmn_xml`: the BPMN and DMN source, as `str` or `bytes`.
  This example uses `tests/fixtures/conditional-approval.bpmn` and
  `tests/fixtures/check_eligibility.dmn`.
- `definition_admin_user_id` and `requester_user_id`: IDs of users in the
  tenant. The admin needs the `process_definition.import` permission, and the
  requester needs `process.start`, `task.claim`, and `task.complete`.

`bpmn_process_id` is required only when the BPMN file contains more than one
executable process.

## Pass form values with the task

Send the values a user enters in a form as `CompleteTaskCommand.task_payload`.
The library stores each key as a process metadata row. Values are stored as
strings, so convert numbers and booleans when you read them back.

To seed metadata when the process starts, use
`InitializeProcessInstanceFromDefinitionCommand.submission_metadata`.

## Assign tasks through lanes

The library assigns a user task to the users who own its BPMN lane. Set the
owners in `properties_json["lane_owners"]` when you import the definition, as
a mapping from lane name to a list of usernames:

```python
properties_json={"lane_owners": {"Manager": ["manager1"], "Finance": ["finance1"]}}
```

A script task in the BPMN can also set `lane_owners` at run time, as the
`conditional-approval.bpmn` and `parallel_review_poc.bpmn` fixtures do.

`lane_owners` is optional. If a lane has no owners, the process still starts
and the work item waits as `READY` with no potential owners. When your
application later syncs lane group membership from its directory, call
`api.assign_pending_tasks_for_user(session, tenant_id=..., user_id=...)` to add
the user to the waiting work items in their lanes.

## Read the workflow state back

Use the following queries with `execute_query` to inspect a workflow:

| Query | Returns |
|---|---|
| `GetProcessInstanceQuery` | The process instance, including its status |
| `GetProcessInstanceMetadataQuery` | The stored metadata, including task payload values |
| `GetProcessInstanceEventsQuery` | The event history |
| `GetPendingTasksQuery` | The pending work items for a tenant, or for one user |
| `ListProcessInstancesQuery` | All instances in a tenant, optionally filtered by status |

## Handle errors

Every public failure is a subclass of `api.BpmnCoreError`. Each subclass is
also a matching built-in exception, so `except LookupError` catches
`NotFoundError` too. The following example catches each kind:

```python
try:
    api.execute_command(connection, api.ClaimTaskCommand(...))
except api.NotFoundError:
    # The work item or user doesn't exist in this tenant.
    ...
except api.AuthorizationError:
    # The user is outside the tenant, lacks the permission, or isn't assigned.
    ...
except api.InvalidStateError:
    # The work item is already completed.
    ...
```

The [errors section of the API reference](../reference/api.md#errors) lists
which errors each command and query raises. To see every error raised for
real, run `uv run python examples/errors_demo.py`.

## Show instances in the m8flow UI

If your application shares a database with an m8flow backend, use the same
`<group>/<model>` process model identifier that the backend catalog uses as
`bpmn_identifier`, for example
`m8flow-core-examples/conditional-approval-poc`. Process instance links
in the m8flow UI then resolve to the deployed model.

Users from a shared m8flow Keycloak realm can be scoped to one tenant by
storing the realm issuer in `user.service` and the tenant ID and slug in
`realm_identifier` and `external_org_id`.

## Related pages

- [Run timers and scheduled retries](run-timers-and-retries.md)
- [Call external services from service tasks](call-external-services.md)
- [Customize authorization](customize-authorization.md)
- [About the architecture](../explanation/architecture.md)
