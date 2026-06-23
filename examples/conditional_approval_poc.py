from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from pprint import pformat
from typing import Any
from uuid import uuid4

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.db import build_engine, create_schema
from m8flow_bpmn_core.models import Base
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
from m8flow_bpmn_core.models.process_model_bpmn_version import (
    ProcessModelBpmnVersionModel,
)
from m8flow_bpmn_core.models.task import TaskModel
from m8flow_bpmn_core.models.task_definition import TaskDefinitionModel
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.services.authorization import (
    ROLE_MANAGER,
    ROLE_USER,
    ensure_v1_role,
)
from m8flow_bpmn_core.services.tenant_users import user_belongs_to_tenant
from m8flow_bpmn_core.services.workflow_runtime import (
    repair_process_instance_runtime_representation,
)
from m8flow_bpmn_core.utils.keycloak import (
    KeycloakOrganizationSpec,
    KeycloakProvisioningError,
    KeycloakUserSpec,
    ProvisionedKeycloakSharedRealmContext,
    ensure_shared_realm_organizations_and_users,
)

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
M8FLOW_BACKEND_PROCESS_GROUP_ID = "m8flow-bpmn-core-examples"
M8FLOW_BACKEND_PROCESS_GROUP_DISPLAY_NAME = "m8flow-bpmn-core Examples"
M8FLOW_BACKEND_PROCESS_GROUP_DESCRIPTION = (
    "Process models published by the m8flow-bpmn-core examples."
)
M8FLOW_BACKEND_PROCESS_MODEL_ID = "conditional-approval-poc"
CONDITIONAL_APPROVAL_PROCESS_MODEL_IDENTIFIER = (
    f"{M8FLOW_BACKEND_PROCESS_GROUP_ID}/{M8FLOW_BACKEND_PROCESS_MODEL_ID}"
)
M8FLOW_BACKEND_PROCESS_MODEL_DISPLAY_NAME = "Conditional Approval POC"
M8FLOW_BACKEND_PROCESS_MODELS_TARGET = "/app/data/process_models"
M8FLOW_BACKEND_DEFAULT_CONTAINER_NAMES = (
    "m8flow-m8flow-backend-1",
    "m8flow-backend",
    "m8flow-backend-1",
)
EXAMPLE_KEYCLOAK_DEFAULT_PASSWORD = "poc-demo-password"
FALLBACK_POSTGRES_DATABASE_NAME = "m8flow_bpmn_core_example"
FALLBACK_POSTGRES_IMAGE = "postgres:16"

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
KEYCLOAK_GROUPS_BY_DEMO_ROLE = {
    "manager": ("Approvers", "Viewers"),
    "reviewer": ("Approvers", "Viewers"),
    "finance": ("Approvers", "Viewers"),
    "requester": ("Submitters", "Viewers"),
    "observer": ("Viewers",),
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


@dataclass(frozen=True, slots=True)
class BackendProcessModelDeployment:
    process_models_root: Path
    tenant_root: str
    process_group_id: str
    process_model_id: str
    deployed: bool
    already_deployed: bool
    warnings: tuple[str, ...]
    container_name: str | None = None


def main() -> None:
    database_url, display_database_url = _resolve_database_url()
    temporary_container_name: str | None = None
    engine: Engine | None = None

    print("m8flow-bpmn-core conditional-approval usage example")
    print(
        "This script creates the schema if needed, seeds demo data, and then "
        "drives the workflow through the public API."
    )
    print(_describe_workflow_mode())
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

    try:
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
                "Hint: start PostgreSQL locally or set "
                "M8FLOW_EXAMPLE_DATABASE_URL to a reachable database, then "
                "rerun the example."
            )
            raise SystemExit(1) from exc

        print("Status: creating schema...")
        create_schema(engine)
        print("Status: database connection and schema are ready.")

        with engine.begin() as connection:
            session = Session(
                bind=connection,
                autoflush=False,
                expire_on_commit=False,
            )
            try:
                context = _seed_demo_context(
                    session,
                    database_url=database_url,
                )
            finally:
                session.close()
        print("Status: seed data committed and visible in the database.")
        deployment = _maybe_deploy_process_model_to_m8flow_backend(
            database_url=database_url,
            tenant_id=context.tenant_id,
            tenant_slug=context.tenant_slug,
            lane_owners=context.lane_owners,
        )
        if deployment is not None:
            _print_backend_deployment_summary(deployment)

        _run_isolation_checks(engine, context)
        _run_workflow(engine, context)
    except KeyboardInterrupt:
        print("\nInterrupted. The current step was rolled back.")
    finally:
        if engine is not None:
            engine.dispose()
        _remove_temporary_postgres_container(temporary_container_name)


def _resolve_database_url() -> tuple[str, str]:
    raw_url = os.getenv("M8FLOW_EXAMPLE_DATABASE_URL") or DEFAULT_LOCAL_DATABASE_URL
    return _normalize_database_url(raw_url)


def _current_timestamp() -> int:
    return round(time.time())


def _offset_timestamp(timestamp: int, offset_seconds: int) -> int:
    return max(1, timestamp + offset_seconds)


