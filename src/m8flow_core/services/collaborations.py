from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from lxml import etree
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from m8flow_core.errors import NotFoundError, ValidationError
from m8flow_core.models.bpmn_process_definition import BpmnProcessDefinitionModel
from m8flow_core.models.process_instance import ProcessInstanceModel

_BPMN = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
_THROWS = {_BPMN + tag for tag in ("intermediateThrowEvent", "endEvent")}
_CATCHES = {
    _BPMN + "startEvent": "start",
    _BPMN + "intermediateCatchEvent": "catch",
}
_ISO_DURATION = re.compile(r"P(?=[\dT])(?:[\d.,]+[YMWDHS]|T)+")


def _parse_xml(source: bytes) -> Any:
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        root = etree.fromstring(source, parser)
    except etree.XMLSyntaxError as exc:
        raise ValidationError(f"BPMN source is not parseable: {exc}") from exc
    if root.getroottree().docinfo.doctype:
        raise ValidationError("BPMN source must not contain a document type")
    return root


def _local_ref(node: Any, attribute: str, namespace: str | None) -> str | None:
    ref: str | None = node.get(attribute)
    if ref is None or ":" not in ref:
        return ref
    prefix, local = ref.split(":", 1)
    if prefix in node.nsmap and node.nsmap[prefix] == namespace:
        return local
    return ref


def read_collaboration(source: bytes) -> dict[str, Any] | None:
    root = _parse_xml(source)
    collaborations = root.findall(_BPMN + "collaboration")
    if not any(
        len(node.findall(_BPMN + "participant")) >= 2 for node in collaborations
    ):
        return None

    namespace = root.get("targetNamespace")
    processes = {node.get("id"): node for node in root.findall(_BPMN + "process")}
    executable = {
        process_id
        for process_id, node in processes.items()
        if node.get("isExecutable") in {"true", "1"}
    }
    if len(collaborations) != 1:
        raise ValidationError(
            "A collaboration deployment requires one BPMN collaboration"
        )

    collaboration = collaborations[0]
    collaboration_id = collaboration.get("id")
    if not collaboration_id or len(collaboration_id) > 255:
        raise ValidationError(
            "A collaboration requires an ID of at most 255 characters"
        )
    indexed_nodes: dict[str, Any] = {}
    for node in root.iter(f"{_BPMN}*"):
        node_id = node.get("id")
        if node_id:
            if node_id in indexed_nodes:
                raise ValidationError(f"Duplicate BPMN ID {node_id!r}")
            indexed_nodes[node_id] = node

    participants: dict[str, dict[str, Any]] = {}
    participant_by_process: dict[str, str] = {}
    for node in collaboration.findall(_BPMN + "participant"):
        participant_id = node.get("id")
        if not participant_id:
            raise ValidationError("A participant is missing its ID")
        process_id = _local_ref(node, "processRef", namespace)
        if process_id is not None:
            if process_id not in processes:
                raise ValidationError(
                    f"Participant {participant_id!r} references unknown process "
                    f"{process_id!r}"
                )
            if process_id in participant_by_process:
                raise ValidationError(
                    f"Process {process_id!r} is referenced by multiple participants"
                )
            participant_by_process[process_id] = participant_id
        participants[participant_id] = {
            "id": participant_id,
            "name": node.get("name"),
            "process_ref": process_id,
            "is_executable": process_id in executable,
        }
    executable_ids = [
        process_id for process_id in participant_by_process if process_id in executable
    ]
    if len(executable_ids) < 2:
        raise ValidationError(
            "A collaboration deployment requires two executable participant processes"
        )

    def endpoint(ref: str | None) -> tuple[Any, str | None, str]:
        if ref is None or ref not in indexed_nodes:
            raise ValidationError(f"Message Flow references unknown endpoint {ref!r}")
        node = indexed_nodes[ref]
        if ref in participants:
            return node, participants[ref]["process_ref"], ref
        node_type = etree.QName(node).localname
        if not (
            node_type.endswith(("Task", "Event"))
            or node_type in {"task", "subProcess", "callActivity", "transaction"}
        ):
            raise ValidationError(
                f"Message Flow endpoint {ref!r} is not a BPMN interaction node"
            )
        process = next(node.iterancestors(_BPMN + "process"), None)
        process_id = process.get("id") if process is not None else None
        if process_id not in participant_by_process:
            raise ValidationError(
                f"Message Flow endpoint {ref!r} is outside the collaboration"
            )
        return node, process_id, participant_by_process[process_id]

    messages = {
        node.get("id") for node in root.findall(_BPMN + "message") if node.get("id")
    }
    flows: list[dict[str, Any]] = []
    for flow in collaboration.findall(_BPMN + "messageFlow"):
        flow_id = flow.get("id")
        if not flow_id:
            raise ValidationError("A Message Flow is missing its ID")
        source_ref = _local_ref(flow, "sourceRef", namespace)
        target_ref = _local_ref(flow, "targetRef", namespace)
        message_ref = _local_ref(flow, "messageRef", namespace)
        if message_ref is not None and message_ref not in messages:
            raise ValidationError(
                f"Message Flow {flow_id!r} references unknown message {message_ref!r}"
            )
        throw, source_process, source_pool = endpoint(source_ref)
        catch, target_process, target_pool = endpoint(target_ref)
        if source_pool == target_pool:
            raise ValidationError(
                f"Message Flow {flow_id!r} must connect different participants"
            )

        metadata = {
            "id": flow_id,
            "name": flow.get("name"),
            "source_ref": source_ref,
            "target_ref": target_ref,
            "message_ref": message_ref,
            "source_process_id": source_process,
            "target_process_id": target_process,
            "target_kind": None,
            "routed_message_ref": None,
        }
        throw_def = _message_event_definition(throw)
        catch_def = _message_event_definition(catch)
        if (
            source_process in executable_ids
            and target_process in executable_ids
            and throw.getparent().tag == catch.getparent().tag == _BPMN + "process"
            and throw.tag in _THROWS
            and catch.tag in _CATCHES
            and throw_def is not None
            and catch_def is not None
        ):
            throw_message = _local_ref(throw_def, "messageRef", namespace)
            catch_message = _local_ref(catch_def, "messageRef", namespace)
            if (
                throw_message not in messages
                or throw_message != catch_message
                or message_ref not in {None, throw_message}
            ):
                raise ValidationError(
                    f"Message Flow {flow_id!r} requires matching message references "
                    "on its events"
                )
            metadata.update(
                target_kind=_CATCHES[catch.tag], routed_message_ref=throw_message
            )
        flows.append(metadata)

    return {
        "id": collaboration_id,
        "participants": list(participants.values()),
        "executable_process_ids": executable_ids,
        "message_flows": flows,
    }


