from __future__ import annotations

# ruff: noqa: E402
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from pprint import pformat
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from conditional_approval_poc import (
    EXAMPLE_KEYCLOAK_DEFAULT_PASSWORD,
    BackendProcessModelDeployment,
    _align_shared_db_tenants_with_keycloak_organizations,
    _confirm_shared_database_usage,
    _describe_connection,
    _format_command,
    _format_connection_details,
    _format_result,
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
    _wait_for_database,
    _write_json_file,
)
from SpiffWorkflow.util.task import TaskState
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.db import build_engine, create_schema, session_scope
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.services.authorization import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ensure_v1_role,
)
from m8flow_bpmn_core.services.workflow_runtime import (
    _load_process_definition,
    _prepare_process_instance_from_definition_in_session,
    _restore_workflow,
)
from m8flow_bpmn_core.utils.keycloak import (
    KeycloakOrganizationSpec,
    KeycloakProvisioningError,
    KeycloakUserSpec,
    ProvisionedKeycloakSharedRealmContext,
    ensure_shared_realm_organizations_and_users,
)

SECTION_SEPARATOR = "=" * 88
PROCESS_GROUP_ID = "m8flow-bpmn-core-examples"
PROCESS_GROUP_DISPLAY_NAME = "m8flow-bpmn-core Examples"
PROCESS_GROUP_DESCRIPTION = (
    "Process models published by the m8flow-bpmn-core examples."
)
PROCESS_MODEL_ID = "service-task-failure-retry-poc"
PROCESS_MODEL_IDENTIFIER = f"{PROCESS_GROUP_ID}/{PROCESS_MODEL_ID}"
PROCESS_MODEL_DISPLAY_NAME = "Service Task Failure Retry POC"
PRIMARY_PROCESS_ID = "Process_service_task_failure_retry_poc"
PRIMARY_FILE_NAME = "service_task_failure_retry_poc.bpmn"
FAILURE_RETRY_BPMN_PATH = REPO_ROOT / "tests" / "fixtures" / PRIMARY_FILE_NAME

TASK_LANE_NAME = "Operations"
SUBMISSION_MESSAGE = "rollback-safe-service-task-failure"

DEMO_TENANT = {
    "id": "tenant-service-task-failure-retry-example",
    "name": "Service Task Failure Retry Example",
    "slug": "service-task-failure-retry-example",
}
DEMO_USERS = {
    "admin": {
        "username": "service-task-failure-admin",
        "email": "service-task-failure-admin@example.com",
        "service_id": "service-task-failure-admin-keycloak",
        "display_name": "Service Task Failure Admin",
        "keycloak_groups": ("Approvers", "Viewers"),
    },
    "operator": {
        "username": "service-task-failure-operator",
        "email": "service-task-failure-operator@example.com",
        "service_id": "service-task-failure-operator-keycloak",
        "display_name": "Service Task Failure Operator",
        "keycloak_groups": ("Approvers", "Viewers"),
    },
}


@dataclass(frozen=True, slots=True)
class ServiceTaskFailureRetryPocContext:
    tenant_id: str
    tenant_slug: str
    admin_user_id: int
    admin_username: str
    operator_user_id: int
    operator_username: str
    keycloak_password: str | None


@dataclass(frozen=True, slots=True)
class RecordedServiceTaskInvocation:
    attempt: int
    operation_id: str
    parameters: dict[str, Any]
    outcome: str
    error_message: str | None
    tenant_id: str
    process_instance_id: int | None
    process_definition_id: int | None
    task_name: str | None
    task_type: str | None
    timestamp: str


@dataclass(slots=True)
class RecordingDemoServiceTaskConnector:
    connector_key: str = "demo"
    recorded_invocations: list[RecordedServiceTaskInvocation] = field(
        default_factory=list
    )
    _attempt_counter: int = 0

    def list_commands(self) -> tuple[api.ServiceTaskCommandDefinition, ...]:
        return (
            api.ServiceTaskCommandDefinition(
                connector_key=self.connector_key,
                command_name="PrepareReview",
            ),
            api.ServiceTaskCommandDefinition(
                connector_key=self.connector_key,
                command_name="FinalizeReview",
            ),
        )

    def execute(self, request: api.ServiceTaskRequest) -> api.ServiceTaskResult:
        self._attempt_counter += 1
        parameters = dict(request.parameters or {})
        context = request.context
        timestamp = datetime.now(UTC).replace(microsecond=0).isoformat()

        self.recorded_invocations.append(
            RecordedServiceTaskInvocation(
                attempt=self._attempt_counter,
                operation_id=request.operation_id,
                parameters=parameters,
                outcome="success",
                error_message=None,
                tenant_id=context.tenant_id if context else "",
                process_instance_id=context.process_instance_id if context else None,
                process_definition_id=(
                    context.process_definition_id if context else None
                ),
                task_name=context.task_name if context else None,
                task_type=context.task_type if context else None,
                timestamp=timestamp,
            )
        )
        return api.ServiceTaskResult(
            payload={
                "operation_id": request.operation_id,
                "parameters": parameters,
                "attempt": self._attempt_counter,
            }
        )


