from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from m8flow_bpmn_core import api
from m8flow_bpmn_core.errors import NotFoundError, ValidationError
from m8flow_bpmn_core.services.service_tasks import (
    build_connector_proxy_execute_path,
    resolve_service_task_registry,
)


@dataclass
class FakeConnector:
    connector_key: str
    commands: tuple[api.ServiceTaskCommandDefinition, ...]
    seen_operation_ids: list[str] = field(default_factory=list)

    def list_commands(self) -> tuple[api.ServiceTaskCommandDefinition, ...]:
        return self.commands

    def execute(self, request: api.ServiceTaskRequest) -> api.ServiceTaskResult:
        self.seen_operation_ids.append(request.operation_id)
        return api.ServiceTaskResult(
            payload={
                "operation_id": request.operation_id,
                "parameters": dict(request.parameters or {}),
            }
        )


def test_service_task_operation_id_round_trips() -> None:
    operation_id = api.build_service_task_operation_id("http", "GetRequestV2")

    assert operation_id == "http/GetRequestV2"
    assert api.split_service_task_operation_id(operation_id) == (
        "http",
        "GetRequestV2",
    )
    assert (
        build_connector_proxy_execute_path("http", "GetRequestV2")
        == "/v1/do/http/GetRequestV2"
    )


def test_invalid_service_task_operation_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        api.split_service_task_operation_id("http")

    with pytest.raises(ValidationError):
        api.build_service_task_operation_id("http/service", "GetRequestV2")


def test_registry_registers_lists_and_executes_service_tasks() -> None:
    registry = api.ServiceTaskRegistry()
    connector = FakeConnector(
        connector_key="http",
        commands=(
            api.ServiceTaskCommandDefinition(
                connector_key="http",
                command_name="GetRequestV2",
                display_name="HTTP GET",
            ),
        ),
    )

    registry.register_connector(connector)
    result = registry.execute(
        api.ServiceTaskRequest(
            operation_id="http/GetRequestV2",
            parameters={"url": "https://example.test"},
            context=api.ServiceTaskContext(
                tenant_id="tenant-a",
                process_instance_id=17,
                task_guid="task-guid-1",
                task_name="Task_fetch",
                task_type="ServiceTask",
            ),
        )
    )

    assert registry.list_connectors() == ("http",)
    assert [command.operation_id for command in registry.list_commands()] == [
        "http/GetRequestV2"
    ]
    assert connector.seen_operation_ids == ["http/GetRequestV2"]
    assert result.payload == {
        "operation_id": "http/GetRequestV2",
        "parameters": {"url": "https://example.test"},
    }


def test_registry_rejects_duplicate_connector_without_replace() -> None:
    registry = api.ServiceTaskRegistry()
    connector = FakeConnector(
        connector_key="smtp",
        commands=(
            api.ServiceTaskCommandDefinition(
                connector_key="smtp",
                command_name="SendHTMLEmail",
            ),
        ),
    )

    registry.register_connector(connector)

    with pytest.raises(ValidationError):
        registry.register_connector(connector)


def test_registry_raises_not_found_for_unknown_command() -> None:
    registry = api.ServiceTaskRegistry()
    registry.register_connector(
        FakeConnector(
            connector_key="http",
            commands=(
                api.ServiceTaskCommandDefinition(
                    connector_key="http",
                    command_name="GetRequestV2",
                ),
            ),
        )
    )

    with pytest.raises(NotFoundError):
        registry.execute(
            api.ServiceTaskRequest(
                operation_id="http/PostRequestV2",
                parameters={"url": "https://example.test"},
            )
        )


def test_service_task_registry_scope_overrides_default_factory() -> None:
    scoped_registry = api.ServiceTaskRegistry(
        connectors=(
            FakeConnector(
                connector_key="http",
                commands=(
                    api.ServiceTaskCommandDefinition(
                        connector_key="http",
                        command_name="GetRequestV2",
                    ),
                ),
            ),
        )
    )

    api.set_default_service_task_registry_factory(api.ServiceTaskRegistry)
    default_registry = resolve_service_task_registry()
    assert isinstance(default_registry, api.ServiceTaskRegistry)
    assert default_registry is not scoped_registry

    with api.service_task_registry_scope(scoped_registry):
        assert resolve_service_task_registry() is scoped_registry

    restored_registry = resolve_service_task_registry()
    assert isinstance(restored_registry, api.ServiceTaskRegistry)
    assert restored_registry is not scoped_registry
