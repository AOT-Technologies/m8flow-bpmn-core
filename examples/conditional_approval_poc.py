from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from pprint import pformat
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.db import build_engine, create_schema
from m8flow_bpmn_core.models.bpmn_process import BpmnProcessModel
from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_bpmn_core.models.human_task import HumanTaskModel
from m8flow_bpmn_core.models.human_task_user import HumanTaskUserModel
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel
from m8flow_bpmn_core.models.process_instance_event import (
    ProcessInstanceEventModel,
)
from m8flow_bpmn_core.models.process_instance_metadata import (
    ProcessInstanceMetadataModel,
)
from m8flow_bpmn_core.models.task import TaskModel
from m8flow_bpmn_core.models.task_definition import TaskDefinitionModel
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_BPMN_PATH = REPO_ROOT / "tests" / "fixtures" / "conditional-approval.bpmn"
EXAMPLE_DMN_PATH = REPO_ROOT / "tests" / "fixtures" / "check_eligibility.dmn"
NOISE_BPMN_PATH = REPO_ROOT / "tests" / "fixtures" / "invoice_approval_poc.bpmn"

CONDITIONAL_APPROVAL_PROCESS_ID = "Process_conditional_approval_8qpy9gh"
CONDITIONAL_APPROVAL_TASK_IDS = {
    "submit": "Activity_0qoxmh9",
    "manager_review": "Activity_0b1dd0g",
    "finance_review": "Activity_1uha89x",
}

SCENARIO_AMOUNT = 1_500
MANAGER_DECISION = "Approved"
FINANCE_DECISION = "Approved"
SECTION_SEPARATOR = "=" * 88
DEFAULT_LOCAL_DATABASE_URL = (
    "postgresql+psycopg://postgres:postgres@localhost:6843/postgres"
)
SHARED_POSTGRES_DATABASE_NAME = "postgres"

DEMO_TENANT = {
    "id": "tenant-conditional-approval-example",
    "name": "Conditional Approval Example",
    "slug": "conditional-approval-example",
}
OTHER_DEMO_TENANTS = [
    {
        "id": "tenant-conditional-approval-noise-a",
        "name": "Conditional Approval Noise A",
        "slug": "conditional-approval-noise-a",
    },
    {
        "id": "tenant-conditional-approval-noise-b",
        "name": "Conditional Approval Noise B",
        "slug": "conditional-approval-noise-b",
    },
]
DEMO_USERS = {
    "manager": {
        "username": "poc-manager",
        "email": "poc-manager@example.com",
        "service_id": "poc-manager-keycloak",
        "display_name": "Manager",
    },
    "reviewer": {
        "username": "poc-reviewer",
        "email": "poc-reviewer@example.com",
        "service_id": "poc-reviewer-keycloak",
        "display_name": "Reviewer",
    },
    "finance": {
        "username": "poc-finance",
        "email": "poc-finance@example.com",
        "service_id": "poc-finance-keycloak",
        "display_name": "Finance",
    },
    "requester": {
        "username": "poc-requester",
        "email": "poc-requester@example.com",
        "service_id": "poc-requester-keycloak",
        "display_name": "Requester",
    },
    "observer": {
        "username": "poc-observer",
        "email": "poc-observer@example.com",
        "service_id": "poc-observer-keycloak",
        "display_name": "Observer",
    },
}
FOREIGN_NOISE_USER = {
    "username": "poc-foreign-noise",
    "email": "poc-foreign-noise@example.com",
    "service_id": "poc-foreign-noise-keycloak",
    "display_name": "Foreign Noise",
}


@dataclass(frozen=True, slots=True)
class ExampleContext:
    tenant_id: str
    tenant_slug: str
    other_tenants: list[dict[str, str]]
    user_ids: dict[str, int]
    user_names: dict[str, str]
    lane_owners: dict[str, list[str]]
    noise_user_ids: dict[str, int]
    noise_tenant_ids: dict[str, str]
    noise_process_instance_ids: dict[str, int]
    noise_task_ids: dict[str, int]
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
    print(SECTION_SEPARATOR)
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
    _confirm_shared_database_usage(database_url, display_database_url)
    _pause("Press Enter to start the example.")

    print()
    print(SECTION_SEPARATOR)
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

        _run_isolation_checks(engine, context)
        _run_workflow(engine, context)
    except KeyboardInterrupt:
        print("\nInterrupted. The current step was rolled back.")
    finally:
        engine.dispose()


