from __future__ import annotations

import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from pprint import pformat
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.db import build_engine, create_schema
from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_bpmn_core.models.human_task import HumanTaskModel
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel
from m8flow_bpmn_core.models.process_instance_event import (
    ProcessInstanceEventModel,
)
from m8flow_bpmn_core.models.process_instance_metadata import (
    ProcessInstanceMetadataModel,
)
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_BPMN_PATH = REPO_ROOT / "tests" / "fixtures" / "conditional-approval.bpmn"
EXAMPLE_DMN_PATH = REPO_ROOT / "tests" / "fixtures" / "check_eligibility.dmn"

CONDITIONAL_APPROVAL_PROCESS_ID = "Process_conditional_approval_8qpy9gh"
CONDITIONAL_APPROVAL_TASK_IDS = {
    "submit": "Activity_0qoxmh9",
    "manager_review": "Activity_0b1dd0g",
    "finance_review": "Activity_1uha89x",
}

SCENARIO_AMOUNT = 1_500
MANAGER_DECISION = "Approved"
FINANCE_DECISION = "Approved"


@dataclass(frozen=True, slots=True)
class ExampleContext:
    tenant_id: str
    tenant_slug: str
    other_tenants: list[dict[str, str]]
    user_ids: dict[str, int]
    user_names: dict[str, str]
    lane_owners: dict[str, list[str]]
    scenario_name: str


def main() -> None:
    database_url, display_database_url = _resolve_database_url()
    print("m8flow-bpmn-core conditional-approval usage example")
    print(f"Database URL: {display_database_url}")
    print(
        "This script creates the schema if needed, seeds demo data, and then "
        "drives the workflow through the public API."
    )
    print(_describe_workflow_mode())
    print()
    print("=" * 88)
    print("Database connection details")
    print(
        "Open DBeaver or another PostgreSQL client with the settings below "
        "if you want to inspect the demo database before the workflow starts."
    )
    print(_format_connection_details(_describe_connection(database_url)))
    print(
        "Each workflow command runs in its own committed transaction, so "
        "you can watch the database change step by step."
    )
    _pause("Press Enter to start the example.")

    print()
    print("=" * 88)
    print("Database setup")
    print("Connecting to Postgres and creating the schema...")
    print(
        "If this takes a moment, the example is waiting for the database "
        "server to accept connections."
    )

    print("Status: connecting...")
    engine = build_engine(database_url)
    try:
        _wait_for_database(engine)
    except RuntimeError as exc:
        print(f"Status: unable to reach Postgres. {exc}")
        print(
            "Hint: start PostgreSQL locally or set M8FLOW_EXAMPLE_DATABASE_URL "
            "to a reachable database, then rerun the example."
        )
        engine.dispose()
        raise SystemExit(1) from exc

    print("Status: creating schema...")
    create_schema(engine)
    print("Status: database connection and schema are ready.")

    try:
        with engine.begin() as connection:
            session = Session(
                bind=connection,
                autoflush=False,
                expire_on_commit=False,
            )
            try:
                context = _seed_demo_context(session)
            finally:
                session.close()
        print("Status: seed data committed and visible in the database.")

        _run_workflow(engine, context)
    except KeyboardInterrupt:
        print("\nInterrupted. The current step was rolled back.")
    finally:
        engine.dispose()


def _resolve_database_url() -> tuple[str, str]:
    raw_url = (
        os.getenv("M8FLOW_EXAMPLE_DATABASE_URL")
        or "postgresql+psycopg://postgres:postgres@localhost:5432/"
        "m8flow_bpmn_core_example"
    )
    try:
        url = make_url(raw_url)
    except Exception:
        return raw_url, raw_url

    if url.get_backend_name().startswith("postgresql"):
        query = dict(url.query)
        query.setdefault("connect_timeout", "1")
        url = url.set(query=query)

    return str(url), url.render_as_string(hide_password=True)


