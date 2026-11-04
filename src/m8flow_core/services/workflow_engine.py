"""Spiff engine construction and serialization, independent of orchestration."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from SpiffWorkflow.bpmn.script_engine.python_engine import PythonScriptEngine
from SpiffWorkflow.bpmn.serializer.workflow import BpmnWorkflowSerializer
from SpiffWorkflow.bpmn.workflow import BpmnWorkflow
from SpiffWorkflow.dmn.specs.model import DecisionTable
from SpiffWorkflow.spiff.parser.process import SpiffBpmnParser
from SpiffWorkflow.spiff.serializer.config import SPIFF_CONFIG

from m8flow_core.errors import ServiceTaskExecutionError, ValidationError
from m8flow_core.services.service_tasks import (
    ServiceTaskContext,
    ServiceTaskRequest,
    resolve_service_task_registry,
)
from m8flow_core.services.signal_runtime import (
    adapt_signal_definitions,
    configure_signal_serializer,
    signal_parser_overrides,
)

_WORKFLOW_SERIALIZER = BpmnWorkflowSerializer(
    registry=BpmnWorkflowSerializer.configure(SPIFF_CONFIG),
    version="5",
)
configure_signal_serializer(_WORKFLOW_SERIALIZER.registry)
_WORKFLOW_STATE_SERIALIZER_VERSION = "5"


class _NoopDMNEngine:
    """Minimal DMN engine that leaves task data untouched."""

    def __init__(self, decision_ref: str) -> None:
        self.decision_table = DecisionTable(decision_ref, decision_ref, "UNIQUE")

    def result(self, task: object) -> dict[str, object]:
        return {}


class _RuntimeSpiffBpmnParser(SpiffBpmnParser):
    """Spiff parser that can fall back to a no-op DMN engine for POC runs."""

    OVERRIDE_PARSER_CLASSES = {
        **SpiffBpmnParser.OVERRIDE_PARSER_CLASSES,
        **signal_parser_overrides(),
    }

    def __init__(
        self, *args: Any, use_noop_dmn_engine: bool = True, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._use_noop_dmn_engine = use_noop_dmn_engine

    def get_engine(self, decision_ref: str, node: object) -> Any:
        if not self._use_noop_dmn_engine:
            return super().get_engine(decision_ref, node)
        return _NoopDMNEngine(decision_ref)


class _RuntimeServiceTaskScriptEngine(PythonScriptEngine):
    """Script engine that routes Spiff service tasks through the registry."""

    def __init__(
        self,
        *,
        tenant_id: str,
        process_instance_id: int | None,
        process_definition_id: int | None,
    ) -> None:
        super().__init__()
        self._tenant_id = tenant_id
        self._process_instance_id = process_instance_id
        self._process_definition_id = process_definition_id

    def evaluate(
        self,
        task: object,
        expression: str,
        external_context: Mapping[str, object] | None = None,
    ) -> object:
        merged_context: dict[str, object] = {}
        workflow_data = getattr(getattr(task, "workflow", None), "data", None)
        if isinstance(workflow_data, Mapping):
            merged_context.update(dict(workflow_data))
        if external_context:
            merged_context.update(dict(external_context))
        return super().evaluate(
            task,
            expression,
            external_context=merged_context or None,
        )

    def call_service(self, task: object, **kwargs: Any) -> str:
        operation_name = kwargs.get("operation_name")
        operation_params = kwargs.get("operation_params")
        if not isinstance(operation_name, str) or not operation_name.strip():
            raise ServiceTaskExecutionError(
                "Service task execution is missing its operator id"
            )
        if not isinstance(operation_params, Mapping):
            raise ServiceTaskExecutionError(
                f"Service task {operation_name!r} is missing its parameters"
            )

        request = ServiceTaskRequest(
            operation_id=operation_name,
            parameters=_service_task_parameter_values(
                operation_name=operation_name,
                operation_params=operation_params,
            ),
            context=ServiceTaskContext(
                tenant_id=self._tenant_id,
                process_instance_id=self._process_instance_id,
                process_definition_id=self._process_definition_id,
                task_guid=str(getattr(task, "id", "")) or None,
                task_name=getattr(getattr(task, "task_spec", None), "name", None),
                task_type=type(getattr(task, "task_spec", object())).__name__,
                metadata={
                    "result_variable": getattr(
                        getattr(task, "task_spec", None),
                        "result_variable",
                        None,
                    ),
                },
            ),
            task_data=_service_task_task_data(task),
            metadata={
                "parameter_types": _service_task_parameter_types(operation_params),
            },
        )

        try:
            result = resolve_service_task_registry().execute(request)
            return json.dumps(result.payload)
        except ServiceTaskExecutionError:
            raise
        except Exception as exc:
            raise ServiceTaskExecutionError(
                "Service task "
                f"{operation_name!r} failed for process instance "
                f"{self._process_instance_id or 'unknown'}"
            ) from exc


def _build_workflow(
    *,
    bpmn_xml: str | bytes,
    dmn_xml: str | bytes | None,
    bpmn_process_id: str | None,
    service_task_context: ServiceTaskContext | None = None,
) -> BpmnWorkflow:
    parser = _RuntimeSpiffBpmnParser(
        validator=None,
        use_noop_dmn_engine=dmn_xml is None,
    )
    parser.add_bpmn_str(
        _coerce_xml_bytes(bpmn_xml),
        filename="runtime-workflow.bpmn",
    )
    if dmn_xml is not None:
        parser.add_dmn_str(
            _coerce_xml_bytes(dmn_xml),
            filename="runtime-workflow.dmn",
        )
    process_ids = parser.get_process_ids()
    if bpmn_process_id is None:
        if len(process_ids) != 1:
            raise ValidationError(
                "A BPMN process id must be supplied when the file contains "
                "multiple executable processes"
            )
        selected_process_id = process_ids[0]
    else:
        selected_process_id = bpmn_process_id

    spec = parser.get_spec(selected_process_id)
    subprocess_specs = parser.get_subprocess_specs(selected_process_id)
    workflow = BpmnWorkflow(
        spec,
        subprocess_specs,
        script_engine=_service_task_script_engine(service_task_context),
    )
    adapt_signal_definitions(workflow)
    return workflow


def _restore_workflow(
    serialized_state: str,
    *,
    service_task_context: ServiceTaskContext | None = None,
) -> BpmnWorkflow:
    workflow = _WORKFLOW_SERIALIZER.deserialize_json(serialized_state)
    workflow.script_engine = _service_task_script_engine(service_task_context)
    adapt_signal_definitions(workflow)
    return workflow


def _coerce_xml_bytes(xml: str | bytes) -> bytes:
    if isinstance(xml, bytes):
        return xml
    return xml.encode("utf-8")


def _service_task_execution_context(
    *,
    tenant_id: str,
    process_instance_id: int | None,
    process_definition_id: int | None,
) -> ServiceTaskContext:
    return ServiceTaskContext(
        tenant_id=tenant_id,
        process_instance_id=process_instance_id,
        process_definition_id=process_definition_id,
    )


def _service_task_script_engine(
    service_task_context: ServiceTaskContext | None,
) -> PythonScriptEngine:
    if service_task_context is None:
        return PythonScriptEngine()
    return _RuntimeServiceTaskScriptEngine(
        tenant_id=service_task_context.tenant_id,
        process_instance_id=service_task_context.process_instance_id,
        process_definition_id=service_task_context.process_definition_id,
    )


def _service_task_parameter_values(
    *,
    operation_name: str,
    operation_params: Mapping[str, object],
) -> dict[str, object]:
    values: dict[str, object] = {}
    for name, raw_definition in operation_params.items():
        if not isinstance(raw_definition, Mapping):
            raise ServiceTaskExecutionError(
                f"Service task {operation_name!r} parameter {name!r} is not a mapping"
            )
        if "value" not in raw_definition:
            raise ServiceTaskExecutionError(
                "Service task "
                f"{operation_name!r} parameter {name!r} is missing its value"
            )
        values[str(name)] = raw_definition["value"]
    return values


def _service_task_parameter_types(
    operation_params: Mapping[str, object],
) -> dict[str, object]:
    parameter_types: dict[str, object] = {}
    for name, raw_definition in operation_params.items():
        if not isinstance(raw_definition, Mapping):
            continue
        parameter_types[str(name)] = raw_definition.get("type")
    return parameter_types


def _service_task_task_data(task: object) -> dict[str, object] | None:
    merged_task_data: dict[str, object] = {}
    workflow_data = getattr(getattr(task, "workflow", None), "data", None)
    if isinstance(workflow_data, Mapping):
        merged_task_data.update(dict(workflow_data))

    task_data = getattr(task, "data", None)
    if isinstance(task_data, Mapping):
        merged_task_data.update(dict(task_data))

    return merged_task_data or None
