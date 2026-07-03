from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from m8flow_bpmn_core.errors import NotFoundError, ValidationError

CONNECTOR_PROXY_COMMANDS_PATH = "/v1/commands"
CONNECTOR_PROXY_EXECUTE_PATH_TEMPLATE = "/v1/do/{connector_key}/{command_name}"
CONNECTOR_PROXY_RESERVED_PARAMETER_PREFIX = "spiff__"
CONNECTOR_PROXY_CALLBACK_URL_PARAMETER = "spiff__callback_url"
CONNECTOR_PROXY_TASK_DATA_PARAMETER = "spiff__task_data"


def _normalize_non_blank(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValidationError(f"{field_name} must not be blank")
    return normalized


def build_service_task_operation_id(connector_key: str, command_name: str) -> str:
    normalized_connector_key = _normalize_non_blank(
        connector_key,
        field_name="connector_key",
    )
    normalized_command_name = _normalize_non_blank(
        command_name,
        field_name="command_name",
    )
    if "/" in normalized_connector_key:
        raise ValidationError("connector_key must not contain '/'")
    if "/" in normalized_command_name:
        raise ValidationError("command_name must not contain '/'")
    return f"{normalized_connector_key}/{normalized_command_name}"


def split_service_task_operation_id(operation_id: str) -> tuple[str, str]:
    normalized_operation_id = _normalize_non_blank(
        operation_id,
        field_name="operation_id",
    )
    parts = normalized_operation_id.split("/")
    if len(parts) != 2 or not all(part.strip() for part in parts):
        raise ValidationError(
            "operation_id must use the '<connector_key>/<command_name>' format"
        )
    return parts[0], parts[1]


def build_connector_proxy_execute_path(connector_key: str, command_name: str) -> str:
    normalized_connector_key, normalized_command_name = split_service_task_operation_id(
        build_service_task_operation_id(connector_key, command_name)
    )
    return CONNECTOR_PROXY_EXECUTE_PATH_TEMPLATE.format(
        connector_key=normalized_connector_key,
        command_name=normalized_command_name,
    )


@dataclass(frozen=True, slots=True)
class ServiceTaskParameterDefinition:
    name: str
    parameter_type: str | None = None
    required: bool = False
    description: str | None = None
    metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "name",
            _normalize_non_blank(self.name, field_name="parameter name"),
        )


@dataclass(frozen=True, slots=True)
class ServiceTaskCommandDefinition:
    connector_key: str
    command_name: str
    display_name: str | None = None
    description: str | None = None
    parameters: tuple[ServiceTaskParameterDefinition, ...] = ()
    metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "connector_key",
            _normalize_non_blank(self.connector_key, field_name="connector_key"),
        )
        object.__setattr__(
            self,
            "command_name",
            _normalize_non_blank(self.command_name, field_name="command_name"),
        )
        if "/" in self.connector_key:
            raise ValidationError("connector_key must not contain '/'")
        if "/" in self.command_name:
            raise ValidationError("command_name must not contain '/'")
        object.__setattr__(self, "parameters", tuple(self.parameters))

    @property
    def operation_id(self) -> str:
        return build_service_task_operation_id(
            self.connector_key,
            self.command_name,
        )


@dataclass(frozen=True, slots=True)
class ServiceTaskContext:
    tenant_id: str
    process_instance_id: int | None = None
    process_definition_id: int | None = None
    task_guid: str | None = None
    task_name: str | None = None
    task_type: str | None = None
    metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "tenant_id",
            _normalize_non_blank(self.tenant_id, field_name="tenant_id"),
        )


@dataclass(frozen=True, slots=True)
class ServiceTaskRequest:
    operation_id: str
    parameters: Mapping[str, object] | None = None
    context: ServiceTaskContext | None = None
    task_data: Mapping[str, object] | None = None
    callback_url: str | None = None
    metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        split_service_task_operation_id(self.operation_id)

    @property
    def connector_key(self) -> str:
        connector_key, _command_name = split_service_task_operation_id(
            self.operation_id
        )
        return connector_key

    @property
    def command_name(self) -> str:
        _connector_key, command_name = split_service_task_operation_id(
            self.operation_id
        )
        return command_name


@dataclass(frozen=True, slots=True)
class ServiceTaskResult:
    payload: object | None = None
    metadata: Mapping[str, object] | None = None


@runtime_checkable
class ServiceTaskConnector(Protocol):
    connector_key: str

    def list_commands(self) -> Sequence[ServiceTaskCommandDefinition]: ...

    def execute(self, request: ServiceTaskRequest) -> ServiceTaskResult: ...


ServiceTaskRegistryFactory = Callable[[], "ServiceTaskRegistry"]