def _resolve_database_url() -> tuple[str, str]:
    raw_url = os.getenv("M8FLOW_EXAMPLE_DATABASE_URL") or DEFAULT_LOCAL_DATABASE_URL
    try:
        url = make_url(raw_url)
    except Exception:
        return raw_url, raw_url

    if url.get_backend_name().startswith("postgresql"):
        query = dict(url.query)
        query.setdefault("connect_timeout", "1")
        url = url.set(query=query)

    return (
        url.render_as_string(hide_password=False),
        url.render_as_string(hide_password=True),
    )


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


def _confirm_shared_database_usage(
    database_url: str,
    display_database_url: str,
) -> None:
    try:
        url = make_url(database_url)
    except Exception:
        return

    if (url.database or "").lower() != SHARED_POSTGRES_DATABASE_NAME:
        return

    print()
    print(SECTION_SEPARATOR)
    print("Shared database confirmation")
    print(
        "This run will use the existing Postgres database behind the local "
        f"m8flow instance: {display_database_url}"
    )
    print(
        "The example keeps the demo tenant, users, process definitions, "
        "process instances, and tasks in place so you can inspect them in "
        "the m8flow UI."
    )
    print(
        "If the seed rows already exist, the example will reuse them and "
        "print warnings instead of failing."
    )
    response = input("Continue with the shared database? [Y/n] ").strip().lower()
    if response in {"", "y", "yes"}:
        return
    raise SystemExit(
        "Cancelled. Set M8FLOW_EXAMPLE_DATABASE_URL to an isolated database if "
        "you want a disposable run."
    )


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
        f"the database did not become available within {timeout_seconds} seconds"
    ) from last_error


def _write_spinner(message: str, spinner_char: str) -> None:
    sys.stdout.write(f"\r{message} {spinner_char}")
    sys.stdout.flush()


def _clear_spinner(message: str) -> None:
    sys.stdout.write("\r" + " " * (len(message) + 2) + "\r")
    sys.stdout.flush()


def _seed_demo_context(session: Session) -> ExampleContext:
    print()
    print(SECTION_SEPARATOR)
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
    print(
        "It also seeds a same-tenant noise task and a foreign-tenant noise "
        "task so the next verification steps can show that user worklists "
        "stay isolated and lane assignments are respected."
    )
    _pause("Press Enter to seed the demo data.")

    print("Status: seeding tenant and users...")
    warnings: list[str] = []
    tenant = _get_or_create_tenant(
        session,
        tenant_id=DEMO_TENANT["id"],
        name=DEMO_TENANT["name"],
        slug=DEMO_TENANT["slug"],
        warnings=warnings,
    )
    tenant_service = f"http://localhost:7002/realms/{tenant.slug}"
    other_tenants = [
        _get_or_create_tenant(
            session,
            tenant_id=tenant_spec["id"],
            name=tenant_spec["name"],
            slug=tenant_spec["slug"],
            warnings=warnings,
        )
        for tenant_spec in OTHER_DEMO_TENANTS
    ]
    users = {
        role: _get_or_create_user(
            session,
            service=tenant_service,
            username=user_spec["username"],
            email=user_spec["email"],
            service_id=user_spec["service_id"],
            display_name=user_spec["display_name"],
            warnings=warnings,
        )
        for role, user_spec in DEMO_USERS.items()
    }
    foreign_noise_tenant = other_tenants[0]
    foreign_noise_service = f"http://localhost:7002/realms/{foreign_noise_tenant.slug}"
    foreign_noise_user = _get_or_create_user(
        session,
        service=foreign_noise_service,
        username=FOREIGN_NOISE_USER["username"],
        email=FOREIGN_NOISE_USER["email"],
        service_id=FOREIGN_NOISE_USER["service_id"],
        display_name=FOREIGN_NOISE_USER["display_name"],
        warnings=warnings,
    )

    observer_noise_process_instance, observer_noise_task = _seed_noise_work_item(
        session,
        tenant=tenant,
        user=users["observer"],
        label="observer-noise",
        process_display_name="Observer Noise Example",
        task_title="Observer Noise Task",
        lane_name="Noise Lane",
        created_at_in_seconds=1_010,
        warnings=warnings,
    )
    foreign_noise_process_instance, foreign_noise_task = _seed_noise_work_item(
        session,
        tenant=foreign_noise_tenant,
        user=foreign_noise_user,
        label="foreign-noise",
        process_display_name="Foreign Noise Example",
        task_title="Foreign Noise Task",
        lane_name="Noise Lane",
        created_at_in_seconds=1_020,
        warnings=warnings,
    )
    print("Status: seed data is ready.")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")

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
        noise_user_ids={
            "foreign_noise": foreign_noise_user.id,
        },
        noise_tenant_ids={
            "foreign_noise": foreign_noise_tenant.id,
        },
        noise_process_instance_ids={
            "observer": observer_noise_process_instance.id,
            "foreign_noise": foreign_noise_process_instance.id,
        },
        noise_task_ids={
            "observer": observer_noise_task.id,
            "foreign_noise": foreign_noise_task.id,
        },
        scenario_name=f"interactive-{round(time.time())}",
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
                }
                | {
                    "foreign_noise": {
                        "username": foreign_noise_user.username,
                        "service": foreign_noise_user.service,
                        "service_id": foreign_noise_user.service_id,
                        "display_name": foreign_noise_user.display_name,
                    }
                },
                "lane_owners": context.lane_owners,
                "noise_tasks": {
                    "observer": {
                        "process_instance_id": observer_noise_process_instance.id,
                        "task_id": observer_noise_task.id,
                        "owner_username": users["observer"].username,
                    },
                    "foreign_noise": {
                        "tenant_id": foreign_noise_tenant.id,
                        "process_instance_id": foreign_noise_process_instance.id,
                        "task_id": foreign_noise_task.id,
                        "owner_username": foreign_noise_user.username,
                    },
                },
            },
            sort_dicts=False,
            width=100,
        )
    )
    _pause("Press Enter to start the workflow commands.")
    return context


