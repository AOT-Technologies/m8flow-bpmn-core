from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from pprint import pformat
from typing import Any

from conditional_approval_poc import (
    EXAMPLE_KEYCLOAK_DEFAULT_PASSWORD,
    BackendProcessModelDeployment,
    _align_shared_db_tenants_with_keycloak_organizations,
    _confirm_shared_database_usage,
    _describe_connection,
    _format_connection_details,
    _get_or_create_tenant,
    _get_or_create_user,
    _is_shared_database_url,
    _pause,
    _print_backend_deployment_summary,
    _print_note,
    _remove_temporary_postgres_container,
    _require_single_task,
    _resolve_backend_tenant_root,
    _resolve_database_url,
    _resolve_m8flow_backend_process_models_root,
    _run_command_step,
    _wait_for_database,
    _write_json_file,
)
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.db import build_engine, create_schema
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel
from m8flow_bpmn_core.models.scheduler_job import SchedulerJobModel
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.services.authorization import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ensure_v1_role,
)
from m8flow_bpmn_core.utils.keycloak import (
    KeycloakOrganizationSpec,
    KeycloakProvisioningError,
    KeycloakUserSpec,
    ProvisionedKeycloakSharedRealmContext,
    ensure_shared_realm_organizations_and_users,
)

SECTION_SEPARATOR = "=" * 88
SCHEDULE_DELAY_SECONDS = 60
INLINE_SCHEDULER_EXECUTION_MODE = "inline"
EXTERNAL_SCHEDULER_EXECUTION_MODE = "external"
EXTERNAL_SCHEDULER_MAX_WAIT_SECONDS = 180
REPO_ROOT = Path(__file__).resolve().parents[1]
PROCESS_GROUP_ID = "m8flow-bpmn-core-examples"
PROCESS_GROUP_DISPLAY_NAME = "m8flow-bpmn-core Examples"
PROCESS_GROUP_DESCRIPTION = (
    "Process models published by the m8flow-bpmn-core examples."
)
PROCESS_MODEL_ID = "scheduled-timer-poc"
PROCESS_MODEL_IDENTIFIER = f"{PROCESS_GROUP_ID}/{PROCESS_MODEL_ID}"
PROCESS_MODEL_DISPLAY_NAME = "Scheduled Timer POC"
PRIMARY_PROCESS_ID = "Process_scheduled_timer_poc"
PRIMARY_FILE_NAME = "scheduled_timer_poc.bpmn"
TIMER_WORKER_ID = "scheduled-timer-poc-inline-worker"
TIMER_START_BPMN_PATH = REPO_ROOT / "tests" / "fixtures" / PRIMARY_FILE_NAME

DEMO_TENANT = {
    "id": "tenant-scheduled-timer-example",
    "name": "Scheduled Timer Example",
    "slug": "scheduled-timer-example",
}
DEMO_USERS = {
    "admin": {
        "username": "timer-poc-admin",
        "email": "timer-poc-admin@example.com",
        "service_id": "timer-poc-admin-keycloak",
        "display_name": "Timer POC Admin",
        "keycloak_groups": ("Approvers", "Viewers"),
    },
    "operator": {
        "username": "timer-poc-operator",
        "email": "timer-poc-operator@example.com",
        "service_id": "timer-poc-operator-keycloak",
        "display_name": "Timer POC Operator",
        "keycloak_groups": ("Approvers", "Viewers"),
    },
}
TASK_LANE_NAME = "Operations"


@dataclass(frozen=True, slots=True)
class TimerPocContext:
    tenant_id: str
    tenant_slug: str
    admin_user_id: int
    admin_username: str
    operator_user_id: int
    operator_username: str
    keycloak_password: str | None


