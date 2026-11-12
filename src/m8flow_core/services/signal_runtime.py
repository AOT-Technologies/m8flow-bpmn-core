"""Instance-local Spiff adapters. No Spiff classes are patched globally."""

from __future__ import annotations

from typing import Any

from defusedxml import ElementTree
from SpiffWorkflow.bpmn.parser.BpmnParser import full_tag
from SpiffWorkflow.bpmn.parser.event_parsers import SIGNAL_EVENT_XPATH
from SpiffWorkflow.bpmn.specs.defaults import EndEvent
from SpiffWorkflow.bpmn.specs.mixins.events.intermediate_event import BoundaryEvent
from SpiffWorkflow.bpmn.util.event import BpmnEvent
from SpiffWorkflow.spiff.parser.event_parsers import (
    SpiffBoundaryEventParser,
    SpiffEndEventParser,
    SpiffEventDefinitionParser,
    SpiffIntermediateCatchEventParser,
    SpiffIntermediateThrowEventParser,
    SpiffStartEventParser,
)
from SpiffWorkflow.spiff.parser.process import SpiffBpmnParser
from SpiffWorkflow.spiff.serializer.event_definition import (
    ItemAwareEventDefinitionConverter,
)
from SpiffWorkflow.spiff.specs.event_definitions import SignalEventDefinition
from SpiffWorkflow.util.task import TaskState


class RuntimeSignalDefinition(SignalEventDefinition):
    def throw(self, my_task: Any) -> None:
        publisher = getattr(
            my_task.workflow.top_workflow, "_m8f_signal_publisher", None
        )
        if publisher is None:
            super().throw(my_task)
            return

        payload = (
            my_task.workflow.script_engine.evaluate(my_task, self.expression)
            if self.expression is not None
            else None
        )
        publisher(my_task, payload)


class ResolvedSignalParserMixin(SpiffEventDefinitionParser):
    def parse_signal_event(self, node: Any) -> Any:
        reference = node.get("signalRef")
        if reference is None or ":" not in reference:
            return super().parse_signal_event(node)
        prefix, identifier = reference.split(":", 1)
        root = node.getroottree().getroot()
        if node.nsmap.get(prefix) != root.get("targetNamespace"):
            self.raise_validation_exception("Unresolved signal QName", node=node)
        node.set("signalRef", identifier)
        try:
            return super().parse_signal_event(node)
        finally:
            node.set("signalRef", reference)


class SignalStartParser(ResolvedSignalParserMixin, SpiffStartEventParser):
    pass


class SignalCatchParser(ResolvedSignalParserMixin, SpiffIntermediateCatchEventParser):
    pass


class SignalThrowParser(ResolvedSignalParserMixin, SpiffIntermediateThrowEventParser):
    pass


class SignalBoundaryParser(ResolvedSignalParserMixin, SpiffBoundaryEventParser):
    pass


class SignalEndParser(ResolvedSignalParserMixin, SpiffEndEventParser):
    def create_task(self) -> Any:
        if self.node.xpath(SIGNAL_EVENT_XPATH, namespaces=self.nsmap):
            task = self._create_task(self.get_event_definition([SIGNAL_EVENT_XPATH]))
            task.connect(self.spec.end)
            return task
        return super().create_task()


def configure_signal_serializer(registry: Any) -> None:
    # Keep the historical wire typename, while restoring it into our adapter.
    ItemAwareEventDefinitionConverter(
        RuntimeSignalDefinition, registry, typename="SignalEventDefinition"
    )


def signal_parser_overrides() -> dict[str, tuple[type, type]]:
    parsers = {
        "startEvent": SignalStartParser,
        "intermediateCatchEvent": SignalCatchParser,
        "intermediateThrowEvent": SignalThrowParser,
        "boundaryEvent": SignalBoundaryParser,
    }
    return {
        full_tag("endEvent"): (SignalEndParser, EndEvent),
        **{
            full_tag(name): (
                parser,
                SpiffBpmnParser.OVERRIDE_PARSER_CLASSES[full_tag(name)][1],
            )
            for name, parser in parsers.items()
        },
    }