def _describe_connection(database_url: str) -> dict[str, Any]:
    url = make_url(database_url)
    host = url.host or "localhost"
    port = url.port or 5432
    database = url.database or ""
    username = url.username or ""
    password = url.password or ""
    jdbc_url = f"jdbc:postgresql://{host}:{port}/{database}"
    return {
        "jdbc_url": jdbc_url,
        "host": host,
        "port": port,
        "database": database,
        "username": username,
        "password": password or "(blank / trust auth)",
    }


def _wait_for_database(
    engine: Engine,
    *,
    timeout_seconds: int = 15,
    poll_interval_seconds: int = 1,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    spinner = "|/-\\"
    spinner_index = 0
    last_error: Exception | None = None
    status_message = "Waiting for the DB to start up"

    while time.monotonic() < deadline:
        _write_spinner(status_message, spinner[spinner_index])
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("select 1")
            _clear_spinner(status_message)
            return
        except Exception as exc:  # pragma: no cover - transient startup path
            last_error = exc
            time.sleep(poll_interval_seconds)
            spinner_index = (spinner_index + 1) % len(spinner)

    _clear_spinner(status_message)

    raise RuntimeError(
        "the database did not become available within "
        f"{timeout_seconds} seconds"
    ) from last_error


def _write_spinner(message: str, spinner_char: str) -> None:
    sys.stdout.write(f"\r{message} {spinner_char}")
    sys.stdout.flush()


def _clear_spinner(message: str) -> None:
    sys.stdout.write("\r" + " " * (len(message) + 2) + "\r")
    sys.stdout.flush()


def _seed_demo_context(session: Session) -> ExampleContext:
    print()
    print("=" * 88)
    print("Setup: create the demo tenant and users")
    print(
        "The workflow commands below use only the public API. The tenant and "
        "users are seeded directly with SQLAlchemy because the library does "
        "not expose public commands for those rows."
    )
    print(
        "The example also seeds a couple of unrelated tenant rows so it is "
        "clear the task routing depends on user identifiers, not tenant "
        "membership."
    )
    _pause("Press Enter to seed the demo data.")

    print("Status: seeding tenant and users...")
    suffix = uuid.uuid4().hex[:8]
    tenant = M8flowTenantModel(
        id=f"tenant-conditional-approval-{suffix}",
        name="Conditional Approval Example",
        slug=f"conditional-approval-example-{suffix}",
    )
    tenant_service = f"http://localhost:7002/realms/{tenant.slug}"
    other_tenants = [
        M8flowTenantModel(
            id=f"tenant-conditional-approval-noise-a-{suffix}",
            name="Conditional Approval Noise A",
            slug=f"conditional-approval-noise-a-{suffix}",
        ),
        M8flowTenantModel(
            id=f"tenant-conditional-approval-noise-b-{suffix}",
            name="Conditional Approval Noise B",
            slug=f"conditional-approval-noise-b-{suffix}",
        ),
    ]
    users = {
        "manager": UserModel(
            username=f"manager-{suffix}",
            email=f"manager-{suffix}@example.com",
            service=tenant_service,
            service_id=f"manager-{suffix}-keycloak",
            display_name="Manager",
            created_at_in_seconds=1,
            updated_at_in_seconds=1,
        ),
        "reviewer": UserModel(
            username=f"reviewer-{suffix}",
            email=f"reviewer-{suffix}@example.com",
            service=tenant_service,
            service_id=f"reviewer-{suffix}-keycloak",
            display_name="Reviewer",
            created_at_in_seconds=1,
            updated_at_in_seconds=1,
        ),
        "finance": UserModel(
            username=f"finance-{suffix}",
            email=f"finance-{suffix}@example.com",
            service=tenant_service,
            service_id=f"finance-{suffix}-keycloak",
            display_name="Finance",
            created_at_in_seconds=1,
            updated_at_in_seconds=1,
        ),
        "requester": UserModel(
            username=f"requester-{suffix}",
            email=f"requester-{suffix}@example.com",
            service=tenant_service,
            service_id=f"requester-{suffix}-keycloak",
            display_name="Requester",
            created_at_in_seconds=1,
            updated_at_in_seconds=1,
        ),
    }

    session.add(tenant)
    session.add_all(other_tenants)
    session.add_all(users.values())
    session.flush()
    print("Status: seed data is ready.")

    context = ExampleContext(
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        other_tenants=[
            {
                "id": other_tenant.id,
                "slug": other_tenant.slug,
                "name": other_tenant.name,
            }
            for other_tenant in other_tenants
        ],
        user_ids={role: user.id for role, user in users.items()},
        user_names={role: user.username for role, user in users.items()},
        lane_owners={
            "Manager": [users["manager"].username, users["reviewer"].username],
            "Finance": [users["finance"].username],
        },
        scenario_name=f"interactive-{suffix}",
    )

    print("Seeded rows:")
    print(
        pformat(
            {
                "tenant": {
                    "id": context.tenant_id,
                    "slug": context.tenant_slug,
                },
                "other_tenants": context.other_tenants,
                "users": {
                    role: {
                        "username": user.username,
                        "service": user.service,
                        "service_id": user.service_id,
                        "display_name": user.display_name,
                    }
                    for role, user in users.items()
                },
                "lane_owners": context.lane_owners,
            },
            sort_dicts=False,
            width=100,
        )
    )
    _pause("Press Enter to start the workflow commands.")
    return context


def _run_workflow(engine: Engine, context: ExampleContext) -> None:
    bpmn_xml = _render_conditional_approval_bpmn_xml(context.lane_owners)
    dmn_xml = EXAMPLE_DMN_PATH.read_text(encoding="utf-8")

    submission_metadata = {
        "expense_date": "2026-04-01",
        "expense_type": "Travel",
        "amount": str(SCENARIO_AMOUNT),
        "description": "Trip to LA",
    }

    definition = _run_command_step(
        engine,
        step_number=1,
        title="Import the BPMN/DMN definition",
        context_text=(
            "The example starts by storing the workflow definition in the "
            "database. The process can then be started by definition id."
        ),
        command=api.ImportBpmnProcessDefinitionCommand(
            tenant_id=context.tenant_id,
            bpmn_identifier="conditional-approval-poc",
            bpmn_name="Conditional Approval POC",
            source_bpmn_xml=bpmn_xml,
            source_dmn_xml=dmn_xml,
            properties_json={
                "version": 1,
                "flow": "conditional_approval",
                "source_bpmn_fixture": EXAMPLE_BPMN_PATH.name,
                "lane_owners": context.lane_owners,
                "scenario_name": context.scenario_name,
            },
            bpmn_version_control_type="git",
            bpmn_version_control_identifier="main",
            created_at_in_seconds=90,
            updated_at_in_seconds=90,
        ),
    )
    _print_note(
        f"Imported definition id {definition.id} for tenant {context.tenant_id}."
    )

    process_instance = _run_command_step(
        engine,
        step_number=2,
        title="Initialize the process instance from the definition",
        context_text=(
            "The requester submits an expense claim. The runtime creates the "
            "process instance, stores the submission payload, and materializes "
            "the first pending user task."
        ),
        command=api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=context.tenant_id,
            bpmn_process_definition_id=definition.id,
            process_initiator_id=context.user_ids["requester"],
            submission_metadata=submission_metadata,
            summary=f"Scenario: {context.scenario_name}",
            process_version=1,
            started_at_in_seconds=100,
            bpmn_process_id=CONDITIONAL_APPROVAL_PROCESS_ID,
        ),
    )
    _print_payload_values("Submission payload", submission_metadata)
    _print_note(
        f"Process instance {process_instance.id} is now "
        f"{process_instance.status}."
    )

    submit_tasks = _run_command_step(
        engine,
        step_number=3,
        title="List the requester pending tasks",
        context_text=(
            "The submit task should now appear in the requester worklist. "
            "This is the first user-visible step in the workflow."
        ),
        command=api.GetPendingTasksCommand(
            tenant_id=context.tenant_id,
            user_id=context.user_ids["requester"],
        ),
    )
    submit_task = _require_single_task(submit_tasks, "submit task")

    _run_command_step(
        engine,
        step_number=4,
        title="Claim the submit task",
        context_text=(
            "The requester claims the task before completing the expense "
            "submission."
        ),
        command=api.ClaimTaskCommand(
            tenant_id=context.tenant_id,
            human_task_id=submit_task.id,
            user_id=context.user_ids["requester"],
        ),
    )

    _run_command_step(
        engine,
        step_number=5,
        title="Complete the submit task",
        context_text=(
            "The requester completes the task to submit the expense claim "
            "into the workflow."
        ),
        command=api.CompleteTaskCommand(
            tenant_id=context.tenant_id,
            human_task_id=submit_task.id,
            user_id=context.user_ids["requester"],
            completed_at_in_seconds=110,
        ),
    )

    process_instance = _run_command_step(
        engine,
        step_number=6,
        title="Refresh the process instance after submission",
        context_text=(
            "After the submit task completes, the workflow should still be "
            "waiting for user input and the next review task should be ready."
        ),
        command=api.GetProcessInstanceCommand(
            tenant_id=context.tenant_id,
            process_instance_id=process_instance.id,
        ),
    )

    manager_tasks = _run_command_step(
        engine,
        step_number=7,
        title="List the manager pending tasks",
        context_text=(
            "The manager lane should now see the review task waiting to be "
            "claimed."
        ),
        command=api.GetPendingTasksCommand(
            tenant_id=context.tenant_id,
            user_id=context.user_ids["manager"],
        ),
    )
    reviewer_tasks = _run_command_step(
        engine,
        step_number=8,
        title="List the reviewer pending tasks",
        context_text=(
            "The reviewer is also in the Manager lane, so the same review task "
            "should appear in their worklist."
        ),
        command=api.GetPendingTasksCommand(
            tenant_id=context.tenant_id,
            user_id=context.user_ids["reviewer"],
        ),
    )
    manager_task = _require_single_task(manager_tasks, "manager task")
    reviewer_task = _require_single_task(reviewer_tasks, "reviewer task")
    if manager_task.id != reviewer_task.id:
        raise RuntimeError(
            "Manager and reviewer should see the same task id in the Manager lane"
        )
    _print_note(
        f"Manager and reviewer both see task id {manager_task.id} in the Manager lane."
    )

    _run_command_step(
        engine,
        step_number=9,
        title="Record the manager decision metadata",
        context_text=(
            "Before the manager completes the task, the workflow stores the "
            "decision value that will drive the next branch."
        ),
        command=api.UpsertProcessInstanceMetadataCommand(
            tenant_id=context.tenant_id,
            process_instance_id=process_instance.id,
            key="decision",
            value=MANAGER_DECISION,
            updated_at_in_seconds=112,
        ),
    )
    _print_payload_values(
        "Manager decision payload",
        {"key": "decision", "value": MANAGER_DECISION},
    )

    _run_command_step(
        engine,
        step_number=10,
        title="Claim the manager task",
        context_text="The manager claims the review task.",
        command=api.ClaimTaskCommand(
            tenant_id=context.tenant_id,
            human_task_id=manager_task.id,
            user_id=context.user_ids["manager"],
        ),
    )

    _run_command_step(
        engine,
        step_number=11,
        title="Complete the manager task",
        context_text=_manager_completion_context(),
        command=api.CompleteTaskCommand(
            tenant_id=context.tenant_id,
            human_task_id=manager_task.id,
            user_id=context.user_ids["manager"],
            completed_at_in_seconds=120,
        ),
    )

    process_instance = _run_command_step(
        engine,
        step_number=12,
        title="Refresh the process instance after the manager decision",
        context_text=(
            "This read confirms whether the workflow stopped at the manager "
            "branch or moved on to Finance."
        ),
        command=api.GetProcessInstanceCommand(
            tenant_id=context.tenant_id,
            process_instance_id=process_instance.id,
        ),
    )

    finance_task: HumanTaskModel | None = None
    if MANAGER_DECISION == "Approved" and SCENARIO_AMOUNT > 500:
        finance_tasks = _run_command_step(
            engine,
            step_number=13,
            title="List the finance pending tasks",
            context_text=(
                "The amount is above the auto-approval threshold, so the "
                "Finance lane should receive the next task."
            ),
            command=api.GetPendingTasksCommand(
                tenant_id=context.tenant_id,
                user_id=context.user_ids["finance"],
            ),
        )
        finance_task = _require_single_task(finance_tasks, "finance task")

        _run_command_step(
            engine,
            step_number=14,
            title="Record the finance decision metadata",
            context_text=(
                "The finance reviewer sets the final approval decision before "
                "completing the task."
            ),
            command=api.UpsertProcessInstanceMetadataCommand(
                tenant_id=context.tenant_id,
                process_instance_id=process_instance.id,
                key="finance_decision",
                value=FINANCE_DECISION,
                updated_at_in_seconds=123,
            ),
        )
        _print_payload_values(
            "Finance decision payload",
            {"key": "finance_decision", "value": FINANCE_DECISION},
        )

        _run_command_step(
            engine,
            step_number=15,
            title="Claim the finance task",
            context_text="The finance reviewer claims the task.",
            command=api.ClaimTaskCommand(
                tenant_id=context.tenant_id,
                human_task_id=finance_task.id,
                user_id=context.user_ids["finance"],
            ),
        )

        _run_command_step(
            engine,
            step_number=16,
            title="Complete the finance task",
            context_text=(
                f"The finance reviewer {_decision_verb(FINANCE_DECISION)} "
                "the expense claim."
            ),
            command=api.CompleteTaskCommand(
                tenant_id=context.tenant_id,
                human_task_id=finance_task.id,
                user_id=context.user_ids["finance"],
                completed_at_in_seconds=130,
            ),
        )

        process_instance = _run_command_step(
            engine,
            step_number=17,
            title="Refresh the process instance after the finance decision",
            context_text=(
                "The final read should show the workflow has reached the "
                "terminal completed state."
            ),
            command=api.GetProcessInstanceCommand(
                tenant_id=context.tenant_id,
                process_instance_id=process_instance.id,
            ),
        )
    else:
        finance_tasks = _run_command_step(
            engine,
            step_number=13,
            title="Confirm that Finance has no pending task",
            context_text=_finance_not_reached_context(),
            command=api.GetPendingTasksCommand(
                tenant_id=context.tenant_id,
                user_id=context.user_ids["finance"],
            ),
        )
        if finance_tasks:
            raise RuntimeError("Finance worklist should be empty for this branch")

    print()
    print("=" * 88)
    print("Final reads")
    print(
        "These commands verify the persisted metadata and event history using "
        "the same public API surface."
    )

    metadata_rows = _run_command_step(
        engine,
        step_number=18,
        title="Read back the process metadata",
        context_text="This confirms the submission and decision metadata persisted.",
        command=api.GetProcessInstanceMetadataCommand(
            tenant_id=context.tenant_id,
            process_instance_id=process_instance.id,
        ),
    )
    metadata_map = {item.key: item.value for item in metadata_rows}
    print("Metadata map:")
    print(pformat(metadata_map, sort_dicts=False, width=100))

    events = _run_command_step(
        engine,
        step_number=19,
        title="Read back the process event history",
        context_text=(
            "This confirms the workflow created task and completion events "
            "for the whole run."
        ),
        command=api.GetProcessInstanceEventsCommand(
            tenant_id=context.tenant_id,
            process_instance_id=process_instance.id,
        ),
    )
    print("Event types:")
    print([event.event_type for event in events])

    _print_note(
        "The example is complete. Each command was committed immediately "
        "after it ran."
    )


