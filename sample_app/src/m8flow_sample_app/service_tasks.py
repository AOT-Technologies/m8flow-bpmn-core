from __future__ import annotations

import ast
from collections.abc import Sequence

from sqlalchemy import select

from m8flow_bpmn_core import api
from m8flow_bpmn_core.errors import ServiceTaskExecutionError
from m8flow_sample_app.db import session_scope
from m8flow_sample_app.models import SecretModel
from m8flow_sample_app.settings import get_settings

SMTP_SECRET_KEYS = {
    "smtp_host": "MAILTRAP_SMTP_HOST",
    "smtp_port": "MAILTRAP_SMTP_PORT",
    "smtp_user": "MAILTRAP_SMTP_USERNAME",
    "smtp_password": "MAILTRAP_SMTP_PASSWORD",
    "smtp_starttls": "MAILTRAP_SMTP_STARTTLS",
    "email_from": "MAILTRAP_EMAIL_FROM",
}


class TenantSecretBackedSmtpConnector:
    connector_key = "smtp"

    def __init__(self, *, delegate: api.ServiceTaskConnector) -> None:
        self._delegate = delegate

    def list_commands(self) -> Sequence[api.ServiceTaskCommandDefinition]:
        return self._delegate.list_commands()

    def execute(self, request: api.ServiceTaskRequest) -> api.ServiceTaskResult:
        tenant_id = request.context.tenant_id if request.context is not None else None
        if tenant_id is None:
            raise ServiceTaskExecutionError(
                "SMTP service tasks require a tenant_id in the execution context."
            )

        parameters = dict(request.parameters or {})
        tenant_secrets = _load_tenant_secret_map(tenant_id=tenant_id)
        missing_secret_keys: list[str] = []
        for parameter_name, secret_key in SMTP_SECRET_KEYS.items():
            if parameter_name in parameters:
                continue
            secret_value = tenant_secrets.get(secret_key)
            if secret_value is None:
                missing_secret_keys.append(secret_key)
                continue
            parameters[parameter_name] = _coerce_secret_value(secret_value)

        if missing_secret_keys:
            joined_secret_keys = ", ".join(sorted(missing_secret_keys))
            raise ServiceTaskExecutionError(
                "The tenant is missing required SMTP secrets: "
                f"{joined_secret_keys}."
            )

        delegated_request = api.ServiceTaskRequest(
            operation_id=request.operation_id,
            parameters=parameters,
            context=request.context,
            task_data=request.task_data,
            callback_url=request.callback_url,
            metadata=request.metadata,
        )
        return self._delegate.execute(delegated_request)


def build_sample_app_service_task_registry() -> api.ServiceTaskRegistry:
    settings = get_settings()
    registry = api.build_connector_proxy_service_task_registry(
        settings.connector_proxy_base_url,
        timeout_seconds=settings.connector_proxy_timeout_seconds,
    )
    smtp_connector = registry.get_connector("smtp")
    registry.register_connector(
        TenantSecretBackedSmtpConnector(delegate=smtp_connector),
        replace=True,
    )
    return registry


def _load_tenant_secret_map(*, tenant_id: str) -> dict[str, str]:
    with session_scope() as db_session:
        secret_rows = db_session.scalars(
            select(SecretModel).where(SecretModel.m8f_tenant_id == tenant_id)
        )
        return {
            secret.key: secret.value
            for secret in secret_rows
        }


def _coerce_secret_value(value: str) -> object:
    normalized = value.strip()
    lowered = normalized.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"none", "null"}:
        return None
    try:
        return ast.literal_eval(normalized)
    except (SyntaxError, ValueError):
        return normalized