def _get_or_create_tenant(
    session: Session,
    *,
    tenant_id: str,
    name: str,
    slug: str,
    warnings: list[str],
) -> M8flowTenantModel:
    tenant = session.scalar(
        select(M8flowTenantModel).where(M8flowTenantModel.slug == slug)
    )
    if tenant is None:
        tenant = M8flowTenantModel(
            id=tenant_id,
            name=name,
            slug=slug,
        )
        session.add(tenant)
        session.flush()
        return tenant

    warnings.append(f"Tenant '{slug}' already exists; reusing id '{tenant.id}'.")
    if tenant.name != name:
        tenant.name = name
    session.flush()
    return tenant


def _get_or_create_user(
    session: Session,
    *,
    service: str,
    username: str,
    email: str,
    service_id: str,
    display_name: str,
    warnings: list[str],
) -> UserModel:
    user = session.scalar(
        select(UserModel).where(
            UserModel.service == service,
            UserModel.service_id == service_id,
        )
    )
    if user is None:
        user = UserModel(
            username=username,
            email=email,
            service=service,
            service_id=service_id,
            display_name=display_name,
            created_at_in_seconds=1,
            updated_at_in_seconds=1,
        )
        session.add(user)
        session.flush()
        return user

    warnings.append(
        f"User '{username}' already exists for service '{service}'; "
        f"reusing id {user.id}."
    )
    user.username = username
    user.email = email
    user.display_name = display_name
    user.updated_at_in_seconds = 1
    session.flush()
    return user


