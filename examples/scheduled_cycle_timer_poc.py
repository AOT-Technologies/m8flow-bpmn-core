from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
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
CYCLE_REPEAT_COUNT = 3
CYCLE_INTERVAL_SECONDS = 20
REPO_ROOT = Path(__file__).resolve().parents[1]
PROCESS_GROUP_ID = "m8flow-bpmn-core-examples"
PROCESS_GROUP_DISPLAY_NAME = "m8flow-bpmn-core Examples"
PROCESS_GROUP_DESCRIPTION = (
    "Process models published by the m8flow-bpmn-core examples."
)
PROCESS_MODEL_ID = "scheduled-cycle-timer-poc"
PROCESS_MODEL_IDENTIFIER = f"{PROCESS_GROUP_ID}/{PROCESS_MODEL_ID}"
PROCESS_MODEL_DISPLAY_NAME = "Scheduled Cycle Timer POC"
PRIMARY_PROCESS_ID = "Process_scheduled_cycle_timer_poc"
PRIMARY_FILE_NAME = "scheduled_cycle_timer_poc.bpmn"
TIMER_WORKER_ID = "scheduled-cycle-timer-poc-inline-worker"
TIMER_START_BPMN_PATH = REPO_ROOT / "tests" / "fixtures" / PRIMARY_FILE_NAME

DEMO_TENANT = {
    "id": "tenant-scheduled-cycle-timer-example",
    "name": "Scheduled Cycle Timer Example",
    "slug": "scheduled-cycle-timer-example",
}
DEMO_USERS = {
    "admin": {
        "username": "cycle-timer-poc-admin",
        "email": "cycle-timer-poc-admin@example.com",
        "service_id": "cycle-timer-poc-admin-keycloak",
        "display_name": "Cycle Timer POC Admin",
        "keycloak_groups": ("Approvers", "Viewers"),
    },
    "operator": {
        "username": "cycle-timer-poc-operator",
        "email": "cycle-timer-poc-operator@example.com",
        "service_id": "cycle-timer-poc-operator-keycloak",
        "display_name": "Cycle Timer POC Operator",
        "keycloak_groups": ("Approvers", "Viewers"),
    },
}
TASK_LANE_NAME = "Operations"


@dataclass(frozen=True, slots=True)
class CycleTimerPocContext:
    tenant_id: str
    tenant_slug: str
    admin_user_id: int
    admin_username: str
    operator_user_id: int
    operator_username: str
    keycloak_password: str | None


@dataclass(frozen=True, slots=True)
class RecurringTimerState:
    next_due_at: datetime
    duration_seconds: float
    remaining_cycles: int | None


