from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from m8flow_bpmn_core import api
from m8flow_bpmn_core.services.service_tasks import CONNECTOR_PROXY_COMMANDS_PATH


@dataclass
class _RecordedProxyRequest:
    method: str
    path: str
    payload: object | None


@dataclass
class _ProxyResponse:
    status_code: int
    payload: object | None


@dataclass
class _FakeConnectorProxyState:
    command_catalog: list[dict[str, object]]
    responses_by_path: dict[tuple[str, str], _ProxyResponse] = field(
        default_factory=dict
    )
    seen_requests: list[_RecordedProxyRequest] = field(default_factory=list)


class _FakeConnectorProxyHandler(BaseHTTPRequestHandler):
    server: _FakeConnectorProxyServer

    def do_GET(self) -> None:
        if self.path == CONNECTOR_PROXY_COMMANDS_PATH:
            self.server.state.seen_requests.append(
                _RecordedProxyRequest(method="GET", path=self.path, payload=None)
            )
            self._write_json_response(
                200,
                self.server.state.command_catalog,
            )
            return
        self._write_json_response(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        payload = _read_json_request_body(self)
        self.server.state.seen_requests.append(
            _RecordedProxyRequest(method="POST", path=self.path, payload=payload)
        )
        response = self.server.state.responses_by_path.get(("POST", self.path))
        if response is None:
            self._write_json_response(404, {"error": {"message": "not found"}})
            return
        self._write_json_response(response.status_code, response.payload)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _write_json_response(self, status_code: int, payload: object | None) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class _FakeConnectorProxyServer(ThreadingHTTPServer):
    state: _FakeConnectorProxyState


@contextmanager
def _fake_connector_proxy_server(
    state: _FakeConnectorProxyState,
) -> Iterator[str]:
    server = _FakeConnectorProxyServer(("127.0.0.1", 0), _FakeConnectorProxyHandler)
    server.state = state
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_build_connector_proxy_registry_fetches_and_groups_catalog() -> None:
    state = _FakeConnectorProxyState(
        command_catalog=[
            {
                "id": "http/GetRequestV2",
                "parameters": [
                    {"id": "url", "type": "str", "required": True},
                    {"id": "headers", "type": "any", "required": False},
                ],
            },
            {
                "id": "smtp/SendHTMLEmail",
                "parameters": [
                    {"id": "smtp_host", "type": "any", "required": True},
                ],
            },
        ]
    )

    with _fake_connector_proxy_server(state) as base_url:
        registry = api.build_connector_proxy_service_task_registry(base_url)

    assert registry.list_connectors() == ("http", "smtp")
    commands = registry.list_commands(connector_key="http")
    assert len(commands) == 1
    assert commands[0].operation_id == "http/GetRequestV2"
    assert commands[0].display_name == "GetRequestV2"
    assert commands[0].parameters[0].name == "url"
    assert commands[0].parameters[0].parameter_type == "str"
    assert commands[0].parameters[0].required is True
    assert state.seen_requests == [
        _RecordedProxyRequest(
            method="GET",
            path=CONNECTOR_PROXY_COMMANDS_PATH,
            payload=None,
        )
    ]


def test_connector_proxy_connector_executes_service_task_request() -> None:
    state = _FakeConnectorProxyState(
        command_catalog=[
            {
                "id": "http/GetRequestV2",
                "parameters": [
                    {"id": "url", "type": "str", "required": True},
                ],
            },
        ],
        responses_by_path={
            ("POST", "/v1/do/http/GetRequestV2"): _ProxyResponse(
                status_code=200,
                payload={
                    "command_response_version": 2,
                    "status": 200,
                    "body": {"ok": True},
                },
            )
        },
    )

    with _fake_connector_proxy_server(state) as base_url:
        registry = api.build_connector_proxy_service_task_registry(base_url)
        result = registry.execute(
            api.ServiceTaskRequest(
                operation_id="http/GetRequestV2",
                parameters={"url": "https://example.test"},
                task_data={"approval_id": 17},
                callback_url="https://callback.test/hook",
                context=api.ServiceTaskContext(
                    tenant_id="tenant-a",
                    process_instance_id=42,
                ),
            )
        )

    assert result.payload == {
        "command_response_version": 2,
        "status": 200,
        "body": {"ok": True},
    }
    assert result.metadata == {
        "connector_proxy_base_url": base_url,
        "status_code": 200,
    }
    assert state.seen_requests[1] == _RecordedProxyRequest(
        method="POST",
        path="/v1/do/http/GetRequestV2",
        payload={
            "url": "https://example.test",
            "spiff__task_data": {"approval_id": 17},
            "spiff__callback_url": "https://callback.test/hook",
        },
    )


def test_connector_proxy_connector_surfaces_proxy_error_payload() -> None:
    state = _FakeConnectorProxyState(
        command_catalog=[
            {
                "id": "http/GetRequestV2",
                "parameters": [
                    {"id": "url", "type": "str", "required": True},
                ],
            },
        ],
        responses_by_path={
            ("POST", "/v1/do/http/GetRequestV2"): _ProxyResponse(
                status_code=500,
                payload={"error": {"message": "upstream exploded"}},
            )
        },
    )

    with _fake_connector_proxy_server(state) as base_url:
        registry = api.build_connector_proxy_service_task_registry(base_url)
        with pytest.raises(api.ServiceTaskExecutionError) as exc_info:
            registry.execute(
                api.ServiceTaskRequest(
                    operation_id="http/GetRequestV2",
                    parameters={"url": "https://example.test"},
                )
            )

    assert "http/GetRequestV2" in str(exc_info.value)
    assert "upstream exploded" in str(exc_info.value)


def test_connector_proxy_connector_rejects_reserved_request_keys() -> None:
    state = _FakeConnectorProxyState(
        command_catalog=[
            {
                "id": "http/GetRequestV2",
                "parameters": [
                    {"id": "url", "type": "str", "required": True},
                ],
            },
        ]
    )

    with _fake_connector_proxy_server(state) as base_url:
        registry = api.build_connector_proxy_service_task_registry(base_url)
        with pytest.raises(api.ValidationError):
            registry.execute(
                api.ServiceTaskRequest(
                    operation_id="http/GetRequestV2",
                    parameters={"spiff__task_data": "bad"},
                )
            )


def _read_json_request_body(handler: BaseHTTPRequestHandler) -> object | None:
    content_length = int(handler.headers.get("Content-Length", "0"))
    raw_body = handler.rfile.read(content_length)
    if not raw_body:
        return None
    return json.loads(raw_body.decode("utf-8"))