def _current_date_string() -> str:
    return time.strftime(
        "%Y-%m-%d",
        time.localtime(_current_timestamp()),
    )


def _normalize_database_url(raw_url: str) -> tuple[str, str]:
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
) -> tuple[str, str, str | None]:
    if not _is_shared_database_url(database_url):
        return database_url, display_database_url, None

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
        "It will also provision the demo tenants and users in the local "
        "Keycloak shared realm so the same identities work in the UI."
    )
    print(
        "If the seed rows already exist, the example will reuse them and "
        "print warnings instead of failing."
    )
    response = input("Continue with the shared database? [Y/n] ").strip().lower()
    if response in {"", "y", "yes"}:
        return database_url, display_database_url, None

    print(
        "Status: shared database declined, starting a temporary Docker "
        "Postgres container instead."
    )
    return _start_temporary_postgres_container()


def _is_shared_database_url(database_url: str) -> bool:
    try:
        url = make_url(database_url)
    except Exception:
        return False
    return (url.database or "").lower() == SHARED_POSTGRES_DATABASE_NAME


def _start_temporary_postgres_container() -> tuple[str, str, str]:
    if shutil.which("docker") is None:
        raise SystemExit(
            "Shared database use was declined, but Docker is not available for "
            "the fallback Postgres container."
        )

    postgres_image = (
        os.getenv("M8FLOW_EXAMPLE_POSTGRES_IMAGE", "").strip()
        or FALLBACK_POSTGRES_IMAGE
    )
    container_name = f"m8flow-bpmn-core-example-{uuid4().hex[:8]}"
    print(
        "Status: starting temporary Docker Postgres container "
        f"{container_name}..."
    )
    result = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            container_name,
            "-e",
            "POSTGRES_USER=postgres",
            "-e",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            "-e",
            f"POSTGRES_DB={FALLBACK_POSTGRES_DATABASE_NAME}",
            "-P",
            postgres_image,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        error_message = result.stderr.strip() or result.stdout.strip()
        raise SystemExit(
            "Shared database use was declined, but starting the Docker "
            f"fallback failed: {error_message or 'unknown docker error'}"
        )

    try:
        host_port = _get_container_host_port(container_name)
    except Exception:
        _remove_temporary_postgres_container(container_name)
        raise

    database_url, display_database_url = _normalize_database_url(
        "postgresql+psycopg://postgres@127.0.0.1:"
        f"{host_port}/{FALLBACK_POSTGRES_DATABASE_NAME}"
    )
    print(f"Status: temporary container is available on host port {host_port}.")
    return database_url, display_database_url, container_name


def _get_container_host_port(container_name: str) -> int:
    result = subprocess.run(
        ["docker", "port", container_name, "5432/tcp"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        error_message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            f"Could not determine the mapped port for container {container_name}: "
            f"{error_message or 'unknown docker error'}"
        )

    for line in result.stdout.splitlines():
        match = re.search(r":(\d+)$", line.strip())
        if match is not None:
            return int(match.group(1))

    raise RuntimeError(
        f"Could not parse the mapped port for container {container_name}."
    )


def _remove_temporary_postgres_container(container_name: str | None) -> None:
    if not container_name:
        return

    print(f"Status: removing temporary Docker container {container_name}...")
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        check=False,
        capture_output=True,
        text=True,
    )