def _run_command_step(
    engine: Engine,
    *,
    step_number: int,
    title: str,
    context_text: str,
    command: object,
) -> Any:
    print()
    print("=" * 88)
    print(f"Step {step_number}: {title}")
    print(context_text)
    print("Command:")
    print(_format_command(command))
    _pause("Press Enter to execute this command.")
    print("Status: executing command...")
    with engine.begin() as connection:
        result = api.execute_command(connection, command)
    print("Status: command complete and committed.")
    print("Result:")
    print(_format_result(result))
    _pause("Press Enter to continue.")
    return result


def _format_result(result: Any) -> str:
    return pformat(_summarize(result), sort_dicts=False, width=100)


def _format_command(command: Any) -> str:
    if is_dataclass(command):
        payload = {
            key: _summarize_command_value(value)
            for key, value in asdict(command).items()
        }
        rendered_payload = pformat(payload, sort_dicts=False, width=100)
        return f"{command.__class__.__name__}(\n{rendered_payload}\n)"
    return repr(command)


def _format_connection_details(details: dict[str, Any]) -> str:
    return pformat(details, sort_dicts=False, width=100)


def _summarize_command_value(value: Any) -> Any:
    if isinstance(value, str):
        return _truncate_text(value)
    if isinstance(value, bytes):
        return _truncate_text(value.decode("utf-8", errors="replace"))
    if isinstance(value, dict):
        return {key: _summarize_command_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_summarize_command_value(item) for item in value]
    return value