def _message_event_definition(node: Any) -> Any:
    definitions = [
        element
        for element in node
        if isinstance(element.tag, str)
        and element.tag.startswith(_BPMN)
        and element.tag.endswith(("EventDefinition", "}eventDefinitionRef"))
    ]
    if len(definitions) == 1 and definitions[0].tag == _BPMN + "messageEventDefinition":
        return definitions[0]
    return None


def collaboration_parser_source(source: bytes) -> bytes:
    """Normalize local QName references for Spiff while retaining the stored XML."""
    root = _parse_xml(source)
    namespace = root.get("targetNamespace")
    for tag, attribute in (
        ("participant", "processRef"),
        ("messageEventDefinition", "messageRef"),
        ("callActivity", "calledElement"),
    ):
        for node in root.iter(_BPMN + tag):
            ref = _local_ref(node, attribute, namespace)
            if ref is not None:
                node.set(attribute, ref)
    for process in root.findall(_BPMN + "process"):
        if process.get("isExecutable") == "1":
            process.set("isExecutable", "true")
        elif process.get("isExecutable") == "0":
            process.set("isExecutable", "false")
    for timer in root.iter(_BPMN + "timerEventDefinition"):
        for expression in timer:
            value = (expression.text or "").strip()
            if expression.tag == _BPMN + "timeDate":
                try:
                    datetime.fromisoformat(value)
                except ValueError:
                    continue
                expression.text = repr(value)
            elif expression.tag == _BPMN + "timeDuration" and _ISO_DURATION.fullmatch(
                value
            ):
                expression.text = repr(value)
            elif expression.tag == _BPMN + "timeCycle" and re.match(r"^R\d*/", value):
                expression.text = repr(value)
    return bytes(etree.tostring(root, encoding="utf-8"))


def lock_collaboration_definition(
    session: Session, definition: BpmnProcessDefinitionModel
) -> None:
    """Serialize state transitions within a deployment, including on SQLite."""
    session.flush()
    scope = (
        BpmnProcessDefinitionModel.id == definition.id,
        BpmnProcessDefinitionModel.m8f_tenant_id == definition.m8f_tenant_id,
    )
    if session.get_bind().dialect.name == "sqlite":
        session.execute(
            update(BpmnProcessDefinitionModel)
            .where(*scope)
            .values(id=BpmnProcessDefinitionModel.id)
            .execution_options(synchronize_session=False)
        )
    elif (
        session.scalar(
            select(BpmnProcessDefinitionModel.id).where(*scope).with_for_update()
        )
        is None
    ):
        raise NotFoundError("Collaboration deployment was not found")
    session.expire_all()


def lock_collaboration_instance(
    session: Session, instance: ProcessInstanceModel
) -> None:
    if instance.collaboration_correlation_key is None:
        return
    definition = instance.bpmn_process_definition
    if definition is None or definition.m8f_tenant_id != instance.m8f_tenant_id:
        raise NotFoundError("Collaboration deployment was not found")
    lock_collaboration_definition(session, definition)
