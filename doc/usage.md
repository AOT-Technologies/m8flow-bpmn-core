# Usage Guide

This guide shows the typical shape of an integration with `m8flow_bpmn_core`.
For the full contract — every command, query, return type, and error —
see [`api.md`](api.md).

## Use A Caller-Owned Transaction

If you already control a SQLAlchemy `Connection`, pass it directly to
`execute_command(...)` or `execute_query(...)`. The library opens a
temporary session bound to the connection and does not commit or roll
back; the caller owns the transaction boundary.

The snippet below is illustrative. Replace the placeholder XML, tenant id,
and user ids with real values from your own workflow.

```python
from sqlalchemy import create_engine
from m8flow_bpmn_core import api

engine = create_engine("postgresql+psycopg://postgres:postgres@localhost:5432/m8flow_bpmn_core")

with engine.begin() as connection:
    definition = api.execute_command(
        connection,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id="tenant-conditional-approval",
            bpmn_identifier="m8flow-bpmn-core-examples/conditional-approval-poc",
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
            tenant_id="tenant-conditional-approval",
            bpmn_process_definition_id=definition.id,
            process_initiator_id=requester_user_id,
            summary="Expense claim submission",
            process_version=1,
            started_at_in_seconds=100,
            bpmn_process_id="Process_conditional_approval_8qpy9gh",
        ),
    )

    pending_tasks = api.execute_query(
        connection,
        api.GetPendingTasksQuery(
            tenant_id="tenant-conditional-approval",
            user_id=requester_user_id,
        ),
    )

    submit_task = pending_tasks[0]

    api.execute_command(
        connection,
        api.ClaimTaskCommand(
            tenant_id="tenant-conditional-approval",
            human_task_id=submit_task.id,
            user_id=requester_user_id,
        ),
    )

    api.execute_command(
        connection,
        api.CompleteTaskCommand(
            tenant_id="tenant-conditional-approval",
            human_task_id=submit_task.id,
            user_id=requester_user_id,
            completed_at_in_seconds=110,
            task_payload={
                "expense_date": "2026-04-01",
                "expense_type": "Travel",
                "amount": "1500",
                "description": "Trip to LA",
            },
        ),
    )
```

## Read Back State

After the command completes, use queries (with `execute_query`) to
inspect the workflow state:

- `GetProcessInstanceQuery` — process instance snapshot.
- `GetProcessInstanceMetadataQuery` — persisted payload values.
- `GetProcessInstanceEventsQuery` - event history.
- `GetPendingTasksQuery` - current worklist for a tenant or user.

## Poll For Due Timers

When timer-based workflows are waiting, or when a process retry has been
scheduled, the library persists due work in `scheduler_job`. The host
application only needs to wake up periodically and call the public scheduler
entrypoint.

```python
api.execute_command(
    connection,
    api.ScheduleProcessInstanceRetryCommand(
        tenant_id="tenant-conditional-approval",
        process_instance_id=errored_process_instance.id,
        user_id=workflow_admin_user_id,
        retry_at_in_seconds=1_717_000_000,
    ),
)
```

```python
import time

from sqlalchemy import create_engine
from m8flow_bpmn_core import api

engine = create_engine("postgresql+psycopg://postgres:postgres@localhost:5432/m8flow_bpmn_core")

while True:
    with engine.begin() as connection:
        processed = api.run_due_scheduler_jobs(
            connection,
            worker_id="inline-scheduler-1",
            tenant_id="tenant-conditional-approval",
        )
    time.sleep(5 if processed == 0 else 1)
```

The host application owns the wake-up cadence and the surrounding transaction.
The library owns due-job lookup, workflow restoration, timer refresh, workflow
advancement, timer-start instance creation, scheduled retry execution, and
timer rescheduling.

For interrupting timer boundary events, the same poller path is enough. When
the due row is reached, the library reloads the same process instance,
refreshes the workflow, closes the interrupted human task as `CANCELLED`, and
materializes the timeout-path task that became READY.

For delayed retry specifically, the normal host-application sequence is:

1. Move the current process instance into `error` with
   `ErrorProcessInstanceCommand`.