def _truncate_text(value: str, limit: int = 160) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}... <len={len(value)}>"


def _summarize(result: Any) -> Any:
    if isinstance(result, list):
        return [_summarize(item) for item in result]
    if isinstance(result, BpmnProcessDefinitionModel):
        return {
            "id": result.id,
            "identifier": result.bpmn_identifier,
            "name": result.bpmn_name,
            "properties_json": result.properties_json,
            "source_bpmn_xml_length": len(result.source_bpmn_xml or ""),
            "source_dmn_xml_length": len(result.source_dmn_xml or ""),
            "version_control_type": result.bpmn_version_control_type,
            "version_control_identifier": result.bpmn_version_control_identifier,
        }
    if isinstance(result, ProcessInstanceModel):
        workflow_state_json = result.workflow_state_json or ""
        return {
            "id": result.id,
            "status": result.status,
            "summary": result.summary,
            "process_initiator_id": result.process_initiator_id,
            "definition_id": result.bpmn_process_definition_id,
            "process_version": result.process_version,
            "start_in_seconds": result.start_in_seconds,
            "end_in_seconds": result.end_in_seconds,
            "workflow_state_json_present": bool(workflow_state_json),
            "workflow_state_json_length": len(workflow_state_json),
        }
    if isinstance(result, HumanTaskModel):
        return {
            "id": result.id,
            "task_name": result.task_name,
            "task_title": result.task_title,
            "task_status": result.task_status,
            "lane_name": result.lane_name,
            "lane_assignment_id": result.lane_assignment_id,
            "actual_owner_id": result.actual_owner_id,
            "completed_by_user_id": result.completed_by_user_id,
            "completed": result.completed,
            "json_metadata_keys": sorted((result.json_metadata or {}).keys()),
        }
    if isinstance(result, ProcessInstanceMetadataModel):
        return {
            "id": result.id,
            "key": result.key,
            "value": result.value,
            "updated_at_in_seconds": result.updated_at_in_seconds,
            "created_at_in_seconds": result.created_at_in_seconds,
        }
    if isinstance(result, ProcessInstanceEventModel):
        return {
            "id": result.id,
            "event_type": result.event_type,
            "task_guid": result.task_guid,
            "timestamp": str(result.timestamp),
            "user_id": result.user_id,
        }
    if isinstance(result, UserModel):
        return {
            "id": result.id,
            "username": result.username,
            "email": result.email,
            "service": result.service,
            "service_id": result.service_id,
            "display_name": result.display_name,
            "created_at_in_seconds": result.created_at_in_seconds,
            "updated_at_in_seconds": result.updated_at_in_seconds,
        }
    if isinstance(result, M8flowTenantModel):
        return {
            "id": result.id,
            "name": result.name,
            "slug": result.slug,
        }
    return result


