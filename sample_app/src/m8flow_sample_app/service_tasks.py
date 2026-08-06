from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace

from sqlalchemy import select

from m8flow_bpmn_core import api
from m8flow_bpmn_core.errors import ServiceTaskExecutionError
from m8flow_sample_app.db import session_scope
from m8flow_sample_app.models import SecretModel
from m8flow_sample_app.settings import get_settings

M8FLOW_SECRET_REFERENCE_RE = re.compile(r"M8FLOW_SECRET:(?P<name>\w+)")
# Bandit: placeholder marker, not a real credential.
UNCONFIGURED_SECRET_PLACEHOLDER = "CHANGE_ME_IN_SECRETS_UI"  # nosec B105


class TenantSecretResolvingConnector:
    def __init__(self, *, delegate: api.ServiceTaskConnector) -> None:
        self._delegate = delegate
        self.connector_key = delegate.connector_key

    def list_commands(self) -> Sequence[api.ServiceTaskCommandDefinition]:
        return self._delegate.list_commands()

    def execute(self, request: api.ServiceTaskRequest) -> api.ServiceTaskResult:
        parameters = dict(request.parameters or {})
        referenced_secret_keys = _collect_referenced_secret_keys(parameters)
        if not referenced_secret_keys:
            return self._delegate.execute(request)

        tenant_id = request.context.tenant_id if request.context is not None else None
        if tenant_id is None:
            raise ServiceTaskExecutionError(
                "Service tasks that reference M8FLOW_SECRET values require a "
                "tenant_id in the execution context."
            )

        tenant_secrets = _load_tenant_secret_map(tenant_id=tenant_id)
        missing_secret_keys = sorted(
            secret_key
            for secret_key in referenced_secret_keys
            if secret_key not in tenant_secrets
        )
        if missing_secret_keys:
            raise ServiceTaskExecutionError(
                "The tenant is missing required secrets referenced by service "
                f"task {request.operation_id!r}: {', '.join(missing_secret_keys)}."
            )
        _raise_if_placeholder_secret_values_are_used(
            request=request,
            tenant_secrets=tenant_secrets,
            referenced_secret_keys=referenced_secret_keys,
        )

        resolved_parameters = _resolve_secret_references(
            parameters,
            tenant_secrets=tenant_secrets,
        )
        resolved_parameters = _coerce_parameter_values(
            resolved_parameters,
            parameter_types=_parameter_types_from_request(request),
        )
        return self._delegate.execute(
            replace(
                request,
                parameters=resolved_parameters,
            )
        )


def build_sample_app_service_task_registry() -> api.ServiceTaskRegistry:
    settings = get_settings()
    registry = api.build_connector_proxy_service_task_registry(
        settings.connector_proxy_base_url,
        timeout_seconds=settings.connector_proxy_timeout_seconds,
    )
    for connector_key in registry.list_connectors():
        registry.register_connector(
            TenantSecretResolvingConnector(
                delegate=registry.get_connector(connector_key),
            ),
            replace=True,
        )
    return registry


def _load_tenant_secret_map(*, tenant_id: str) -> dict[str, str]:
    with session_scope() as db_session:
        secret_rows = db_session.scalars(
            select(SecretModel).where(SecretModel.m8f_tenant_id == tenant_id)
        )
        return {secret.key: secret.value for secret in secret_rows}


def _raise_if_placeholder_secret_values_are_used(
    *,
    request: api.ServiceTaskRequest,
    tenant_secrets: Mapping[str, str],
    referenced_secret_keys: set[str],
) -> None:
    placeholder_secret_keys = sorted(
        secret_key
        for secret_key in referenced_secret_keys
        if tenant_secrets.get(secret_key, "").strip() == UNCONFIGURED_SECRET_PLACEHOLDER
    )
    if not placeholder_secret_keys:
        return
    verb = "uses" if len(placeholder_secret_keys) == 1 else "use"
    pronoun = "it" if len(placeholder_secret_keys) == 1 else "them"
    raise ServiceTaskExecutionError(
        "The tenant secret"
        f"{'s' if len(placeholder_secret_keys) != 1 else ''} "
        f"{', '.join(placeholder_secret_keys)!r} "
        f"still {verb} the default placeholder value. Update "
        f"{pronoun} in Secrets "
        f"before running service task {request.operation_id!r}."
    )


def _collect_referenced_secret_keys(value: object) -> set[str]:
    referenced_secret_keys: set[str] = set()
    _visit_secret_references(value, referenced_secret_keys)
    return referenced_secret_keys


def _visit_secret_references(value: object, referenced_secret_keys: set[str]) -> None:
    if isinstance(value, str):
        for match in M8FLOW_SECRET_REFERENCE_RE.finditer(value):
            referenced_secret_keys.add(match.group("name"))
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _visit_secret_references(item, referenced_secret_keys)
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for item in value:
            _visit_secret_references(item, referenced_secret_keys)


def _resolve_secret_references(
    value: object,
    *,
    tenant_secrets: Mapping[str, str],
) -> object:
    if isinstance(value, str):
        return M8FLOW_SECRET_REFERENCE_RE.sub(
            lambda match: tenant_secrets[match.group("name")],
            value,
        )
    if isinstance(value, Mapping):
        return {
            key: _resolve_secret_references(item, tenant_secrets=tenant_secrets)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [
            _resolve_secret_references(item, tenant_secrets=tenant_secrets)
            for item in value
        ]
    return value


def _parameter_types_from_request(request: api.ServiceTaskRequest) -> Mapping[str, object]:
    metadata = request.metadata or {}
    parameter_types = metadata.get("parameter_types")
    return parameter_types if isinstance(parameter_types, Mapping) else {}


def _coerce_parameter_values(
    parameters: Mapping[str, object],
    *,
    parameter_types: Mapping[str, object],
) -> dict[str, object]:
    return {
        key: _coerce_parameter_value(
            value,
            declared_type=parameter_types.get(key),
            parameter_name=key,
        )
        for key, value in parameters.items()
    }


def _coerce_parameter_value(
    value: object,
    *,
    declared_type: object,
    parameter_name: str,
) -> object:
    normalized_type = str(declared_type or "").strip().lower()
    if not isinstance(value, str) or not normalized_type:
        return value

    normalized_value = value.strip()
    try:
        if normalized_type in {"bool", "boolean"}:
            lowered = normalized_value.lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
            raise ValueError("expected a boolean value")
        if normalized_type in {"int", "integer"}:
            return int(normalized_value)
        if normalized_type in {"float", "number"}:
            return float(normalized_value)
        if normalized_type in {"dict", "list", "json", "object", "array"}:
            return ast.literal_eval(normalized_value)
    except (SyntaxError, ValueError) as exc:
        raise ServiceTaskExecutionError(
            f"Service task parameter {parameter_name!r} could not be coerced "
            f"to declared type {normalized_type!r}: {exc}"
        ) from exc
    return value