def _seed_noise_work_item(
    session: Session,
    *,
    tenant: M8flowTenantModel,
    user: UserModel,
    label: str,
    process_display_name: str,
    task_title: str,
    lane_name: str,
    created_at_in_seconds: int,
    warnings: list[str],
) -> tuple[ProcessInstanceModel, HumanTaskModel]:
    task_guid = f"{label}-task"
    existing_task = session.get(TaskModel, task_guid)
    if existing_task is not None:
        process_instance = session.get(
            ProcessInstanceModel,
            existing_task.process_instance_id,
        )
        if process_instance is None:
            raise RuntimeError(
                f"Noise task '{label}' exists without a process instance."
            )

        human_task = session.scalar(
            select(HumanTaskModel).where(
                HumanTaskModel.m8f_tenant_id == tenant.id,
                HumanTaskModel.task_guid == task_guid,
            )
        )
        if human_task is None:
            human_task = HumanTaskModel(
                m8f_tenant_id=tenant.id,
                process_instance_id=process_instance.id,
                task_guid=task_guid,
                lane_assignment_id=None,
                completed_by_user_id=None,
                actual_owner_id=None,
                task_name=f"{label.replace('-', '_')}_task",
                task_title=task_title,
                task_type="UserTask",
                task_status="READY",
                process_model_display_name=process_display_name,
                bpmn_process_identifier=f"{label}-process",
                lane_name=lane_name,
                json_metadata={"noise": True, "label": label},
                completed=False,
            )
            session.add(human_task)
            session.flush()

        _reset_noise_work_item(
            session,
            tenant_id=tenant.id,
            user_id=user.id,
            task=existing_task,
            human_task=human_task,
            process_instance=process_instance,
            label=label,
            process_display_name=process_display_name,
            task_title=task_title,
            lane_name=lane_name,
        )
        warnings.append(
            f"Noise task '{label}' already existed; it was reused and reset to READY."
        )
        return process_instance, human_task

    source_bpmn_xml = NOISE_BPMN_PATH.read_text(encoding="utf-8")
    bpmn_identifier = f"{label}-process"
    task_identifier = f"{label.replace('-', '_')}_task"

    definition = BpmnProcessDefinitionModel(
        m8f_tenant_id=tenant.id,
        single_process_hash=f"{label}-single",
        full_process_model_hash=f"{label}-full",
        bpmn_identifier=bpmn_identifier,
        bpmn_name=process_display_name,
        properties_json={"version": 1, "noise": True, "label": label},
        bpmn_version_control_type="git",
        bpmn_version_control_identifier="main",
        created_at_in_seconds=created_at_in_seconds - 10,
        updated_at_in_seconds=created_at_in_seconds - 10,
    )
    definition.source_bpmn_xml = source_bpmn_xml
    session.add(definition)
    session.flush()

    bpmn_process = BpmnProcessModel(
        m8f_tenant_id=tenant.id,
        guid=f"{label}-bpmn-process",
        bpmn_process_definition_id=definition.id,
        top_level_process_id=None,
        direct_parent_process_id=None,
        properties_json={"root": task_identifier},
        json_data_hash=f"{label}-process-json",
    )
    session.add(bpmn_process)
    session.flush()

    task_definition = TaskDefinitionModel(
        m8f_tenant_id=tenant.id,
        bpmn_process_definition_id=definition.id,
        bpmn_identifier=task_identifier,
        bpmn_name=task_title,
        typename="UserTask",
        properties_json={"allowGuest": False, "noise": True},
        created_at_in_seconds=created_at_in_seconds - 5,
        updated_at_in_seconds=created_at_in_seconds - 5,
    )
    session.add(task_definition)
    session.flush()

    process_instance = ProcessInstanceModel(
        m8f_tenant_id=tenant.id,
        process_model_identifier=bpmn_identifier,
        process_model_display_name=process_display_name,
        process_initiator_id=user.id,
        bpmn_process_definition_id=definition.id,
        bpmn_process_id=bpmn_process.id,
        status="running",
        created_at_in_seconds=created_at_in_seconds,
        updated_at_in_seconds=created_at_in_seconds,
    )
    session.add(process_instance)
    session.flush()

    task = TaskModel(
        m8f_tenant_id=tenant.id,
        guid=f"{label}-task",
        bpmn_process_id=bpmn_process.id,
        process_instance_id=process_instance.id,
        task_definition_id=task_definition.id,
        state="READY",
        properties_json={"task_spec": task_title, "noise": True},
        json_data_hash=f"{label}-task-json",
        python_env_data_hash=f"{label}-task-env",
    )
    session.add(task)
    session.flush()

    human_task = HumanTaskModel(
        m8f_tenant_id=tenant.id,
        process_instance_id=process_instance.id,
        task_guid=task.guid,
        lane_assignment_id=None,
        completed_by_user_id=None,
        actual_owner_id=None,
        task_name=task_identifier,
        task_title=task_title,
        task_type="UserTask",
        task_status="READY",
        process_model_display_name=process_display_name,
        bpmn_process_identifier=bpmn_identifier,
        lane_name=lane_name,
        json_metadata={"noise": True, "label": label},
        completed=False,
    )
    session.add(human_task)
    session.flush()

    session.add(
        HumanTaskUserModel(
            m8f_tenant_id=tenant.id,
            human_task_id=human_task.id,
            user_id=user.id,
            added_by="manual",
        )
    )
    session.flush()

    return process_instance, human_task