def main() -> None:
    database_url, display_database_url = _resolve_database_url()
    temporary_container_name: str | None = None
    engine: Engine | None = None
    scheduler_execution_mode = _resolve_scheduler_execution_mode()

    if scheduler_execution_mode == INLINE_SCHEDULER_EXECUTION_MODE:
        print("m8flow-bpmn-core scheduled timer POC")
        print(
            "This example imports a timer-start workflow, persists the "
            "scheduler job, runs an inline poller loop, and shows the "
            "timer-created process instance that can be audited in m8flow."
        )
    else:
        print("m8flow-bpmn-core Celery timer POC")
        print(
            "This example imports the same timer-start workflow, persists the "
            "scheduler job, waits for an external scheduler worker such as "
            "the Celery POC worker to process it, and then shows the "
            "timer-created process instance that can be audited in m8flow."
        )
    (
        database_url,
        display_database_url,
        temporary_container_name,
    ) = _confirm_shared_database_usage(
        database_url,
        display_database_url,
    )
    print(f"Database URL: {display_database_url}")
    print()
    print(SECTION_SEPARATOR)
    print("Database connection details")
    print(_format_connection_details(_describe_connection(database_url)))
    print(
        "The example keeps the definition, scheduler row, process instance, "
        "and task in place so they can be inspected after the run."
    )
    _pause("Press Enter to start the example.")

    try:
        print()
        print(SECTION_SEPARATOR)
        print("Database setup")
        print("Connecting to Postgres and creating the schema...")

        engine = build_engine(database_url)
        try:
            _wait_for_database(engine)
        except RuntimeError as exc:
            print(f"Status: unable to reach Postgres. {exc}")
            raise SystemExit(1) from exc

        create_schema(engine)

        with engine.begin() as connection:
            session = Session(
                bind=connection,
                autoflush=False,
                expire_on_commit=False,
            )
            try:
                context = _seed_timer_poc_context(
                    session,
                    database_url=database_url,
                )
            finally:
                session.close()

        _run_timer_poc(
            engine,
            context,
            database_url=database_url,
            scheduler_execution_mode=scheduler_execution_mode,
        )
    except KeyboardInterrupt:
        print("\nInterrupted. The current step was rolled back.")
    finally:
        if engine is not None:
            engine.dispose()
        _remove_temporary_postgres_container(temporary_container_name)


def _seed_timer_poc_context(
    session: Session,
    *,
    database_url: str,
) -> TimerPocContext:
    print()
    print(SECTION_SEPARATOR)
    print("Setup: create the demo tenant and users")
    print(
        "The workflow itself is driven only through the public API. The tenant "
        "and users are seeded directly because the library does not expose "
        "public tenant-management commands."
    )
    _pause("Press Enter to seed the timer demo data.")

    warnings: list[str] = []
    tenant = _get_or_create_tenant(
        session,
        tenant_id=DEMO_TENANT["id"],
        name=DEMO_TENANT["name"],
        slug=DEMO_TENANT["slug"],
        warnings=warnings,
    )

    keycloak_context: ProvisionedKeycloakSharedRealmContext | None = None
    reuse_by_username_within_tenant = False
    tenant_service = f"http://localhost:7002/realms/{tenant.slug}"
    if _is_shared_database_url(database_url):
        try:
            keycloak_context = _provision_shared_db_keycloak_context(tenant)
        except KeycloakProvisioningError as exc:
            raise SystemExit(
                "Shared m8flow database detected, but provisioning the local "
                f"Keycloak shared realm failed: {exc}"
            ) from exc
        tenant = _align_shared_db_tenants_with_keycloak_organizations(
            session,
            tenants=[tenant],
            keycloak_context=keycloak_context,
            warnings=warnings,
        )[0]
        tenant_service = keycloak_context.service_issuer
        reuse_by_username_within_tenant = True

    users: dict[str, UserModel] = {}
    for role, user_spec in DEMO_USERS.items():
        users[role] = _get_or_create_user(
            session,
            service=tenant_service,
            username=user_spec["username"],
            email=user_spec["email"],
            service_id=_resolved_service_id(
                keycloak_context=keycloak_context,
                username=user_spec["username"],
                fallback_service_id=user_spec["service_id"],
            ),
            display_name=user_spec["display_name"],
            tenant_membership_identifiers=(tenant.id, tenant.slug),
            warnings=warnings,
            reuse_by_username_within_tenant=reuse_by_username_within_tenant,
        )

    ensure_v1_role(
        session,
        tenant_id=tenant.id,
        role_name=ROLE_ADMIN,
        user_ids=[users["admin"].id],
    )
    ensure_v1_role(
        session,
        tenant_id=tenant.id,
        role_name=ROLE_MANAGER,
        user_ids=[users["operator"].id],
    )

    print("Status: seed data is ready.")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")

    context = TimerPocContext(
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        admin_user_id=users["admin"].id,
        admin_username=users["admin"].username,
        operator_user_id=users["operator"].id,
        operator_username=users["operator"].username,
        keycloak_password=(
            _shared_db_keycloak_password() if keycloak_context is not None else None
        ),
    )
    print("Seeded rows:")
    print(
        pformat(
            {
                "tenant": {
                    "id": context.tenant_id,
                    "slug": context.tenant_slug,
                },
                "users": {
                    "admin": {
                        "id": context.admin_user_id,
                        "username": context.admin_username,
                    },
                    "operator": {
                        "id": context.operator_user_id,
                        "username": context.operator_username,
                    },
                },
            },
            sort_dicts=False,
            width=100,
        )
    )
    if context.keycloak_password is not None:
        _print_note(
            "Shared Keycloak users are ready. New users use password "
            f"'{context.keycloak_password}'."
        )
    _pause("Press Enter to continue to the timer workflow.")
    return context