def _require_single_task(tasks: list[HumanTaskModel], label: str) -> HumanTaskModel:
    if len(tasks) != 1:
        raise RuntimeError(f"Expected exactly one {label}, got {len(tasks)}")
    return tasks[0]


def _pause(prompt: str) -> None:
    input(f"{prompt} ")


def _print_note(message: str) -> None:
    print(message)


def _print_payload_values(label: str, payload: Any) -> None:
    print("=" * 88)
    print(f"****** {label} ******")
    print(pformat(payload, sort_dicts=False, width=100))


def _describe_workflow_mode() -> str:
    if MANAGER_DECISION == "Rejected":
        return (
            "It demonstrates the manager rejection path. Change "
            "SCENARIO_AMOUNT, MANAGER_DECISION, or FINANCE_DECISION near the "
            "top of the file to try other branches."
        )
    if SCENARIO_AMOUNT <= 500:
        return (
            "It demonstrates the auto-approved path. Change "
            "SCENARIO_AMOUNT, MANAGER_DECISION, or FINANCE_DECISION near the "
            "top of the file to try other branches."
        )
    if FINANCE_DECISION == "Rejected":
        return (
            "It follows the manager approval path with a finance rejection. "
            "Change SCENARIO_AMOUNT, MANAGER_DECISION, or FINANCE_DECISION "
            "near the top of the file to try other branches."
        )
    return (
        "It defaults to the full manager + finance approval path. Change "
        "SCENARIO_AMOUNT, MANAGER_DECISION, or FINANCE_DECISION near the top "
        "of the file to try other branches."
    )


