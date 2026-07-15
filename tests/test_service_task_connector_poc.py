from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.request import urlopen

import pytest

from m8flow_bpmn_core import api

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

import service_task_connector_poc as example_poc  # noqa: E402


class _FakeConnector:
    def __init__(
        self,
        connector_key: str,
        *command_names: str,
    ) -> None:
        self.connector_key = connector_key
        self._commands = tuple(
            api.ServiceTaskCommandDefinition(
                connector_key=connector_key,
                command_name=command_name,
            )
            for command_name in command_names
        )

    def list_commands(self) -> tuple[api.ServiceTaskCommandDefinition, ...]:
        return self._commands

    def execute(self, request: api.ServiceTaskRequest) -> api.ServiceTaskResult:
        return api.ServiceTaskResult(payload={"operation_id": request.operation_id})


def test_render_service_task_connector_bpmn_xml_inlines_proxy_urls() -> None:
    rendered_bpmn = example_poc._render_service_task_connector_bpmn_xml(
        prepare_url="http://host.docker.internal:8765/prepare",
        finalize_url="http://host.docker.internal:8765/finalize",
    )

    assert "__PREPARE_URL_EXPRESSION__" not in rendered_bpmn
    assert "__FINALIZE_URL_EXPRESSION__" not in rendered_bpmn
    assert (
        "'http://host.docker.internal:8765/prepare' + '?' + "
        "'submission_message' + '=' + str(submission_message)"
    ) in rendered_bpmn
    assert (
        "'http://host.docker.internal:8765/finalize' + '?' + "
        "'decision' + '=' + str(decision)"
    ) in rendered_bpmn
    assert "<bpmndi:BPMNDiagram" in rendered_bpmn
    assert "Participant_service_task_connector_poc" in rendered_bpmn
    assert "Task_prepare_di" in rendered_bpmn
    assert "Task_finalize_di" in rendered_bpmn


def test_demo_connector_server_records_prepare_and_finalize_requests() -> None:
    with example_poc.DemoConnectorServer(proxy_host_alias="127.0.0.1") as server:
        with urlopen(
            f"{server.local_base_url}/prepare?submission_message=hello",
            timeout=5,
        ) as response:
            prepare_payload = json.loads(response.read().decode("utf-8"))
        with urlopen(
            f"{server.local_base_url}/finalize?decision=approved",
            timeout=5,
        ) as response:
            finalize_payload = json.loads(response.read().decode("utf-8"))

        recorded_requests = server.snapshot_requests()

    assert prepare_payload == {
        "stage": "prepare",
        "submission_message": "hello",
        "sequence": 1,
    }
    assert finalize_payload == {
        "stage": "finalize",
        "decision": "approved",
        "sequence": 2,
    }
    assert [(request.path, request.query) for request in recorded_requests] == [
        ("/prepare", {"submission_message": "hello"}),
        ("/finalize", {"decision": "approved"}),
    ]


def test_prepare_connector_proxy_registry_returns_registry_when_required_command_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = api.ServiceTaskRegistry(
        connectors=(
            _FakeConnector("http", "GetRequestV2", "PostRequestV2"),
        )
    )
    monkeypatch.setattr(example_poc, "_pause", lambda _prompt: None)
    monkeypatch.setattr(
        example_poc.api,
        "build_connector_proxy_service_task_registry",
        lambda _base_url: registry,
    )

    with example_poc.DemoConnectorServer(proxy_host_alias="127.0.0.1") as server:
        resolved_registry = example_poc._prepare_connector_proxy_registry(
            demo_server=server
        )

    assert resolved_registry is registry


def test_prepare_connector_proxy_registry_rejects_missing_required_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = api.ServiceTaskRegistry(
        connectors=(
            _FakeConnector("http", "GetRequest"),
        )
    )
    monkeypatch.setattr(example_poc, "_pause", lambda _prompt: None)
    monkeypatch.setattr(
        example_poc.api,
        "build_connector_proxy_service_task_registry",
        lambda _base_url: registry,
    )

    with example_poc.DemoConnectorServer(proxy_host_alias="127.0.0.1") as server:
        with pytest.raises(SystemExit) as exc_info:
            example_poc._prepare_connector_proxy_registry(demo_server=server)

    assert example_poc.REQUIRED_HTTP_OPERATION_ID in str(exc_info.value)


def test_deploy_service_task_definition_to_m8flow_backend_writes_files(
    tmp_path: Path,
) -> None:
    deployment = example_poc._deploy_service_task_definition_to_m8flow_backend(
        process_models_root=tmp_path,
        tenant_root="tenant-service-task-connector-example",
        bpmn_xml="<definitions />",
    )

    assert deployment.deployed is True
    assert deployment.already_deployed is False
    assert deployment.warnings == ()

    group_dir = (
        tmp_path
        / "tenant-service-task-connector-example"
        / example_poc.PROCESS_GROUP_ID
    )
    model_dir = group_dir / example_poc.PROCESS_MODEL_ID

    assert json.loads((group_dir / "process_group.json").read_text()) == (
        example_poc._backend_process_group_payload()
    )
    assert json.loads((model_dir / "process_model.json").read_text()) == (
        example_poc._backend_process_model_payload()
    )
    assert (model_dir / example_poc.PRIMARY_FILE_NAME).read_text(
        encoding="utf-8"
    ) == "<definitions />"


def test_deploy_service_task_definition_to_m8flow_backend_warns_on_refresh(
    tmp_path: Path,
) -> None:
    example_poc._deploy_service_task_definition_to_m8flow_backend(
        process_models_root=tmp_path,
        tenant_root="tenant-service-task-connector-example",
        bpmn_xml="<definitions version='1' />",
    )

    refreshed_deployment = (
        example_poc._deploy_service_task_definition_to_m8flow_backend(
            process_models_root=tmp_path,
            tenant_root="tenant-service-task-connector-example",
            bpmn_xml="<definitions version='2' />",
        )
    )

    assert refreshed_deployment.deployed is True
    assert refreshed_deployment.already_deployed is False
    assert any(
        "Refreshed the existing service-task connector POC deployment"
        in warning
        for warning in refreshed_deployment.warnings
    )