def _provision_shared_db_keycloak_context(
    tenant: M8flowTenantModel,
) -> ProvisionedKeycloakSharedRealmContext:
    print()
    print(SECTION_SEPARATOR)
    print("Keycloak provisioning")
    print(
        "Because the shared Postgres database is in use, the example will "
        "provision the demo organization and users in the local Keycloak "
        "shared realm so the same workflow state is visible in the m8flow UI."
    )

    keycloak_context = ensure_shared_realm_organizations_and_users(
        organizations=[
            KeycloakOrganizationSpec(
                alias=tenant.slug,
                name=tenant.name,
            )
        ],
        users=[
            KeycloakUserSpec(
                username=user_spec["username"],
                email=user_spec["email"],
                password=_shared_db_keycloak_password(),
                organization_alias=tenant.slug,
                display_name=user_spec["display_name"],
                organization_group_names=user_spec["keycloak_groups"],
            )
            for user_spec in DEMO_USERS.values()
        ],
    )
    print(
        "Status: Keycloak shared realm issuer is "
        f"{keycloak_context.service_issuer}."
    )
    print(
        "Status: demo users are "
        + ", ".join(sorted(keycloak_context.users_by_username))
        + "."
    )
    return keycloak_context


def _run_timer_poc(
    engine: Engine,
    context: TimerPocContext,
    *,
    database_url: str,
    scheduler_execution_mode: str,
) -> None:
    print()
    print(SECTION_SEPARATOR)
    print("Workflow plan")
    if scheduler_execution_mode == INLINE_SCHEDULER_EXECUTION_MODE:
        print(
            "This run schedules a timer-start event "
            f"{SCHEDULE_DELAY_SECONDS} seconds in the future."
        )
        print(
            "The due timestamp is generated immediately before the import "
            "command runs so interactive pauses do not make the timer stale."
        )
        print(
            "Once the due time is reached, the inline poller will call "
            "`api.run_due_scheduler_jobs(...)` and the library will create "
            "the process instance automatically."
        )
    else:
        print(
            "This run schedules the same timer-start event "
            f"{SCHEDULE_DELAY_SECONDS} seconds in the future."
        )
        print(
            "The due timestamp is generated immediately before the import "
            "command runs so interactive pauses do not make the timer stale."
        )
        print(
            "Once the due time is reached, a separate scheduler worker is "
            "expected to call `api.run_due_scheduler_jobs(...)`. This "
            "terminal session only waits for the resulting process instance "
            "to appear."
        )
    _pause("Press Enter to continue to the import step.")

    print()
    print(SECTION_SEPARATOR)
    print("Step 1: Import the timer-start definition")
    print(
        "This stores the BPMN definition and immediately persists a "
        "scheduler_job row for the future timer start."
    )
    print(SECTION_SEPARATOR)
    _pause(
        "Press Enter to stamp a fresh due time and execute the import command."
    )

    scheduled_due_at = (
        datetime.now(UTC).replace(microsecond=0)
        + timedelta(seconds=SCHEDULE_DELAY_SECONDS)
    )
    bpmn_xml = _render_timer_start_bpmn_xml(scheduled_due_at)
    import_timestamp = round(time.time())
    import_command = api.ImportBpmnProcessDefinitionCommand(
        tenant_id=context.tenant_id,
        bpmn_identifier=PROCESS_MODEL_IDENTIFIER,
        user_id=context.admin_user_id,
        bpmn_name=PROCESS_MODEL_DISPLAY_NAME,
        source_bpmn_xml=bpmn_xml,
        properties_json={
            "version": 1,
            "flow": "scheduled_timer_poc",
            "lane_owners": {
                TASK_LANE_NAME: [context.operator_username],
            },
            "scheduled_due_at": scheduled_due_at.isoformat(),
        },
        bpmn_version_control_type="git",
        bpmn_version_control_identifier="timer-poc",
        created_at_in_seconds=import_timestamp,
        updated_at_in_seconds=import_timestamp,
    )
    print("Command:")
    print(pformat(import_command, sort_dicts=False, width=100))
    print(
        "Status: executing import with due time "
        f"{scheduled_due_at.isoformat()}."
    )
    with engine.begin() as connection:
        definition = api.execute_command(connection, import_command)
    print("Status: command complete and committed.")
    print("Result:")
    print(
        pformat(
            {
                "id": definition.id,
                "identifier": definition.bpmn_identifier,
                "process_model_identifier": definition.process_model_identifier,
                "name": definition.bpmn_name,
                "properties_json": definition.properties_json,
                "source_bpmn_xml_length": len(definition.source_bpmn_xml or ""),
                "source_dmn_xml_length": len(definition.source_dmn_xml or ""),
                "version_control_type": definition.bpmn_version_control_type,
                "version_control_identifier": (
                    definition.bpmn_version_control_identifier
                ),
            },
            sort_dicts=False,
            width=100,
        )
    )
    _pause("Press Enter to continue.")

    deployment = _maybe_deploy_process_model_to_m8flow_backend(
        database_url=database_url,
        tenant_id=context.tenant_id,
        tenant_slug=context.tenant_slug,
        bpmn_xml=bpmn_xml,
    )
    if deployment is not None:
        _print_backend_deployment_summary(deployment)

    with engine.begin() as connection:
        session = Session(bind=connection, autoflush=False, expire_on_commit=False)
        try:
            scheduler_job = _load_timer_start_job(
                session,
                tenant_id=context.tenant_id,
                bpmn_process_definition_id=definition.id,
                scheduled_due_at=scheduled_due_at,
            )
            print()
            print(SECTION_SEPARATOR)
            print("Persisted scheduler job")
            print(
                pformat(
                    _summarize_scheduler_job(scheduler_job),
                    sort_dicts=False,
                    width=100,
                )
            )
        finally:
            session.close()

    if scheduler_execution_mode == INLINE_SCHEDULER_EXECUTION_MODE:
        _pause(
            "Press Enter to start the inline scheduler loop and wait for the "
            "timer."
        )
        _run_scheduler_until_due(
            engine,
            tenant_id=context.tenant_id,
            scheduled_due_at=scheduled_due_at,
            bpmn_process_definition_id=definition.id,
            minimum_start_in_seconds=import_timestamp,
        )
    else:
        _pause(
            "Press Enter to wait for the external scheduler worker to process "
            "the timer."
        )
        _wait_for_external_scheduler(
            engine,
            tenant_id=context.tenant_id,
            scheduled_due_at=scheduled_due_at,
            bpmn_process_definition_id=definition.id,
            minimum_start_in_seconds=import_timestamp,
        )

    process_instances = _run_command_step(
        engine,
        step_number=2,
        title="List process instances after the timer fired",
        context_text=(
            "The timer-start event should have created a real process instance "
            "without an explicit process.start call."
        ),
        command=api.ListProcessInstancesQuery(tenant_id=context.tenant_id),
    )
    matching_instances = [
        process_instance
        for process_instance in process_instances
        if (
            process_instance.bpmn_process_definition_id == definition.id
            and process_instance.start_in_seconds is not None
            and process_instance.start_in_seconds >= import_timestamp
        )
    ]
    if len(matching_instances) != 1:
        raise RuntimeError(
            "Expected exactly one timer-started process instance for the "
            f"current definition, got {len(matching_instances)}."
        )
    process_instance = matching_instances[0]

    with engine.begin() as connection:
        session = Session(bind=connection, autoflush=False, expire_on_commit=False)
        try:
            timer_system_user = session.get(
                UserModel,
                process_instance.process_initiator_id,
            )
            print()
            print(SECTION_SEPARATOR)
            print("Timer-start initiator")
            print(
                pformat(
                    {
                        "process_instance_id": process_instance.id,
                        "process_initiator_id": process_instance.process_initiator_id,
                        "user": (
                            {
                                "username": timer_system_user.username,
                                "service": timer_system_user.service,
                                "service_id": timer_system_user.service_id,
                            }
                            if timer_system_user is not None
                            else None
                        ),
                    },
                    sort_dicts=False,
                    width=100,
                )
            )
        finally:
            session.close()

    operator_tasks = _run_command_step(
        engine,
        step_number=3,
        title="List the operator pending tasks",
        context_text=(
            "The timer-started instance should now have a human task assigned "
            "through the BPMN lane plus properties_json['lane_owners']."
        ),
        command=api.GetPendingTasksQuery(
            tenant_id=context.tenant_id,
            user_id=context.operator_user_id,
        ),
    )
    operator_task = _require_single_task(
        operator_tasks,
        "scheduled timer task",
        process_instance_id=process_instance.id,
    )

    _run_command_step(
        engine,
        step_number=4,
        title="Claim the scheduled task",
        context_text=(
            "This is a normal user-task interaction after the timer has "
            "started the process instance."
        ),
        command=api.ClaimTaskCommand(
            tenant_id=context.tenant_id,
            human_task_id=operator_task.id,
            user_id=context.operator_user_id,
        ),
    )

    _run_command_step(
        engine,
        step_number=5,
        title="Complete the scheduled task",
        context_text=(
            "Completing the task ends the simple timer-start workflow and "
            "shows that the timer path hands off cleanly to normal task "
            "lifecycle operations."
        ),
        command=api.CompleteTaskCommand(
            tenant_id=context.tenant_id,
            human_task_id=operator_task.id,
            user_id=context.operator_user_id,
            completed_at_in_seconds=round(time.time()),
            task_payload={
                "acknowledged_by": context.operator_username,
                "acknowledged_at": datetime.now(UTC).replace(
                    microsecond=0
                ).isoformat(),
            },
        ),
    )

    _run_command_step(
        engine,
        step_number=6,
        title="Read back the timer-started process instance",
        context_text=(
            "The process should now be complete, and the instance remains "
            "available for audit in the shared database."
        ),
        command=api.GetProcessInstanceQuery(
            tenant_id=context.tenant_id,
            process_instance_id=process_instance.id,
        ),
    )
    _run_command_step(
        engine,
        step_number=7,
        title="Read back the event history",
        context_text=(
            "The event log shows the timer-started lifecycle and the human "
            "task interaction that followed."
        ),
        command=api.GetProcessInstanceEventsQuery(
            tenant_id=context.tenant_id,
            process_instance_id=process_instance.id,
        ),
    )
    _print_note(
        "The scheduled timer POC is complete. The database rows were left in "
        "place so they can still be audited."
    )


