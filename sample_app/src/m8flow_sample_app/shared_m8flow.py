from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree

from sqlalchemy.engine.url import make_url

from m8flow_sample_app.settings import AuditMode, Settings, get_settings

SHARED_M8FLOW_AUDIT_CONTEXT_KEY = "m8flow_shared_audit_context"
BackendCatalogPublishStatus = Literal["skipped", "created", "updated", "unchanged"]


@dataclass(frozen=True, slots=True)
class SharedM8flowAuditContext:
    mode: str
    requested_mode: AuditMode
    database_name: str | None
    process_models_root: Path | None
    backend_container_name: str | None
    backend_tenant_root_override: str | None
    warnings: tuple[str, ...]

    @property
    def uses_shared_m8flow(self) -> bool:
        return self.mode == "shared"


@dataclass(frozen=True, slots=True)
class BackendCatalogPublishResult:
    status: BackendCatalogPublishStatus
    process_models_root: Path | None
    tenant_root: str | None
    process_group_id: str | None
    process_model_id: str | None
    container_name: str | None
    warnings: tuple[str, ...]

    @property
    def published(self) -> bool:
        return self.status in {"created", "updated", "unchanged"}


def discover_shared_m8flow_audit_context(
    database_url: str | None = None,
    *,
    settings: Settings | None = None,
) -> SharedM8flowAuditContext:
    settings = settings or get_settings()
    resolved_database_url = database_url or settings.database_url
    database_name = _database_name_from_url(resolved_database_url)
    requested_mode = settings.m8flow_audit_mode

    uses_shared_m8flow = False
    if requested_mode == "shared":
        uses_shared_m8flow = True
    elif requested_mode == "auto":
        uses_shared_m8flow = (
            (database_name or "").strip() == settings.m8flow_shared_database_name
        )

    warnings: list[str] = []
    process_models_root: Path | None = None
    backend_container_name: str | None = None
    if uses_shared_m8flow:
        (
            process_models_root,
            backend_container_name,
            discovery_warnings,
        ) = resolve_m8flow_backend_process_models_root(settings=settings)
        warnings.extend(discovery_warnings)

    return SharedM8flowAuditContext(
        mode="shared" if uses_shared_m8flow else "standalone",
        requested_mode=requested_mode,
        database_name=database_name,
        process_models_root=process_models_root,
        backend_container_name=backend_container_name,
        backend_tenant_root_override=_normalize_optional_string(
            settings.m8flow_backend_tenant_root
        ),
        warnings=tuple(warnings),
    )


def resolve_m8flow_backend_tenant_root(
    *,
    tenant_id: str,
    tenant_slug: str,
    settings: Settings | None = None,
) -> tuple[str, tuple[str, ...]]:
    settings = settings or get_settings()
    override = _normalize_optional_string(settings.m8flow_backend_tenant_root)
    if override is not None:
        return (
            override,
            (
                "Deploying the process model into backend tenant root "
                f"'{override}' instead of tenant id '{tenant_id}'.",
            ),
        )
    if tenant_slug and tenant_slug != tenant_id:
        return tenant_id, ()
    return tenant_id, ()


def resolve_m8flow_backend_process_models_root(
    *,
    settings: Settings | None = None,
) -> tuple[Path | None, str | None, list[str]]:
    settings = settings or get_settings()
    override = _normalize_optional_string(settings.m8flow_backend_process_models_dir)
    if override is not None:
        return Path(override).expanduser(), None, []

    for container_name in backend_container_names(settings=settings):
        payload = _docker_inspect_container(container_name)
        if payload is None:
            continue
        source = _extract_process_models_mount_source(payload, settings=settings)
        if source is None:
            return (
                None,
                container_name,
                [
                    "Found a running m8flow-backend Docker container, but "
                    "could not determine its process-model mount source.",
                ],
            )
        return Path(source), container_name, []

    return (
        None,
        None,
        [
            "Shared m8flow audit mode is active, but no local m8flow-backend "
            "process-model catalog could be discovered. Set "
            "M8FLOW_SAMPLE_APP_M8FLOW_BACKEND_PROCESS_MODELS_DIR if you want "
            "the sample app BPMN models deployed into the m8flow UI catalog.",
        ],
    )


def backend_container_names(*, settings: Settings | None = None) -> list[str]:
    settings = settings or get_settings()
    candidates = [
        item.strip()
        for item in settings.m8flow_backend_container_names.split(",")
    ]

    ordered_candidates: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        ordered_candidates.append(candidate)
    return ordered_candidates


