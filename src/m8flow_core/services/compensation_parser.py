"""Compile BPMN compensation bindings without introducing normal-flow tokens.

The original source remains unchanged in storage. Handler activities are compiled
as private executable specifications; compensation boundaries are subscriptions,
not Spiff's normal boundary-event split/join branches.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

# Construction/serialization only; untrusted parsing uses defusedxml below.
from xml.etree import ElementTree as Xml  # nosec B405

from defusedxml import ElementTree
from SpiffWorkflow.bpmn.parser.BpmnParser import full_tag
from SpiffWorkflow.bpmn.serializer.default.task_spec import EventConverter
from SpiffWorkflow.bpmn.specs.defaults import EndEvent, IntermediateThrowEvent
from SpiffWorkflow.bpmn.specs.event_definitions.simple import NoneEventDefinition
from SpiffWorkflow.spiff.parser.event_parsers import (
    SpiffEndEventParser,
    SpiffIntermediateThrowEventParser,
)
from SpiffWorkflow.spiff.parser.process import SpiffBpmnParser

from m8flow_core.errors import ValidationError

BPMN = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
ACTIVITIES = {
    "task",
    "userTask",
    "manualTask",
    "scriptTask",
    "serviceTask",
    "businessRuleTask",
    "subProcess",
}


class CompensationThrow(IntermediateThrowEvent):
    def __init__(
        self,
        *args: Any,
        activity_ref: str | None = None,
        wait_for_completion: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.activity_ref = activity_ref
        self.wait_for_completion = wait_for_completion

    def _run_hook(self, task: Any) -> bool | None:
        coordinator = task.workflow.top_workflow._m8f_compensation
        run = coordinator.trigger(task)
        return (
            True
            if not self.wait_for_completion or run["status"] == "completed"
            else None
        )


class CompensationEnd(CompensationThrow, EndEvent):
    pass


class TransactionCancelEnd(EndEvent):
    def _run_hook(self, task: Any) -> None:
        task.workflow.top_workflow._m8f_compensation.cancel_transaction(task)


class CompensationEventConverter(EventConverter):
    def to_dict(self, spec: Any) -> dict[str, Any]:
        return {
            **super().to_dict(spec),
            "activity_ref": spec.activity_ref,
            "wait_for_completion": spec.wait_for_completion,
        }


class _ThrowParser(SpiffIntermediateThrowEventParser):
    def create_task(self) -> Any:
        event = self.node.find(full_tag("compensateEventDefinition"))
        if event is None:
            return super().create_task()
        return CompensationThrow(
            self.spec,
            self.bpmn_id,
            event_definition=NoneEventDefinition(),
            activity_ref=event.get("activityRef"),
            wait_for_completion=event.get("waitForCompletion", "true") in {"true", "1"},
            **self.bpmn_attributes,
        )


class _EndParser(SpiffEndEventParser):
    def create_task(self) -> Any:
        event = self.node.find(full_tag("compensateEventDefinition"))
        if event is None:
            if self.node.find(full_tag("cancelEventDefinition")) is None:
                return super().create_task()
            task = TransactionCancelEnd(
                self.spec,
                self.bpmn_id,
                event_definition=NoneEventDefinition(),
                **self.bpmn_attributes,
            )
            task.connect(self.spec.end)
            return task
        task = CompensationEnd(
            self.spec,
            self.bpmn_id,
            event_definition=NoneEventDefinition(),
            activity_ref=event.get("activityRef"),
            wait_for_completion=event.get("waitForCompletion", "true") in {"true", "1"},
            **self.bpmn_attributes,
        )
        task.connect(self.spec.end)
        return task


class CompensationBpmnParser(SpiffBpmnParser):
    OVERRIDE_PARSER_CLASSES = {
        **SpiffBpmnParser.OVERRIDE_PARSER_CLASSES,
        full_tag("intermediateThrowEvent"): (_ThrowParser, IntermediateThrowEvent),
        full_tag("endEvent"): (_EndParser, EndEvent),
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.compensation_bindings: dict[str, dict[str, Any]] = {}
        self.compensation_handlers: dict[str, str] = {}
        self.has_compensation = False

    def get_process_ids(self) -> list[str]:
        helpers = set(self.compensation_handlers.values())
        return [name for name in super().get_process_ids() if name not in helpers]

    def add_bpmn_str(self, bpmn_str: bytes, filename: str | None = None) -> None:
        root = ElementTree.fromstring(bpmn_str)
        if any(
            node.get("isForCompensation") not in {None, "true", "false", "0", "1"}
            for node in root.iter()
        ):
            raise ValidationError("isForCompensation must be a BPMN boolean")
        if any(
            node.tag
            in {BPMN + "compensateEventDefinition", BPMN + "cancelEventDefinition"}
            or node.get("isForCompensation") in {"true", "1"}
            for node in root.iter()
        ):
            self.has_compensation = True
            self._compile(root)
            bpmn_str = Xml.tostring(root, encoding="utf-8")
        super().add_bpmn_str(bpmn_str, filename=filename)

    def _compile(self, root: Any) -> None:
        ids = [
            node.get("id")
            for node in root.iter()
            if node.tag.startswith(BPMN) and node.get("id")
        ]
        if len(ids) != len(set(ids)):
            raise ValidationError("BPMN compensation identifiers must be unique")
        nodes: dict[str, Any] = {
            node.get("id"): node for node in root.iter() if node.get("id")
        }
        parents: dict[Any, Any] = {
            child: parent for parent in root.iter() for child in parent
        }
        for node in list(root.iter()):
            for ref in list(node.findall(BPMN + "eventDefinitionRef")):
                definition = nodes.get((ref.text or "").strip().split(":")[-1])
                if definition is not None and definition.tag in {
                    BPMN + "compensateEventDefinition",
                    BPMN + "cancelEventDefinition",
                }:
                    copied = deepcopy(definition)
                    copied.attrib.pop("id", None)
                    node.append(copied)
                    node.remove(ref)
            compensation = node.findall(BPMN + "compensateEventDefinition")
            if compensation:
                event_definitions = [
                    child for child in node if child.tag.endswith("EventDefinition")
                ]
                if len(compensation) != 1 or len(event_definitions) != 1:
                    raise ValidationError(
                        "Compensation events require one event definition"
                    )
                if compensation[0].get("activityRef"):
                    compensation[0].set(
                        "activityRef", compensation[0].get("activityRef").split(":")[-1]
                    )
        self._validate_cancel_events(root, nodes, parents)
        scopes = {
            node
            for node in root.iter()
            if node.tag in {BPMN + "process", BPMN + "subProcess", BPMN + "transaction"}
        }
        for node in root.iter():
            # Spiff evaluates timer content as Python; accept standard BPMN
            # literals as well as the existing expression dialect.
            if node.tag in {BPMN + "timeDate", BPMN + "timeDuration"}:
                value = (node.text or "").strip()
                if value.startswith("P") or (len(value) > 10 and value[4:5] == "-"):
                    node.text = repr(value)
            if node.tag in {
                BPMN + name
                for name in (
                    "callActivity",
                    "standardLoopCharacteristics",
                    "multiInstanceLoopCharacteristics",
                    "errorEventDefinition",
                )
            }:
                raise ValidationError(
                    f"Compensation models do not support {node.tag.split('}')[-1]} yet"
                )
            if node.tag == BPMN + "subProcess" and node.get("triggeredByEvent") in {
                "true",
                "1",
            }:
                raise ValidationError(
                    "Event subprocesses in compensation models are unsupported"
                )

        handlers = [
            node
            for node in root.iter()
            if node.get("isForCompensation") in {"true", "1"}
        ]
        bound: set[str] = set()
        for scope in scopes:
            for boundary in list(scope):
                event = boundary.find(BPMN + "compensateEventDefinition")
                if boundary.tag != BPMN + "boundaryEvent" or event is None:
                    continue
                source = nodes.get(boundary.get("attachedToRef"))
                associations = [
                    a
                    for a in scope
                    if a.tag == BPMN + "association"
                    and a.get("sourceRef") == boundary.get("id")
                ]
                if (
                    source is None
                    or parents[source] is not scope
                    or source.tag.removeprefix(BPMN) not in ACTIVITIES
                    or len(associations) != 1
                ):
                    raise ValidationError(
                        "Compensation boundary requires one local activity and "
                        "association"
                    )
                association = associations[0]
                handler = nodes.get(association.get("targetRef"))
                if (
                    handler is None
                    or handler not in handlers
                    or parents[handler] is not scope
                    or handler.tag.removeprefix(BPMN) not in ACTIVITIES
                ):
                    raise ValidationError(
                        "Compensation association must target a local "
                        "isForCompensation "
                        "activity"
                    )
                if source is handler or source.get("id") in self.compensation_bindings:
                    raise ValidationError(
                        "An activity must have exactly one compensation handler"
                    )
                if boundary.get("cancelActivity", "false") not in {"false", "0"}:
                    raise ValidationError(
                        "Compensation boundary cannot interrupt an activity"
                    )
                if len(boundary.findall(BPMN + "compensateEventDefinition")) != 1:
                    raise ValidationError(
                        "Compensation boundary requires one event definition"
                    )
                self.compensation_bindings[source.get("id")] = {
                    "handler_id": handler.get("id"),
                    "scope": scope.get("id"),
                }
                bound.add(handler.get("id"))
                scope.remove(boundary)
                scope.remove(association)

        for scope in scopes:
            for flow in scope.findall(BPMN + "sequenceFlow"):
                if any(
                    nodes.get(flow.get(ref)) in handlers
                    or (
                        nodes.get(flow.get(ref)) is not None
                        and nodes[flow.get(ref)].tag == BPMN + "boundaryEvent"
                        and nodes[flow.get(ref)].find(
                            BPMN + "compensateEventDefinition"
                        )
                        is not None
                    )
                    for ref in ("sourceRef", "targetRef")
                ):
                    raise ValidationError(
                        "Compensation handlers/boundaries cannot have external "
                        "sequence "
                        "flows"
                    )
            for node in scope:
                for event in node.findall(BPMN + "compensateEventDefinition"):
                    if node.tag not in {
                        BPMN + "intermediateThrowEvent",
                        BPMN + "endEvent",
                    }:
                        raise ValidationError(
                            "Only boundary, intermediate throw and end "
                            "compensation events "
                            "are supported"
                        )
                    if event.get("waitForCompletion", "true") not in {
                        "true",
                        "false",
                        "0",
                        "1",
                    }:
                        raise ValidationError(
                            "waitForCompletion must be a BPMN boolean"
                        )
                    ref = event.get("activityRef")
                    if ref is not None and not ref.strip():
                        raise ValidationError("activityRef must not be empty")
                    target = nodes.get(ref)
                    if ref and (
                        target is None
                        or parents[target] is not scope
                        or target in handlers
                        or target.tag.removeprefix(BPMN) not in ACTIVITIES
                    ):
                        raise ValidationError(
                            "activityRef must identify an activity in the throw's scope"
                        )

        for handler in handlers:
            handler_id = handler.get("id")
            if handler_id not in bound:
                raise ValidationError(
                    "Compensation handler has no boundary association"
                )
            helper_id = f"__m8f_compensation_{handler_id}"
            if helper_id in nodes:
                raise ValidationError(
                    "BPMN identifier conflicts with a compensation helper"
                )
            helper = Xml.SubElement(
                root, BPMN + "process", {"id": helper_id, "isExecutable": "true"}
            )
            start_id, end_id = helper_id + "_start", helper_id + "_end"
            Xml.SubElement(helper, BPMN + "startEvent", {"id": start_id})
            copy = deepcopy(handler)
            copy.attrib.pop("isForCompensation", None)
            helper.append(copy)
            Xml.SubElement(helper, BPMN + "endEvent", {"id": end_id})
            for index, (source, target) in enumerate(
                ((start_id, handler_id), (handler_id, end_id))
            ):
                Xml.SubElement(
                    helper,
                    BPMN + "sequenceFlow",
                    {
                        "id": f"{helper_id}_flow_{index}",
                        "sourceRef": source,
                        "targetRef": target,
                    },
                )
            # Preserve the handler's original lane when moving it to a private spec.
            scope = parents[handler]
            for lane in scope.iter(BPMN + "lane"):
                if any(
                    ref.text == handler_id for ref in lane.findall(BPMN + "flowNodeRef")
                ):
                    lane_set = Xml.SubElement(
                        helper, BPMN + "laneSet", {"id": helper_id + "_lanes"}
                    )
                    copied_lane = Xml.SubElement(
                        lane_set,
                        BPMN + "lane",
                        {"id": helper_id + "_lane", "name": lane.get("name", "")},
                    )
                    Xml.SubElement(copied_lane, BPMN + "flowNodeRef").text = handler_id
                    break
            parents[handler].remove(handler)
            self.compensation_handlers[handler_id] = helper_id

    @staticmethod
    def _validate_cancel_events(
        root: Any, nodes: dict[str, Any], parents: dict[Any, Any]
    ) -> None:
        boundaries: set[str] = set()
        ends: set[str] = set()
        for node in root.iter():
            if node.find(BPMN + "cancelEventDefinition") is None:
                continue
            definitions = [
                child
                for child in node
                if child.tag.endswith(("EventDefinition", "eventDefinitionRef"))
            ]
            if len(definitions) != 1:
                raise ValidationError(
                    "Cancel Events require exactly one event definition"
                )
            scope = parents.get(node)
            if (
                node.tag == BPMN + "endEvent"
                and scope is not None
                and scope.tag == BPMN + "transaction"
            ):
                ends.add(scope.get("id"))
                continue
            if node.tag == BPMN + "boundaryEvent":
                attached_id = node.get("attachedToRef", "").split(":")[-1]
                transaction = nodes.get(attached_id)
                if (
                    transaction is not None
                    and transaction.tag == BPMN + "transaction"
                    and parents[transaction] is scope
                    and node.get("cancelActivity", "true") in {"true", "1"}
                    and attached_id not in boundaries
                ):
                    node.set("attachedToRef", attached_id)
                    node.set("cancelActivity", "true")
                    boundaries.add(attached_id)
                    continue
            raise ValidationError(
                "A Cancel End must be inside a Transaction and each Transaction "
                "can have one interrupting Cancel Boundary Event"
            )
        if ends.difference(boundaries):
            raise ValidationError(
                "A Transaction with a Cancel End requires a Cancel Boundary Event"
            )
        for transaction_id in ends:
            transaction = nodes.get(transaction_id)
            if transaction is not None and any(
                nested is not transaction
                for nested in transaction.iter(BPMN + "transaction")
            ):
                raise ValidationError(
                    "A cancellable Transaction cannot contain another Transaction"
                )


def validate_compensation_bpmn(source_bpmn_xml: str | bytes) -> None:
    """Validate the supported compensation and Transaction Cancel BPMN forms."""
    source = (
        source_bpmn_xml.encode("utf-8")
        if isinstance(source_bpmn_xml, str)
        else source_bpmn_xml
    )
    try:
        CompensationBpmnParser(validator=None).add_bpmn_str(source)
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError(f"BPMN source is not parseable: {exc}") from exc
