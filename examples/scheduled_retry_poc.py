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
RETRY_DELAY_SECONDS = 20
REPO_ROOT = Path(__file__).resolve().parents[1]
PROCESS_GROUP_ID = "m8flow-bpmn-core-examples"
PROCESS_GROUP_DISPLAY_NAME = "m8flow-bpmn-core Examples"
PROCESS_GROUP_DESCRIPTION = (
    "Process models published by the m8flow-bpmn-core examples."
)
PROCESS_MODEL_ID = "scheduled-retry-poc"
PROCESS_MODEL_IDENTIFIER = f"{PROCESS_GROUP_ID}/{PROCESS_MODEL_ID}"
PROCESS_MODEL_DISPLAY_NAME = "Scheduled Retry POC"
PRIMARY_PROCESS_ID = "Process_scheduled_retry_poc"
PRIMARY_FILE_NAME = "scheduled_retry_poc.bpmn"
RETRY_WORKER_ID = "scheduled-retry-poc-inline-worker"
RETRY_BPMN_PATH = REPO_ROOT / "tests" / "fixtures" / PRIMARY_FILE_NAME

DEMO_TENANT = {
    "id": "tenant-scheduled-retry-example",
    "name": "Scheduled Retry Example",
    "slug": "scheduled-retry-example",
}
DEMO_USERS = {
    "admin": {
        "username": "retry-poc-admin",
        "email": "retry-poc-admin@example.com",
        "service_id": "retry-poc-admin-keycloak",
        "display_name": "Retry POC Admin",
        "keycloak_groups": ("Approvers", "Viewers"),
    },
    "operator": {
        "username": "retry-poc-operator",
        "email": "retry-poc-operator@example.com",
        "service_id": "retry-poc-operator-keycloak",
        "display_name": "Retry POC Operator",
        "keycloak_groups": ("Approvers", "Viewers"),
    },
}
TASK_LANE_NAME = "Operations"