def _maybe_deploy_process_model_to_m8flow_backend(
    *,
    database_url: str,
    tenant_id: str,
    tenant_slug: str,
    bpmn_xml: str,
) -> BackendProcessModelDeployment | None:
    if not _is_shared_database_url(database_url):
        return None

    print()
    print(SECTION_SEPARATOR)
    print("m8flow backend deployment")
    print(
        "Because the shared Postgres database is in use, the example will "
        "also publish the rendered BPMN file into the local m8flow backend "
        "process-model catalog so the model is visible in the UI."
    )

    process_models_root, container_name, root_warnings = (
        _resolve_m8flow_backend_process_models_root()
    )
    if process_models_root is None:
        for warning in root_warnings:
            _print_note(f"Warning: {warning}")
        return None

    backend_tenant_root, tenant_warnings = _resolve_backend_tenant_root(
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
    )
    deployment = _deploy_timer_definition_to_m8flow_backend(
        process_models_root=process_models_root,
        tenant_root=backend_tenant_root,
        bpmn_xml=bpmn_xml,
    )
    return BackendProcessModelDeployment(
        process_models_root=deployment.process_models_root,
        tenant_root=deployment.tenant_root,
        process_group_id=deployment.process_group_id,
        process_model_id=deployment.process_model_id,
        deployed=deployment.deployed,
        already_deployed=deployment.already_deployed,
        warnings=tuple(
            [
                *root_warnings,
                *tenant_warnings,
                *deployment.warnings,
            ]
        ),
        container_name=container_name,
    )


