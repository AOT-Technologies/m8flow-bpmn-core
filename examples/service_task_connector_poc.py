from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from pprint import pformat
from typing import Any
from urllib.parse import parse_qs, urlparse

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
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.db import build_engine, create_schema
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.services.authorization import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ensure_v1_role,
)
from m8flow_bpmn_core.services.workflow_runtime import _restore_workflow
from m8flow_bpmn_core.utils.keycloak import (
    KeycloakOrganizationSpec,
    KeycloakProvisioningError,
    KeycloakUserSpec,
    ProvisionedKeycloakSharedRealmContext,
    ensure_shared_realm_organizations_and_users,
)

SECTION_SEPARATOR = "=" * 88
REPO_ROOT = Path(__file__).resolve().parents[1]
PROCESS_GROUP_ID = "m8flow-bpmn-core-examples"
PROCESS_GROUP_DISPLAY_NAME = "m8flow-bpmn-core Examples"
PROCESS_GROUP_DESCRIPTION = (
    "Process models published by the m8flow-bpmn-core examples."
)
PROCESS_MODEL_ID = "service-task-connector-poc"
PROCESS_MODEL_IDENTIFIER = f"{PROCESS_GROUP_ID}/{PROCESS_MODEL_ID}"
PROCESS_MODEL_DISPLAY_NAME = "Service Task Connector POC"
PRIMARY_PROCESS_ID = "Process_service_task_connector_poc"
PRIMARY_FILE_NAME = "service_task_connector_poc.bpmn"
SERVICE_TASK_CONNECTOR_BPMN_PATH = (
    REPO_ROOT / "tests" / "fixtures" / PRIMARY_FILE_NAME
)

CONNECTOR_PROXY_BASE_URL = (
    os.getenv("M8FLOW_CONNECTOR_PROXY_BASE_URL", "").strip()
    or "http://localhost:6844"
)
CONNECTOR_POC_HOST_ALIAS = (
    os.getenv("M8FLOW_CONNECTOR_POC_HOST_ALIAS", "").strip()
    or "host.docker.internal"
)
REQUIRED_HTTP_OPERATION_ID = "http/GetRequestV2"

PROCESS_START_MESSAGE = "hello-service-task-connector"
TASK_COMPLETION_DECISION = "approved"
TASK_LANE_NAME = "Operations"

DEMO_TENANT = {
    "id": "tenant-service-task-connector-example",
    "name": "Service Task Connector Example",
    "slug": "service-task-connector-example",
}
DEMO_USERS = {
    "admin": {
        "username": "connector-poc-admin",
        "email": "connector-poc-admin@example.com",
        "service_id": "connector-poc-admin-keycloak",
        "display_name": "Connector POC Admin",
        "keycloak_groups": ("Approvers", "Viewers"),
    },
    "operator": {
        "username": "connector-poc-operator",
        "email": "connector-poc-operator@example.com",
        "service_id": "connector-poc-operator-keycloak",
        "display_name": "Connector POC Operator",
        "keycloak_groups": ("Approvers", "Viewers"),
    },
}


@dataclass(frozen=True, slots=True)
class ServiceTaskPocContext:
    tenant_id: str
    tenant_slug: str
    admin_user_id: int
    admin_username: str
    operator_user_id: int
    operator_username: str
    keycloak_password: str | None


@dataclass(frozen=True, slots=True)
class DemoConnectorRequest:
    method: str
    path: str
    query: dict[str, Any]
    headers: dict[str, str]
    timestamp: str


class _DemoConnectorHttpServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int]):
        super().__init__(server_address, _DemoConnectorRequestHandler)
        self._lock = threading.Lock()
        self._requests: list[DemoConnectorRequest] = []

    def record_request(self, request: DemoConnectorRequest) -> int:
        with self._lock:
            self._requests.append(request)
            return len(self._requests)

    def snapshot_requests(self) -> list[DemoConnectorRequest]:
        with self._lock:
            return list(self._requests)


class _DemoConnectorRequestHandler(BaseHTTPRequestHandler):
    server: _DemoConnectorHttpServer

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        query = {
            key: values[0] if len(values) == 1 else values
            for key, values in parse_qs(
                parsed_url.query,
                keep_blank_values=True,
            ).items()
        }
        sequence = self.server.record_request(
            DemoConnectorRequest(
                method="GET",
                path=parsed_url.path,
                query=query,
                headers={
                    key: value for key, value in self.headers.items()
                },
                timestamp=datetime.now(UTC).replace(microsecond=0).isoformat(),
            )
        )

        if parsed_url.path == "/prepare":
            response_payload = {
                "stage": "prepare",
                "submission_message": query.get("submission_message"),
                "sequence": sequence,
            }
            self._write_json_response(200, response_payload)
            return

        if parsed_url.path == "/finalize":
            response_payload = {
                "stage": "finalize",
                "decision": query.get("decision"),
                "sequence": sequence,
            }
            self._write_json_response(200, response_payload)
            return

        self._write_json_response(
            404,
            {
                "error": f"Unsupported path {parsed_url.path!r}",
                "sequence": sequence,
            },
        )

    def log_message(self, format: str, *args: object) -> None:
        return

    def _write_json_response(
        self,
        status_code: int,
        payload: dict[str, Any],
    ) -> None:
        response_body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)