def _reset_noise_work_item(
    session: Session,
    *,
    tenant_id: str,
    user_id: int,
    task: TaskModel,
    human_task: HumanTaskModel,
    process_instance: ProcessInstanceModel,
    label: str,
    process_display_name: str,
    task_title: str,
    lane_name: str,
) -> None:
    task_identifier = f"{label.replace('-', '_')}_task"
    task.state = "READY"
    task.properties_json = {"task_spec": task_title, "noise": True}
    task.start_in_seconds = None
    task.end_in_seconds = None

    process_instance.status = "running"
    process_instance.process_model_identifier = f"{label}-process"
    process_instance.process_model_display_name = process_display_name
    process_instance.end_in_seconds = None

    human_task.completed = False
    human_task.completed_by_user_id = None
    human_task.actual_owner_id = None
    human_task.task_name = task_identifier
    human_task.task_title = task_title
    human_task.task_status = "READY"
    human_task.task_type = "UserTask"
    human_task.process_model_display_name = process_display_name
    human_task.bpmn_process_identifier = f"{label}-process"
    human_task.lane_name = lane_name
    human_task.json_metadata = {"noise": True, "label": label}

    assignment = session.scalar(
        select(HumanTaskUserModel).where(
            HumanTaskUserModel.m8f_tenant_id == tenant_id,
            HumanTaskUserModel.human_task_id == human_task.id,
            HumanTaskUserModel.user_id == user_id,
        )
    )
    if assignment is None:
        session.add(
            HumanTaskUserModel(
                m8f_tenant_id=tenant_id,
                human_task_id=human_task.id,
                user_id=user_id,
                added_by="manual",
            )
        )
    session.flush()


