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
from m8flow_bpmn_core.models.human_task import HumanTaskModel
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
BOUNDARY_TIMER_DELAY_SECONDS = 60
REPO_ROOT = Path(__file__).resolve().parents[1]
PROCESS_GROUP_ID = "m8flow-bpmn-core-examples"
PROCESS_GROUP_DISPLAY_NAME = "m8flow-bpmn-core Examples"
PROCESS_GROUP_DESCRIPTION = (
    "Process models published by the m8flow-bpmn-core examples."
)
PROCESS_MODEL_ID = "scheduled-boundary-timer-poc"
PROCESS_MODEL_IDENTIFIER = f"{PROCESS_GROUP_ID}/{PROCESS_MODEL_ID}"
PROCESS_MODEL_DISPLAY_NAME = "Scheduled Boundary Timer POC"
PRIMARY_PROCESS_ID = "Process_scheduled_boundary_timer_poc"
PRIMARY_FILE_NAME = "scheduled_boundary_timer_poc.bpmn"
BOUNDARY_TIMER_WORKER_ID = "scheduled-boundary-timer-poc-inline-worker"
BOUNDARY_TIMER_BPMN_PATH = REPO_ROOT / "tests" / "fixtures" / PRIMARY_FILE_NAME
TASK_LANE_NAME = "Operations"

DEMO_TENANT = {
    "id": "tenant-scheduled-boundary-timer-example",
    "name": "Scheduled Boundary Timer Example",
    "slug": "scheduled-boundary-timer-example",
}
DEMO_USERS = {
    "admin": {
        "username": "boundary-timer-poc-admin",
        "email": "boundary-timer-poc-admin@example.com",
        "service_id": "boundary-timer-poc-admin-keycloak",
        "display_name": "Boundary Timer POC Admin",
        "keycloak_groups": ("Approvers", "Viewers"),
    },
    "operator": {
        "username": "boundary-timer-poc-operator",
        "email": "boundary-timer-poc-operator@example.com",
        "service_id": "boundary-timer-poc-operator-keycloak",
        "display_name": "Boundary Timer POC Operator",
        "keycloak_groups": ("Approvers", "Viewers"),
    },
}