def publish_process_model_to_m8flow_backend(
    *,
    audit_context: SharedM8flowAuditContext | None,
    tenant_id: str,
    tenant_slug: str,
    process_model_identifier: str,
    bpmn_name: str,
    source_bpmn_xml: str,
    primary_file_name: str,
    model_description: str | None = None,
    settings: Settings | None = None,
) -> BackendCatalogPublishResult | None:
    if audit_context is None or not audit_context.uses_shared_m8flow:
        return None

    settings = settings or get_settings()
    warnings = list(audit_context.warnings)
    tenant_root, tenant_warnings = resolve_m8flow_backend_tenant_root(
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        settings=settings,
    )
    warnings.extend(tenant_warnings)

    process_models_root = audit_context.process_models_root
    if process_models_root is None:
        return BackendCatalogPublishResult(
            status="skipped",
            process_models_root=None,
            tenant_root=tenant_root,
            process_group_id=None,
            process_model_id=None,
            container_name=audit_context.backend_container_name,
            warnings=tuple(warnings),
        )

    parsed_identifier = _parse_backend_process_model_identifier(
        process_model_identifier
    )
    if parsed_identifier is None:
        warnings.append(
            "m8flow backend catalog publishing requires a process model "
            "identifier in '<group>/<model>' format."
        )
        return BackendCatalogPublishResult(
            status="skipped",
            process_models_root=process_models_root,
            tenant_root=tenant_root,
            process_group_id=None,
            process_model_id=None,
            container_name=audit_context.backend_container_name,
            warnings=tuple(warnings),
        )

    process_group_id, process_model_id = parsed_identifier
    primary_process_id = _primary_process_id_from_bpmn(source_bpmn_xml)
    if primary_process_id is None:
        warnings.append(
            "The sample app BPMN could not be published into the m8flow "
            "backend catalog because its primary BPMN process id could not be "
            "determined."
        )
        return BackendCatalogPublishResult(
            status="skipped",
            process_models_root=process_models_root,
            tenant_root=tenant_root,
            process_group_id=process_group_id,
            process_model_id=process_model_id,
            container_name=audit_context.backend_container_name,
            warnings=tuple(warnings),
        )

    group_dir = process_models_root / tenant_root / process_group_id
    model_dir = group_dir / process_model_id
    group_json_path = group_dir / "process_group.json"
    model_json_path = model_dir / "process_model.json"
    bpmn_path = model_dir / primary_file_name

    desired_group_payload = _backend_process_group_payload(process_group_id)
    desired_model_payload = _backend_process_model_payload(
        bpmn_name=bpmn_name,
        primary_file_name=primary_file_name,
        primary_process_id=primary_process_id,
        model_description=model_description,
    )

    existing_files = [group_json_path, model_json_path, bpmn_path]
    deployment_already_current = all(
        path.exists() for path in existing_files
    ) and _existing_backend_catalog_deployment_matches(
        group_json_path=group_json_path,
        desired_group_payload=desired_group_payload,
        model_json_path=model_json_path,
        desired_model_payload=desired_model_payload,
        bpmn_path=bpmn_path,
        desired_bpmn_xml=source_bpmn_xml,
    )
    if deployment_already_current:
        return BackendCatalogPublishResult(
            status="unchanged",
            process_models_root=process_models_root,
            tenant_root=tenant_root,
            process_group_id=process_group_id,
            process_model_id=process_model_id,
            container_name=audit_context.backend_container_name,
            warnings=tuple(warnings),
        )

    had_existing_files = any(path.exists() for path in existing_files)
    try:
        model_dir.mkdir(parents=True, exist_ok=True)
        _write_json_file(group_json_path, desired_group_payload)
        _write_json_file(model_json_path, desired_model_payload)
        bpmn_path.write_text(source_bpmn_xml, encoding="utf-8")
    except OSError as exc:
        warnings.append(
            "The sample app imported the workflow definition, but publishing "
            "it into the m8flow backend catalog failed: "
            f"{exc}"
        )
        return BackendCatalogPublishResult(
            status="skipped",
            process_models_root=process_models_root,
            tenant_root=tenant_root,
            process_group_id=process_group_id,
            process_model_id=process_model_id,
            container_name=audit_context.backend_container_name,
            warnings=tuple(warnings),
        )

    return BackendCatalogPublishResult(
        status="updated" if had_existing_files else "created",
        process_models_root=process_models_root,
        tenant_root=tenant_root,
        process_group_id=process_group_id,
        process_model_id=process_model_id,
        container_name=audit_context.backend_container_name,
        warnings=tuple(warnings),
    )