def _deploy_timer_definition_to_m8flow_backend(
    *,
    process_models_root: Path,
    tenant_root: str,
    bpmn_xml: str,
) -> BackendProcessModelDeployment:
    group_dir = process_models_root / tenant_root / PROCESS_GROUP_ID
    model_dir = group_dir / PROCESS_MODEL_ID
    group_json_path = group_dir / "process_group.json"
    model_json_path = model_dir / "process_model.json"
    bpmn_path = model_dir / PRIMARY_FILE_NAME

    warnings: list[str] = []
    model_dir.mkdir(parents=True, exist_ok=True)
    if bpmn_path.exists():
        warnings.append(
            "Refreshed the existing scheduled-timer POC deployment so the UI "
            "matches this run's due timestamp."
        )

    _write_json_file(group_json_path, _backend_process_group_payload())
    _write_json_file(model_json_path, _backend_process_model_payload())
    bpmn_path.write_text(bpmn_xml, encoding="utf-8")

    return BackendProcessModelDeployment(
        process_models_root=process_models_root,
        tenant_root=tenant_root,
        process_group_id=PROCESS_GROUP_ID,
        process_model_id=PROCESS_MODEL_ID,
        deployed=True,
        already_deployed=False,
        warnings=tuple(warnings),
    )


def _backend_process_group_payload() -> dict[str, Any]:
    return {
        "correlation_keys": None,
        "correlation_properties": None,
        "data_store_specifications": {},
        "description": PROCESS_GROUP_DESCRIPTION,
        "display_name": PROCESS_GROUP_DISPLAY_NAME,
        "messages": None,
    }