@dataclass(frozen=True, slots=True)
class BoundaryTimerPocContext:
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

    print("m8flow-bpmn-core scheduled boundary timer POC")
    print(
        "This example imports an interrupting timer boundary-event workflow, "
        "starts a normal process instance, persists the waiting timer in "
        "scheduler_job, runs an inline poller loop, and shows the timer "
        "cancelling the active task and opening the timeout path."
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
        "The example keeps the definition, process instance, human tasks, and "
        "event history in place so the timeout transition can be audited after "
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
                context = _seed_boundary_timer_poc_context(
                    session,
                    database_url=database_url,
                )
            finally:
                session.close()

        _run_boundary_timer_poc(
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


def _seed_boundary_timer_poc_context(
    session: Session,
    *,
    database_url: str,
) -> BoundaryTimerPocContext:
    print()
    print(SECTION_SEPARATOR)
    print("Setup: create the demo tenant and users")
    print(
        "The workflow itself is driven only through the public API. The tenant "
        "and users are seeded directly because the library does not expose "
        "public tenant-management commands."
    )
    _pause("Press Enter to seed the boundary timer demo data.")

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

    context = BoundaryTimerPocContext(
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
    _pause("Press Enter to continue to the boundary timer workflow.")
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


def _run_boundary_timer_poc(
    engine: Engine,
    context: BoundaryTimerPocContext,
    *,
    database_url: str,
) -> None:
    print()
    print(SECTION_SEPARATOR)
    print("Workflow plan")
    print(
        "This run starts a normal process instance and attaches an interrupting "
        f"timer boundary event due {BOUNDARY_TIMER_DELAY_SECONDS} seconds later."
    )
    print(
        "Before the due time, the operator sees the original review task. When "
        "the timer becomes due, the inline poller calls "
        "`api.run_due_scheduler_jobs(...)`, the library cancels that active "
        "task, and the timeout user task becomes READY on the same process "
        "instance."
    )
    _pause("Press Enter to continue to the import step.")

    print()
    print(SECTION_SEPARATOR)
    print("Step 1: Import the boundary-timer definition")
    print(
        "This stores the BPMN definition. The boundary-timer scheduler row is "
        "not created yet because it belongs to a specific process instance and "
        "will be synchronized when the workflow is started."
    )
    print(SECTION_SEPARATOR)
    _pause(
        "Press Enter to stamp a fresh boundary due time and execute the import "
        "command."
    )

    boundary_due_at = (
        datetime.now(UTC).replace(microsecond=0)
        + timedelta(seconds=BOUNDARY_TIMER_DELAY_SECONDS)
    )
    bpmn_xml = _render_boundary_timer_bpmn_xml(boundary_due_at)
    import_timestamp = round(time.time())
    definition = _run_command_step(
        engine,
        step_number=1,
        title="Import the boundary-timer demonstration definition",
        context_text=(
            "The BPMN fixture uses an interrupting timer boundary event "
            "attached to the first user task. The quoted `timeDate` expression "
            "is rendered immediately before import so interactive pauses do not "
            "make the timer stale."
        ),
        command=api.ImportBpmnProcessDefinitionCommand(
            tenant_id=context.tenant_id,
            bpmn_identifier=PROCESS_MODEL_IDENTIFIER,
            user_id=context.admin_user_id,
            bpmn_name=PROCESS_MODEL_DISPLAY_NAME,
            source_bpmn_xml=bpmn_xml,
            properties_json={
                "version": 1,
                "flow": "scheduled_boundary_timer_poc",
                "lane_owners": {
                    TASK_LANE_NAME: [context.operator_username],
                },
                "boundary_due_at": boundary_due_at.isoformat(),
            },
            bpmn_version_control_type="git",
            bpmn_version_control_identifier="boundary-timer-poc",
            created_at_in_seconds=import_timestamp,
            updated_at_in_seconds=import_timestamp,
        ),
    )
    if definition is None:
        raise RuntimeError("Definition import did not return a process definition")

    deployment = _maybe_deploy_process_model_to_m8flow_backend(
        database_url=database_url,
        tenant_id=context.tenant_id,
        tenant_slug=context.tenant_slug,
        bpmn_xml=bpmn_xml,
    )
    if deployment is not None:
        _print_backend_deployment_summary(deployment)

    started_at_in_seconds = round(time.time())
    process_instance = _run_command_step(
        engine,
        step_number=2,
        title="Start a process instance from the stored definition",
        context_text=(
            "Starting the process persists the workflow state, materializes the "
            "first user task, and synchronizes one instance-scoped "
            "`intermediate_timer` scheduler row for the attached boundary timer."
        ),
        command=api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=context.tenant_id,
            bpmn_process_definition_id=definition.id,
            process_initiator_id=context.admin_user_id,
            summary="Scheduled boundary timer demonstration instance",
            started_at_in_seconds=started_at_in_seconds,
        ),
    )
    if process_instance is None:
        raise RuntimeError("Process start did not return a process instance")

    operator_tasks = _run_command_step(
        engine,
        step_number=3,
        title="List the operator pending tasks before the timeout fires",
        context_text=(
            "The process should now be paused at the original review task. The "
            "boundary timer exists in waiting state beside it, but the timeout "
            "path should not be active yet."
        ),
        command=api.GetPendingTasksQuery(
            tenant_id=context.tenant_id,
            user_id=context.operator_user_id,
        ),
    )
    if operator_tasks is None:
        raise RuntimeError("Pending-task query did not return a task list")
    review_task = _require_single_task(
        operator_tasks,
        "boundary-timer review task",
        process_instance_id=process_instance.id,
        task_name="Task_review",
    )

    with engine.begin() as connection:
        session = Session(bind=connection, autoflush=False, expire_on_commit=False)
        try:
            scheduler_job = _load_boundary_timer_job(
                session,
                tenant_id=context.tenant_id,
                process_instance_id=process_instance.id,
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

    _pause(
        "Press Enter to start the inline scheduler loop and wait for the "
        "boundary timer."
    )
    _run_scheduler_until_boundary_timer_fires(
        engine,
        tenant_id=context.tenant_id,
        process_instance_id=process_instance.id,
        operator_user_id=context.operator_user_id,
        review_human_task_id=review_task.id,
        boundary_due_at=boundary_due_at,
    )

    operator_tasks_after_timeout = _run_command_step(
        engine,
        step_number=4,
        title="List the operator pending tasks after the timeout fires",
        context_text=(
            "The original review task should be gone from the pending list, and "
            "the timeout escalation task should now be READY on the same "
            "process instance."
        ),
        command=api.GetPendingTasksQuery(
            tenant_id=context.tenant_id,
            user_id=context.operator_user_id,
        ),
    )
    if operator_tasks_after_timeout is None:
        raise RuntimeError(
            "Post-timeout pending-task query did not return a task list"
        )
    timeout_task = _require_single_task(
        operator_tasks_after_timeout,
        "boundary timeout task",
        process_instance_id=process_instance.id,
        task_name="Task_timeout",
    )

    with engine.begin() as connection:
        session = Session(bind=connection, autoflush=False, expire_on_commit=False)
        try:
            current_human_tasks = _load_process_human_tasks(
                session,
                tenant_id=context.tenant_id,
                process_instance_id=process_instance.id,
            )
            cancelled_review_task = next(
                task for task in current_human_tasks if task.id == review_task.id
            )
            ready_timeout_task = next(
                task for task in current_human_tasks if task.id == timeout_task.id
            )
            print()
            print(SECTION_SEPARATOR)
            print("Boundary transition summary")
            print(
                pformat(
                    {
                        "process_instance_id": process_instance.id,
                        "cancelled_review_task": _summarize_human_task(
                            cancelled_review_task
                        ),
                        "ready_timeout_task": _summarize_human_task(
                            ready_timeout_task
                        ),
                    },
                    sort_dicts=False,
                    width=100,
                )
            )
        finally:
            session.close()

    _run_command_step(
        engine,
        step_number=5,
        title="Claim the timeout task",
        context_text=(
            "After the boundary timer interrupts the original task, the timeout "
            "path is still handled through the normal claim-and-complete task "
            "lifecycle."
        ),
        command=api.ClaimTaskCommand(
            tenant_id=context.tenant_id,
            human_task_id=timeout_task.id,
            user_id=context.operator_user_id,
        ),
    )

    _run_command_step(
        engine,
        step_number=6,
        title="Complete the timeout task",
        context_text=(
            "Completing the timeout task shows that an interrupted boundary "
            "path hands off cleanly to normal workflow completion."
        ),
        command=api.CompleteTaskCommand(
            tenant_id=context.tenant_id,
            human_task_id=timeout_task.id,
            user_id=context.operator_user_id,
            completed_at_in_seconds=round(time.time()),
            task_payload={
                "completed_after_boundary_timeout": "true",
                "completed_by": context.operator_username,
                "completed_at": datetime.now(UTC).replace(
                    microsecond=0
                ).isoformat(),
            },
        ),
    )

    _run_command_step(
        engine,
        step_number=7,
        title="Read back the completed process instance",
        context_text=(
            "The same process instance should now be complete after the timeout "
            "path task is finished."
        ),
        command=api.GetProcessInstanceQuery(
            tenant_id=context.tenant_id,
            process_instance_id=process_instance.id,
        ),
    )

    _run_command_step(
        engine,
        step_number=8,
        title="Read back the event history",
        context_text=(
            "The event log should show the boundary interruption through "
            "`task_cancelled`, followed by the timeout task completion and "
            "process completion."
        ),
        command=api.GetProcessInstanceEventsQuery(
            tenant_id=context.tenant_id,
            process_instance_id=process_instance.id,
        ),
    )
    _print_note(
        "The scheduled boundary timer POC is complete. The database rows were "
        "left in place so the interrupted task and timeout path can still be "
        "audited."
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
        "also publish the BPMN file into the local m8flow backend process-"
        "model catalog so the model is visible in the UI."
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
    deployment = _deploy_boundary_timer_definition_to_m8flow_backend(
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


def _deploy_boundary_timer_definition_to_m8flow_backend(
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
            "Refreshed the existing scheduled-boundary-timer POC deployment so "
            "the UI matches this run's BPMN model."
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
            "Published by the m8flow-bpmn-core scheduled boundary timer example."
        ),
        "display_name": PROCESS_MODEL_DISPLAY_NAME,
        "exception_notification_addresses": [],
        "fault_or_suspend_on_exception": "fault",
        "metadata_extraction_paths": None,
        "primary_file_name": PRIMARY_FILE_NAME,
        "primary_process_id": PRIMARY_PROCESS_ID,
    }


def _render_boundary_timer_bpmn_xml(boundary_due_at: datetime) -> str:
    return BOUNDARY_TIMER_BPMN_PATH.read_text(encoding="utf-8").replace(
        "__BOUNDARY_TIMER_DUE_AT__",
        boundary_due_at.isoformat(),
    )


def _load_boundary_timer_job(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
) -> SchedulerJobModel:
    scheduler_job = session.scalar(
        select(SchedulerJobModel).where(
            SchedulerJobModel.m8f_tenant_id == tenant_id,
            SchedulerJobModel.process_instance_id == process_instance_id,
            SchedulerJobModel.job_type == "intermediate_timer",
        )
    )
    if scheduler_job is None:
        raise RuntimeError(
            "Expected a persisted boundary-timer scheduler job for process "
            f"instance {process_instance_id}, but none was found."
        )
    return scheduler_job


def _load_process_human_tasks(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
) -> list[HumanTaskModel]:
    return list(
        session.scalars(
            select(HumanTaskModel).where(
                HumanTaskModel.m8f_tenant_id == tenant_id,
                HumanTaskModel.process_instance_id == process_instance_id,
            )
        ).all()
    )


def _summarize_scheduler_job(scheduler_job: SchedulerJobModel) -> dict[str, Any]:
    return {
        "id": scheduler_job.id,
        "job_key": scheduler_job.job_key,
        "job_type": scheduler_job.job_type,
        "tenant_id": scheduler_job.m8f_tenant_id,
        "process_instance_id": scheduler_job.process_instance_id,
        "definition_id": scheduler_job.bpmn_process_definition_id,
        "run_at_in_seconds": scheduler_job.run_at_in_seconds,
        "locked_by": scheduler_job.locked_by,
        "payload_json": scheduler_job.payload_json,
    }


def _summarize_human_task(task: HumanTaskModel) -> dict[str, Any]:
    return {
        "id": task.id,
        "task_name": task.task_name,
        "task_status": task.task_status,
        "completed": task.completed,
        "process_instance_id": task.process_instance_id,
        "actual_owner_id": task.actual_owner_id,
        "completed_by_user_id": task.completed_by_user_id,
    }


def _run_scheduler_until_boundary_timer_fires(
    engine: Engine,
    *,
    tenant_id: str,
    process_instance_id: int,
    operator_user_id: int,
    review_human_task_id: int,
    boundary_due_at: datetime,
) -> None:
    print()
    print(SECTION_SEPARATOR)
    print("Inline scheduler loop")
    print(
        "The host application is responsible for wake-up cadence. This loop "
        "polls once per second and waits for the current process instance to "
        "switch from its review task to the timeout task."
    )

    total_processed = 0
    while True:
        (
            review_task,
            timeout_task,
            scheduler_job,
        ) = _current_boundary_timer_runtime_state(
            engine,
            tenant_id=tenant_id,
            process_instance_id=process_instance_id,
            operator_user_id=operator_user_id,
            review_human_task_id=review_human_task_id,
        )
        if (
            timeout_task is not None
            and review_task is not None
            and review_task.completed
        ):
            print()
            print(
                "Status: the inline poller processed "
                f"{total_processed} due scheduler job(s) before the timeout "
                "task became READY."
            )
            return

        now = datetime.now(UTC)
        remaining_seconds = int((boundary_due_at - now).total_seconds())
        if remaining_seconds > 0:
            print(
                "\rWaiting for boundary timer due time "
                f"{boundary_due_at.isoformat()} ({remaining_seconds}s remaining)...",
                end="",
                flush=True,
            )
            time.sleep(1)
            continue

        with engine.begin() as connection:
            processed = api.run_due_scheduler_jobs(
                connection,
                worker_id=BOUNDARY_TIMER_WORKER_ID,
                tenant_id=tenant_id,
            )
        total_processed += processed

        (
            review_task,
            timeout_task,
            scheduler_job,
        ) = _current_boundary_timer_runtime_state(
            engine,
            tenant_id=tenant_id,
            process_instance_id=process_instance_id,
            operator_user_id=operator_user_id,
            review_human_task_id=review_human_task_id,
        )
        if (
            timeout_task is not None
            and review_task is not None
            and review_task.completed
        ):
            print()
            print(
                "Status: the inline poller processed "
                f"{total_processed} due scheduler job(s) before the timeout "
                "task became READY."
            )
            return
        if scheduler_job is None:
            raise RuntimeError(
                "The boundary-timer scheduler job disappeared, but the timeout "
                "task did not become READY for the current process instance."
            )
        time.sleep(1)


def _current_boundary_timer_runtime_state(
    engine: Engine,
    *,
    tenant_id: str,
    process_instance_id: int,
    operator_user_id: int,
    review_human_task_id: int,
) -> tuple[HumanTaskModel | None, HumanTaskModel | None, SchedulerJobModel | None]:
    with engine.begin() as connection:
        session = Session(bind=connection, autoflush=False, expire_on_commit=False)
        try:
            human_tasks = _load_process_human_tasks(
                session,
                tenant_id=tenant_id,
                process_instance_id=process_instance_id,
            )
            pending_tasks = api.execute_query(
                session,
                api.GetPendingTasksQuery(
                    tenant_id=tenant_id,
                    user_id=operator_user_id,
                ),
            )
            scheduler_job = session.scalar(
                select(SchedulerJobModel).where(
                    SchedulerJobModel.m8f_tenant_id == tenant_id,
                    SchedulerJobModel.process_instance_id == process_instance_id,
                    SchedulerJobModel.job_type == "intermediate_timer",
                )
            )
            review_task = next(
                (task for task in human_tasks if task.id == review_human_task_id),
                None,
            )
            timeout_task = next(
                (
                    task
                    for task in pending_tasks
                    if task.process_instance_id == process_instance_id
                    and task.task_name == "Task_timeout"
                ),
                None,
            )
            return review_task, timeout_task, scheduler_job
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


if __name__ == "__main__":
    main()