def _database_name_from_url(database_url: str) -> str | None:
    try:
        return make_url(database_url).database
    except Exception:
        return None


def _docker_inspect_container(container_name: str) -> list[dict[str, Any]] | None:
    try:
        result = subprocess.run(
            ["docker", "inspect", container_name],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None

    if result.returncode != 0:
        return None

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, list) else None


def _extract_process_models_mount_source(
    inspect_payload: list[dict[str, Any]],
    *,
    settings: Settings,
) -> str | None:
    if not inspect_payload:
        return None

    target = settings.m8flow_backend_process_models_target
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
            return _normalize_docker_desktop_mount_source(source.strip())
    return None


def _normalize_docker_desktop_mount_source(source: str) -> str:
    normalized_source = source.strip()
    docker_desktop_source = normalized_source.replace("\\", "/")

    host_mnt_match = re.match(
        r"^(?:[a-zA-Z]:)?/host_mnt/([a-zA-Z])(?:/(.*))?$",
        docker_desktop_source,
    )
    if host_mnt_match is None:
        return normalized_source

    drive_letter = host_mnt_match.group(1).upper()
    remaining_path = (host_mnt_match.group(2) or "").replace("/", "\\")
    if not remaining_path:
        return f"{drive_letter}:\\"
    return f"{drive_letter}:\\{remaining_path}"


def _parse_backend_process_model_identifier(
    process_model_identifier: str,
) -> tuple[str, str] | None:
    parts = [part.strip() for part in process_model_identifier.split("/")]
    if len(parts) != 2:
        return None

    process_group_id, process_model_id = parts
    if not process_group_id or not process_model_id:
        return None
    if not _is_safe_backend_catalog_segment(process_group_id):
        return None
    if not _is_safe_backend_catalog_segment(process_model_id):
        return None
    return process_group_id, process_model_id


def _is_safe_backend_catalog_segment(value: str) -> bool:
    if value in {".", ".."}:
        return False
    return re.search(r'[<>:"\\|?*]', value) is None


def _primary_process_id_from_bpmn(source_bpmn_xml: str) -> str | None:
    try:
        root = ElementTree.fromstring(source_bpmn_xml)
    except ElementTree.ParseError:
        return None

    for element in root.iter():
        if not str(element.tag).endswith("process"):
            continue
        process_id = element.attrib.get("id", "").strip()
        if process_id:
            return process_id
    return None


def _backend_process_group_payload(process_group_id: str) -> dict[str, Any]:
    return {
        "correlation_keys": None,
        "correlation_properties": None,
        "data_store_specifications": {},
        "description": (
            "Process models published by the m8flow-bpmn-core sample app."
        ),
        "display_name": _humanize_identifier(process_group_id),
        "messages": None,
    }


def _backend_process_model_payload(
    *,
    bpmn_name: str,
    primary_file_name: str,
    primary_process_id: str,
    model_description: str | None,
) -> dict[str, Any]:
    return {
        "description": (
            model_description
            or "Published by the m8flow-bpmn-core sample app."
        ),
        "display_name": bpmn_name,
        "exception_notification_addresses": [],
        "fault_or_suspend_on_exception": "fault",
        "metadata_extraction_paths": None,
        "primary_file_name": primary_file_name,
        "primary_process_id": primary_process_id,
    }


def _existing_backend_catalog_deployment_matches(
    *,
    group_json_path: Path,
    desired_group_payload: dict[str, Any],
    model_json_path: Path,
    desired_model_payload: dict[str, Any],
    bpmn_path: Path,
    desired_bpmn_xml: str,
) -> bool:
    return (
        _json_file_matches(group_json_path, desired_group_payload)
        and _json_file_matches(model_json_path, desired_model_payload)
        and bpmn_path.read_text(encoding="utf-8") == desired_bpmn_xml
    )


def _json_file_matches(path: Path, expected_payload: dict[str, Any]) -> bool:
    try:
        current_payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return current_payload == expected_payload


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(payload, indent=4)}\n", encoding="utf-8")


def _humanize_identifier(value: str) -> str:
    normalized = re.sub(r"[-_]+", " ", value).strip()
    if not normalized:
        return value
    return normalized.title()


def _normalize_optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