@dataclass(frozen=True, slots=True)
class RetryPocContext:
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

    print("m8flow-bpmn-core scheduled retry POC")
    print(
        "This example imports a simple workflow, starts a process instance, "
        "forces it into error, schedules a delayed retry, runs an inline "
        "poller loop, reopens the original user task, and completes the "
        "workflow so the full retry lifecycle can be audited in m8flow."
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
        "The example keeps the definition, completed process instance, and "
        "event history in place so they can be inspected after the run."
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
                context = _seed_retry_poc_context(
                    session,
                    database_url=database_url,
                )
            finally:
                session.close()

        _run_retry_poc(
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


def _seed_retry_poc_context(
    session: Session,
    *,
    database_url: str,
) -> RetryPocContext:
    print()
    print(SECTION_SEPARATOR)
    print("Setup: create the demo tenant and users")
    print(
        "The workflow itself is driven only through the public API. The tenant "
        "and users are seeded directly because the library does not expose "
        "public tenant-management commands."
    )
    _pause("Press Enter to seed the retry demo data.")

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

    context = RetryPocContext(
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
    _pause("Press Enter to continue to the retry workflow.")
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


def _run_retry_poc(
    engine: Engine,
    context: RetryPocContext,
    *,
    database_url: str,
) -> None:
    print()
    print(SECTION_SEPARATOR)
    print("Workflow plan")
    print(
        "This run starts a normal process instance, forces it into `error`, "
        f"then schedules a retry {RETRY_DELAY_SECONDS} seconds in the future."
    )
    print(
        "Once the due time is reached, the inline poller will call "
        "`api.run_due_scheduler_jobs(...)` and the library will execute the "
        "same retry lifecycle that an immediate `process.retry` command uses."
    )
    print(
        "The key outcomes to watch are that the same process instance is "
        "reused, the same human task id becomes READY again, and completing "
        "that reopened task finishes the workflow."
    )
    _pause("Press Enter to continue to the import step.")

    bpmn_xml = _read_retry_bpmn_xml()
    import_timestamp = round(time.time())
    definition = _run_command_step(
        engine,
        step_number=1,
        title="Import the retry demonstration definition",
        context_text=(
            "This stores a simple user-task workflow that will later be put "
            "into error and retried."
        ),
        command=api.ImportBpmnProcessDefinitionCommand(
            tenant_id=context.tenant_id,
            bpmn_identifier=PROCESS_MODEL_IDENTIFIER,
            user_id=context.admin_user_id,
            bpmn_name=PROCESS_MODEL_DISPLAY_NAME,
            source_bpmn_xml=bpmn_xml,
            properties_json={
                "version": 1,
                "flow": "scheduled_retry_poc",
                "lane_owners": {
                    TASK_LANE_NAME: [context.operator_username],
                },
                "retry_delay_seconds": RETRY_DELAY_SECONDS,
            },
            bpmn_version_control_type="git",
            bpmn_version_control_identifier="retry-poc",
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
            "This is a normal `process.start` flow. The new instance should "
            "stop at its first human task and assign that task through the "
            "BPMN lane plus properties_json['lane_owners']."
        ),
        command=api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=context.tenant_id,
            bpmn_process_definition_id=definition.id,
            process_initiator_id=context.admin_user_id,
            summary="Scheduled retry demonstration instance",
            started_at_in_seconds=started_at_in_seconds,
        ),
    )
    if process_instance is None:
        raise RuntimeError("Process start did not return a process instance")

    operator_tasks = _run_command_step(
        engine,
        step_number=3,
        title="List the operator pending tasks",
        context_text=(
            "This shows the instance in its normal waiting state before any "
            "failure or retry scheduling is involved."
        ),
        command=api.GetPendingTasksQuery(
            tenant_id=context.tenant_id,
            user_id=context.operator_user_id,
        ),
    )
    if operator_tasks is None:
        raise RuntimeError("Pending-task query did not return a task list")
    operator_task = _require_single_task(
        operator_tasks,
        "scheduled retry task",
        process_instance_id=process_instance.id,
    )

    errored_at_in_seconds = round(time.time())
    errored_process_instance = _run_command_step(
        engine,
        step_number=4,
        title="Force the running instance into error",
        context_text=(
            "This simulates an operator or host application marking the "
            "instance as failed. The runtime state and current human task are "
            "closed so the instance can be retried later."
        ),
        command=api.ErrorProcessInstanceCommand(
            tenant_id=context.tenant_id,
            process_instance_id=process_instance.id,
            user_id=context.admin_user_id,
            errored_at_in_seconds=errored_at_in_seconds,
        ),
    )
    if errored_process_instance is None:
        raise RuntimeError("Erroring the process instance did not return a result")

    error_instances = _run_command_step(
        engine,
        step_number=5,
        title="List errored process instances",
        context_text=(
            "Only errored instances can be scheduled for retry, so the current "
            "instance should now be visible in the tenant's error list."
        ),
        command=api.ListErrorProcessInstancesQuery(tenant_id=context.tenant_id),
    )
    if error_instances is None:
        raise RuntimeError("Error-instance query did not return a result")
    if process_instance.id not in {item.id for item in error_instances}:
        raise RuntimeError(
            "Expected the current process instance to appear in the tenant "
            "error list after being marked errored."
        )

    print()
    print(SECTION_SEPARATOR)
    print("Step 6: Schedule a delayed retry")
    print(
        "This persists a tenant-scoped `process_retry` scheduler row for the "
        "current process instance. The due time is generated immediately "
        "before the command runs so interactive pauses do not make it stale."
    )
    print(SECTION_SEPARATOR)
    _pause(
        "Press Enter to stamp a fresh retry due time and execute the "
        "scheduling command."
    )

    retry_due_at = datetime.now(UTC).replace(microsecond=0) + timedelta(
        seconds=RETRY_DELAY_SECONDS
    )
    retry_due_at_in_seconds = int(retry_due_at.timestamp())
    retry_scheduled_at_in_seconds = round(time.time())
    schedule_retry_command = api.ScheduleProcessInstanceRetryCommand(
        tenant_id=context.tenant_id,
        process_instance_id=process_instance.id,
        user_id=context.admin_user_id,
        retry_at_in_seconds=retry_due_at_in_seconds,
        scheduled_at_in_seconds=retry_scheduled_at_in_seconds,
    )
    print("Command:")
    print(pformat(schedule_retry_command, sort_dicts=False, width=100))
    print(
        "Status: executing retry scheduling with due time "
        f"{retry_due_at.isoformat()}."
    )
    with engine.begin() as connection:
        scheduled_retry_job = api.execute_command(connection, schedule_retry_command)
    print("Status: command complete and committed.")
    print("Result:")
    print(
        pformat(
            _summarize_scheduler_job(scheduled_retry_job),
            sort_dicts=False,
            width=100,
        )
    )
    _pause("Press Enter to continue.")

    with engine.begin() as connection:
        session = Session(bind=connection, autoflush=False, expire_on_commit=False)
        try:
            persisted_retry_job = _load_process_retry_job(
                session,
                tenant_id=context.tenant_id,
                process_instance_id=process_instance.id,
            )
            print()
            print(SECTION_SEPARATOR)
            print("Persisted scheduler job")
            print(
                pformat(
                    _summarize_scheduler_job(persisted_retry_job),
                    sort_dicts=False,
                    width=100,
                )
            )
        finally:
            session.close()

    _pause(
        "Press Enter to start the inline scheduler loop and wait for the retry."
    )
    _run_scheduler_until_retry_executes(
        engine,
        tenant_id=context.tenant_id,
        process_instance_id=process_instance.id,
        operator_user_id=context.operator_user_id,
        human_task_id=operator_task.id,
        retry_due_at=retry_due_at,
    )

    retried_process_instance = _run_command_step(
        engine,
        step_number=7,
        title="Read back the retried process instance",
        context_text=(
            "The same process instance should now be running again. Retry "
            "clears the previous end timestamp and reopens the runtime state "
            "instead of creating a new instance."
        ),
        command=api.GetProcessInstanceQuery(
            tenant_id=context.tenant_id,
            process_instance_id=process_instance.id,
        ),
    )
    if retried_process_instance is None:
        raise RuntimeError("Retried process query did not return a process instance")

    operator_tasks_after_retry = _run_command_step(
        engine,
        step_number=8,
        title="List the operator pending tasks after retry",
        context_text=(
            "The key retry outcome is that the original human task comes back "
            "READY on the same process instance. The next steps will claim "
            "and complete that same task to show the workflow finishing cleanly."
        ),
        command=api.GetPendingTasksQuery(
            tenant_id=context.tenant_id,
            user_id=context.operator_user_id,
        ),
    )
    if operator_tasks_after_retry is None:
        raise RuntimeError("Post-retry pending-task query did not return a task list")
    reopened_task = _require_single_task(
        operator_tasks_after_retry,
        "reopened scheduled retry task",
        task_id=operator_task.id,
        process_instance_id=process_instance.id,
    )
    if reopened_task.id != operator_task.id:
        raise RuntimeError(
            "Expected retry to reopen the original human task row, but it "
            "returned a different task id."
        )

    print()
    print(SECTION_SEPARATOR)
    print("Reopened task identity")
    print(
        pformat(
            {
                "process_instance_id": process_instance.id,
                "original_task_id": operator_task.id,
                "reopened_task_id": reopened_task.id,
                "task_name": reopened_task.task_name,
                "task_status": reopened_task.task_status,
            },
            sort_dicts=False,
            width=100,
        )
    )

    error_instances_after_retry = _run_command_step(
        engine,
        step_number=9,
        title="List errored process instances after retry",
        context_text=(
            "Once the due retry is executed, the current instance should no "
            "longer be present in the tenant's errored-instance list."
        ),
        command=api.ListErrorProcessInstancesQuery(tenant_id=context.tenant_id),
    )
    if error_instances_after_retry is None:
        raise RuntimeError("Post-retry error-instance query did not return a result")
    if process_instance.id in {item.id for item in error_instances_after_retry}:
        raise RuntimeError(
            "Expected the current process instance to leave the error list "
            "after the scheduled retry executed."
        )

    _run_command_step(
        engine,
        step_number=10,
        title="Claim the reopened user task",
        context_text=(
            "Retry reopens the task in READY state; task interaction after "
            "retry is still the normal claim-and-complete lifecycle."
        ),
        command=api.ClaimTaskCommand(
            tenant_id=context.tenant_id,
            human_task_id=reopened_task.id,
            user_id=context.operator_user_id,
        ),
    )

    _run_command_step(
        engine,
        step_number=11,
        title="Complete the reopened user task",
        context_text=(
            "Completing the reopened task proves that delayed retry hands the "
            "process back to the normal workflow path rather than leaving it "
            "in a special retry-only state."
        ),
        command=api.CompleteTaskCommand(
            tenant_id=context.tenant_id,
            human_task_id=reopened_task.id,
            user_id=context.operator_user_id,
            completed_at_in_seconds=round(time.time()),
            task_payload={
                "completed_after_retry": "true",
                "completed_by": context.operator_username,
                "completed_at": datetime.now(UTC).replace(
                    microsecond=0
                ).isoformat(),
            },
        ),
    )

    completed_process_instance = _run_command_step(
        engine,
        step_number=12,
        title="Read back the completed process instance",
        context_text=(
            "After the reopened task is completed, the same process instance "
            "should move to its normal completed end state."
        ),
        command=api.GetProcessInstanceQuery(
            tenant_id=context.tenant_id,
            process_instance_id=process_instance.id,
        ),
    )
    if completed_process_instance is None:
        raise RuntimeError(
            "Completed process query did not return a process instance"
        )

    pending_tasks_after_completion = _run_command_step(
        engine,
        step_number=13,
        title="List the operator pending tasks after completion",
        context_text=(
            "Once the reopened task is completed, this process instance should "
            "no longer contribute any pending work for the operator."
        ),
        command=api.GetPendingTasksQuery(
            tenant_id=context.tenant_id,
            user_id=context.operator_user_id,
        ),
    )
    if pending_tasks_after_completion is None:
        raise RuntimeError(
            "Post-completion pending-task query did not return a task list"
        )
    remaining_instance_tasks = [
        task
        for task in pending_tasks_after_completion
        if task.process_instance_id == process_instance.id
    ]
    if remaining_instance_tasks:
        raise RuntimeError(
            "Expected no remaining pending tasks for the completed retried "
            f"instance, but found {len(remaining_instance_tasks)}."
        )

    _run_command_step(
        engine,
        step_number=14,
        title="Read back the event history",
        context_text=(
            "The event log should now show the full lifecycle: creation, "
            "error, retry, task completion, and process completion."
        ),
        command=api.GetProcessInstanceEventsQuery(
            tenant_id=context.tenant_id,
            process_instance_id=process_instance.id,
        ),
    )
    _print_note(
        "The scheduled retry POC is complete. The database rows were left in "
        "place so the completed retried instance can still be audited."
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
    deployment = _deploy_retry_definition_to_m8flow_backend(
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


def _deploy_retry_definition_to_m8flow_backend(
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
            "Refreshed the existing scheduled-retry POC deployment so the UI "
            "matches this run's BPMN model."
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
            "Published by the m8flow-bpmn-core scheduled retry example."
        ),
        "display_name": PROCESS_MODEL_DISPLAY_NAME,
        "exception_notification_addresses": [],
        "fault_or_suspend_on_exception": "fault",
        "metadata_extraction_paths": None,
        "primary_file_name": PRIMARY_FILE_NAME,
        "primary_process_id": PRIMARY_PROCESS_ID,
    }


def _read_retry_bpmn_xml() -> str:
    return RETRY_BPMN_PATH.read_text(encoding="utf-8")


def _load_process_retry_job(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
) -> SchedulerJobModel:
    scheduler_job = session.scalar(
        select(SchedulerJobModel).where(
            SchedulerJobModel.m8f_tenant_id == tenant_id,
            SchedulerJobModel.process_instance_id == process_instance_id,
            SchedulerJobModel.job_type == "process_retry",
        )
    )
    if scheduler_job is None:
        raise RuntimeError(
            "Expected a persisted process-retry scheduler job for process "
            f"instance {process_instance_id}, but none was found."
        )
    return scheduler_job


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


def _run_scheduler_until_retry_executes(
    engine: Engine,
    *,
    tenant_id: str,
    process_instance_id: int,
    operator_user_id: int,
    human_task_id: int,
    retry_due_at: datetime,
) -> None:
    print()
    print(SECTION_SEPARATOR)
    print("Inline scheduler loop")
    print(
        "The host application is responsible for wake-up cadence. This loop "
        "polls once per second and waits for the current process instance to "
        "leave `error` and reopen its original task."
    )

    total_processed = 0
    while True:
        process_instance, scheduler_job, task_reopened = _current_retry_runtime_state(
            engine,
            tenant_id=tenant_id,
            process_instance_id=process_instance_id,
            operator_user_id=operator_user_id,
            human_task_id=human_task_id,
        )
        if (
            process_instance.status == api.ProcessInstanceStatus.running.value
            and scheduler_job is None
            and task_reopened
        ):
            print()
            print(
                "Status: the inline poller processed "
                f"{total_processed} due scheduler job(s) before the current "
                "process instance reopened its task."
            )
            return

        now = datetime.now(UTC)
        remaining_seconds = int((retry_due_at - now).total_seconds())
        if remaining_seconds > 0:
            print(
                "\rWaiting for retry due time "
                f"{retry_due_at.isoformat()} ({remaining_seconds}s remaining)...",
                end="",
                flush=True,
            )
            time.sleep(1)
            continue

        with engine.begin() as connection:
            processed = api.run_due_scheduler_jobs(
                connection,
                worker_id=RETRY_WORKER_ID,
                tenant_id=tenant_id,
            )
        total_processed += processed

        process_instance, scheduler_job, task_reopened = _current_retry_runtime_state(
            engine,
            tenant_id=tenant_id,
            process_instance_id=process_instance_id,
            operator_user_id=operator_user_id,
            human_task_id=human_task_id,
        )
        if (
            process_instance.status == api.ProcessInstanceStatus.running.value
            and scheduler_job is None
            and task_reopened
        ):
            print()
            print(
                "Status: the inline poller processed "
                f"{total_processed} due scheduler job(s) before the current "
                "process instance reopened its task."
            )
            return

        if (
            process_instance.status == api.ProcessInstanceStatus.error.value
            and scheduler_job is None
        ):
            raise RuntimeError(
                "The retry scheduler job disappeared, but the current process "
                "instance is still in error."
            )

        if processed == 0:
            print(
                "\rRetry is due, but no job was processed yet. Polling again...   ",
                end="",
                flush=True,
            )
        else:
            print(
                "\rProcessed due work for this tenant, but the current process "
                "instance has not reopened its task yet. Polling again...   ",
                end="",
                flush=True,
            )
            time.sleep(1)


def _current_retry_runtime_state(
    engine: Engine,
    *,
    tenant_id: str,
    process_instance_id: int,
    operator_user_id: int,
    human_task_id: int,
) -> tuple[ProcessInstanceModel, SchedulerJobModel | None, bool]:
    with engine.begin() as connection:
        session = Session(
            bind=connection,
            autoflush=False,
            expire_on_commit=False,
        )
        try:
            process_instance = api.execute_query(
                session,
                api.GetProcessInstanceQuery(
                    tenant_id=tenant_id,
                    process_instance_id=process_instance_id,
                ),
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
                    SchedulerJobModel.job_type == "process_retry",
                )
            )
            task_reopened = any(
                task.id == human_task_id
                and task.process_instance_id == process_instance_id
                for task in pending_tasks
            )
            return process_instance, scheduler_job, task_reopened
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