def _backend_process_model_payload() -> dict[str, Any]:
    return {
        "description": (
            "Published by the m8flow-bpmn-core scheduled timer example."
        ),
        "display_name": PROCESS_MODEL_DISPLAY_NAME,
        "exception_notification_addresses": [],
        "fault_or_suspend_on_exception": "fault",
        "metadata_extraction_paths": None,
        "primary_file_name": PRIMARY_FILE_NAME,
        "primary_process_id": PRIMARY_PROCESS_ID,
    }


def _render_timer_start_bpmn_xml(scheduled_due_at: datetime) -> str:
    return TIMER_START_BPMN_PATH.read_text(encoding="utf-8").replace(
        "__TIMER_DUE_AT__",
        scheduled_due_at.isoformat(),
    )


def _load_timer_start_job(
    session: Session,
    *,
    tenant_id: str,
    bpmn_process_definition_id: int,
    scheduled_due_at: datetime,
) -> SchedulerJobModel:
    scheduler_job = session.scalar(
        select(SchedulerJobModel).where(
            SchedulerJobModel.m8f_tenant_id == tenant_id,
            SchedulerJobModel.bpmn_process_definition_id
            == bpmn_process_definition_id,
        )
    )
    if scheduler_job is None:
        matching_process_instances = session.scalars(
            select(ProcessInstanceModel).where(
                ProcessInstanceModel.m8f_tenant_id == tenant_id,
                ProcessInstanceModel.bpmn_process_definition_id
                == bpmn_process_definition_id,
            )
        ).all()
        raise RuntimeError(
            "Expected a persisted scheduler job for the imported timer-start "
            "definition, but none was found. "
            f"Scheduled due time was {scheduled_due_at.isoformat()}. "
            "If the timer was already due at import time, the current V1 "
            "timer-start sync path will not persist a waiting scheduler row. "
            "Matching process instances already present: "
            f"{len(matching_process_instances)}."
        )
    return scheduler_job


