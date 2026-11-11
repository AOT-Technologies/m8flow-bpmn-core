"""BPMN error and escalation catch semantics for SpiffWorkflow (M8F-665).

Instance-local adapters: no Spiff classes are patched globally.
"""

from __future__ import annotations

from copy import deepcopy
from types import MethodType
from typing import Any

from SpiffWorkflow.bpmn.parser.event_parsers import (
    CONDITIONAL_EVENT_XPATH,
    ERROR_EVENT_XPATH,
    ESCALATION_EVENT_XPATH,
    MESSAGE_EVENT_XPATH,
    SIGNAL_EVENT_XPATH,
    TIMER_EVENT_XPATH,
)
from SpiffWorkflow.bpmn.specs.event_definitions.item_aware_event import (
    ErrorEventDefinition,
    EscalationEventDefinition,
)
from SpiffWorkflow.bpmn.specs.mixins.events.intermediate_event import BoundaryEvent
from SpiffWorkflow.bpmn.util import BpmnEvent
from SpiffWorkflow.bpmn.workflow import BpmnWorkflow
from SpiffWorkflow.util.deep_merge import DeepMerge
from SpiffWorkflow.util.task import TaskState

from m8flow_core.services.signal_runtime import SignalStartParser

_INTERRUPTING_EXTENSION = "m8f_interrupting"


def _xml_true(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"true", "1"}


class EventSubprocessStartEventParser(SignalStartParser):
    """Parse error and escalation start events of event subprocesses as triggers.

    Spiff only parses message, signal, timer and conditional start events, so an
    error or escalation event subprocess became a none start: it ran every time
    its scope started and caught nothing (M8F-665). As a trigger, Spiff lists the
    event subprocess in the start task's ``trigger_specs`` and
    ``_throw_to_nearest_catcher`` starts it. ``isInterrupting`` is kept in the
    start event's extensions, which the workflow serializer saves.
    """

    def _in_event_subprocess(self) -> bool:
        parent = self.node.getparent()
        return parent is not None and _xml_true(parent.get("triggeredByEvent"))

    def create_task(self) -> Any:
        xpaths = [
            MESSAGE_EVENT_XPATH,
            SIGNAL_EVENT_XPATH,
            TIMER_EVENT_XPATH,
            CONDITIONAL_EVENT_XPATH,
        ]
        if self._in_event_subprocess():
            xpaths += [ERROR_EVENT_XPATH, ESCALATION_EVENT_XPATH]
        task = self._create_task(self.get_event_definition(xpaths))
        self.spec.start.connect(task)
        return task

    def parse_extensions(self, node: Any = None) -> dict[str, Any]:
        extensions: dict[str, Any] = super().parse_extensions(node)
        if node is None and self._in_event_subprocess():
            interrupting = self.node.get("isInterrupting", "true")
            extensions[_INTERRUPTING_EXTENSION] = (
                interrupting.strip().lower() != "false"
            )
        return extensions


def install_scoped_error_escalation_throw(workflow: BpmnWorkflow) -> None:
    """Route error and escalation throws through ``_throw_to_nearest_catcher``.

    Spiff's throw for these events has no target scope, so every matching
    waiting catcher in the whole process caught it (M8F-665). The override is an
    instance attribute, so it is not serialized and is installed on every build
    and restore.
    """
    for spec in (workflow.spec, *workflow.subprocess_specs.values()):
        for task_spec in spec.task_specs.values():
            definition = getattr(task_spec, "event_definition", None)
            if isinstance(definition, ErrorEventDefinition | EscalationEventDefinition):
                definition.throw = MethodType(_throw_to_nearest_catcher, definition)


def _throw_to_nearest_catcher(definition: Any, thrower: Any) -> None:
    """Hand an error or escalation to the nearest matching catcher (BPMN 2.0).

    The walk starts in the thrower's scope and moves out one scope at a time. In
    each scope, its event subprocesses come first, then boundary events on the
    activity that hosts the scope. The first match handles the event. Nothing
    matches: the event is dropped. An uncaught escalation lets the flow go on,
    and an uncaught error end event ends its path (M8F-665 policy).
    """
    expression = getattr(definition, "expression", None)
    payload = (
        thrower.workflow.script_engine.evaluate(thrower, expression)
        if expression
        else None
    )
    event = BpmnEvent(definition, payload=payload)
    top = thrower.workflow.top_workflow
    scope = thrower.workflow
    while True:
        if _start_matching_event_subprocess(scope, event, thrower):
            return
        host_id = getattr(scope, "parent_task_id", None)
        if host_id is None:
            return
        host = top.get_task_from_id(host_id)
        if _catch_on_boundary(host, event):
            return
        scope = host.workflow


def _catch_on_boundary(host: Any, event: BpmnEvent) -> bool:
    """Catch ``event`` on a waiting boundary event attached to ``host``."""
    for task in list(host.workflow.top_workflow.event_manager.tasks.values()):
        if (
            task.parent is host.parent
            and task is not host
            and isinstance(task.task_spec, BoundaryEvent)
            and task.task_spec.catches(task, event)
        ):
            _deliver(task, event)
            return True
    return False


def _start_matching_event_subprocess(
    scope: Any, event: BpmnEvent, thrower: Any
) -> bool:
    """Start the first event subprocess of ``scope`` whose start event matches.

    Mirrors Spiff's ``BpmnStartTask.trigger_wf``, but the handler starts from the
    thrower's data rather than the scope root's data from when the scope began,
    continues to the scope's end, and an interrupting handler cancels the rest of
    the scope.
    """
    top = scope.top_workflow
    start = scope.spec.start
    for name in getattr(start, "trigger_specs", []):
        matching = [
            start_event
            for start_event in top.subprocess_specs[name].bpmn_start_events
            if start_event.event_definition == event.event_definition
        ]
        if not matching:
            continue
        task_spec = scope.spec.task_specs[name]
        if start not in task_spec.inputs:
            start.connect(task_spec)
            task_spec.connect(scope.spec.end)
        handler = scope.task_tree._add_child(task_spec, TaskState.FUTURE)
        handler.triggered = True
        task_spec._update(handler)
        # Spiff starts the subprocess without predicting the handler's path to
        # the scope's end, so it would finish without completing the scope.
        task_spec._predict(handler, mask=TaskState.NOT_FINISHED_MASK)
        thrower_data = deepcopy(thrower.data)
        handler.data = DeepMerge.merge(handler.data, deepcopy(thrower_data))
        subprocess = top.subprocesses[handler.id]
        for task in subprocess.get_tasks():
            task.data = DeepMerge.merge(task.data, deepcopy(thrower_data))
        if matching[0].extensions.get(_INTERRUPTING_EXTENSION, True):
            for sibling in handler.parent.children:
                if sibling is not handler:
                    sibling.cancel()
        for task in list(top.event_manager.tasks.values()):
            if task.workflow is subprocess and task.task_spec.catches(task, event):
                _deliver(task, event)
        return True
    return False


def _deliver(task: Any, event: BpmnEvent) -> None:
    task.task_spec.catch(task, event)
    task.task_spec._update(task)