def main() -> None:
    database_url, display_database_url = _resolve_database_url()
    temporary_container_name: str | None = None
    engine: Engine | None = None

    print("m8flow-bpmn-core service-task failure retry POC")
    print(
        "This example intentionally starts a service-task workflow without any "
        "registered service-task connector, lets that failure escape a "
        "caller-owned session_scope helper, proves the errored process "
        "instance still persisted, and then retries the same instance with a "
        "working connector registry until it completes."
    )
    print(
        "The demo intentionally avoids the live connector-proxy so the "
        "failure path is deterministic and focused on the transaction "
        "behavior that the regression tests already verify."
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
        "The example keeps the definition, process instance, workflow state, "
        "and event history in place so they can be inspected after the run."
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
                context = _seed_failure_retry_poc_context(
                    session,
                    database_url=database_url,
                )
            finally:
                session.close()

        _run_failure_retry_poc(
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


def _seed_failure_retry_poc_context(
    session: Session,
    *,
    database_url: str,
) -> ServiceTaskFailureRetryPocContext:
    print()
    print(SECTION_SEPARATOR)
    print("Setup: create the demo tenant and users")
    print(
        "The workflow is exercised through the public API. The tenant and "
        "users are seeded directly because the library still does not expose "
        "public tenant-management commands."
    )
    _pause("Press Enter to seed the failure-retry demo data.")

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

    context = ServiceTaskFailureRetryPocContext(
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
    _pause("Press Enter to continue to the failure-retry workflow.")
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


def _run_failure_retry_poc(
    engine: Engine,
    context: ServiceTaskFailureRetryPocContext,
    *,
    database_url: str,
) -> None:
    print()
    print(SECTION_SEPARATOR)
    print("Workflow plan")
    print(
        "This run imports a service-task workflow, commits a normal process-"
        "instance shell, then initializes the workflow inside a host-style "
        "`session_scope(...)` without any registered connector."
    )
    print(
        "The library should still leave behind a retryable errored process "
        "instance with a persisted workflow snapshot after that initialization "
        "fails. The example then retries the same process instance, reopens the "
        "workflow at the user task, and finishes the process normally."
    )
    _pause("Press Enter to continue to the import step.")

    failing_registry = api.ServiceTaskRegistry()
    working_connector = RecordingDemoServiceTaskConnector()
    working_registry = api.ServiceTaskRegistry(connectors=(working_connector,))
    bpmn_xml = FAILURE_RETRY_BPMN_PATH.read_text(encoding="utf-8")

    definition = _run_registry_step(
        engine,
        step_number=1,
        title="Import the failure-retry service-task definition",
        context_text=(
            "This stores a visual BPMN model whose first service task will be "
            "used to demonstrate rollback-safe failure persistence."
        ),
        command=api.ImportBpmnProcessDefinitionCommand(
            tenant_id=context.tenant_id,
            bpmn_identifier=PROCESS_MODEL_IDENTIFIER,
            user_id=context.admin_user_id,
            bpmn_name=PROCESS_MODEL_DISPLAY_NAME,
            source_bpmn_xml=bpmn_xml,
            properties_json={
                "version": 1,
                "flow": "service_task_failure_retry_poc",
                "lane_owners": {
                    TASK_LANE_NAME: [context.operator_username],
                },
                "failure_mode": "missing_registry_on_initial_start",
            },
            bpmn_version_control_type="git",
            bpmn_version_control_identifier="service-task-failure-retry-poc",
            created_at_in_seconds=round(time.time()),
            updated_at_in_seconds=round(time.time()),
        ),
        registry=working_registry,
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

    run_summary = (
        "Service-task failure retry run "
        + datetime.now(UTC).replace(microsecond=0).isoformat()
    )
    shell_created_at_in_seconds = round(time.time())
    process_instance_shell_id, selected_process_id = (
        _prepare_committed_process_instance_shell(
            engine,
            tenant_id=context.tenant_id,
            process_definition_id=definition.id,
            process_initiator_id=context.admin_user_id,
            submission_metadata={"submission_message": SUBMISSION_MESSAGE},
            summary=run_summary,
            process_version=1,
            created_at_in_seconds=shell_created_at_in_seconds,
        )
    )

    print()
    print(SECTION_SEPARATOR)
    print("Step 2: Prepare a committed process-instance shell")
    print(
        "This commits the process instance row, BPMN process row, and start "
        "metadata before any risky service-task execution happens. The next "
        "step will initialize that shell inside `session_scope(...)` with an "
        "empty registry."
    )
    print("Result:")
    print(
        pformat(
            {
                "process_instance_id": process_instance_shell_id,
                "selected_process_id": selected_process_id,
                "summary": run_summary,
                "shell_created_at_in_seconds": shell_created_at_in_seconds,
            },
            sort_dicts=False,
            width=100,
        )
    )
    _pause("Press Enter to continue to workflow initialization.")

    started_at_in_seconds = round(time.time())
    _run_registry_step(
        engine,
        step_number=3,
        title="Initialize the workflow inside a caller-owned session_scope",
        context_text=(
            "The host application pattern here is intentional: the workflow "
            "initialization command runs inside `session_scope(...)`, but with "
            "an intentionally empty service-task registry. "
            "`ServiceTaskExecutionError` escapes, and the outer helper rolls "
            "its transaction back."
        ),
        command=api.InitializeProcessInstanceWorkflowCommand(
            tenant_id=context.tenant_id,
            process_instance_id=process_instance_shell_id,
            bpmn_xml=definition.source_bpmn_xml,
            bpmn_process_id=selected_process_id,
            started_at_in_seconds=started_at_in_seconds,
        ),
        registry=failing_registry,
        use_session_scope=True,
        expected_failure=api.ServiceTaskExecutionError,
        expected_failure_contains="demo/PrepareReview",
    )

    _print_note(
        "No connector call was recorded during the failed start because the "
        "POC intentionally used an empty registry for that step."
    )

    process_instances = _run_registry_step(
        engine,
        step_number=4,
        title=(
            "Read back the process instance after rollback-on-exception "
            "initialization"
        ),
        context_text=(
            "Even though the outer session_scope rolled back, the current "
            "process instance should still exist in `error` because the "
            "library persisted the recovery state autonomously."
        ),
        command=api.GetProcessInstanceQuery(
            tenant_id=context.tenant_id,
            process_instance_id=process_instance_shell_id,
        ),
        registry=working_registry,
    )
    if process_instances is None:
        raise RuntimeError("Process-instance query did not return a result")
    failed_process_instance = process_instances
    if failed_process_instance.status != api.ProcessInstanceStatus.error:
        raise RuntimeError(
            "Expected the current process instance to persist in error, got "
            f"{failed_process_instance.status!r}."
        )

    _print_failed_workflow_snapshot(
        engine=engine,
        label="Persisted workflow snapshot after the failed start",
        tenant_id=context.tenant_id,
        process_instance_id=failed_process_instance.id,
    )

    events = _run_registry_step(
        engine,
        step_number=5,
        title="Read back the event history after the failed initialization",
        context_text=(
            "The event log should show `task_failed` and "
            "`process_instance_error`, proving the failure state was persisted "
            "even though the caller-owned transaction rolled back."
        ),
        command=api.GetProcessInstanceEventsQuery(
            tenant_id=context.tenant_id,
            process_instance_id=failed_process_instance.id,
        ),
        registry=working_registry,
    )
    if events is None:
        raise RuntimeError("Event-history query did not return a result")

    _print_note(
        "A working in-process demo connector registry is now installed. "
        "Retrying the same process instance should rerun the failed service "
        "task and continue."
    )

    retried_process_instance = _run_registry_step(
        engine,
        step_number=6,
        title="Retry the same errored process instance",
        context_text=(
            "This uses the normal `process.retry` lifecycle. The library should "
            "restore the persisted workflow, reset the failed service-task "
            "branch, rerun it successfully, and stop at the user task."
        ),
        command=api.RetryProcessInstanceCommand(
            tenant_id=context.tenant_id,
            process_instance_id=failed_process_instance.id,
            user_id=context.admin_user_id,
            retried_at_in_seconds=round(time.time()),
        ),
        registry=working_registry,
        use_session_scope=True,
    )
    if retried_process_instance is None:
        raise RuntimeError("Retry command did not return a process instance")

    _print_connector_invocations(
        title="Captured demo connector calls after retry",
        connector=working_connector,
    )

    pending_tasks = _run_registry_step(
        engine,
        step_number=7,
        title="List the operator pending tasks after retry",
        context_text=(
            "Retry should not create a new process instance. The same process "
            "instance should now be waiting on its normal review task."
        ),
        command=api.GetPendingTasksQuery(
            tenant_id=context.tenant_id,
            user_id=context.operator_user_id,
        ),
        registry=working_registry,
    )
    if pending_tasks is None:
        raise RuntimeError("Pending-task query did not return a result")
    operator_task = _require_single_task(
        pending_tasks,
        "retried service-task review task",
        process_instance_id=failed_process_instance.id,
    )

    _run_registry_step(
        engine,
        step_number=8,
        title="Claim the review task",
        context_text=(
            "Once the failed service task has been retried successfully, the "
            "rest of the flow uses the normal human-task lifecycle."
        ),
        command=api.ClaimTaskCommand(
            tenant_id=context.tenant_id,
            human_task_id=operator_task.id,
            user_id=context.operator_user_id,
        ),
        registry=working_registry,
        use_session_scope=True,
    )

    _run_registry_step(
        engine,
        step_number=9,
        title="Complete the review task and execute the final service task",
        context_text=(
            "Completing the user task triggers the second service task. In this "
            "POC the final service task succeeds so the same process instance "
            "can be seen moving all the way to completion."
        ),
        command=api.CompleteTaskCommand(
            tenant_id=context.tenant_id,
            human_task_id=operator_task.id,
            user_id=context.operator_user_id,
            completed_at_in_seconds=round(time.time()),
            task_payload={
                "decision": "approved",
                "completed_by": context.operator_username,
                "completed_at": datetime.now(UTC).replace(
                    microsecond=0
                ).isoformat(),
            },
        ),
        registry=working_registry,
        use_session_scope=True,
    )

    _print_connector_invocations(
        title="Captured demo connector calls after workflow completion",
        connector=working_connector,
    )

    completed_process_instance = _run_registry_step(
        engine,
        step_number=10,
        title="Read back the completed process instance",
        context_text=(
            "The same process instance should now be complete. The persisted "
            "workflow state should include both successful service-task result "
            "variables."
        ),
        command=api.GetProcessInstanceQuery(
            tenant_id=context.tenant_id,
            process_instance_id=failed_process_instance.id,
        ),
        registry=working_registry,
    )
    if completed_process_instance is None:
        raise RuntimeError("Completed-process query did not return a result")

    _print_completed_workflow_snapshot(
        engine=engine,
        label="Persisted workflow data after retry and completion",
        tenant_id=context.tenant_id,
        process_instance_id=failed_process_instance.id,
    )

    _run_registry_step(
        engine,
        step_number=11,
        title="Read back the final event history",
        context_text=(
            "The event history should now include the original failure, the "
            "retry lifecycle event, task completion, and final process "
            "completion."
        ),
        command=api.GetProcessInstanceEventsQuery(
            tenant_id=context.tenant_id,
            process_instance_id=failed_process_instance.id,
        ),
        registry=working_registry,
    )

    _print_note(
        "The service-task failure retry POC is complete. The definition and "
        "process instance were left in place for audit."
    )


def _run_registry_step(
    engine: Engine,
    *,
    step_number: int,
    title: str,
    context_text: str,
    command: object,
    registry: api.ServiceTaskRegistry,
    use_session_scope: bool = False,
    expected_failure: type[Exception] | tuple[type[Exception], ...] | None = None,
    expected_failure_contains: str | None = None,
) -> Any | None:
    print()
    print(SECTION_SEPARATOR)
    print(f"Step {step_number}: {title}")
    print(context_text)
    print(SECTION_SEPARATOR)
    print("Command:")
    print(_format_command(command))
    print(SECTION_SEPARATOR)
    _pause("Press Enter to execute this command.")
    print("Status: executing command...")
    is_query = type(command).__name__.endswith("Query")

    try:
        with api.service_task_registry_scope(registry):
            if use_session_scope:
                with session_scope(engine) as session:
                    if is_query:
                        result = api.execute_query(session, command)
                    else:
                        result = api.execute_command(session, command)
            else:
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


def _print_connector_invocations(
    *,
    title: str,
    connector: RecordingDemoServiceTaskConnector,
) -> None:
    print()
    print(SECTION_SEPARATOR)
    print(title)
    print(
        pformat(
            [
                {
                    "attempt": invocation.attempt,
                    "operation_id": invocation.operation_id,
                    "parameters": invocation.parameters,
                    "outcome": invocation.outcome,
                    "error_message": invocation.error_message,
                    "process_instance_id": invocation.process_instance_id,
                    "task_name": invocation.task_name,
                    "timestamp": invocation.timestamp,
                }
                for invocation in connector.recorded_invocations
            ],
            sort_dicts=False,
            width=100,
        )
    )


def _print_failed_workflow_snapshot(
    *,
    engine: Engine,
    label: str,
    tenant_id: str,
    process_instance_id: int,
) -> None:
    workflow_state_json = _load_process_instance_workflow_state_json(
        engine,
        tenant_id=tenant_id,
        process_instance_id=process_instance_id,
    )
    if workflow_state_json is None:
        raise RuntimeError("Expected a persisted workflow state for the POC")

    workflow = _restore_workflow(workflow_state_json)
    errored_tasks = [
        task.task_spec.name
        for task in workflow.get_tasks(state=TaskState.ERROR)
    ]
    print()
    print(SECTION_SEPARATOR)
    print(label)
    print(
        pformat(
            {
                "errored_tasks": errored_tasks,
                "workflow_data_keys": sorted(workflow.data.keys()),
            },
            sort_dicts=False,
            width=100,
        )
    )


def _print_completed_workflow_snapshot(
    *,
    engine: Engine,
    label: str,
    tenant_id: str,
    process_instance_id: int,
) -> None:
    workflow_state_json = _load_process_instance_workflow_state_json(
        engine,
        tenant_id=tenant_id,
        process_instance_id=process_instance_id,
    )
    if workflow_state_json is None:
        raise RuntimeError("Expected a persisted workflow state for the POC")

    workflow = _restore_workflow(workflow_state_json)
    service_task_data = {
        key: workflow.data.get(key)
        for key in ("service_stage_one", "service_stage_two")
        if key in workflow.data
    }
    print()
    print(SECTION_SEPARATOR)
    print(label)
    print(pformat(service_task_data, sort_dicts=False, width=100))


def _load_process_instance_workflow_state_json(
    engine: Engine,
    *,
    tenant_id: str,
    process_instance_id: int,
) -> str | None:
    with engine.begin() as connection:
        session = Session(
            bind=connection,
            autoflush=False,
            expire_on_commit=False,
        )
        try:
            process_instance = session.get(
                ProcessInstanceModel,
                process_instance_id,
            )
            if (
                process_instance is None
                or process_instance.m8f_tenant_id != tenant_id
            ):
                raise RuntimeError(
                    "Expected process instance "
                    f"{process_instance_id} for tenant {tenant_id}, but it "
                    "was not found."
                )
            return process_instance.workflow_state_json
        finally:
            session.close()


def _prepare_committed_process_instance_shell(
    engine: Engine,
    *,
    tenant_id: str,
    process_definition_id: int,
    process_initiator_id: int,
    submission_metadata: dict[str, str] | None,
    summary: str | None,
    process_version: int,
    created_at_in_seconds: int | None,
) -> tuple[int, str]:
    with engine.begin() as connection:
        session = Session(
            bind=connection,
            autoflush=False,
            expire_on_commit=False,
        )
        try:
            process_definition = _load_process_definition(
                session,
                tenant_id=tenant_id,
                bpmn_process_definition_id=process_definition_id,
            )
            process_model_identifier = (
                process_definition.process_model_identifier
                or str(process_definition.id)
            )
            process_instance, selected_process_id = (
                _prepare_process_instance_from_definition_in_session(
                    session,
                    tenant_id=tenant_id,
                    process_definition=process_definition,
                    process_model_identifier=process_model_identifier,
                    process_initiator_id=process_initiator_id,
                    submission_metadata=submission_metadata,
                    summary=summary,
                    process_version=process_version,
                    started_at_in_seconds=created_at_in_seconds,
                    bpmn_process_id=None,
                )
            )
            session.flush()
            return process_instance.id, selected_process_id
        finally:
            session.close()


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
        "also publish the BPMN file into the local m8flow backend process-model "
        "catalog so the model is visible in the UI."
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
    deployment = _deploy_failure_retry_definition_to_m8flow_backend(
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


def _deploy_failure_retry_definition_to_m8flow_backend(
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
            "Refreshed the existing service-task failure retry POC deployment "
            "so the UI matches this run."
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
            "Published by the m8flow-bpmn-core service-task failure retry "
            "example."
        ),
        "display_name": PROCESS_MODEL_DISPLAY_NAME,
        "exception_notification_addresses": [],
        "fault_or_suspend_on_exception": "fault",
        "metadata_extraction_paths": None,
        "primary_file_name": PRIMARY_FILE_NAME,
        "primary_process_id": PRIMARY_PROCESS_ID,
    }


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