def _summarize_scheduler_job(scheduler_job: SchedulerJobModel) -> dict[str, Any]:
    return {
        "id": scheduler_job.id,
        "job_key": scheduler_job.job_key,
        "job_type": scheduler_job.job_type,
        "tenant_id": scheduler_job.m8f_tenant_id,
        "definition_id": scheduler_job.bpmn_process_definition_id,
        "run_at_in_seconds": scheduler_job.run_at_in_seconds,
        "locked_by": scheduler_job.locked_by,
        "payload_json": scheduler_job.payload_json,
    }


def _run_scheduler_until_due(
    engine: Engine,
    *,
    tenant_id: str,
    scheduled_due_at: datetime,
    bpmn_process_definition_id: int,
    minimum_start_in_seconds: int,
) -> None:
    print()
    print(SECTION_SEPARATOR)
    print("Inline scheduler loop")
    print(
        "The host application is responsible for wake-up cadence. This loop "
        "polls once per second and waits for the current definition to create "
        "its process instance."
    )

    total_processed = 0
    while True:
        process_instances, current_job_exists = _current_timer_start_runtime_state(
            engine,
            tenant_id=tenant_id,
            bpmn_process_definition_id=bpmn_process_definition_id,
            minimum_start_in_seconds=minimum_start_in_seconds,
        )
        if process_instances:
            print()
            print(
                "Status: the inline poller processed "
                f"{total_processed} due scheduler job(s) before the current "
                "definition produced its process instance."
            )
            return

        now = datetime.now(UTC)
        remaining_seconds = int((scheduled_due_at - now).total_seconds())
        if remaining_seconds > 0:
            print(
                "\rWaiting for timer due time "
                f"{scheduled_due_at.isoformat()} ({remaining_seconds}s remaining)...",
                end="",
                flush=True,
            )
            time.sleep(1)
            continue

        with engine.begin() as connection:
            processed = api.run_due_scheduler_jobs(
                connection,
                worker_id=TIMER_WORKER_ID,
                tenant_id=tenant_id,
            )
        total_processed += processed

        process_instances, current_job_exists = _current_timer_start_runtime_state(
            engine,
            tenant_id=tenant_id,
            bpmn_process_definition_id=bpmn_process_definition_id,
            minimum_start_in_seconds=minimum_start_in_seconds,
        )
        if process_instances:
            print()
            print(
                "Status: the inline poller processed "
                f"{total_processed} due scheduler job(s) before the current "
                "definition produced its process instance."
            )
            return
        if not current_job_exists:
            raise RuntimeError(
                "The current timer-start scheduler job disappeared, but no "
                "process instance was created for the current definition."
            )
        if processed == 0:
            print(
                "\rTimer is due, but no job was processed yet. Polling again...   ",
                end="",
                flush=True,
            )
        else:
            print(
                "\rProcessed due work for this tenant, but the current "
                "definition has not fired yet. Polling again...   ",
                end="",
                flush=True,
            )
            time.sleep(1)