class ServiceTaskRegistry:
    def __init__(
        self,
        connectors: Sequence[ServiceTaskConnector] = (),
    ) -> None:
        self._connectors: dict[str, ServiceTaskConnector] = {}
        for connector in connectors:
            self.register_connector(connector)

    def register_connector(
        self,
        connector: ServiceTaskConnector,
        *,
        replace: bool = False,
    ) -> None:
        connector_key = _normalize_non_blank(
            connector.connector_key,
            field_name="connector.connector_key",
        )
        if "/" in connector_key:
            raise ValidationError("connector.connector_key must not contain '/'")
        existing = self._connectors.get(connector_key)
        if existing is not None and not replace:
            raise ValidationError(
                f"A connector is already registered for key '{connector_key}'"
            )
        self._connectors[connector_key] = connector

    def unregister_connector(self, connector_key: str) -> None:
        normalized_connector_key = _normalize_non_blank(
            connector_key,
            field_name="connector_key",
        )
        if self._connectors.pop(normalized_connector_key, None) is None:
            raise NotFoundError(
                "No service task connector is registered for "
                f"'{normalized_connector_key}'"
            )

    def get_connector(self, connector_key: str) -> ServiceTaskConnector:
        normalized_connector_key = _normalize_non_blank(
            connector_key,
            field_name="connector_key",
        )
        connector = self._connectors.get(normalized_connector_key)
        if connector is None:
            raise NotFoundError(
                "No service task connector is registered for "
                f"'{normalized_connector_key}'"
            )
        return connector

    def list_connectors(self) -> tuple[str, ...]:
        return tuple(self._connectors)

    def list_commands(
        self,
        *,
        connector_key: str | None = None,
    ) -> tuple[ServiceTaskCommandDefinition, ...]:
        if connector_key is not None:
            connector = self.get_connector(connector_key)
            return tuple(connector.list_commands())

        commands: list[ServiceTaskCommandDefinition] = []
        for connector in self._connectors.values():
            commands.extend(connector.list_commands())
        return tuple(commands)

    def get_command(self, operation_id: str) -> ServiceTaskCommandDefinition:
        connector_key, command_name = split_service_task_operation_id(operation_id)
        connector = self.get_connector(connector_key)
        for command_definition in connector.list_commands():
            if command_definition.operation_id == build_service_task_operation_id(
                connector_key,
                command_name,
            ):
                return command_definition
        raise NotFoundError(
            f"No service task command is registered for '{operation_id}'"
        )

    def execute(self, request: ServiceTaskRequest) -> ServiceTaskResult:
        self.get_command(request.operation_id)
        connector = self.get_connector(request.connector_key)
        return connector.execute(request)


_DEFAULT_SERVICE_TASK_REGISTRY_FACTORY: ServiceTaskRegistryFactory
_ACTIVE_SERVICE_TASK_REGISTRY_FACTORY: ContextVar[
    ServiceTaskRegistryFactory | None
] = ContextVar(
    "m8flow_bpmn_core_service_task_registry_factory",
    default=None,
)

_DEFAULT_SERVICE_TASK_REGISTRY_FACTORY = ServiceTaskRegistry


def resolve_service_task_registry(
    registry: ServiceTaskRegistry | None = None,
) -> ServiceTaskRegistry:
    if registry is not None:
        return registry

    active_factory = _ACTIVE_SERVICE_TASK_REGISTRY_FACTORY.get()
    factory = active_factory or _DEFAULT_SERVICE_TASK_REGISTRY_FACTORY
    return factory()


def set_default_service_task_registry_factory(
    factory: ServiceTaskRegistryFactory,
) -> None:
    global _DEFAULT_SERVICE_TASK_REGISTRY_FACTORY
    _DEFAULT_SERVICE_TASK_REGISTRY_FACTORY = factory


@contextmanager
def service_task_registry_scope(
    registry_or_factory: ServiceTaskRegistry | ServiceTaskRegistryFactory,
) -> Iterator[None]:
    if isinstance(registry_or_factory, ServiceTaskRegistry):

        def factory() -> ServiceTaskRegistry:
            return registry_or_factory

    else:
        factory = registry_or_factory

    token = _ACTIVE_SERVICE_TASK_REGISTRY_FACTORY.set(factory)
    try:
        yield
    finally:
        _ACTIVE_SERVICE_TASK_REGISTRY_FACTORY.reset(token)


__all__ = [
    "CONNECTOR_PROXY_CALLBACK_URL_PARAMETER",
    "CONNECTOR_PROXY_COMMANDS_PATH",
    "CONNECTOR_PROXY_EXECUTE_PATH_TEMPLATE",
    "CONNECTOR_PROXY_RESERVED_PARAMETER_PREFIX",
    "CONNECTOR_PROXY_TASK_DATA_PARAMETER",
    "ServiceTaskCommandDefinition",
    "ServiceTaskConnector",
    "ServiceTaskContext",
    "ServiceTaskParameterDefinition",
    "ServiceTaskRegistry",
    "ServiceTaskRegistryFactory",
    "ServiceTaskRequest",
    "ServiceTaskResult",
    "build_connector_proxy_execute_path",
    "build_service_task_operation_id",
    "resolve_service_task_registry",
    "service_task_registry_scope",
    "set_default_service_task_registry_factory",
    "split_service_task_operation_id",
]