def _run_workflow(engine: Engine, context: ExampleContext) -> None:
    bpmn_xml = _render_conditional_approval_bpmn_xml(context.lane_owners)
    dmn_xml = EXAMPLE_DMN_PATH.read_text(encoding="utf-8")

    submission_payload = {
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
            "The requester starts the workflow. The runtime creates the "
            "process instance and materializes the first pending user task."
        ),
        command=api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=context.tenant_id,
            bpmn_process_definition_id=definition.id,
            process_initiator_id=context.user_ids["requester"],
            summary=f"Scenario: {context.scenario_name}",
            process_version=1,
            started_at_in_seconds=100,
            bpmn_process_id=CONDITIONAL_APPROVAL_PROCESS_ID,
        ),
    )
    _print_note(
        f"Process instance {process_instance.id} is now {process_instance.status}."
    )

    submit_tasks = _run_command_step(
        engine,
        step_number=3,
        title="List the requester pending tasks",
        context_text=(
            "The submit task should now appear in the requester worklist. "
            "This is the first user-visible step in the workflow."
        ),
        command=api.GetPendingTasksQuery(
            tenant_id=context.tenant_id,
            user_id=context.user_ids["requester"],
        ),
    )
    submit_task = _require_single_task(
        submit_tasks,
        "submit task",
        process_instance_id=process_instance.id,
        task_name=CONDITIONAL_APPROVAL_TASK_IDS["submit"],
    )

    _run_command_step(
        engine,
        step_number=4,
        title="Claim the submit task",
        context_text=(
            "The requester claims the task before completing the expense submission."
        ),
        command=api.ClaimTaskCommand(
            tenant_id=context.tenant_id,
            human_task_id=submit_task.id,
            user_id=context.user_ids["requester"],
        ),
    )

    _print_payload_values("Submission payload", submission_payload)

    _run_command_step(
        engine,
        step_number=5,
        title="Complete the submit task",
        context_text=(
            "The requester completes the task to submit the expense claim "
            "into the workflow. The payload is attached to this task "
            "completion so it is persisted with the claimed task."
        ),
        command=api.CompleteTaskCommand(
            tenant_id=context.tenant_id,
            human_task_id=submit_task.id,
            user_id=context.user_ids["requester"],
            completed_at_in_seconds=110,
            task_payload=submission_payload,
        ),
    )
    _print_note(
        "Negative check: once the submit task is completed, it should not "
        "appear in any main workflow worklist."
    )
    for username in (
        "requester",
        "manager",
        "reviewer",
        "finance",
        "observer",
    ):
        other_tasks = api.execute_query(
            engine,
            api.GetPendingTasksQuery(
                tenant_id=context.tenant_id,
                user_id=context.user_ids[username],
            ),
        )
        if submit_task.id in [task.id for task in other_tasks]:
            raise RuntimeError(f"Submit task leaked into the {username} worklist")

    process_instance = _run_command_step(
        engine,
        step_number=6,
        title="Refresh the process instance after submission",
        context_text=(
            "After the submit task completes, the workflow should still be "
            "waiting for user input and the next review task should be ready."
        ),
        command=api.GetProcessInstanceQuery(
            tenant_id=context.tenant_id,
            process_instance_id=process_instance.id,
        ),
    )

    manager_tasks = _run_command_step(
        engine,
        step_number=7,
        title="List the manager pending tasks",
        context_text=(
            "The manager lane should now see the review task waiting to be claimed."
        ),
        command=api.GetPendingTasksQuery(
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
        command=api.GetPendingTasksQuery(
            tenant_id=context.tenant_id,
            user_id=context.user_ids["reviewer"],
        ),
    )
    manager_task = _require_single_task(
        manager_tasks,
        "manager task",
        process_instance_id=process_instance.id,
        task_name=CONDITIONAL_APPROVAL_TASK_IDS["manager_review"],
    )
    reviewer_task = _require_single_task(
        reviewer_tasks,
        "reviewer task",
        process_instance_id=process_instance.id,
        task_name=CONDITIONAL_APPROVAL_TASK_IDS["manager_review"],
    )
    if manager_task.id != reviewer_task.id:
        raise RuntimeError(
            "Manager and reviewer should see the same task id in the Manager lane"
        )
    _print_note(
        f"Manager and reviewer both see task id {manager_task.id} in the Manager lane."
    )

    _print_payload_values(
        "Manager decision payload",
        {"decision": MANAGER_DECISION},
    )

    _run_command_step(
        engine,
        step_number=9,
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
        step_number=10,
        title="Complete the manager task",
        context_text=_manager_completion_context(),
        command=api.CompleteTaskCommand(
            tenant_id=context.tenant_id,
            human_task_id=manager_task.id,
            user_id=context.user_ids["manager"],
            completed_at_in_seconds=120,
            task_payload={"decision": MANAGER_DECISION},
        ),
    )
    _print_note(
        "Negative check: the manager task should stay out of the Requester, "
        "Manager, Reviewer, Finance, and Observer worklists."
    )
    for username in (
        "requester",
        "manager",
        "reviewer",
        "finance",
        "observer",
    ):
        other_tasks = api.execute_query(
            engine,
            api.GetPendingTasksQuery(
                tenant_id=context.tenant_id,
                user_id=context.user_ids[username],
            ),
        )
        if manager_task.id in [task.id for task in other_tasks]:
            raise RuntimeError(f"Manager task leaked into the {username} worklist")

    process_instance = _run_command_step(
        engine,
        step_number=11,
        title="Refresh the process instance after the manager decision",
        context_text=(
            "This read confirms whether the workflow stopped at the manager "
            "branch or moved on to Finance."
        ),
        command=api.GetProcessInstanceQuery(
            tenant_id=context.tenant_id,
            process_instance_id=process_instance.id,
        ),
    )

    finance_task: HumanTaskModel | None = None
    if MANAGER_DECISION == "Approved" and SCENARIO_AMOUNT > 500:
        finance_tasks = _run_command_step(
            engine,
            step_number=12,
            title="List the finance pending tasks",
            context_text=(
                "The amount is above the auto-approval threshold, so the "
                "Finance lane should receive the next task."
            ),
            command=api.GetPendingTasksQuery(
                tenant_id=context.tenant_id,
                user_id=context.user_ids["finance"],
            ),
        )
        finance_task = _require_single_task(
            finance_tasks,
            "finance task",
            process_instance_id=process_instance.id,
            task_name=CONDITIONAL_APPROVAL_TASK_IDS["finance_review"],
        )

        _print_payload_values(
            "Finance decision payload",
            {"finance_decision": FINANCE_DECISION},
        )

        _run_command_step(
            engine,
            step_number=13,
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
            step_number=14,
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
                task_payload={"finance_decision": FINANCE_DECISION},
            ),
        )
        _print_note(
            "Negative check: the finance task should stay out of the "
            "Requester, Manager, Reviewer, Finance, and Observer worklists."
        )
        for username in (
            "requester",
            "manager",
            "reviewer",
            "finance",
            "observer",
        ):
            other_tasks = api.execute_query(
                engine,
                api.GetPendingTasksQuery(
                    tenant_id=context.tenant_id,
                    user_id=context.user_ids[username],
                ),
            )
            if finance_task.id in [task.id for task in other_tasks]:
                raise RuntimeError(f"Finance task leaked into the {username} worklist")

        process_instance = _run_command_step(
            engine,
            step_number=15,
            title="Refresh the process instance after the finance decision",
            context_text=(
                "The final read should show the workflow has reached the "
                "terminal completed state."
            ),
            command=api.GetProcessInstanceQuery(
                tenant_id=context.tenant_id,
                process_instance_id=process_instance.id,
            ),
        )
    else:
        finance_tasks = _run_command_step(
            engine,
            step_number=12,
            title="Confirm that Finance has no pending task",
            context_text=_finance_not_reached_context(),
            command=api.GetPendingTasksQuery(
                tenant_id=context.tenant_id,
                user_id=context.user_ids["finance"],
            ),
        )
        matching_finance_tasks = _matching_tasks(
            finance_tasks,
            process_instance_id=process_instance.id,
            task_name=CONDITIONAL_APPROVAL_TASK_IDS["finance_review"],
        )
        if matching_finance_tasks:
            raise RuntimeError(
                "Finance worklist should not contain a task for the current branch"
            )

    print()
    print(SECTION_SEPARATOR)
    print("Final reads")
    print(
        "These commands verify the persisted metadata and event history using "
        "the same public API surface."
    )

    metadata_rows = _run_command_step(
        engine,
        step_number=16,
        title="Read back the process metadata",
        context_text="This confirms the submission and decision metadata persisted.",
        command=api.GetProcessInstanceMetadataQuery(
            tenant_id=context.tenant_id,
            process_instance_id=process_instance.id,
        ),
    )
    metadata_map = {item.key: item.value for item in metadata_rows}
    print("Metadata map:")
    print(pformat(metadata_map, sort_dicts=False, width=100))

    events = _run_command_step(
        engine,
        step_number=17,
        title="Read back the process event history",
        context_text=(
            "This confirms the workflow created task and completion events "
            "for the whole run."
        ),
        command=api.GetProcessInstanceEventsQuery(
            tenant_id=context.tenant_id,
            process_instance_id=process_instance.id,
        ),
    )
    print("Event types:")
    print([event.event_type for event in events])

    _print_note(
        "The example is complete. Each command was committed immediately after it ran."
    )