def _wait_for_external_scheduler(
    engine: Engine,
    *,
    tenant_id: str,
    scheduled_due_at: datetime,
    bpmn_process_definition_id: int,
    minimum_start_in_seconds: int,
) -> None:
    print()
    print(SECTION_SEPARATOR)
    print("External scheduler wait")
    print(
        "This run expects a separate worker, such as the Celery scheduler POC "
        "worker, to call `api.run_due_scheduler_jobs(...)`. This loop only "
        "polls the database until the timer-created process instance appears."
    )

    wait_deadline = datetime.now(UTC) + timedelta(
        seconds=EXTERNAL_SCHEDULER_MAX_WAIT_SECONDS
    )
    while True:
        process_instances, current_job_exists = _current_timer_start_runtime_state(
            engine,
            tenant_id=tenant_id,
            bpmn_process_definition_id=bpmn_process_definition_id,
            minimum_start_in_seconds=minimum_start_in_seconds,
        )
        if process_instances:
            print()
            print(
                "Status: the external scheduler worker created the process "
                "instance for the current definition."
            )
            return

        now = datetime.now(UTC)
        if now >= wait_deadline:
            raise RuntimeError(
                "Timed out waiting for an external scheduler worker to create "
                "the timer-start process instance."
            )

        remaining_seconds = int((scheduled_due_at - now).total_seconds())
        timeout_remaining = int((wait_deadline - now).total_seconds())
        if remaining_seconds > 0:
            print(
                "\rWaiting for timer due time "
                f"{scheduled_due_at.isoformat()} ({remaining_seconds}s "
                f"remaining, {timeout_remaining}s before timeout)...",
                end="",
                flush=True,
            )
        else:
            print(
                "\rTimer is due. Waiting for external scheduler worker to "
                f"process the row ({timeout_remaining}s before timeout)...   ",
                end="",
                flush=True,
            )

        if not current_job_exists and remaining_seconds <= 0:
            raise RuntimeError(
                "The current timer-start scheduler job disappeared, but no "
                "process instance was created for the current definition."
            )
        time.sleep(1)


def _current_timer_start_runtime_state(
    engine: Engine,
    *,
    tenant_id: str,
    bpmn_process_definition_id: int,
    minimum_start_in_seconds: int | None = None,
) -> tuple[list[ProcessInstanceModel], bool]:
    with engine.begin() as connection:
        session = Session(
            bind=connection,
            autoflush=False,
            expire_on_commit=False,
        )
        try:
            stmt = select(ProcessInstanceModel).where(
                ProcessInstanceModel.m8f_tenant_id == tenant_id,
                ProcessInstanceModel.bpmn_process_definition_id
                == bpmn_process_definition_id,
            )
            if minimum_start_in_seconds is not None:
                stmt = stmt.where(
                    ProcessInstanceModel.start_in_seconds
                    >= minimum_start_in_seconds
                )
            process_instances = list(session.scalars(stmt).all())
            current_job_exists = (
                session.scalar(
                    select(SchedulerJobModel.id).where(
                        SchedulerJobModel.m8f_tenant_id == tenant_id,
                        SchedulerJobModel.bpmn_process_definition_id
                        == bpmn_process_definition_id,
                    )
                )
                is not None
            )
            return process_instances, current_job_exists
        finally:
            session.close()


def _resolved_service_id(
    *,
    keycloak_context: ProvisionedKeycloakSharedRealmContext | None,
    username: str,
    fallback_service_id: str,
) -> str:
    if keycloak_context is None:
        return fallback_service_id

    provisioned_user = keycloak_context.users_by_username.get(username)
    if provisioned_user is None:
        return fallback_service_id
    return provisioned_user.user_id


def _shared_db_keycloak_password() -> str:
    return (
        os.getenv("M8FLOW_EXAMPLE_KEYCLOAK_PASSWORD", "").strip()
        or EXAMPLE_KEYCLOAK_DEFAULT_PASSWORD
    )


def _resolve_scheduler_execution_mode() -> str:
    configured_mode = (
        os.getenv("M8FLOW_SCHEDULER_EXECUTION_MODE", "").strip().lower()
        or INLINE_SCHEDULER_EXECUTION_MODE
    )
    if configured_mode not in {
        INLINE_SCHEDULER_EXECUTION_MODE,
        EXTERNAL_SCHEDULER_EXECUTION_MODE,
    }:
        raise SystemExit(
            "M8FLOW_SCHEDULER_EXECUTION_MODE must be either 'inline' or "
            f"'external', got {configured_mode!r}."
        )
    return configured_mode


if __name__ == "__main__":
    main()