2. Persist one delayed retry row for that same process instance with
   `ScheduleProcessInstanceRetryCommand`.
3. Keep calling `api.run_due_scheduler_jobs(...)` from the application's
   scheduler loop.
4. When the row becomes due, the library reloads the same process instance and
   executes the normal retry lifecycle for it.
5. If the instance is still in `error`, the library changes it back to
   `running`, clears `end_in_seconds`, reopens terminated runtime tasks and
   human tasks to `READY`, records a `process_instance_retried` event, and
   deletes the consumed scheduler row.
6. If that errored instance was caused by a synchronous service task failure,
   the retry path also restores the persisted Spiff workflow snapshot, resets
   the errored service-task branch, and reruns it immediately inside the same
   command or scheduler invocation.
7. If the rerun succeeds, the instance continues to its next waiting state,
   user task, or completion state as normal. If it fails again, the instance
   returns to `error` and surfaces `ServiceTaskExecutionError` again.
8. If the instance is no longer in `error` by the time the worker reaches the
   due row, the library treats that scheduler row as stale and deletes it
   instead of forcing a retry.

The current V1 runner is intentionally simple and is best used as a single
logical poller per database or tenant scope. A Celery-backed dispatcher can be
added later on top of the same persisted scheduler rows.

Timer-started workflows do not require a caller-supplied initiator id. The
library creates those process instances with an internal tenant-scoped system
user because there is no external actor at trigger time.

## Error Handling

Every public failure is a subclass of `api.BpmnCoreError`:

```python
try:
    api.execute_command(connection, api.ClaimTaskCommand(...))
except api.NotFoundError:
    ...
except api.AuthorizationError:
    ...
except api.InvalidStateError:
    ...
```

See [`api.md`](api.md) for which errors each command and query can raise.

## Custom Authorization Policies

If you need policy logic beyond the built-in V1 role grants, install a custom
policy temporarily with `api.authorization_policy_scope(...)` or globally with
`api.set_default_authorization_policy_factory(...)`.

```python
class FinanceGatePolicy:
    def authorize(self, session, request):
        if (
            request.command_key == api.TASK_COMPLETE_COMMAND
            and request.metadata is not None
            and request.metadata.get("lane_name") == "Finance"
        ):
            return api.AuthorizationDecision(
                False,
                reason="Finance completions require external approval",
            )
        return api.DatabaseAuthorizationPolicy().authorize(session, request)


with api.authorization_policy_scope(FinanceGatePolicy()):
    api.execute_command(connection, api.CompleteTaskCommand(...))
```

The authorization request includes stable command keys for all currently
covered V1 actions. It also carries contextual metadata for the currently
enriched enforcement points (`process.start`, `task.claim`, `task.complete`).

The built-in database policy resolves those command checks through
`permission_target` rows. In practice, callers register URI targets together
with the relevant `permission_target.command` values so the same permission
catalog can distinguish generic URI access from specific workflow commands.

## Practical Notes

- Use `Session` if you want the library to work inside an existing ORM session.
- Use `Connection` if you want to control when the transaction commits.
- If you want instances to resolve cleanly in the m8flow UI, use the same
  grouped process model identifier that the backend catalog uses, for example
  `m8flow-bpmn-core-examples/conditional-approval-poc`.
- The API validates tenant membership before user-scoped operations.
- Importing a definition, starting a process from a stored definition,
  claiming a task, completing a task, and the covered lifecycle admin
  operations all require tenant-scoped command grants.
- The built-in covered command keys are `process_definition.import`,
  `process.start`, `task.claim`, `task.complete`, `process.suspend`,
  `process.resume`, `process.retry`, and `process.terminate`.
- Those command grants are backed by `permission_target.command`, so a shared
  permission catalog can register both URI-only targets and command-specific
  targets for the same URI pattern.
- Shared-realm m8flow users can still be scoped to one tenant locally by
  storing the shared-realm issuer in `user.service` and persisting the tenant
  id and slug in `tenant_specific_field_1` / `tenant_specific_field_2`.
- `task_payload` values are stored as process metadata keys at completion time,
  which is the closest match to a form submit in the example flows.
