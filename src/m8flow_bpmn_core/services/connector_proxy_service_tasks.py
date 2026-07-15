from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from m8flow_bpmn_core.errors import (
    NotFoundError,
    ServiceTaskExecutionError,
    ValidationError,
)
from m8flow_bpmn_core.services.service_tasks import (
    CONNECTOR_PROXY_CALLBACK_URL_PARAMETER,
    CONNECTOR_PROXY_COMMANDS_PATH,
    CONNECTOR_PROXY_RESERVED_PARAMETER_PREFIX,
    CONNECTOR_PROXY_TASK_DATA_PARAMETER,
    ServiceTaskCommandDefinition,
    ServiceTaskParameterDefinition,
    ServiceTaskRegistry,
    ServiceTaskRequest,
    ServiceTaskResult,
    build_connector_proxy_execute_path,
    split_service_task_operation_id,
)

DEFAULT_CONNECTOR_PROXY_TIMEOUT_SECONDS = 10.0


class ConnectorProxyServiceTaskConnector:
    def __init__(
        self,
        *,
        connector_key: str,
        base_url: str,
        commands: Sequence[ServiceTaskCommandDefinition],
        timeout_seconds: float = DEFAULT_CONNECTOR_PROXY_TIMEOUT_SECONDS,
    ) -> None:
        normalized_connector_key = _normalize_connector_key(connector_key)
        normalized_base_url = _normalize_base_url(base_url)
        normalized_timeout_seconds = _normalize_timeout_seconds(timeout_seconds)
        normalized_commands = tuple(commands)
        commands_by_operation_id: dict[str, ServiceTaskCommandDefinition] = {}
        for command in normalized_commands:
            if command.connector_key != normalized_connector_key:
                raise ValidationError(
                    "Connector command "
                    f"{command.operation_id!r} does not belong to "
                    f"connector key {normalized_connector_key!r}"
                )
            commands_by_operation_id[command.operation_id] = command

        self.connector_key = normalized_connector_key
        self.base_url = normalized_base_url
        self.timeout_seconds = normalized_timeout_seconds
        self._commands = normalized_commands
        self._commands_by_operation_id = commands_by_operation_id

    def list_commands(self) -> tuple[ServiceTaskCommandDefinition, ...]:
        return self._commands

    def execute(self, request: ServiceTaskRequest) -> ServiceTaskResult:
        if request.connector_key != self.connector_key:
            raise ValidationError(
                "Connector "
                f"{self.connector_key!r} cannot execute operation "
                f"{request.operation_id!r}"
            )
        if request.operation_id not in self._commands_by_operation_id:
            raise NotFoundError(
                f"No service task command is registered for '{request.operation_id}'"
            )

        payload = _connector_proxy_request_payload(request)
        response_payload, status_code = _connector_proxy_json_request(
            base_url=self.base_url,
            path=build_connector_proxy_execute_path(
                request.connector_key,
                request.command_name,
            ),
            method="POST",
            payload=payload,
            timeout_seconds=self.timeout_seconds,
            operation_id=request.operation_id,
        )
        return ServiceTaskResult(
            payload=response_payload,
            metadata={
                "connector_proxy_base_url": self.base_url,
                "status_code": status_code,
            },
        )


def fetch_connector_proxy_command_definitions(
    base_url: str,
    *,
    timeout_seconds: float = DEFAULT_CONNECTOR_PROXY_TIMEOUT_SECONDS,
) -> tuple[ServiceTaskCommandDefinition, ...]:
    response_payload, _status_code = _connector_proxy_json_request(
        base_url=_normalize_base_url(base_url),
        path=CONNECTOR_PROXY_COMMANDS_PATH,
        method="GET",
        payload=None,
        timeout_seconds=timeout_seconds,
        operation_id="connector-proxy catalog",
    )
    if not isinstance(response_payload, list):
        raise ServiceTaskExecutionError(
            "Connector proxy command catalog must be a JSON list"
        )

    command_definitions: list[ServiceTaskCommandDefinition] = []
    for entry in response_payload:
        if not isinstance(entry, Mapping):
            raise ServiceTaskExecutionError(
                "Connector proxy command catalog contains a non-object entry"
            )
        operation_id = entry.get("id")
        if not isinstance(operation_id, str):
            raise ServiceTaskExecutionError(
                "Connector proxy command catalog entry is missing its id"
            )
        connector_key, command_name = split_service_task_operation_id(operation_id)
        command_definitions.append(
            ServiceTaskCommandDefinition(
                connector_key=connector_key,
                command_name=command_name,
                display_name=command_name,
                parameters=_parse_connector_proxy_parameter_definitions(entry),
                metadata={"catalog_entry": dict(entry)},
            )
        )

    command_definitions.sort(key=lambda item: (item.connector_key, item.command_name))
    return tuple(command_definitions)