def adapt_signal_definitions(root_spec: Any, subprocess_specs: Any) -> None:
    for spec in [root_spec, *subprocess_specs.values()]:
        for task_spec in spec.task_specs.values():
            definition = getattr(task_spec, "event_definition", None)
            _adapt_definition(definition)


def _adapt_definition(definition: Any) -> None:
    if isinstance(definition, SignalEventDefinition):
        definition.__class__ = RuntimeSignalDefinition
    for child in getattr(definition, "event_definitions", []):
        _adapt_definition(child)


def scopes(workflow: Any) -> list[Any]:
    return [workflow, *workflow.get_active_subprocesses()]


def scope_key(scope: Any) -> str:
    return str(scope.parent_task_id) if scope.parent_task_id else "root"


def event_starts(workflow: Any, scope: Any) -> list[tuple[str, Any]]:
    result = []
    for name in scope.spec.start.trigger_specs:
        spec = workflow.subprocess_specs[name]
        for start in spec.bpmn_start_events:
            if isinstance(start.event_definition, SignalEventDefinition):
                result.append((name, start))
    return result


def interrupting_starts(xml: str | bytes | None) -> dict[str, bool]:
    if not xml:
        return {}
    root = ElementTree.fromstring(xml)
    return {
        node.attrib["id"]: node.get("isInterrupting", "true") != "false"
        for sub in root.iter(full_tag("subProcess"))
        if sub.get("triggeredByEvent") == "true"
        for node in sub.findall(full_tag("startEvent"))
    }


def deliver_to_subscription(
    workflow: Any, subscription: Any, payload: Any, interrupts: dict[str, bool]
) -> bool:
    scope = next(
        (s for s in scopes(workflow) if scope_key(s) == subscription.scope_id), None
    )
    if scope is None or scope.completed:
        return False
    if subscription.kind == "event_subprocess":
        starts = event_starts(workflow, scope)
        match = next(
            (
                (name, start)
                for name, start in starts
                if name == subscription.task_spec_name
                and start.event_definition.name == subscription.signal_name
            ),
            None,
        )
        if match is None:
            return False
        name, start_spec = match
        child_id = scope.spec.start.trigger_wf(scope.task_tree, name)
        child = workflow.subprocesses[child_id]
        start = child.get_next_task(spec_name=start_spec.name, state=TaskState.WAITING)
        if start is None:
            return False
        if interrupts.get(start_spec.name, True):
            # Cancel competing execution, preserving the newly created handler.
            for task in list(scope.tasks.values()):
                if task.id != child_id and task.state in (
                    TaskState.READY,
                    TaskState.WAITING,
                    TaskState.STARTED,
                ):
                    task.cancel()
            # Cancelling the main branch can remove its predicted end join.
            # The handler must then own a continuation to that scope's end.
            unfinished_end = scope.get_tasks(
                spec_name=scope.spec.end.name,
                state=TaskState.NOT_FINISHED_MASK,
            )
            if not unfinished_end:
                handler = workflow.get_task_from_id(child_id)
                handler.task_spec._predict(handler, mask=TaskState.NOT_FINISHED_MASK)
        event = BpmnEvent(start.task_spec.event_definition, payload=payload)
        start.task_spec.catch(start, event)
        start.task_spec._update(start)
        return True

    from uuid import UUID

    task = workflow.get_task_from_id(UUID(subscription.task_guid))
    if task is None or task.state != TaskState.WAITING:
        return False
    if not subscription.repeatable and not subscription.subscription_key.endswith(
        task.internal_data.get("m8f_signal_activation", "missing")
    ):
        return False
    event = BpmnEvent(task.task_spec.event_definition, payload=payload)
    task.task_spec.catch(task, event)
    task.task_spec._update(task)
    if isinstance(task.task_spec, BoundaryEvent) and not task.task_spec.cancel_activity:
        listener = task.parent._add_child(task.task_spec, TaskState.WAITING)
        listener.data.update(task.data)
        listener.task_spec._predict(listener, mask=TaskState.NOT_FINISHED_MASK)
        listener.task_spec._update(listener)
    return True