class DemoConnectorServer:
    def __init__(self, *, proxy_host_alias: str) -> None:
        self._proxy_host_alias = proxy_host_alias
        self._server: _DemoConnectorHttpServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> DemoConnectorServer:
        self._server = _DemoConnectorHttpServer(("0.0.0.0", 0))
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="service-task-connector-poc-http-server",
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def host_port(self) -> int:
        if self._server is None:
            raise RuntimeError("Demo connector server is not running")
        return int(self._server.server_address[1])

    @property
    def local_base_url(self) -> str:
        return f"http://127.0.0.1:{self.host_port}"

    @property
    def proxy_base_url(self) -> str:
        return f"http://{self._proxy_host_alias}:{self.host_port}"

    @property
    def prepare_url_for_proxy(self) -> str:
        return f"{self.proxy_base_url}/prepare"

    @property
    def finalize_url_for_proxy(self) -> str:
        return f"{self.proxy_base_url}/finalize"

    def snapshot_requests(self) -> list[DemoConnectorRequest]:
        if self._server is None:
            raise RuntimeError("Demo connector server is not running")
        return self._server.snapshot_requests()


def main() -> None:
    database_url, display_database_url = _resolve_database_url()
    temporary_container_name: str | None = None
    engine: Engine | None = None

    print("m8flow-bpmn-core service-task connector POC")
    print(
        "This example imports a BPMN model with real service tasks, registers "
        "the live m8flow connector-proxy catalog, calls a local demo HTTP "
        "endpoint through that proxy, and then continues the workflow through "
        "a normal user task."
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
        "The example keeps the definition, process instance, and event "
        "history in place so they can be inspected after the run."
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
                context = _seed_service_task_poc_context(
                    session,
                    database_url=database_url,
                )
            finally:
                session.close()

        _run_service_task_connector_poc(
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


def _seed_service_task_poc_context(
    session: Session,
    *,
    database_url: str,
) -> ServiceTaskPocContext:
    print()
    print(SECTION_SEPARATOR)
    print("Setup: create the demo tenant and users")
    print(
        "The workflow itself is driven through the public API. The tenant and "
        "users are seeded directly because the library still does not expose "
        "public tenant-management commands."
    )
    _pause("Press Enter to seed the service-task demo data.")

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

    context = ServiceTaskPocContext(
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
    _pause("Press Enter to continue to the service-task workflow.")
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


def _run_service_task_connector_poc(
    engine: Engine,
    context: ServiceTaskPocContext,
    *,
    database_url: str,
) -> None:
    print()
    print(SECTION_SEPARATOR)
    print("Workflow plan")
    print(
        "This run starts a local demo HTTP endpoint on the host, builds a "
        "real connector registry from m8flow-connector-proxy, imports a BPMN "
        "model with two HTTP service tasks, and then walks the workflow from "
        "start to completion."
    )
    _pause("Press Enter to continue to the connector setup.")

    with DemoConnectorServer(
        proxy_host_alias=CONNECTOR_POC_HOST_ALIAS
    ) as demo_server:
        registry = _prepare_connector_proxy_registry(demo_server=demo_server)
        bpmn_xml = _render_service_task_connector_bpmn_xml(
            prepare_url=demo_server.prepare_url_for_proxy,
            finalize_url=demo_server.finalize_url_for_proxy,
        )

        definition = _run_poc_step(
            engine,
            step_number=1,
            title="Import the service-task definition",
            context_text=(
                "This stores the BPMN model with real `http/GetRequestV2` "
                "operators that will execute through the live "
                "connector-proxy registry."
            ),
            command=api.ImportBpmnProcessDefinitionCommand(
                tenant_id=context.tenant_id,
                bpmn_identifier=PROCESS_MODEL_IDENTIFIER,
                user_id=context.admin_user_id,
                bpmn_name=PROCESS_MODEL_DISPLAY_NAME,
                source_bpmn_xml=bpmn_xml,
                properties_json={
                    "version": 1,
                    "flow": "service_task_connector_poc",
                    "lane_owners": {
                        TASK_LANE_NAME: [context.operator_username],
                    },
                    "connector_proxy_base_url": CONNECTOR_PROXY_BASE_URL,
                    "demo_server_proxy_base_url": demo_server.proxy_base_url,
                },
                bpmn_version_control_type="git",
                bpmn_version_control_identifier="service-task-poc",
                created_at_in_seconds=round(time.time()),
                updated_at_in_seconds=round(time.time()),
            ),
            registry=registry,
        )

        deployment = _maybe_deploy_process_model_to_m8flow_backend(
            database_url=database_url,
            tenant_id=context.tenant_id,
            tenant_slug=context.tenant_slug,
            bpmn_xml=bpmn_xml,
        )
        if deployment is not None:
            _print_backend_deployment_summary(deployment)

        process_instance = _run_poc_step(
            engine,
            step_number=2,
            title="Start the workflow and execute the first service task",
            context_text=(
                "Starting the instance executes the first BPMN service task "
                "immediately. The connector-proxy calls the host-side demo "
                "endpoint before the workflow stops at the user task."
            ),
            command=api.InitializeProcessInstanceFromDefinitionCommand(
                tenant_id=context.tenant_id,
                bpmn_process_definition_id=definition.id,
                process_initiator_id=context.admin_user_id,
                submission_metadata={
                    "submission_message": PROCESS_START_MESSAGE,
                },
                started_at_in_seconds=round(time.time()),
            ),
            registry=registry,
        )

        _print_demo_connector_requests(
            title="Captured connector calls after process start",
            requests=demo_server.snapshot_requests(),
        )
        _assert_demo_connector_requests(
            demo_server.snapshot_requests(),
            expected_paths=("/prepare",),
        )

        pending_tasks = _run_poc_step(
            engine,
            step_number=3,
            title="List the operator pending tasks",
            context_text=(
                "The first service task has completed and the workflow is now "
                "waiting on a normal user task assigned through the BPMN lane "
                "plus properties_json['lane_owners']."
            ),
            command=api.GetPendingTasksQuery(
                tenant_id=context.tenant_id,
                user_id=context.operator_user_id,
            ),
            registry=registry,
        )
        operator_task = _require_single_task(
            pending_tasks,
            "service-task connector review task",
            process_instance_id=process_instance.id,
        )

        _print_workflow_service_task_snapshot(
            engine=engine,
            label="Persisted workflow data after the first service task",
            tenant_id=context.tenant_id,
            process_instance_id=process_instance.id,
        )

        _run_poc_step(
            engine,
            step_number=5,
            title="Claim the review task",
            context_text=(
                "Once the service task has handed off to a human task, the "
                "rest of the lifecycle uses the normal task API."
            ),
            command=api.ClaimTaskCommand(
                tenant_id=context.tenant_id,
                human_task_id=operator_task.id,
                user_id=context.operator_user_id,
            ),
            registry=registry,
        )

        _run_poc_step(
            engine,
            step_number=6,
            title="Complete the review task and execute the second service task",
            context_text=(
                "Completing the user task pushes the workflow into the second "
                "HTTP service task, which calls the host-side finalize "
                "endpoint through connector-proxy."
            ),
            command=api.CompleteTaskCommand(
                tenant_id=context.tenant_id,
                human_task_id=operator_task.id,
                user_id=context.operator_user_id,
                completed_at_in_seconds=round(time.time()),
                task_payload={
                    "decision": TASK_COMPLETION_DECISION,
                    "completed_by": context.operator_username,
                    "completed_at": datetime.now(UTC).replace(
                        microsecond=0
                    ).isoformat(),
                },
            ),
            registry=registry,
        )

        _print_demo_connector_requests(
            title="Captured connector calls after workflow completion",
            requests=demo_server.snapshot_requests(),
        )
        _assert_demo_connector_requests(
            demo_server.snapshot_requests(),
            expected_paths=("/prepare", "/finalize"),
        )

        final_process_instance = _run_poc_step(
            engine,
            step_number=7,
            title="Read back the completed process instance",
            context_text=(
                "The workflow should now be complete. The persisted workflow "
                "state still includes both connector-proxy responses for audit "
                "and inspection."
            ),
            command=api.GetProcessInstanceQuery(
                tenant_id=context.tenant_id,
                process_instance_id=process_instance.id,
            ),
            registry=registry,
        )
        _print_workflow_service_task_snapshot(
            engine=engine,
            label="Persisted workflow data after both service tasks",
            tenant_id=context.tenant_id,
            process_instance_id=final_process_instance.id,
        )

        _run_poc_step(
            engine,
            step_number=8,
            title="Read back the event history",
            context_text=(
                "The event log should show the normal process-instance and "
                "task lifecycle. Because the connector calls succeeded, there "
                "should be no `task_failed` event."
            ),
            command=api.GetProcessInstanceEventsQuery(
                tenant_id=context.tenant_id,
                process_instance_id=process_instance.id,
            ),
            registry=registry,
        )

    _print_note(
        "The service-task connector POC is complete. The definition and "
        "process instance were left in place for audit."
    )


def _prepare_connector_proxy_registry(
    *,
    demo_server: DemoConnectorServer,
) -> api.ServiceTaskRegistry:
    print()
    print(SECTION_SEPARATOR)
    print("Connector-proxy setup")
    print(
        "This example expects the local m8flow connector-proxy to be running. "
        "It builds the registry from the live `/v1/commands` catalog and "
        "points the BPMN service tasks at a host-side demo HTTP server."
    )
    print(
        "Connector-proxy base URL: "
        f"{CONNECTOR_PROXY_BASE_URL}"
    )
    print(
        "Demo server local base URL: "
        f"{demo_server.local_base_url}"
    )
    print(
        "Demo server proxy-visible base URL: "
        f"{demo_server.proxy_base_url}"
    )
    _pause("Press Enter to query the connector-proxy catalog.")

    try:
        registry = api.build_connector_proxy_service_task_registry(
            CONNECTOR_PROXY_BASE_URL
        )
    except api.BpmnCoreError as exc:
        raise SystemExit(
            "Could not build the connector-proxy service-task registry from "
            f"{CONNECTOR_PROXY_BASE_URL}: {exc}"
        ) from exc

    http_operations = [
        command.operation_id
        for command in registry.list_commands()
        if command.connector_key == "http"
    ]
    if REQUIRED_HTTP_OPERATION_ID not in http_operations:
        raise SystemExit(
            "The live connector-proxy catalog does not include the required "
            f"{REQUIRED_HTTP_OPERATION_ID!r} command. Available HTTP "
            f"commands: {http_operations}"
        )

    print("Status: connector-proxy registry is ready.")
    print(
        pformat(
            {
                "required_operation_id": REQUIRED_HTTP_OPERATION_ID,
                "http_operations": http_operations,
            },
            sort_dicts=False,
            width=100,
        )
    )
    _pause("Press Enter to continue to the BPMN import.")
    return registry


def _run_poc_step(
    engine: Engine,
    *,
    step_number: int,
    title: str,
    context_text: str,
    command: object,
    registry: api.ServiceTaskRegistry,
) -> Any:
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
    with engine.begin() as connection:
        with api.service_task_registry_scope(registry):
            if is_query:
                result = api.execute_query(connection, command)
            else:
                result = api.execute_command(connection, command)
    print("Status: command complete and committed.")
    print("Result:")
    print(_format_result(result))
    _pause("Press Enter to continue.")
    return result


def _render_service_task_connector_bpmn_xml(
    *,
    prepare_url: str,
    finalize_url: str,
) -> str:
    return (
        SERVICE_TASK_CONNECTOR_BPMN_PATH.read_text(encoding="utf-8")
        .replace(
            "__PREPARE_URL_EXPRESSION__",
            _service_task_url_expression(
                base_url=prepare_url,
                query_key="submission_message",
                variable_name="submission_message",
            ),
        )
        .replace(
            "__FINALIZE_URL_EXPRESSION__",
            _service_task_url_expression(
                base_url=finalize_url,
                query_key="decision",
                variable_name="decision",
            ),
        )
    )


def _service_task_url_expression(
    *,
    base_url: str,
    query_key: str,
    variable_name: str,
) -> str:
    return (
        repr(base_url)
        + " + '?' + "
        + repr(query_key)
        + " + '=' + str("
        + variable_name
        + ")"
    )


def _print_demo_connector_requests(
    *,
    title: str,
    requests: list[DemoConnectorRequest],
) -> None:
    print()
    print(SECTION_SEPARATOR)
    print(title)
    print(
        pformat(
            [
                {
                    "method": request.method,
                    "path": request.path,
                    "query": request.query,
                    "timestamp": request.timestamp,
                }
                for request in requests
            ],
            sort_dicts=False,
            width=100,
        )
    )


def _assert_demo_connector_requests(
    requests: list[DemoConnectorRequest],
    *,
    expected_paths: tuple[str, ...],
) -> None:
    actual_paths = tuple(request.path for request in requests)
    if actual_paths != expected_paths:
        raise RuntimeError(
            "Expected demo connector requests "
            f"{expected_paths}, got {actual_paths}."
        )


def _print_workflow_service_task_snapshot(
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
        for key in ("connector_stage_one", "connector_stage_two")
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
    deployment = _deploy_service_task_definition_to_m8flow_backend(
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


def _deploy_service_task_definition_to_m8flow_backend(
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
            "Refreshed the existing service-task connector POC deployment so "
            "the UI matches this run."
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
            "Published by the m8flow-bpmn-core service-task connector example."
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