def build_connector_proxy_service_task_connectors(
    base_url: str,
    *,
    timeout_seconds: float = DEFAULT_CONNECTOR_PROXY_TIMEOUT_SECONDS,
) -> tuple[ConnectorProxyServiceTaskConnector, ...]:
    command_definitions = fetch_connector_proxy_command_definitions(
        base_url,
        timeout_seconds=timeout_seconds,
    )
    commands_by_connector_key: dict[str, list[ServiceTaskCommandDefinition]] = {}
    for command in command_definitions:
        commands_by_connector_key.setdefault(command.connector_key, []).append(command)

    connectors: list[ConnectorProxyServiceTaskConnector] = []
    for connector_key in sorted(commands_by_connector_key):
        connectors.append(
            ConnectorProxyServiceTaskConnector(
                connector_key=connector_key,
                base_url=base_url,
                commands=tuple(commands_by_connector_key[connector_key]),
                timeout_seconds=timeout_seconds,
            )
        )
    return tuple(connectors)


def build_connector_proxy_service_task_registry(
    base_url: str,
    *,
    timeout_seconds: float = DEFAULT_CONNECTOR_PROXY_TIMEOUT_SECONDS,
) -> ServiceTaskRegistry:
    return ServiceTaskRegistry(
        connectors=build_connector_proxy_service_task_connectors(
            base_url,
            timeout_seconds=timeout_seconds,
        )
    )


def _parse_connector_proxy_parameter_definitions(
    entry: Mapping[str, object],
) -> tuple[ServiceTaskParameterDefinition, ...]:
    raw_parameters = entry.get("parameters", ())
    if not isinstance(raw_parameters, Sequence) or isinstance(
        raw_parameters, str | bytes
    ):
        raise ServiceTaskExecutionError(
            "Connector proxy command catalog entry parameters must be a list"
        )

    parameters: list[ServiceTaskParameterDefinition] = []
    for raw_parameter in raw_parameters:
        if not isinstance(raw_parameter, Mapping):
            raise ServiceTaskExecutionError(
                "Connector proxy command parameter metadata must be an object"
            )
        parameter_name = raw_parameter.get("id")
        if not isinstance(parameter_name, str):
            raise ServiceTaskExecutionError(
                "Connector proxy command parameter is missing its id"
            )
        parameter_type = raw_parameter.get("type")
        if parameter_type is not None and not isinstance(parameter_type, str):
            parameter_type = str(parameter_type)
        parameters.append(
            ServiceTaskParameterDefinition(
                name=parameter_name,
                parameter_type=parameter_type,
                required=bool(raw_parameter.get("required", False)),
                metadata=dict(raw_parameter),
            )
        )
    return tuple(parameters)


def _connector_proxy_request_payload(
    request: ServiceTaskRequest,
) -> dict[str, object]:
    payload: dict[str, object] = {}
    for raw_name, value in (request.parameters or {}).items():
        parameter_name = str(raw_name)
        if parameter_name.startswith(CONNECTOR_PROXY_RESERVED_PARAMETER_PREFIX):
            raise ValidationError(
                "Service task request parameters must not use the reserved "
                f"{CONNECTOR_PROXY_RESERVED_PARAMETER_PREFIX!r} prefix"
            )
        payload[parameter_name] = value

    if request.task_data is not None:
        payload[CONNECTOR_PROXY_TASK_DATA_PARAMETER] = dict(request.task_data)
    if request.callback_url is not None:
        normalized_callback_url = request.callback_url.strip()
        if not normalized_callback_url:
            raise ValidationError("callback_url must not be blank")
        payload[CONNECTOR_PROXY_CALLBACK_URL_PARAMETER] = normalized_callback_url
    return payload


