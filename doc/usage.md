# Usage Guide

This guide shows the typical shape of an integration with `m8flow_bpmn_core`.

## Use A Caller-Owned Transaction

If you already control a SQLAlchemy `Connection`, pass it directly to
`execute_command(...)` or `execute_query(...)`.

The snippet below is illustrative. Replace the placeholder XML, tenant id, and
user ids with real values from your own workflow.

```python
from sqlalchemy import create_engine
from m8flow_bpmn_core import api

engine = create_engine("postgresql+psycopg://postgres:postgres@localhost:5432/m8flow_bpmn_core")

with engine.begin() as connection:
    definition = api.execute_command(
        connection,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id="tenant-conditional-approval",
            bpmn_identifier="conditional-approval-poc",
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

    pending_tasks = api.execute_command(
        connection,
        api.GetPendingTasksCommand(
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

After the command completes, use read operations to inspect the workflow state:

- `GetProcessInstanceCommand` for the process snapshot
- `GetProcessInstanceMetadataCommand` for persisted payload values
- `GetProcessInstanceEventsCommand` for the event history
- `GetPendingTasksCommand` for the current worklist

## Practical Notes

- Use `Session` if you want the library to work inside an existing ORM session.
- Use `Connection` if you want to control when the transaction commits.
- The API validates tenant membership before user-scoped operations such as
  pending-task reads, claims, and completions.
- `task_payload` values are stored as process metadata keys at completion time,
  which is the closest match to a form submit in the example flows.
