"""Workflow snapshot persistence without execution or lifecycle dependencies."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from SpiffWorkflow.bpmn.workflow import BpmnWorkflow
from sqlalchemy.orm import Session

from m8flow_core.errors import ValidationError
from m8flow_core.models.json_data import JsonDataModel
from m8flow_core.models.process_instance import (
    WORKFLOW_STATE_JSON_DATA_KEY,
    ProcessInstanceModel,
)
from m8flow_core.services.compensation_runtime import coordinator
from m8flow_core.services.workflow_engine import (
    _WORKFLOW_SERIALIZER,
    _WORKFLOW_STATE_SERIALIZER_VERSION,
)


def _persist_workflow_recovery_state(
    session: Session,
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
) -> None:
    serialized_state = _serialize_runtime_workflow(workflow)
    process_instance.workflow_engine_version = _WORKFLOW_STATE_SERIALIZER_VERSION
    serialized_workflow = _serialize_workflow_dict(workflow)
    _sync_bpmn_process_from_workflow(
        session,
        process_instance=process_instance,
        serialized_workflow=serialized_workflow,
        serialized_state=serialized_state,
    )


def _serialize_runtime_workflow(workflow: BpmnWorkflow) -> str:
    serialized: str = _WORKFLOW_SERIALIZER.serialize_json(workflow)
    comp = coordinator(workflow)
    if comp is None:
        return serialized
    payload = json.loads(serialized)
    payload["m8f_compensation"] = comp.snapshot()
    return json.dumps(payload)


def _serialize_workflow_dict(workflow: BpmnWorkflow) -> dict[str, Any]:
    serialized_workflow: dict[str, Any] = _WORKFLOW_SERIALIZER.to_dict(workflow)
    if not isinstance(serialized_workflow, dict):
        raise ValidationError("Workflow serializer did not produce a dictionary")
    if comp := coordinator(workflow):
        # Project detached handlers as subprocesses so both use scoped task rows.
        subprocess_specs = serialized_workflow["subprocess_specs"]
        subprocesses = serialized_workflow["subprocesses"]
        for handler in comp.handlers.values():
            payload = _WORKFLOW_SERIALIZER.to_dict(handler)
            spec = payload["spec"]
            subprocess_specs[spec["name"]] = spec
            subprocess_specs.update(payload["subprocess_specs"])
            payload["spec"] = spec["name"]
            subprocesses[str(handler.task_tree.id)] = payload
        serialized_workflow = comp.encode_payload(serialized_workflow)
    if serialized_workflow.get("serializer_version") is None:
        serialized_workflow["serializer_version"] = _WORKFLOW_STATE_SERIALIZER_VERSION
    return serialized_workflow


def _sync_bpmn_process_from_workflow(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    serialized_workflow: Mapping[str, Any],
    serialized_state: str,
) -> None:
    bpmn_process = process_instance.bpmn_process
    if bpmn_process is None:
        return

    process_payload = {
        key: value
        for key, value in serialized_workflow.items()
        if key
        not in {
            "serializer_version",
            "spec",
            "subprocess_specs",
            "subprocesses",
            "tasks",
            "data",
        }
    }
    bpmn_process.properties_json = process_payload

    serialized_data = serialized_workflow.get("data")
    persisted_workflow_data = (
        dict(serialized_data) if isinstance(serialized_data, Mapping) else {}
    )
    persisted_workflow_data[WORKFLOW_STATE_JSON_DATA_KEY] = serialized_state
    bpmn_process.json_data_hash = JsonDataModel.create_or_update_from_payload(
        session,
        process_instance.m8f_tenant_id,
        persisted_workflow_data,
    )