def _run_isolation_checks(engine: Engine, context: ExampleContext) -> None:
    print()
    print(SECTION_SEPARATOR)
    print("Verification: noise users, tasks, and tenant isolation")
    print(
        "These checks are intentionally noisy. They prove that extra users "
        "and tasks in the database do not leak across tenants and that "
        "pending-task visibility still depends on assignment."
    )
    _pause("Press Enter to run the isolation checks.")

    observer_tasks = _run_command_step(
        engine,
        step_number=1,
        title="Show the observer noise task",
        context_text=(
            "The observer belongs to the main tenant and has a dedicated "
            "noise task assigned to them. This proves a user can see their "
            "own worklist items without affecting the approval workflow."
        ),
        command=api.GetPendingTasksQuery(
            tenant_id=context.tenant_id,
            user_id=context.user_ids["observer"],
        ),
        prefix="Verification",
    )
    _require_single_task(
        observer_tasks,
        "observer noise task",
        task_id=context.noise_task_ids["observer"],
    )

    manager_tasks = _run_command_step(
        engine,
        step_number=2,
        title="Show that the manager has no noise task yet",
        context_text=(
            "The manager belongs to the same tenant but should not see the "
            "observer's unrelated noise task. This is the negative case "
            "that shows assignment still matters."
        ),
        command=api.GetPendingTasksQuery(
            tenant_id=context.tenant_id,
            user_id=context.user_ids["manager"],
        ),
        prefix="Verification",
    )
    if any(task.id == context.noise_task_ids["observer"] for task in manager_tasks):
        raise RuntimeError("Manager should not see the observer noise task")

    _run_command_step(
        engine,
        step_number=3,
        title="Reject cross-tenant worklist access",
        context_text=(
            "A user from the foreign noise tenant should not be able to "
            "read the main tenant worklist. This is expected to fail with "
            "a PermissionError."
        ),
        command=api.GetPendingTasksQuery(
            tenant_id=context.tenant_id,
            user_id=context.noise_user_ids["foreign_noise"],
        ),
        prefix="Verification",
        expected_failure=api.AuthorizationError,
        expected_failure_contains="does not belong to tenant",
    )

    foreign_tasks = _run_command_step(
        engine,
        step_number=4,
        title="Show the foreign tenant noise task",
        context_text=(
            "The foreign tenant has its own noise task. Seeing it here shows "
            "that tenant-scoped data stays visible to the correct tenant and "
            "user only."
        ),
        command=api.GetPendingTasksQuery(
            tenant_id=context.noise_tenant_ids["foreign_noise"],
            user_id=context.noise_user_ids["foreign_noise"],
        ),
        prefix="Verification",
    )
    _require_single_task(
        foreign_tasks,
        "foreign noise task",
        task_id=context.noise_task_ids["foreign_noise"],
    )

    _run_command_step(
        engine,
        step_number=5,
        title="Reject cross-tenant process-instance access",
        context_text=(
            "The foreign tenant must not be able to read the main tenant "
            "process instance. This is expected to fail with a NotFoundError."
        ),
        command=api.GetProcessInstanceQuery(
            tenant_id=context.noise_tenant_ids["foreign_noise"],
            process_instance_id=context.noise_process_instance_ids["observer"],
        ),
        prefix="Verification",
        expected_failure=api.NotFoundError,
        expected_failure_contains="was not found for tenant",
    )


