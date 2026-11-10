"""Spiff engine construction and serialization, independent of orchestration."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from SpiffWorkflow.bpmn.script_engine.python_engine import PythonScriptEngine
from SpiffWorkflow.bpmn.serializer.workflow import BpmnWorkflowSerializer
from SpiffWorkflow.bpmn.workflow import BpmnWorkflow
from SpiffWorkflow.dmn.specs.model import DecisionTable
from SpiffWorkflow.spiff.serializer.config import SPIFF_CONFIG

from m8flow_core.errors import (
    CallActivityResolutionError,
    ServiceTaskExecutionError,
    ValidationError,
)
from m8flow_core.services.call_activities import (
    CallActivityContext,
    CallActivityDefinition,
    CallActivityResolver,
)
from m8flow_core.services.compensation_parser import (
    CompensationBpmnParser,
    CompensationEnd,
    CompensationEventConverter,
    CompensationThrow,
)
from m8flow_core.services.compensation_runtime import (
    CompensationCoordinator,
    connector_compensation_metadata,
)
from m8flow_core.services.service_tasks import (
    ServiceTaskContext,
    ServiceTaskRequest,
    resolve_service_task_registry,
)
from m8flow_core.services.signal_runtime import (
    adapt_signal_definitions,
    configure_signal_serializer,
)

_WORKFLOW_STATE_SERIALIZER_VERSION = "5"

_WORKFLOW_SERIALIZER = BpmnWorkflowSerializer(
    registry=BpmnWorkflowSerializer.configure(
        {
            **SPIFF_CONFIG,
            CompensationThrow: CompensationEventConverter,
            CompensationEnd: CompensationEventConverter,
        }
    ),
    version=_WORKFLOW_STATE_SERIALIZER_VERSION,
)
configure_signal_serializer(_WORKFLOW_SERIALIZER.registry)


class _NoopDMNEngine:
    """Minimal DMN engine that leaves task data untouched."""

    def __init__(self, decision_ref: str) -> None:
        self.decision_table = DecisionTable(decision_ref, decision_ref, "UNIQUE")

    def result(self, task: object) -> dict[str, object]:
        return {}


class _RuntimeSpiffBpmnParser(CompensationBpmnParser):
    """Spiff parser that can fall back to a no-op DMN engine for POC runs."""

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
                    **connector_compensation_metadata(task),
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
    call_activity_resolver: CallActivityResolver | None = None,
    call_activity_context: CallActivityContext | None = None,
) -> BpmnWorkflow:
    parser = _RuntimeSpiffBpmnParser(
        validator=None,
        use_noop_dmn_engine=dmn_xml is None,
    )
    parser.add_bpmn_str(
        _coerce_xml_bytes(bpmn_xml),
        filename="runtime-workflow.bpmn",
    )
    if bpmn_process_id is None:
        process_ids = parser.get_process_ids()
        if len(process_ids) != 1:
            raise ValidationError(
                "A BPMN process id must be supplied when the file contains "
                "multiple executable processes"
            )
        selected_process_id = process_ids[0]
    else:
        selected_process_id = bpmn_process_id

    _load_called_process_definitions(
        parser,
        root_process_id=selected_process_id,
        resolver=call_activity_resolver,
        context=call_activity_context,
    )
    if dmn_xml is not None:
        parser.add_dmn_str(
            _coerce_xml_bytes(dmn_xml),
            filename="runtime-workflow.dmn",
        )
    spec = parser.get_spec(selected_process_id)
    subprocess_specs = parser.get_subprocess_specs(selected_process_id)
    workflow = BpmnWorkflow(
        spec,
        subprocess_specs,
        script_engine=_service_task_script_engine(service_task_context),
    )
    adapt_signal_definitions(spec, subprocess_specs)
    if parser.has_compensation:
        comp = CompensationCoordinator(workflow, _WORKFLOW_SERIALIZER)
        comp.state["bindings"] = parser.compensation_bindings
        for handler_id, process_id in parser.compensation_handlers.items():
            handler_spec = parser.get_spec(process_id)
            handler_subs = parser.get_subprocess_specs(process_id)
            adapt_signal_definitions(handler_spec, handler_subs)
            comp.state["templates"][handler_id] = {
                "spec": _WORKFLOW_SERIALIZER.to_dict(handler_spec),
                "subprocess_specs": {
                    key: _WORKFLOW_SERIALIZER.to_dict(value)
                    for key, value in handler_subs.items()
                },
            }
    return workflow


def _load_called_process_definitions(
    parser: _RuntimeSpiffBpmnParser,
    *,
    root_process_id: str,
    resolver: CallActivityResolver | None,
    context: CallActivityContext | None,
) -> None:
    """Resolve recursive external call references into Spiff's parser."""
    pending = [root_process_id]
    inspected: set[str] = set()
    while pending:
        process_id = pending.pop()
        if process_id in inspected:
            continue
        inspected.add(process_id)
        process_parser = parser.get_process_parser(process_id)
        if process_parser is None:
            raise CallActivityResolutionError(
                f"Called process {process_id!r} was not found in the resolved BPMN"
            )
        for called_element in process_parser.called_element_ids():
            if not isinstance(called_element, str) or not called_element.strip():
                raise CallActivityResolutionError(
                    f"Call activity in process {process_id!r} has no calledElement"
                )
            if parser.get_process_parser(called_element) is None:
                if resolver is None:
                    raise CallActivityResolutionError(
                        f"Call activity references {called_element!r}, but no "
                        "call_activity_resolver was supplied"
                    )
                if context is None:
                    raise ValidationError(
                        "Call-activity resolver requires tenant and parent context"
                    )
                resolution_context = CallActivityContext(
                    tenant_id=context.tenant_id,
                    called_element=called_element,
                    parent_process_instance_id=context.parent_process_instance_id,
                    parent_process_definition_id=context.parent_process_definition_id,
                )
                try:
                    result = resolver(resolution_context)
                except CallActivityResolutionError:
                    raise
                except Exception as exc:
                    raise CallActivityResolutionError(
                        f"Resolver failed for calledElement {called_element!r} "
                        f"in tenant {context.tenant_id!r}: {exc}"
                    ) from exc
                if result is None:
                    raise CallActivityResolutionError(
                        f"No child process definition for calledElement "
                        f"{called_element!r} in tenant {context.tenant_id!r}"
                    )
                if isinstance(result, CallActivityDefinition):
                    definitions = [result]
                elif isinstance(result, Sequence) and not isinstance(
                    result, (str, bytes, bytearray)
                ):
                    definitions = list(result)
                else:
                    raise CallActivityResolutionError(
                        f"Resolver returned an invalid result for calledElement "
                        f"{called_element!r}; return one definition or a sequence"
                    )
                if not definitions:
                    raise CallActivityResolutionError(
                        f"No child process definition for calledElement "
                        f"{called_element!r} in tenant {context.tenant_id!r}"
                    )
                if len(definitions) != 1:
                    raise CallActivityResolutionError(
                        f"Ambiguous child process definition for calledElement "
                        f"{called_element!r} in tenant {context.tenant_id!r}: "
                        f"resolver returned {len(definitions)} definitions"
                    )
                definition = definitions[0]
                if not isinstance(definition, CallActivityDefinition):
                    raise CallActivityResolutionError(
                        f"Resolver returned an invalid definition for "
                        f"calledElement {called_element!r}"
                    )
                if definition.process_id != called_element:
                    raise CallActivityResolutionError(
                        f"Resolved process id {definition.process_id!r} does not "
                        f"match calledElement {called_element!r}"
                    )
                try:
                    parser.add_bpmn_str(
                        _coerce_xml_bytes(definition.bpmn_xml),
                        filename=f"called-process-{called_element}.bpmn",
                    )
                    if parser.has_compensation:
                        raise CallActivityResolutionError(
                            f"Called process {called_element!r} uses compensation, "
                            "which is unsupported with call activities"
                        )
                    if definition.process_id not in parser.get_process_ids():
                        raise CallActivityResolutionError(
                            f"Resolved BPMN does not contain executable process "
                            f"{definition.process_id!r}"
                        )
                    if definition.dmn_xml is not None:
                        parser.add_dmn_str(
                            _coerce_xml_bytes(definition.dmn_xml),
                            filename=f"called-process-{called_element}.dmn",
                        )
                except CallActivityResolutionError:
                    raise
                except Exception as exc:
                    raise CallActivityResolutionError(
                        f"Resolved definition for calledElement "
                        f"{called_element!r} is invalid: {exc}"
                    ) from exc
            pending.append(called_element)


def _restore_workflow(
    serialized_state: str,
    *,
    service_task_context: ServiceTaskContext | None = None,
) -> BpmnWorkflow:
    payload = json.loads(serialized_state, cls=_WORKFLOW_SERIALIZER.json_decoder_cls)
    compensation = payload.pop("m8f_compensation", None)
    _WORKFLOW_SERIALIZER.migrate(payload)
    workflow = _WORKFLOW_SERIALIZER.from_dict(payload)
    workflow.script_engine = _service_task_script_engine(service_task_context)
    adapt_signal_definitions(workflow.spec, workflow.subprocess_specs)
    if compensation is not None:
        handlers = compensation.pop("handlers", {})
        comp = CompensationCoordinator(workflow, _WORKFLOW_SERIALIZER, compensation)
        comp.restore_handlers(handlers)
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