def main() -> None:
    database_url, display_database_url = _resolve_database_url()
    temporary_container_name: str | None = None
    engine: Engine | None = None

    print("m8flow-bpmn-core scheduled cycle timer POC")
    print(
        "This example imports a recurring timer-start workflow, persists the "
        "scheduler job, runs an inline poller loop until all configured cycle "
        "occurrences fire, and shows the process instances that can be "
        "audited in m8flow."
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
        "The example keeps the definition, scheduler row, process instances, "
        "and tasks in place so the recurring schedule can be inspected after "
        "the run."
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
                context = _seed_cycle_timer_poc_context(
                    session,
                    database_url=database_url,
                )
            finally:
                session.close()

        _run_cycle_timer_poc(
            engine,
            context,
            database_url=database_url,
        )
    except KeyboardInterrupt:
        print("\nInterrupted. The current step was rolled back.")
    finally:
        if engine is not None:
            engine.dispose()
        _remove_temporary_postgres_container(temporary_container_name)


def _seed_cycle_timer_poc_context(
    session: Session,
    *,
    database_url: str,
) -> CycleTimerPocContext:
    print()
    print(SECTION_SEPARATOR)
    print("Setup: create the demo tenant and users")
    print(
        "The workflow itself is driven only through the public API. The tenant "
        "and users are seeded directly because the library does not expose "
        "public tenant-management commands."
    )
    _pause("Press Enter to seed the cycle timer demo data.")

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

    context = CycleTimerPocContext(
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
    _pause("Press Enter to continue to the cycle timer workflow.")
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


def _run_cycle_timer_poc(
    engine: Engine,
    context: CycleTimerPocContext,
    *,
    database_url: str,
) -> None:
    cycle_expression = _timer_cycle_expression()

    print()
    print(SECTION_SEPARATOR)
    print("Workflow plan")
    print(
        "This run imports a recurring timer-start event with timeCycle "
        f"expression {cycle_expression!r}."
    )
    print(
        "The BPMN engine calculates the first due timestamp at import time, "
        "and this POC waits through every scheduled occurrence so one run "
        "creates all three process instances."
    )
    print(
        "After each occurrence, the library reschedules the same logical "
        "scheduler job for the next cycle with one fewer remaining repetition "
        "until the finite cycle is exhausted."
    )
    print(
        "If the poller starts late, overdue cycle occurrences are caught up "
        "immediately. This example starts the poller right after import so "
        "the 20-second spacing is visible."
    )
    _pause("Press Enter to continue to the import step.")

    print()
    print(SECTION_SEPARATOR)
    print("Step 1: Import the recurring timer-start definition")
    print(
        "This stores the BPMN definition and immediately persists a "
        "scheduler_job row for the first cycle occurrence."
    )
    print(SECTION_SEPARATOR)
    _pause("Press Enter to execute the import command.")

    bpmn_xml = _render_cycle_timer_bpmn_xml(cycle_expression)
    import_timestamp = round(time.time())
    import_command = api.ImportBpmnProcessDefinitionCommand(
        tenant_id=context.tenant_id,
        bpmn_identifier=PROCESS_MODEL_IDENTIFIER,
        user_id=context.admin_user_id,
        bpmn_name=PROCESS_MODEL_DISPLAY_NAME,
        source_bpmn_xml=bpmn_xml,
        properties_json={
            "version": 1,
            "flow": "scheduled_cycle_timer_poc",
            "cycle_expression": cycle_expression,
            "cycle_repeat_count": CYCLE_REPEAT_COUNT,
            "cycle_interval_seconds": CYCLE_INTERVAL_SECONDS,
            "lane_owners": {
                TASK_LANE_NAME: [context.operator_username],
            },
        },
        bpmn_version_control_type="git",
        bpmn_version_control_identifier="timer-cycle-poc",
        created_at_in_seconds=import_timestamp,
        updated_at_in_seconds=import_timestamp,
    )
    print("Command:")
    print(pformat(import_command, sort_dicts=False, width=100))
    print(
        "Status: executing import with cycle expression "
        f"{cycle_expression!r}."
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
            scheduler_job = _load_timer_start_job_for_definition(
                session,
                tenant_id=context.tenant_id,
                bpmn_process_definition_id=definition.id,
            )
            first_cycle_state = _recurring_timer_state(scheduler_job)
            print()
            print(SECTION_SEPARATOR)
            print("Persisted recurring scheduler job")
            print(
                pformat(
                    {
                        "job": _summarize_scheduler_job(scheduler_job),
                        "cycle_state": {
                            "next_due_at": first_cycle_state.next_due_at.isoformat(),
                            "duration_seconds": first_cycle_state.duration_seconds,
                            "remaining_cycles": first_cycle_state.remaining_cycles,
                        },
                    },
                    sort_dicts=False,
                    width=100,
                )
            )
        finally:
            session.close()

    print()
    print(
        "Status: starting the inline scheduler loop immediately so the "
        "20-second cycle cadence is not made stale by an interactive pause."
    )
    _run_scheduler_until_all_cycles_fire(
        engine,
        tenant_id=context.tenant_id,
        bpmn_process_definition_id=definition.id,
        minimum_start_in_seconds=import_timestamp,
        expected_instance_count=CYCLE_REPEAT_COUNT,
    )

    process_instances = _run_command_step(
        engine,
        step_number=2,
        title="List process instances after all cycles fired",
        context_text=(
            "The recurring timer-start event should have created one real "
            "process instance per cycle occurrence without an explicit "
            "process.start call."
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
    if len(matching_instances) != CYCLE_REPEAT_COUNT:
        raise RuntimeError(
            "Expected exactly "
            f"{CYCLE_REPEAT_COUNT} timer-started process instance(s) for the "
            "current definition after all cycles fired, got "
            f"{len(matching_instances)}."
        )
    matching_instances.sort(
        key=lambda process_instance: (
            process_instance.start_in_seconds or 0,
            process_instance.id,
        )
    )
    process_instance_ids = {
        process_instance.id for process_instance in matching_instances
    }

    with engine.begin() as connection:
        session = Session(bind=connection, autoflush=False, expire_on_commit=False)
        try:
            initiators = []
            for process_instance in matching_instances:
                timer_system_user = session.get(
                    UserModel,
                    process_instance.process_initiator_id,
                )
                initiators.append(
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
                    }
                )
            rescheduled_job = _find_timer_start_job_for_definition(
                session,
                tenant_id=context.tenant_id,
                bpmn_process_definition_id=definition.id,
            )
            print()
            print(SECTION_SEPARATOR)
            print("Timer-start initiators")
            print(
                pformat(
                    initiators,
                    sort_dicts=False,
                    width=100,
                )
            )
        finally:
            session.close()

    if rescheduled_job is not None:
        rescheduled_cycle_state = _recurring_timer_state(rescheduled_job)
        raise RuntimeError(
            "Expected the finite recurring timer to delete its scheduler job "
            "after all configured cycles fired, but a recurring job still "
            "exists with "
            f"{rescheduled_cycle_state.remaining_cycles!r} remaining cycle(s)."
        )

    print()
    print(SECTION_SEPARATOR)
    print("Recurring scheduler status")
    print(
        pformat(
            {
                "job_present": False,
                "created_process_instance_ids": [
                    process_instance.id for process_instance in matching_instances
                ],
            },
            sort_dicts=False,
            width=100,
        )
    )

    operator_tasks = _run_command_step(
        engine,
        step_number=3,
        title="List the operator pending tasks",
        context_text=(
            "Each cycle-created instance should now have its own human task "
            "assigned through the BPMN lane plus "
            "properties_json['lane_owners']."
        ),
        command=api.GetPendingTasksQuery(
            tenant_id=context.tenant_id,
            user_id=context.operator_user_id,
        ),
    )
    current_run_tasks = [
        task
        for task in operator_tasks
        if task.process_instance_id in process_instance_ids
    ]
    if len(current_run_tasks) != CYCLE_REPEAT_COUNT:
        raise RuntimeError(
            "Expected exactly "
            f"{CYCLE_REPEAT_COUNT} current-run scheduled cycle timer task(s), "
            f"got {len(current_run_tasks)}."
        )
    ignored_tasks = len(operator_tasks) - len(current_run_tasks)
    if ignored_tasks > 0:
        _print_note(
            f"Warning: ignoring {ignored_tasks} unrelated pending task(s) while "
            "selecting the current cycle-run tasks."
        )
    current_run_tasks.sort(
        key=lambda task: (
            task.process_instance_id,
            task.id,
        )
    )

    print()
    print(SECTION_SEPARATOR)
    print("Current-run task summary")
    print(
        pformat(
            [
                {
                    "human_task_id": task.id,
                    "process_instance_id": task.process_instance_id,
                    "task_name": task.task_name,
                    "task_status": task.task_status,
                }
                for task in current_run_tasks
            ],
            sort_dicts=False,
            width=100,
        )
    )

    current_run_instances = _run_command_step(
        engine,
        step_number=4,
        title="Read back the current-run process instances",
        context_text=(
            "All three cycle-created process instances should remain available "
            "for audit in the shared database after the finite recurring "
            "schedule is exhausted."
        ),
        command=api.ListProcessInstancesQuery(tenant_id=context.tenant_id),
    )
    current_run_instance_snapshots = [
        process_instance
        for process_instance in current_run_instances
        if process_instance.id in process_instance_ids
    ]
    if len(current_run_instance_snapshots) != CYCLE_REPEAT_COUNT:
        raise RuntimeError(
            "Expected exactly "
            f"{CYCLE_REPEAT_COUNT} current-run process instance snapshots, got "
            f"{len(current_run_instance_snapshots)}."
        )

    _run_command_step(
        engine,
        step_number=5,
        title="Read back the final cycle event history",
        context_text=(
            "The event log shows the timer-started lifecycle for the last "
            "cycle-created instance. The human tasks are intentionally left "
            "pending so they can be audited or completed later in the UI or "
            "through the public API."
        ),
        command=api.GetProcessInstanceEventsQuery(
            tenant_id=context.tenant_id,
            process_instance_id=matching_instances[-1].id,
        ),
    )

    _print_note(
        "The scheduled cycle timer POC completed all configured occurrences. "
        "Three process instances were created, the recurring scheduler job "
        "was exhausted, and the generated operator tasks were left in place "
        "for audit."
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
    deployment = _deploy_cycle_timer_definition_to_m8flow_backend(
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


def _deploy_cycle_timer_definition_to_m8flow_backend(
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
            "Refreshed the existing scheduled-cycle-timer POC deployment so "
            "the UI matches this run's current cycle expression."
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
            "Published by the m8flow-bpmn-core scheduled cycle timer example."
        ),
        "display_name": PROCESS_MODEL_DISPLAY_NAME,
        "exception_notification_addresses": [],
        "fault_or_suspend_on_exception": "fault",
        "metadata_extraction_paths": None,
        "primary_file_name": PRIMARY_FILE_NAME,
        "primary_process_id": PRIMARY_PROCESS_ID,
    }


def _timer_cycle_expression() -> str:
    return f"R{CYCLE_REPEAT_COUNT}/{_iso8601_duration(CYCLE_INTERVAL_SECONDS)}"


def _iso8601_duration(total_seconds: int) -> str:
    minutes, seconds = divmod(total_seconds, 60)
    parts: list[str] = []
    if minutes:
        parts.append(f"{minutes}M")
    if seconds or not parts:
        parts.append(f"{seconds}S")
    return "PT" + "".join(parts)


def _render_cycle_timer_bpmn_xml(cycle_expression: str) -> str:
    return TIMER_START_BPMN_PATH.read_text(encoding="utf-8").replace(
        "__TIMER_CYCLE__",
        cycle_expression,
    )


def _find_timer_start_job_for_definition(
    session: Session,
    *,
    tenant_id: str,
    bpmn_process_definition_id: int,
) -> SchedulerJobModel | None:
    return session.scalar(
        select(SchedulerJobModel).where(
            SchedulerJobModel.m8f_tenant_id == tenant_id,
            SchedulerJobModel.bpmn_process_definition_id
            == bpmn_process_definition_id,
        )
    )


def _load_timer_start_job_for_definition(
    session: Session,
    *,
    tenant_id: str,
    bpmn_process_definition_id: int,
) -> SchedulerJobModel:
    scheduler_job = _find_timer_start_job_for_definition(
        session,
        tenant_id=tenant_id,
        bpmn_process_definition_id=bpmn_process_definition_id,
    )
    if scheduler_job is None:
        raise RuntimeError(
            "Expected a persisted recurring timer-start scheduler job for the "
            f"definition id {bpmn_process_definition_id}, but none was found."
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


def _recurring_timer_state(scheduler_job: SchedulerJobModel) -> RecurringTimerState:
    payload_json = scheduler_job.payload_json
    if not isinstance(payload_json, Mapping):
        raise RuntimeError("Scheduler job payload must be a dictionary")

    timer_task = payload_json.get("timer_task")
    if not isinstance(timer_task, Mapping):
        raise RuntimeError("Scheduler job payload is missing its timer_task state")

    event_value = timer_task.get("event_value")
    if not isinstance(event_value, Mapping):
        raise RuntimeError(
            "Recurring timer start payload is missing its mapped event_value"
        )

    next_due_at_value = event_value.get("next")
    if not isinstance(next_due_at_value, str) or not next_due_at_value.strip():
        raise RuntimeError("Recurring timer start payload is missing its next due")

    duration_seconds = event_value.get("duration")
    if not isinstance(duration_seconds, (int, float)) or duration_seconds <= 0:
        raise RuntimeError(
            "Recurring timer start payload is missing its positive duration"
        )

    remaining_cycles_value = event_value.get("cycles")
    if remaining_cycles_value is None:
        remaining_cycles = None
    elif type(remaining_cycles_value) is int and remaining_cycles_value > 0:
        remaining_cycles = remaining_cycles_value
    else:
        raise RuntimeError(
            "Recurring timer start payload has an invalid remaining cycles value"
        )

    return RecurringTimerState(
        next_due_at=_parse_due_at(next_due_at_value),
        duration_seconds=float(duration_seconds),
        remaining_cycles=remaining_cycles,
    )


def _run_scheduler_until_all_cycles_fire(
    engine: Engine,
    *,
    tenant_id: str,
    bpmn_process_definition_id: int,
    minimum_start_in_seconds: int,
    expected_instance_count: int,
) -> None:
    print()
    print(SECTION_SEPARATOR)
    print("Inline scheduler loop")
    print(
        "The host application is responsible for wake-up cadence. This loop "
        "polls once per second and waits for the current definition to create "
        "every configured cycle-created process instance."
    )

    total_processed = 0
    observed_instance_count = 0
    while True:
        process_instances, scheduler_job = _current_timer_start_runtime_state(
            engine,
            tenant_id=tenant_id,
            bpmn_process_definition_id=bpmn_process_definition_id,
            minimum_start_in_seconds=minimum_start_in_seconds,
        )
        current_instance_count = len(process_instances)
        if current_instance_count > observed_instance_count:
            print()
            print(
                "Status: observed "
                f"{current_instance_count} of {expected_instance_count} "
                "cycle-created process instance(s)."
            )
            if scheduler_job is None:
                print(
                    "Status: no recurring scheduler job remains; the finite "
                    "cycle has been exhausted."
                )
            else:
                cycle_state = _recurring_timer_state(scheduler_job)
                print(
                    "Status: next cycle is queued for "
                    f"{cycle_state.next_due_at.isoformat()} with "
                    f"{cycle_state.remaining_cycles} remaining cycle(s)."
                )
            observed_instance_count = current_instance_count

        if current_instance_count >= expected_instance_count:
            print()
            print(
                "Status: the inline poller processed "
                f"{total_processed} due scheduler job(s) before the current "
                "definition produced all configured cycle-created process "
                "instances."
            )
            return

        if scheduler_job is None:
            raise RuntimeError(
                "The recurring timer-start scheduler job disappeared before "
                "all configured cycle-created process instances were created."
            )

        now = datetime.now(UTC)
        cycle_state = _recurring_timer_state(scheduler_job)
        remaining_seconds = int((cycle_state.next_due_at - now).total_seconds())
        if remaining_seconds > 0:
            print(
                "\rWaiting for cycle "
                f"{current_instance_count + 1}/{expected_instance_count} due time "
                f"{cycle_state.next_due_at.isoformat()} "
                f"({remaining_seconds}s remaining)...",
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

        process_instances, scheduler_job = _current_timer_start_runtime_state(
            engine,
            tenant_id=tenant_id,
            bpmn_process_definition_id=bpmn_process_definition_id,
            minimum_start_in_seconds=minimum_start_in_seconds,
        )
        if processed == 0:
            print(
                "\rThe current cycle is due, but no job was processed yet. "
                "Polling again...   ",
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


def _current_timer_start_runtime_state(
    engine: Engine,
    *,
    tenant_id: str,
    bpmn_process_definition_id: int,
    minimum_start_in_seconds: int | None = None,
) -> tuple[list[ProcessInstanceModel], SchedulerJobModel | None]:
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
            stmt = stmt.order_by(
                ProcessInstanceModel.start_in_seconds,
                ProcessInstanceModel.id,
            )
            process_instances = list(session.scalars(stmt).all())
            scheduler_job = _find_timer_start_job_for_definition(
                session,
                tenant_id=tenant_id,
                bpmn_process_definition_id=bpmn_process_definition_id,
            )
            return process_instances, scheduler_job
        finally:
            session.close()


def _parse_due_at(value: str) -> datetime:
    due_at = datetime.fromisoformat(value)
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=UTC)
    return due_at


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


if __name__ == "__main__":
    main()