def _connector_proxy_json_request(
    *,
    base_url: str,
    path: str,
    method: str,
    payload: Mapping[str, object] | None,
    timeout_seconds: float,
    operation_id: str,
) -> tuple[object | None, int]:
    normalized_base_url = _normalize_base_url(base_url)
    normalized_timeout_seconds = _normalize_timeout_seconds(timeout_seconds)
    url = urljoin(f"{normalized_base_url}/", path.lstrip("/"))
    request_body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        request_body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(
        url,
        data=request_body,
        headers=headers,
        method=method,
    )

    try:
        with urlopen(request, timeout=normalized_timeout_seconds) as response:
            raw_body = response.read()
            parsed_body = _parse_connector_proxy_response_body(raw_body)
            _raise_for_connector_proxy_error_payload(
                parsed_body,
                operation_id=operation_id,
                base_url=normalized_base_url,
            )
            return parsed_body, response.status
    except HTTPError as exc:
        raw_body = exc.read()
        raise ServiceTaskExecutionError(
            _connector_proxy_http_error_message(
                operation_id=operation_id,
                base_url=normalized_base_url,
                status_code=exc.code,
                raw_body=raw_body,
            )
        ) from exc
    except URLError as exc:
        raise ServiceTaskExecutionError(
            "Connector proxy call for "
            f"{operation_id!r} could not reach {normalized_base_url}: {exc.reason}"
        ) from exc


def _parse_connector_proxy_response_body(raw_body: bytes) -> object | None:
    if not raw_body:
        return None

    decoded = raw_body.decode("utf-8")
    try:
        return json.loads(decoded)
    except json.JSONDecodeError:
        return decoded


def _raise_for_connector_proxy_error_payload(
    parsed_body: object | None,
    *,
    operation_id: str,
    base_url: str,
) -> None:
    if not isinstance(parsed_body, Mapping):
        return
    raw_error = parsed_body.get("error")
    if not isinstance(raw_error, Mapping) or not raw_error:
        return

    error_message = raw_error.get("message")
    if not isinstance(error_message, str) or not error_message.strip():
        error_message = json.dumps(dict(raw_error), sort_keys=True)
    raise ServiceTaskExecutionError(
        "Connector proxy call for "
        f"{operation_id!r} at {base_url} failed: {error_message}"
    )


def _connector_proxy_http_error_message(
    *,
    operation_id: str,
    base_url: str,
    status_code: int,
    raw_body: bytes,
) -> str:
    parsed_body = _parse_connector_proxy_response_body(raw_body)
    if isinstance(parsed_body, Mapping):
        raw_error = parsed_body.get("error")
        if isinstance(raw_error, Mapping):
            message = raw_error.get("message")
            if isinstance(message, str) and message.strip():
                return (
                    "Connector proxy call for "
                    f"{operation_id!r} at {base_url} failed with HTTP "
                    f"{status_code}: {message}"
                )
    detail = (
        parsed_body
        if isinstance(parsed_body, str)
        else json.dumps(parsed_body, sort_keys=True)
        if parsed_body is not None
        else "empty response body"
    )
    return (
        "Connector proxy call for "
        f"{operation_id!r} at {base_url} failed with HTTP {status_code}: {detail}"
    )


def _normalize_base_url(base_url: str) -> str:
    normalized_base_url = base_url.strip().rstrip("/")
    if not normalized_base_url:
        raise ValidationError("base_url must not be blank")
    return normalized_base_url


def _normalize_connector_key(connector_key: str) -> str:
    normalized_connector_key = connector_key.strip()
    if not normalized_connector_key:
        raise ValidationError("connector_key must not be blank")
    if "/" in normalized_connector_key:
        raise ValidationError("connector_key must not contain '/'")
    return normalized_connector_key


def _normalize_timeout_seconds(timeout_seconds: float) -> float:
    if timeout_seconds <= 0:
        raise ValidationError("timeout_seconds must be greater than zero")
    return float(timeout_seconds)


__all__ = [
    "ConnectorProxyServiceTaskConnector",
    "DEFAULT_CONNECTOR_PROXY_TIMEOUT_SECONDS",
    "build_connector_proxy_service_task_connectors",
    "build_connector_proxy_service_task_registry",
    "fetch_connector_proxy_command_definitions",
]