def _decision_verb(decision: str) -> str:
    if decision.strip().lower() == "rejected":
        return "rejects"
    return "approves"


def _manager_completion_context() -> str:
    if MANAGER_DECISION == "Rejected":
        return (
            "The manager rejects the claim. The workflow ends here and the "
            "Finance lane is not reached."
        )
    if SCENARIO_AMOUNT <= 500:
        return (
            "The manager approves the claim. Because the amount is at or "
            "below the auto-approval threshold, the workflow ends here."
        )
    return (
        "The manager approves the claim. Because the amount is above the "
        "auto-approval threshold, the Finance lane will be activated next."
    )


def _finance_not_reached_context() -> str:
    if MANAGER_DECISION == "Rejected":
        return (
            "The manager rejected the claim, so the workflow ends before the "
            "Finance lane is reached."
        )
    return (
        "The amount is at or below the auto-approval threshold, so the "
        "workflow ends before the Finance lane is reached."
    )


def _render_conditional_approval_bpmn_xml(
    lane_owners: dict[str, list[str]],
) -> str:
    bpmn_xml = EXAMPLE_BPMN_PATH.read_text(encoding="utf-8")
    lane_owners_script = (
        "<bpmn:script>lane_owners = {\n"
        f"    \"Manager\" : {lane_owners['Manager']!r},\n"
        f"    \"Finance\" : {lane_owners['Finance']!r}\n"
        "}</bpmn:script>"
    )
    return re.sub(
        r"<bpmn:script>.*?</bpmn:script>",
        lane_owners_script,
        bpmn_xml,
        count=1,
        flags=re.DOTALL,
    )


if __name__ == "__main__":
    main()