def _run_command_step(
    engine: Engine,
    *,
    step_number: int,
    title: str,
    context_text: str,
    command: object,
    prefix: str = "Step",
    expected_failure: type[Exception] | tuple[type[Exception], ...] | None = None,
    expected_failure_contains: str | None = None,
) -> Any | None:
    print()
    print(SECTION_SEPARATOR)
    print(f"{prefix} {step_number}: {title}")
    print(context_text)
    print(SECTION_SEPARATOR)
    print("Command:")
    print(_format_command(command))
    print(SECTION_SEPARATOR)
    _pause("Press Enter to execute this command.")
    print("Status: executing command...")
    is_query = type(command).__name__.endswith("Query")
    try:
        with engine.begin() as connection:
            if is_query:
                result = api.execute_query(connection, command)
            else:
                result = api.execute_command(connection, command)
    except Exception as exc:
        if expected_failure is None or not isinstance(exc, expected_failure):
            raise
        if (
            expected_failure_contains is not None
            and expected_failure_contains not in str(exc)
        ):
            raise RuntimeError(
                f"The command failed, but not with the expected message: {exc}"
            ) from exc
        print(f"Status: expected {type(exc).__name__} was raised.")
        print("Result:")
        print(f"{type(exc).__name__}: {exc}")
        _pause("Press Enter to continue.")
        return None

    if expected_failure is not None:
        raise RuntimeError(
            "The command succeeded, but a failure was expected: "
            f"{_format_command(command)}"
        )

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
        public_properties = {
            key: value
            for key, value in result.properties_json.items()
            if not key.startswith("__m8f_source_")
        }
        return {
            "id": result.id,
            "identifier": result.bpmn_identifier,
            "name": result.bpmn_name,
            "properties_json": public_properties,
            "source_bpmn_xml_length": len(result.source_bpmn_xml or ""),
            "source_dmn_xml_length": len(result.source_dmn_xml or ""),
            "version_control_type": result.bpmn_version_control_type,
            "version_control_identifier": result.bpmn_version_control_identifier,
        }
    if isinstance(result, ProcessInstanceModel):
        return {
            "id": result.id,
            "status": result.status,
            "summary": result.summary,
            "process_initiator_id": result.process_initiator_id,
            "definition_id": result.bpmn_process_definition_id,
            "start_in_seconds": result.start_in_seconds,
            "end_in_seconds": result.end_in_seconds,
            "workflow_state_json_present": bool(result.spiff_serializer_version),
            "workflow_state_json_length": "(stored in json_data)",
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


def _require_single_task(
    tasks: list[HumanTaskModel],
    label: str,
    *,
    task_id: int | None = None,
    process_instance_id: int | None = None,
    task_name: str | None = None,
) -> HumanTaskModel:
    matching_tasks = _matching_tasks(
        tasks,
        task_id=task_id,
        process_instance_id=process_instance_id,
        task_name=task_name,
    )
    if len(matching_tasks) != 1:
        raise RuntimeError(
            f"Expected exactly one {label}, got {len(matching_tasks)} matching task(s)"
        )
    ignored_tasks = len(tasks) - len(matching_tasks)
    if ignored_tasks > 0:
        _print_note(
            f"Warning: ignoring {ignored_tasks} unrelated pending task(s) while "
            f"selecting the current {label}."
        )
    return matching_tasks[0]


def _matching_tasks(
    tasks: list[HumanTaskModel],
    *,
    task_id: int | None = None,
    process_instance_id: int | None = None,
    task_name: str | None = None,
) -> list[HumanTaskModel]:
    matching_tasks: list[HumanTaskModel] = []
    for task in tasks:
        if task_id is not None and task.id != task_id:
            continue
        if (
            process_instance_id is not None
            and task.process_instance_id != process_instance_id
        ):
            continue
        if task_name is not None and task.task_name != task_name:
            continue
        matching_tasks.append(task)
    return matching_tasks


def _pause(prompt: str) -> None:
    input(f"{prompt} ")


def _print_note(message: str) -> None:
    print(message)


def _print_payload_values(label: str, payload: Any) -> None:
    print(SECTION_SEPARATOR)
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
        f'    "Manager" : {lane_owners["Manager"]!r},\n'
        f'    "Finance" : {lane_owners["Finance"]!r}\n'
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