def _maybe_deploy_process_model_to_m8flow_backend(
    *,
    database_url: str,
    tenant_id: str,
    tenant_slug: str,
    lane_owners: dict[str, list[str]],
) -> BackendProcessModelDeployment | None:
    if not _is_shared_database_url(database_url):
        return None

    print()
    print(SECTION_SEPARATOR)
    print("m8flow backend deployment")
    print(
        "Because the shared Postgres database is in use, the example will "
        "also try to publish the BPMN and DMN files to the local m8flow "
        "backend process-model catalog so the model is visible in the UI."
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
    deployment = _deploy_conditional_approval_definition_to_m8flow_backend(
        process_models_root=process_models_root,
        tenant_root=backend_tenant_root,
        lane_owners=lane_owners,
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


def _resolve_m8flow_backend_process_models_root(
) -> tuple[Path | None, str | None, list[str]]:
    override = os.getenv("M8FLOW_EXAMPLE_PROCESS_MODELS_DIR", "").strip()
    if override:
        return Path(override).expanduser(), None, []

    for container_name in _backend_container_names():
        payload = _docker_inspect_container(container_name)
        if payload is None:
            continue
        source = _extract_process_models_mount_source(payload)
        if source is None:
            return (
                None,
                container_name,
                [
                    "Found a running m8flow-backend Docker container, but "
                    "could not determine its process-model mount source."
                ],
            )
        return Path(source), container_name, []

    return (
        None,
        None,
        [
            "Shared m8flow database detected, but the example could not find "
            "a local m8flow-backend deployment target. Set "
            "M8FLOW_EXAMPLE_PROCESS_MODELS_DIR to a process_models directory "
            "if you want the BPMN and DMN files deployed into the UI catalog."
        ],
    )


def _resolve_backend_tenant_root(
    *,
    tenant_id: str,
    tenant_slug: str,
) -> tuple[str, list[str]]:
    override = os.getenv("M8FLOW_EXAMPLE_BACKEND_TENANT_ROOT", "").strip()
    if override:
        return (
            override,
            [
                "Deploying the process model into backend tenant root "
                f"'{override}' instead of the example tenant id '{tenant_id}'."
            ],
        )
    if tenant_slug and tenant_slug != tenant_id:
        return tenant_id, []
    return tenant_id, []


def _backend_container_names() -> list[str]:
    candidates: list[str] = []
    override = os.getenv("M8FLOW_EXAMPLE_BACKEND_CONTAINER", "").strip()
    if override:
        candidates.append(override)
    candidates.extend(M8FLOW_BACKEND_DEFAULT_CONTAINER_NAMES)

    ordered_candidates: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        ordered_candidates.append(candidate)
    return ordered_candidates


def _docker_inspect_container(container_name: str) -> list[dict[str, Any]] | None:
    result = subprocess.run(
        ["docker", "inspect", container_name],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, list) else None


def _extract_process_models_mount_source(
    inspect_payload: list[dict[str, Any]],
) -> str | None:
    if not inspect_payload:
        return None

    target = M8FLOW_BACKEND_PROCESS_MODELS_TARGET
    config_env = inspect_payload[0].get("Config", {}).get("Env", [])
    if isinstance(config_env, list):
        for entry in config_env:
            if not isinstance(entry, str):
                continue
            key, _, value = entry.partition("=")
            if key == "M8FLOW_BACKEND_BPMN_SPEC_ABSOLUTE_DIR" and value.strip():
                target = value.strip()
                break

    mounts = inspect_payload[0].get("Mounts", [])
    if not isinstance(mounts, list):
        return None
    for mount in mounts:
        if not isinstance(mount, dict):
            continue
        destination = mount.get("Destination") or mount.get("Target")
        source = mount.get("Source")
        if destination == target and isinstance(source, str) and source.strip():
            return source.strip()
    return None


def _deploy_conditional_approval_definition_to_m8flow_backend(
    *,
    process_models_root: Path,
    tenant_root: str,
    lane_owners: dict[str, list[str]],
) -> BackendProcessModelDeployment:
    group_dir = process_models_root / tenant_root / M8FLOW_BACKEND_PROCESS_GROUP_ID
    model_dir = group_dir / M8FLOW_BACKEND_PROCESS_MODEL_ID
    group_json_path = group_dir / "process_group.json"
    model_json_path = model_dir / "process_model.json"
    bpmn_path = model_dir / EXAMPLE_BPMN_PATH.name
    dmn_path = model_dir / EXAMPLE_DMN_PATH.name

    desired_group_payload = _backend_process_group_payload()
    desired_model_payload = _backend_process_model_payload()
    desired_bpmn_xml = _render_conditional_approval_bpmn_xml(lane_owners)
    desired_dmn_xml = EXAMPLE_DMN_PATH.read_text(encoding="utf-8")

    warnings: list[str] = []
    required_paths = [group_json_path, model_json_path, bpmn_path, dmn_path]
    if all(path.exists() for path in required_paths):
        warnings.append(
            "m8flow backend process model "
            f"'{M8FLOW_BACKEND_PROCESS_GROUP_ID}/{M8FLOW_BACKEND_PROCESS_MODEL_ID}' "
            f"is already deployed under tenant root '{tenant_root}'. Leaving "
            "the existing files unchanged."
        )
        if not _existing_backend_deployment_matches(
            group_json_path=group_json_path,
            desired_group_payload=desired_group_payload,
            model_json_path=model_json_path,
            desired_model_payload=desired_model_payload,
            bpmn_path=bpmn_path,
            desired_bpmn_xml=desired_bpmn_xml,
            dmn_path=dmn_path,
            desired_dmn_xml=desired_dmn_xml,
        ):
            warnings.append(
                "The existing backend deployment differs from the current "
                "example sources, so the UI model may not exactly match this run."
            )
        return BackendProcessModelDeployment(
            process_models_root=process_models_root,
            tenant_root=tenant_root,
            process_group_id=M8FLOW_BACKEND_PROCESS_GROUP_ID,
            process_model_id=M8FLOW_BACKEND_PROCESS_MODEL_ID,
            deployed=False,
            already_deployed=True,
            warnings=tuple(warnings),
        )

    if model_dir.exists() and any(model_dir.iterdir()):
        warnings.append(
            "The backend process-model directory already existed but was "
            "missing one or more deployment files. Missing files were added."
        )

    model_dir.mkdir(parents=True, exist_ok=True)
    if not group_json_path.exists():
        _write_json_file(group_json_path, desired_group_payload)
    if not model_json_path.exists():
        _write_json_file(model_json_path, desired_model_payload)
    if not bpmn_path.exists():
        bpmn_path.write_text(desired_bpmn_xml, encoding="utf-8")
    if not dmn_path.exists():
        dmn_path.write_text(desired_dmn_xml, encoding="utf-8")

    return BackendProcessModelDeployment(
        process_models_root=process_models_root,
        tenant_root=tenant_root,
        process_group_id=M8FLOW_BACKEND_PROCESS_GROUP_ID,
        process_model_id=M8FLOW_BACKEND_PROCESS_MODEL_ID,
        deployed=True,
        already_deployed=False,
        warnings=tuple(warnings),
    )


def _backend_process_group_payload() -> dict[str, Any]:
    return {
        "correlation_keys": None,
        "correlation_properties": None,
        "data_store_specifications": {},
        "description": M8FLOW_BACKEND_PROCESS_GROUP_DESCRIPTION,
        "display_name": M8FLOW_BACKEND_PROCESS_GROUP_DISPLAY_NAME,
        "messages": None,
    }


def _backend_process_model_payload() -> dict[str, Any]:
    return {
        "description": (
            "Published by the m8flow-bpmn-core conditional-approval example."
        ),
        "display_name": M8FLOW_BACKEND_PROCESS_MODEL_DISPLAY_NAME,
        "exception_notification_addresses": [],
        "fault_or_suspend_on_exception": "fault",
        "metadata_extraction_paths": None,
        "primary_file_name": EXAMPLE_BPMN_PATH.name,
        "primary_process_id": CONDITIONAL_APPROVAL_PROCESS_ID,
    }


def _existing_backend_deployment_matches(
    *,
    group_json_path: Path,
    desired_group_payload: dict[str, Any],
    model_json_path: Path,
    desired_model_payload: dict[str, Any],
    bpmn_path: Path,
    desired_bpmn_xml: str,
    dmn_path: Path,
    desired_dmn_xml: str,
) -> bool:
    return (
        _json_file_matches(group_json_path, desired_group_payload)
        and _json_file_matches(model_json_path, desired_model_payload)
        and bpmn_path.read_text(encoding="utf-8") == desired_bpmn_xml
        and dmn_path.read_text(encoding="utf-8") == desired_dmn_xml
    )


def _json_file_matches(path: Path, expected_payload: dict[str, Any]) -> bool:
    try:
        current_payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return current_payload == expected_payload


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(f"{json.dumps(payload, indent=4)}\n", encoding="utf-8")


def _print_backend_deployment_summary(
    deployment: BackendProcessModelDeployment,
) -> None:
    if deployment.container_name is not None:
        print(
            "Status: resolved the backend process-model catalog from Docker "
            f"container {deployment.container_name}."
        )
    print(
        "Status: backend process-model catalog is "
        f"{deployment.process_models_root}."
    )
    if deployment.deployed:
        print(
            "Status: deployed process model "
            f"'{deployment.process_group_id}/{deployment.process_model_id}' "
            f"for tenant root '{deployment.tenant_root}'."
        )
    elif deployment.already_deployed:
        print(
            "Status: backend process model "
            f"'{deployment.process_group_id}/{deployment.process_model_id}' "
            "was already present."
        )
    for warning in deployment.warnings:
        _print_note(f"Warning: {warning}")


def _provision_shared_db_keycloak_context(
    *,
    tenant: M8flowTenantModel,
    other_tenants: list[M8flowTenantModel],
) -> ProvisionedKeycloakSharedRealmContext:
    print()
    print(SECTION_SEPARATOR)
    print("Keycloak provisioning")
    print(
        "Because the shared Postgres database is in use, the example will "
        "also provision the demo tenants and users in the local Keycloak "
        "shared realm so the same workflow state is visible in the m8flow UI."
    )

    keycloak_context = ensure_shared_realm_organizations_and_users(
        organizations=_shared_db_keycloak_organizations(
            tenant=tenant,
            other_tenants=other_tenants,
        ),
        users=_shared_db_keycloak_users(
            tenant=tenant,
            foreign_noise_tenant=other_tenants[0],
        ),
    )
    _print_keycloak_provisioning_summary(keycloak_context)
    return keycloak_context


def _shared_db_keycloak_organizations(
    *,
    tenant: M8flowTenantModel,
    other_tenants: list[M8flowTenantModel],
) -> list[KeycloakOrganizationSpec]:
    return [
        KeycloakOrganizationSpec(alias=item.slug, name=item.name)
        for item in [tenant, *other_tenants]
    ]


def _shared_db_keycloak_users(
    *,
    tenant: M8flowTenantModel,
    foreign_noise_tenant: M8flowTenantModel,
) -> list[KeycloakUserSpec]:
    default_password = _shared_db_keycloak_password()
    user_specs = [
        KeycloakUserSpec(
            username=user_spec["username"],
            email=user_spec["email"],
            password=default_password,
            organization_alias=tenant.slug,
            display_name=user_spec["display_name"],
            organization_group_names=KEYCLOAK_GROUPS_BY_DEMO_ROLE[role],
        )
        for role, user_spec in DEMO_USERS.items()
    ]
    user_specs.append(
        KeycloakUserSpec(
            username=FOREIGN_NOISE_USER["username"],
            email=FOREIGN_NOISE_USER["email"],
            password=default_password,
            organization_alias=foreign_noise_tenant.slug,
            display_name=FOREIGN_NOISE_USER["display_name"],
            organization_group_names=("Viewers",),
        )
    )
    return user_specs


def _shared_db_keycloak_password() -> str:
    return (
        os.getenv("M8FLOW_EXAMPLE_KEYCLOAK_PASSWORD", "").strip()
        or EXAMPLE_KEYCLOAK_DEFAULT_PASSWORD
    )


def _print_keycloak_provisioning_summary(
    keycloak_context: ProvisionedKeycloakSharedRealmContext,
) -> None:
    print(
        "Status: Keycloak shared realm issuer is "
        f"{keycloak_context.service_issuer}."
    )
    print(
        "Status: demo Keycloak organizations are "
        + ", ".join(sorted(keycloak_context.organizations_by_alias))
        + "."
    )
    print(
        "Status: new Keycloak demo users use password "
        f"'{_shared_db_keycloak_password()}'. Existing users keep their "
        "current password."
    )
    print(
        pformat(
            {
                username: {
                    "user_id": user.user_id,
                    "organization_alias": user.organization_alias,
                    "organization_groups": list(user.organization_group_names),
                    "created": user.created,
                }
                for username, user in keycloak_context.users_by_username.items()
            },
            sort_dicts=False,
            width=100,
        )
    )
    for warning in keycloak_context.warnings:
        _print_note(f"Warning: {warning}")


def _resolved_demo_service_id(
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


def _align_shared_db_tenants_with_keycloak_organizations(
    session: Session,
    *,
    tenants: list[M8flowTenantModel],
    keycloak_context: ProvisionedKeycloakSharedRealmContext,
    warnings: list[str],
) -> list[M8flowTenantModel]:
    aligned_tenants: list[M8flowTenantModel] = []
    for tenant in tenants:
        organization = keycloak_context.organizations_by_alias.get(tenant.slug)
        if organization is None:
            raise KeycloakProvisioningError(
                "Keycloak organization "
                f"'{tenant.slug}' was not available for shared-db tenant "
                "alignment."
            )
        aligned_tenants.append(
            _align_shared_db_tenant_with_keycloak_organization(
                session,
                tenant=tenant,
                organization_id=organization.organization_id,
                organization_name=organization.name,
                warnings=warnings,
            )
        )
    return aligned_tenants


def _align_shared_db_tenant_with_keycloak_organization(
    session: Session,
    *,
    tenant: M8flowTenantModel,
    organization_id: str,
    organization_name: str,
    warnings: list[str],
) -> M8flowTenantModel:
    desired_tenant_id = organization_id.strip()
    if tenant.id == desired_tenant_id:
        if tenant.name != organization_name:
            tenant.name = organization_name
            session.flush()
        return tenant

    original_tenant_id = tenant.id
    original_slug = tenant.slug
    tenant.slug = _legacy_tenant_slug(original_slug, desired_tenant_id)
    tenant.name = f"{organization_name} (legacy)"
    session.flush()

    canonical_tenant = M8flowTenantModel(
        id=desired_tenant_id,
        name=organization_name,
        slug=original_slug,
        status=tenant.status,
        created_by=tenant.created_by,
        modified_by=tenant.modified_by,
        created_at_in_seconds=tenant.created_at_in_seconds,
        updated_at_in_seconds=tenant.updated_at_in_seconds,
    )
    session.add(canonical_tenant)
    session.flush()

    for table in Base.metadata.sorted_tables:
        if table.name == M8flowTenantModel.__tablename__:
            continue
        if "m8f_tenant_id" not in table.c:
            continue
        session.execute(
            table.update()
            .where(table.c.m8f_tenant_id == original_tenant_id)
            .values(m8f_tenant_id=desired_tenant_id)
        )

    session.execute(
        UserModel.__table__.update()
        .where(UserModel.__table__.c.tenant_specific_field_1 == original_tenant_id)
        .values(tenant_specific_field_1=desired_tenant_id)
    )
    session.flush()
    session.delete(tenant)
    session.flush()

    warnings.append(
        "Shared m8flow tenant "
        f"'{original_slug}' was realigned from legacy id "
        f"'{original_tenant_id}' to Keycloak organization id "
        f"'{desired_tenant_id}'."
    )
    return canonical_tenant


def _legacy_tenant_slug(slug: str, desired_tenant_id: str) -> str:
    suffix = desired_tenant_id.replace("-", "")[:8] or "legacy"
    return f"{slug}--legacy-{suffix}"


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


def _seed_demo_context(
    session: Session,
    *,
    database_url: str,
) -> ExampleContext:
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
    seed_anchor = _offset_timestamp(_current_timestamp(), -120)
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
    foreign_noise_tenant = other_tenants[0]
    shared_realm_keycloak_context: ProvisionedKeycloakSharedRealmContext | None = (
        None
    )
    reuse_users_by_username = False
    if _is_shared_database_url(database_url):
        try:
            shared_realm_keycloak_context = (
                _provision_shared_db_keycloak_context(
                    tenant=tenant,
                    other_tenants=other_tenants,
                )
            )
        except KeycloakProvisioningError as exc:
            raise SystemExit(
                "Shared m8flow database detected, but provisioning the local "
                f"Keycloak shared realm failed: {exc}"
            ) from exc
        aligned_tenants = _align_shared_db_tenants_with_keycloak_organizations(
            session,
            tenants=[tenant, *other_tenants],
            keycloak_context=shared_realm_keycloak_context,
            warnings=warnings,
        )
        tenant = aligned_tenants[0]
        other_tenants = aligned_tenants[1:]
        foreign_noise_tenant = other_tenants[0]
        tenant_service = shared_realm_keycloak_context.service_issuer
        foreign_noise_service = shared_realm_keycloak_context.service_issuer
        reuse_users_by_username = True
    else:
        tenant_service = f"http://localhost:7002/realms/{tenant.slug}"
        foreign_noise_service = (
            f"http://localhost:7002/realms/{foreign_noise_tenant.slug}"
        )

    users = {
        role: _get_or_create_user(
            session,
            service=tenant_service,
            username=user_spec["username"],
            email=user_spec["email"],
            service_id=_resolved_demo_service_id(
                keycloak_context=shared_realm_keycloak_context,
                username=user_spec["username"],
                fallback_service_id=user_spec["service_id"],
            ),
            display_name=user_spec["display_name"],
            tenant_membership_identifiers=(tenant.id, tenant.slug),
            warnings=warnings,
            reuse_by_username_within_tenant=reuse_users_by_username,
        )
        for role, user_spec in DEMO_USERS.items()
    }
    foreign_noise_user = _get_or_create_user(
        session,
        service=foreign_noise_service,
        username=FOREIGN_NOISE_USER["username"],
        email=FOREIGN_NOISE_USER["email"],
        service_id=_resolved_demo_service_id(
            keycloak_context=shared_realm_keycloak_context,
            username=FOREIGN_NOISE_USER["username"],
            fallback_service_id=FOREIGN_NOISE_USER["service_id"],
        ),
        display_name=FOREIGN_NOISE_USER["display_name"],
        tenant_membership_identifiers=(
            foreign_noise_tenant.id,
            foreign_noise_tenant.slug,
        ),
        warnings=warnings,
        reuse_by_username_within_tenant=reuse_users_by_username,
    )
    ensure_v1_role(
        session,
        tenant_id=tenant.id,
        role_name=ROLE_USER,
        user_ids=[users["requester"].id],
    )
    ensure_v1_role(
        session,
        tenant_id=tenant.id,
        role_name=ROLE_MANAGER,
        user_ids=[
            users["manager"].id,
            users["reviewer"].id,
            users["finance"].id,
        ],
    )

    observer_noise_process_instance, observer_noise_task = _seed_noise_work_item(
        session,
        tenant=tenant,
        user=users["observer"],
        label="observer-noise",
        process_display_name="Observer Noise Example",
        task_title="Observer Noise Task",
        lane_name="Noise Lane",
        created_at_in_seconds=seed_anchor,
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
        created_at_in_seconds=_offset_timestamp(seed_anchor, 10),
        warnings=warnings,
    )
    _realign_existing_example_process_model_identifiers(
        session,
        tenant_id=tenant.id,
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


def _realign_existing_example_process_model_identifiers(
    session: Session,
    *,
    tenant_id: str,
    warnings: list[str],
) -> None:
    legacy_identifier = M8FLOW_BACKEND_PROCESS_MODEL_ID
    desired_identifier = CONDITIONAL_APPROVAL_PROCESS_MODEL_IDENTIFIER
    desired_process_definition_identifier = CONDITIONAL_APPROVAL_PROCESS_ID

    definition_count = 0
    definition_process_id_count = 0
    for definition in session.scalars(
        select(BpmnProcessDefinitionModel).where(
            BpmnProcessDefinitionModel.m8f_tenant_id == tenant_id,
        )
    ).all():
        current_process_model_identifier = definition.process_model_identifier
        if current_process_model_identifier not in (
            legacy_identifier,
            desired_identifier,
        ):
            continue
        if current_process_model_identifier != desired_identifier:
            definition.process_model_identifier = desired_identifier
            definition_count += 1
        if definition.bpmn_identifier != desired_process_definition_identifier:
            definition.bpmn_identifier = desired_process_definition_identifier
            definition_process_id_count += 1

    process_instance_count = 0
    for process_instance in session.scalars(
        select(ProcessInstanceModel).where(
            ProcessInstanceModel.m8f_tenant_id == tenant_id,
            ProcessInstanceModel.process_model_identifier == legacy_identifier,
        )
    ).all():
        process_instance.process_model_identifier = desired_identifier
        process_instance_count += 1

    human_task_count = 0
    for human_task in session.scalars(
        select(HumanTaskModel).where(
            HumanTaskModel.m8f_tenant_id == tenant_id,
            HumanTaskModel.bpmn_process_identifier == legacy_identifier,
        )
    ).all():
        human_task.bpmn_process_identifier = desired_identifier
        human_task_count += 1

    snapshot_count = 0
    skipped_snapshot_count = 0
    for snapshot in session.scalars(
        select(ProcessModelBpmnVersionModel).where(
            ProcessModelBpmnVersionModel.m8f_tenant_id == tenant_id,
            ProcessModelBpmnVersionModel.process_model_identifier
            == legacy_identifier,
        )
    ).all():
        duplicate_snapshot = session.scalar(
            select(ProcessModelBpmnVersionModel.id).where(
                ProcessModelBpmnVersionModel.m8f_tenant_id == tenant_id,
                ProcessModelBpmnVersionModel.process_model_identifier
                == desired_identifier,
                ProcessModelBpmnVersionModel.bpmn_xml_hash == snapshot.bpmn_xml_hash,
            )
        )
        if duplicate_snapshot is not None:
            skipped_snapshot_count += 1
            continue
        snapshot.process_model_identifier = desired_identifier
        snapshot_count += 1

    repaired_process_instance_count = 0
    for process_instance in session.scalars(
        select(ProcessInstanceModel).where(
            ProcessInstanceModel.m8f_tenant_id == tenant_id,
            ProcessInstanceModel.process_model_identifier.in_(
                [legacy_identifier, desired_identifier]
            ),
        )
    ).all():
        if process_instance.workflow_state_json is None:
            continue
        repair_process_instance_runtime_representation(
            session,
            tenant_id=tenant_id,
            process_instance_id=process_instance.id,
        )
        repaired_process_instance_count += 1

    if not any(
        (
            definition_count,
            definition_process_id_count,
            process_instance_count,
            human_task_count,
            snapshot_count,
            skipped_snapshot_count,
            repaired_process_instance_count,
        )
    ):
        return

    session.flush()
    warnings.append(
        "Updated existing conditional-approval example rows to use "
        f"'{desired_identifier}' instead of '{legacy_identifier}'"
        " ("
        f"definitions={definition_count}, "
        f"definition_process_ids={definition_process_id_count}, "
        f" process_instances={process_instance_count},"
        f" human_tasks={human_task_count}, snapshots={snapshot_count},"
        f" skipped_duplicate_snapshots={skipped_snapshot_count},"
        f" repaired_process_instances={repaired_process_instance_count})."
    )


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
    tenant_membership_identifiers: tuple[str, ...] = (),
    warnings: list[str],
    reuse_by_username_within_tenant: bool = False,
) -> UserModel:
    user = session.scalar(
        select(UserModel).where(
            UserModel.service == service,
            UserModel.service_id == service_id,
        )
    )
    if user is None:
        user = session.scalar(
            select(UserModel).where(
                UserModel.username == username,
                UserModel.service == service,
            )
        )
        if user is not None:
            warnings.append(
                f"User '{username}' already exists for service '{service}' with "
                "a different service identity; updating it to match the current "
                "Keycloak user id."
            )
    if user is None and reuse_by_username_within_tenant:
        tenant_identifier_set = {
            identifier.strip()
            for identifier in tenant_membership_identifiers
            if identifier.strip()
        }
        matching_users = [
            candidate
            for candidate in session.scalars(
                select(UserModel).where(UserModel.username == username)
            ).all()
            if not tenant_identifier_set
            or user_belongs_to_tenant(candidate, tenant_identifier_set)
        ]
        if matching_users:
            user = sorted(
                matching_users,
                key=lambda candidate: (
                    int(candidate.updated_at_in_seconds or 0),
                    int(candidate.created_at_in_seconds or 0),
                    int(candidate.id or 0),
                ),
                reverse=True,
            )[0]
            warnings.append(
                f"User '{username}' already exists for this tenant with a "
                "different service identity; updating it to match the shared "
                "Keycloak realm."
            )
    current_timestamp = _current_timestamp()
    if user is None:
        user = UserModel(
            username=username,
            email=email,
            service=service,
            service_id=service_id,
            display_name=display_name,
            tenant_specific_field_1=(
                tenant_membership_identifiers[0]
                if len(tenant_membership_identifiers) > 0
                else None
            ),
            tenant_specific_field_2=(
                tenant_membership_identifiers[1]
                if len(tenant_membership_identifiers) > 1
                else None
            ),
            tenant_specific_field_3=(
                tenant_membership_identifiers[2]
                if len(tenant_membership_identifiers) > 2
                else None
            ),
            created_at_in_seconds=current_timestamp,
            updated_at_in_seconds=current_timestamp,
        )
        session.add(user)
        session.flush()
        _ensure_principal_row_for_user(session, user=user, warnings=warnings)
        return user

    warnings.append(
        f"User '{username}' already exists for service '{service}'; "
        f"reusing id {user.id}."
    )
    user.service = service
    user.service_id = service_id
    user.username = username
    user.email = email
    user.display_name = display_name
    user.tenant_specific_field_1 = (
        tenant_membership_identifiers[0]
        if len(tenant_membership_identifiers) > 0
        else None
    )
    user.tenant_specific_field_2 = (
        tenant_membership_identifiers[1]
        if len(tenant_membership_identifiers) > 1
        else None
    )
    user.tenant_specific_field_3 = (
        tenant_membership_identifiers[2]
        if len(tenant_membership_identifiers) > 2
        else None
    )
    user.updated_at_in_seconds = current_timestamp
    session.flush()
    _ensure_principal_row_for_user(session, user=user, warnings=warnings)
    return user


def _ensure_principal_row_for_user(
    session: Session,
    *,
    user: UserModel,
    warnings: list[str],
) -> None:
    bind = session.get_bind()
    if bind is None:
        return

    try:
        inspector = inspect(bind)
    except Exception:
        return

    if not inspector.has_table("principal"):
        return

    existing_principal_id = session.execute(
        text("select id from principal where user_id = :user_id"),
        {"user_id": user.id},
    ).scalar_one_or_none()
    if existing_principal_id is not None:
        return

    session.execute(
        text("insert into principal (user_id) values (:user_id)"),
        {"user_id": user.id},
    )
    session.flush()
    warnings.append(
        f"Created missing principal row for user '{user.username}' "
        f"(local user id {user.id})."
    )


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
    workflow_anchor = _current_timestamp()
    definition_created_at = _offset_timestamp(workflow_anchor, -40)
    unauthorized_process_start_at = _offset_timestamp(workflow_anchor, -35)
    unauthorized_task_complete_at = _offset_timestamp(workflow_anchor, -34)
    process_started_at = _offset_timestamp(workflow_anchor, -30)
    submit_completed_at = _offset_timestamp(workflow_anchor, -20)
    manager_completed_at = _offset_timestamp(workflow_anchor, -10)
    finance_completed_at = workflow_anchor

    submission_payload = {
        "expense_date": _current_date_string(),
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
            bpmn_identifier=CONDITIONAL_APPROVAL_PROCESS_MODEL_IDENTIFIER,
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
            created_at_in_seconds=definition_created_at,
            updated_at_in_seconds=definition_created_at,
        ),
    )
    _print_note(
        f"Imported definition id {definition.id} for tenant {context.tenant_id}."
    )
    _run_rbac_checks(
        engine,
        context,
        definition,
        unauthorized_process_start_at=unauthorized_process_start_at,
        unauthorized_task_complete_at=unauthorized_task_complete_at,
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
            started_at_in_seconds=process_started_at,
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
            completed_at_in_seconds=submit_completed_at,
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
            completed_at_in_seconds=manager_completed_at,
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
                completed_at_in_seconds=finance_completed_at,
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


def _run_rbac_checks(
    engine: Engine,
    context: ExampleContext,
    definition: BpmnProcessDefinitionModel,
    *,
    unauthorized_process_start_at: int,
    unauthorized_task_complete_at: int,
) -> None:
    print()
    print(SECTION_SEPARATOR)
    print("Verification: command-level RBAC")
    print(
        "These checks prove that tenant membership by itself is not enough. "
        "The observer belongs to the main tenant and has an assigned noise "
        "task, but no V1 role, so command authorization should reject "
        "process start, task claim, and task completion."
    )
    _pause("Press Enter to run the RBAC checks.")

    _run_command_step(
        engine,
        step_number=1,
        title="Reject process start without process.start permission",
        context_text=(
            "The observer is a tenant member, but does not have the User role. "
            "Starting a process from the stored definition should fail with "
            "an AuthorizationError mentioning process.start."
        ),
        command=api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=context.tenant_id,
            bpmn_process_definition_id=definition.id,
            process_initiator_id=context.user_ids["observer"],
            summary="Unauthorized start attempt",
            process_version=1,
            started_at_in_seconds=unauthorized_process_start_at,
            bpmn_process_id=CONDITIONAL_APPROVAL_PROCESS_ID,
        ),
        prefix="RBAC",
        expected_failure=api.AuthorizationError,
        expected_failure_contains="process.start",
    )

    _run_command_step(
        engine,
        step_number=2,
        title="Reject task claim without task.claim permission",
        context_text=(
            "The observer has a dedicated noise task assigned, so this is a "
            "pure RBAC check rather than an assignment check. Claiming that "
            "task should fail with an AuthorizationError mentioning task.claim."
        ),
        command=api.ClaimTaskCommand(
            tenant_id=context.tenant_id,
            human_task_id=context.noise_task_ids["observer"],
            user_id=context.user_ids["observer"],
        ),
        prefix="RBAC",
        expected_failure=api.AuthorizationError,
        expected_failure_contains="task.claim",
    )

    _run_command_step(
        engine,
        step_number=3,
        title="Reject task completion without task.complete permission",
        context_text=(
            "The same assigned noise task is used again here. Completing it "
            "should fail at the RBAC layer before any task-state transition "
            "logic runs, with an AuthorizationError mentioning task.complete."
        ),
        command=api.CompleteTaskCommand(
            tenant_id=context.tenant_id,
            human_task_id=context.noise_task_ids["observer"],
            user_id=context.user_ids["observer"],
            completed_at_in_seconds=unauthorized_task_complete_at,
        ),
        prefix="RBAC",
        expected_failure=api.AuthorizationError,
        expected_failure_contains="task.complete",
    )

    _print_note(
        "RBAC check complete: the observer can belong to the tenant and still "
        "be blocked from protected commands until a role grants them."
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
            if not key.startswith("__m8f_")
        }
        return {
            "id": result.id,
            "identifier": result.bpmn_identifier,
            "process_model_identifier": result.process_model_identifier,
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
