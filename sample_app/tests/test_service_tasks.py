from __future__ import annotations

from collections.abc import Sequence

import pytest

from m8flow_bpmn_core import api
from m8flow_sample_app import service_tasks


class _RecordingConnector:
    connector_key = "http"

    def __init__(self) -> None:
        self.requests: list[api.ServiceTaskRequest] = []
        self._command = api.ServiceTaskCommandDefinition(
            connector_key="http",
            command_name="GetRequestV2",
        )

    def list_commands(self) -> Sequence[api.ServiceTaskCommandDefinition]:
        return (self._command,)

    def execute(self, request: api.ServiceTaskRequest) -> api.ServiceTaskResult:
        self.requests.append(request)
        return api.ServiceTaskResult(payload={"ok": True})


def test_tenant_secret_resolving_connector_resolves_generic_secret_references(
    monkeypatch,
) -> None:
    delegate = _RecordingConnector()
    connector = service_tasks.TenantSecretResolvingConnector(delegate=delegate)
    monkeypatch.setattr(
        service_tasks,
        "_load_tenant_secret_map",
        lambda *, tenant_id: {
            "API_HOST": "api.example.test",
            "API_TOKEN": "super-secret-token",
            "API_TIMEOUT_SECONDS": "30",
            "API_VERIFY_TLS": "false",
        },
    )

    connector.execute(
        api.ServiceTaskRequest(
            operation_id="http/GetRequestV2",
            parameters={
                "url": "https://M8FLOW_SECRET:API_HOST/v1/orders",
                "headers": {
                    "Authorization": "Bearer M8FLOW_SECRET:API_TOKEN",
                },
                "timeout_seconds": "M8FLOW_SECRET:API_TIMEOUT_SECONDS",
                "verify_tls": "M8FLOW_SECRET:API_VERIFY_TLS",
            },
            context=api.ServiceTaskContext(tenant_id="tenant-alpha"),
            metadata={
                "parameter_types": {
                    "url": "str",
                    "headers": "dict",
                    "timeout_seconds": "int",
                    "verify_tls": "bool",
                }
            },
        )
    )

    assert len(delegate.requests) == 1
    delegated_request = delegate.requests[0]
    assert delegated_request.parameters == {
        "url": "https://api.example.test/v1/orders",
        "headers": {
            "Authorization": "Bearer super-secret-token",
        },
        "timeout_seconds": 30,
        "verify_tls": False,
    }


def test_tenant_secret_resolving_connector_rejects_placeholder_secret_values(
    monkeypatch,
) -> None:
    delegate = _RecordingConnector()
    connector = service_tasks.TenantSecretResolvingConnector(delegate=delegate)
    monkeypatch.setattr(
        service_tasks,
        "_load_tenant_secret_map",
        lambda *, tenant_id: {
            "SMTP_PASSWORD": service_tasks.UNCONFIGURED_SECRET_PLACEHOLDER,
        },
    )

    with pytest.raises(api.ServiceTaskExecutionError) as exc_info:
        connector.execute(
            api.ServiceTaskRequest(
                operation_id="http/GetRequestV2",
                parameters={
                    "password": "M8FLOW_SECRET:SMTP_PASSWORD",
                },
                context=api.ServiceTaskContext(tenant_id="tenant-alpha"),
                metadata={
                    "parameter_types": {
                        "password": "str",
                    }
                },
            )
        )

    assert "SMTP_PASSWORD" in str(exc_info.value)
    assert "default placeholder value" in str(exc_info.value)
    assert delegate.requests == []
