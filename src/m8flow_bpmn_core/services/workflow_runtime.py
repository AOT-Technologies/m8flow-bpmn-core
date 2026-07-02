from __future__ import annotations

import ast
import hashlib
import json
import re
import time
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from SpiffWorkflow.bpmn.serializer.workflow import BpmnWorkflowSerializer
from SpiffWorkflow.bpmn.workflow import BpmnWorkflow
from SpiffWorkflow.dmn.specs.model import DecisionTable
from SpiffWorkflow.spiff.parser.process import SpiffBpmnParser
from SpiffWorkflow.spiff.serializer.config import SPIFF_CONFIG
from SpiffWorkflow.util.task import TaskState
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from m8flow_bpmn_core.errors import (
    InvalidStateError,
    NotFoundError,
    ValidationError,
)
from m8flow_bpmn_core.models.bpmn_process import BpmnProcessModel
from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_bpmn_core.models.future_task import FutureTaskModel
from m8flow_bpmn_core.models.group import GroupModel
from m8flow_bpmn_core.models.human_task import HumanTaskModel
from m8flow_bpmn_core.models.human_task_user import (
    HumanTaskUserAddedBy,
    HumanTaskUserModel,
)
from m8flow_bpmn_core.models.json_data import JsonDataModel
from m8flow_bpmn_core.models.process_instance import (
    WORKFLOW_STATE_JSON_DATA_KEY,
    ProcessInstanceModel,
    ProcessInstanceStatus,
)
from m8flow_bpmn_core.models.process_instance_event import ProcessInstanceEventType
from m8flow_bpmn_core.models.process_model_bpmn_version import (
    ProcessModelBpmnVersionModel,
)
from m8flow_bpmn_core.models.task import TaskModel
from m8flow_bpmn_core.models.task_definition import TaskDefinitionModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.services.authorization import (
    PROCESS_START_COMMAND,
    require_command_authorization,
)
from m8flow_bpmn_core.services.process_instances import (
    create_process_instance,
    get_process_instance_metadata,
    record_process_instance_event,
    upsert_process_instance_metadata,
)
from m8flow_bpmn_core.services.tenant_users import (
    ensure_user_belongs_to_tenant,
    tenant_identifiers_for,
    user_belongs_to_tenant,
)

_WORKFLOW_SERIALIZER = BpmnWorkflowSerializer(
    registry=BpmnWorkflowSerializer.configure(SPIFF_CONFIG),
    version="5",
)
_WORKFLOW_STATE_SERIALIZER_VERSION = "5"


class _NoopDMNEngine:
    """Minimal DMN engine that leaves task data untouched."""

    def __init__(self, decision_ref: str):
        self.decision_table = DecisionTable(decision_ref, decision_ref, "UNIQUE")

    def result(self, task: object) -> dict[str, object]:
        return {}


class _RuntimeSpiffBpmnParser(SpiffBpmnParser):
    """Spiff parser that can fall back to a no-op DMN engine for POC runs."""

    def __init__(self, *args: Any, use_noop_dmn_engine: bool = True, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._use_noop_dmn_engine = use_noop_dmn_engine

    def get_engine(self, decision_ref: str, node: object) -> Any:
        if not self._use_noop_dmn_engine:
            return super().get_engine(decision_ref, node)
        return _NoopDMNEngine(decision_ref)


def resolve_lane_assignment_id(lane_name: str) -> int:
    """Return a stable lane identifier that fits m8flow's integer group ids."""
    normalized_lane = lane_name.strip().lower()
    digest = hashlib.sha256(normalized_lane.encode("utf-8")).hexdigest()
    stable_int = int(digest[:8], 16) & 0x7FFFFFFF
    return stable_int or 1


def initialize_process_instance_workflow(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
    bpmn_xml: str | bytes,
    dmn_xml: str | bytes | None = None,
    bpmn_process_id: str | None = None,
    started_at_in_seconds: int | None = None,
) -> ProcessInstanceModel:
    process_instance = _load_process_instance(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance_id,
    )
    if process_instance.has_terminal_status():
        raise InvalidStateError("Cannot initialize a terminal process instance")
    if process_instance.workflow_state_json is not None:
        raise InvalidStateError("Process instance workflow already initialized")

    occurred_at = _resolve_timestamp(started_at_in_seconds)
    workflow = _build_workflow(
        bpmn_xml=bpmn_xml,
        dmn_xml=dmn_xml,
        bpmn_process_id=bpmn_process_id,
    )
    _seed_runtime_definitions(
        session,
        tenant_id=tenant_id,
        process_instance=process_instance,
        workflow=workflow,
        occurred_at=occurred_at,
    )
    _apply_metadata_to_workflow(
        workflow,
        _load_process_instance_metadata_payload(
            session,
            tenant_id=tenant_id,
            process_instance_id=process_instance_id,
        ),
    )
    workflow.run_all(halt_on_manual=True)

    _persist_workflow_state(
        session,
        process_instance,
        workflow,
        occurred_at=occurred_at,
    )
    _materialize_ready_manual_tasks(
        session,
        process_instance=process_instance,
        workflow=workflow,
        occurred_at=occurred_at,
    )
    process_instance.start_in_seconds = occurred_at
    _update_process_instance_status_from_workflow(
        process_instance,
        workflow,
        occurred_at=occurred_at,
    )

    session.flush()

    ready_tasks = _get_ready_human_tasks(process_instance)
    record_process_instance_event(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance_id,
        event_type=ProcessInstanceEventType.process_instance_created,
        timestamp=float(occurred_at),
        task_guid=ready_tasks[0].task_guid if ready_tasks else None,
        user_id=process_instance.process_initiator_id,
    )

    session.flush()
    return process_instance


def initialize_process_instance_from_definition(
    session: Session,
    *,
    tenant_id: str,
    bpmn_process_definition_id: int,
    process_initiator_id: int,
    submission_metadata: Mapping[str, Any] | None = None,
    summary: str | None = None,
    process_version: int = 1,
    started_at_in_seconds: int | None = None,
    bpmn_process_id: str | None = None,
) -> ProcessInstanceModel:
    ensure_user_belongs_to_tenant(
        session,
        tenant_id=tenant_id,
        user_id=process_initiator_id,
    )
    process_definition = _load_process_definition(
        session,
        tenant_id=tenant_id,
        bpmn_process_definition_id=bpmn_process_definition_id,
    )
    process_model_identifier = (
        process_definition.process_model_identifier or str(process_definition.id)
    )
    require_command_authorization(
        session,
        tenant_id=tenant_id,
        actor_user_id=process_initiator_id,
        command_key=PROCESS_START_COMMAND,
        target_uri=f"/process-models/{process_model_identifier}",
        target_id=process_definition.id,
        metadata=_process_start_authorization_metadata(
            process_definition=process_definition,
            requested_bpmn_process_id=bpmn_process_id,
        ),
    )
    if process_definition.source_bpmn_xml is None:
        raise ValidationError(
            "Process definition does not include stored BPMN XML"
        )

    selected_process_id = _select_process_identifier(
        process_definition.source_bpmn_xml,
        bpmn_process_id=bpmn_process_id,
    )
    bpmn_process = _resolve_or_create_bpmn_process(
        session,
        tenant_id=tenant_id,
        process_definition=process_definition,
        process_identifier=selected_process_id,
    )

    process_instance = create_process_instance(
        session,
        tenant_id=tenant_id,
        process_model_identifier=process_model_identifier,
        process_model_display_name=(
            process_definition.bpmn_name or process_model_identifier
        ),
        process_initiator_id=process_initiator_id,
        bpmn_process_definition_id=process_definition.id,
        bpmn_process_id=bpmn_process.id,
        summary=summary,
        process_version=process_version,
        created_at_in_seconds=started_at_in_seconds,
        updated_at_in_seconds=started_at_in_seconds,
    )
    process_instance.bpmn_version_control_type = (
        process_definition.bpmn_version_control_type
    )
    process_instance.bpmn_version_control_identifier = (
        process_definition.bpmn_version_control_identifier
    )
    process_instance.last_milestone_bpmn_name = (
        process_definition.bpmn_name or process_definition.bpmn_identifier
    )

    metadata_timestamp = _resolve_timestamp(started_at_in_seconds)
    process_instance.bpmn_version_id = _ensure_bpmn_version_snapshot(
        session,
        tenant_id=tenant_id,
        process_model_identifier=process_model_identifier,
        bpmn_xml_text=process_definition.source_bpmn_xml,
        occurred_at=metadata_timestamp,
    ).id
    for key, value in (submission_metadata or {}).items():
        upsert_process_instance_metadata(
            session,
            tenant_id=tenant_id,
            process_instance_id=process_instance.id,
            key=key,
            value=str(value),
            updated_at_in_seconds=metadata_timestamp,
            created_at_in_seconds=metadata_timestamp,
        )

    return initialize_process_instance_workflow(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance.id,
        bpmn_xml=process_definition.source_bpmn_xml,
        dmn_xml=process_definition.source_dmn_xml,
        bpmn_process_id=selected_process_id,
        started_at_in_seconds=started_at_in_seconds,
    )


def advance_process_instance_workflow(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
    completed_task_guid: str,
    completed_at_in_seconds: int | None = None,
) -> ProcessInstanceModel:
    process_instance = _load_process_instance(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance_id,
    )
    if process_instance.workflow_state_json is None:
        return process_instance
    if process_instance.has_terminal_status():
        return process_instance

    occurred_at = _resolve_timestamp(completed_at_in_seconds)
    workflow = _restore_workflow(process_instance.workflow_state_json)
    completed_task = workflow.get_task_from_id(UUID(completed_task_guid))
    _apply_metadata_to_task(
        workflow,
        completed_task,
        _load_process_instance_metadata_payload(
            session,
            tenant_id=tenant_id,
            process_instance_id=process_instance_id,
        ),
    )
    completed_task.complete()
    workflow.run_all(halt_on_manual=True)

    _persist_workflow_state(
        session,
        process_instance,
        workflow,
        occurred_at=occurred_at,
    )
    _materialize_ready_manual_tasks(
        session,
        process_instance=process_instance,
        workflow=workflow,
        occurred_at=occurred_at,
    )
    _update_process_instance_status_from_workflow(
        process_instance,
        workflow,
        occurred_at=occurred_at,
    )

    session.flush()
    return process_instance


def repair_process_instance_runtime_representation(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
    occurred_at: int | None = None,
) -> ProcessInstanceModel:
    process_instance = _load_process_instance(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance_id,
    )
    serialized_state = process_instance.workflow_state_json
    if serialized_state is None:
        return process_instance

    workflow = _restore_workflow(serialized_state)
    timestamp = _resolve_timestamp(occurred_at)
    process_instance.spiff_serializer_version = _WORKFLOW_STATE_SERIALIZER_VERSION
    _persist_workflow_state(
        session,
        process_instance,
        workflow,
        occurred_at=timestamp,
    )
    session.flush()
    return process_instance


def _load_process_definition(
    session: Session,
    *,
    tenant_id: str,
    bpmn_process_definition_id: int,
) -> BpmnProcessDefinitionModel:
    process_definition = session.scalar(
        select(BpmnProcessDefinitionModel).where(
            BpmnProcessDefinitionModel.m8f_tenant_id == tenant_id,
            BpmnProcessDefinitionModel.id == bpmn_process_definition_id,
        )
    )
    if process_definition is None:
        raise NotFoundError(
            "BPMN process definition "
            f"{bpmn_process_definition_id} was not found for tenant {tenant_id}"
        )
    return process_definition


def _resolve_or_create_bpmn_process(
    session: Session,
    *,
    tenant_id: str,
    process_definition: BpmnProcessDefinitionModel,
    process_identifier: str,
) -> BpmnProcessModel:
    # A stored definition can be reused across many process instances, but each
    # instance needs its own runtime BPMN process row and workflow-state blob.
    bpmn_process = BpmnProcessModel(
        m8f_tenant_id=tenant_id,
        guid=None,
        bpmn_process_definition_id=process_definition.id,
        top_level_process_id=None,
        direct_parent_process_id=None,
        properties_json={"root": process_identifier},
        json_data_hash=JsonDataModel.create_or_update_from_payload(
            session,
            {
                "bpmn_process_definition_id": process_definition.id,
                "process_identifier": process_identifier,
            },
        ),
        start_in_seconds=None,
        end_in_seconds=None,
    )
    session.add(bpmn_process)
    session.flush()
    return bpmn_process


def _ensure_bpmn_version_snapshot(
    session: Session,
    *,
    tenant_id: str,
    process_model_identifier: str,
    bpmn_xml_text: str | bytes | None,
    occurred_at: int,
) -> ProcessModelBpmnVersionModel:
    if bpmn_xml_text is None:
        raise ValidationError("Cannot create a BPMN version snapshot without BPMN XML")

    normalized_bpmn_xml = (
        bpmn_xml_text.decode("utf-8")
        if isinstance(bpmn_xml_text, bytes)
        else bpmn_xml_text
    )
    bpmn_xml_hash = hashlib.sha256(
        normalized_bpmn_xml.encode("utf-8")
    ).hexdigest()
    snapshot = session.scalar(
        select(ProcessModelBpmnVersionModel).where(
            ProcessModelBpmnVersionModel.m8f_tenant_id == tenant_id,
            ProcessModelBpmnVersionModel.process_model_identifier
            == process_model_identifier,
            ProcessModelBpmnVersionModel.bpmn_xml_hash == bpmn_xml_hash,
        )
    )
    if snapshot is None:
        snapshot = ProcessModelBpmnVersionModel(
            m8f_tenant_id=tenant_id,
            process_model_identifier=process_model_identifier,
            bpmn_xml_hash=bpmn_xml_hash,
            bpmn_xml_file_contents=normalized_bpmn_xml,
            created_at_in_seconds=occurred_at,
        )
        session.add(snapshot)
        session.flush()
    return snapshot


def _select_process_identifier(
    bpmn_xml: str | bytes,
    *,
    bpmn_process_id: str | None,
) -> str:
    parser = _RuntimeSpiffBpmnParser(validator=None)
    parser.add_bpmn_str(
        _coerce_xml_bytes(bpmn_xml),
        filename="definition-process-selector.bpmn",
    )
    process_ids = parser.get_process_ids()
    if bpmn_process_id is None:
        if len(process_ids) != 1:
            raise ValidationError(
                "A BPMN process id must be supplied when the definition contains "
                "multiple executable processes"
            )
        return process_ids[0]
    if bpmn_process_id not in process_ids:
        raise NotFoundError(
            f"BPMN process id {bpmn_process_id!r} was not found in the definition"
        )
    return bpmn_process_id


def _build_workflow(
    *,
    bpmn_xml: str | bytes,
    dmn_xml: str | bytes | None,
    bpmn_process_id: str | None,
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
    return BpmnWorkflow(spec, subprocess_specs)


def _restore_workflow(serialized_state: str) -> BpmnWorkflow:
    return _WORKFLOW_SERIALIZER.deserialize_json(serialized_state)


def _persist_workflow_state(
    session: Session,
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
    *,
    occurred_at: int,
) -> None:
    serialized_state = _WORKFLOW_SERIALIZER.serialize_json(workflow)
    process_instance.spiff_serializer_version = _WORKFLOW_STATE_SERIALIZER_VERSION
    serialized_workflow = _serialize_workflow_dict(workflow)
    _sync_process_definition_from_workflow(
        session,
        process_instance=process_instance,
        serialized_workflow=serialized_workflow,
        occurred_at=occurred_at,
    )
    _sync_bpmn_process_from_workflow(
        session,
        process_instance=process_instance,
        serialized_workflow=serialized_workflow,
        serialized_state=serialized_state,
    )
    _sync_task_models_from_workflow(
        session,
        process_instance=process_instance,
        serialized_workflow=serialized_workflow,
        occurred_at=occurred_at,
    )


def _serialize_workflow_dict(workflow: BpmnWorkflow) -> dict[str, Any]:
    serialized_workflow = _WORKFLOW_SERIALIZER.to_dict(workflow)
    if not isinstance(serialized_workflow, dict):
        raise ValidationError("Workflow serializer did not produce a dictionary")
    if serialized_workflow.get("serializer_version") is None:
        serialized_workflow["serializer_version"] = _WORKFLOW_STATE_SERIALIZER_VERSION
    return serialized_workflow


def _sync_process_definition_from_workflow(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    serialized_workflow: Mapping[str, Any],
    occurred_at: int,
) -> None:
    process_definition = process_instance.bpmn_process_definition
    if process_definition is None:
        return

    serialized_spec = serialized_workflow.get("spec")
    if not isinstance(serialized_spec, Mapping):
        raise ValidationError("Serialized workflow is missing the top-level spec")
    serialized_process_identifier = serialized_spec.get("name")
    if not isinstance(serialized_process_identifier, str):
        raise ValidationError("Serialized workflow spec is missing its name")

    task_specs = serialized_spec.get("task_specs")
    spec_payload = dict(serialized_spec)
    spec_payload.pop("task_specs", None)

    if process_definition.explicit_process_model_identifier is None:
        process_definition.process_model_identifier = (
            process_instance.process_model_identifier
        )
    process_definition.bpmn_identifier = serialized_process_identifier
    existing_properties = dict(process_definition.properties_json or {})
    merged_properties = dict(spec_payload)
    for key, value in existing_properties.items():
        if key.startswith("__m8f_") or key not in merged_properties:
            merged_properties[key] = value
    process_definition.properties_json = merged_properties
    process_definition.updated_at_in_seconds = occurred_at

    if not isinstance(task_specs, Mapping):
        raise ValidationError("Serialized workflow spec is missing task specs")
    for task_name, task_spec_payload in task_specs.items():
        if not isinstance(task_spec_payload, Mapping):
            continue
        _upsert_task_definition_from_payload(
            session,
            tenant_id=process_instance.m8f_tenant_id,
            process_definition_id=process_definition.id,
            task_identifier=str(task_name),
            task_spec_payload=task_spec_payload,
            occurred_at=occurred_at,
        )


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
        dict(serialized_data)
        if isinstance(serialized_data, Mapping)
        else {}
    )
    persisted_workflow_data[WORKFLOW_STATE_JSON_DATA_KEY] = serialized_state
    bpmn_process.json_data_hash = JsonDataModel.create_or_update_from_payload(
        session,
        persisted_workflow_data,
    )


def _sync_task_models_from_workflow(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    serialized_workflow: Mapping[str, Any],
    occurred_at: int,
) -> None:
    serialized_tasks = serialized_workflow.get("tasks")
    if not isinstance(serialized_tasks, Mapping):
        raise ValidationError("Serialized workflow is missing task rows")
    if process_instance.bpmn_process_definition_id is None:
        raise ValidationError(
            "Process instance is missing a BPMN process definition"
        )

    task_definitions = {
        task_definition.bpmn_identifier: task_definition
        for task_definition in session.scalars(
            select(TaskDefinitionModel).where(
                TaskDefinitionModel.m8f_tenant_id == process_instance.m8f_tenant_id,
                TaskDefinitionModel.bpmn_process_definition_id
                == process_instance.bpmn_process_definition_id,
            )
        ).all()
    }

    for task_guid, task_payload in serialized_tasks.items():
        if not isinstance(task_payload, Mapping):
            continue
        task_spec_identifier = task_payload.get("task_spec")
        if not isinstance(task_spec_identifier, str):
            raise ValidationError(
                f"Serialized task {task_guid!r} is missing its task spec identifier"
            )
        task_definition = task_definitions.get(task_spec_identifier)
        if task_definition is None:
            raise ValidationError(
                f"Task definition {task_spec_identifier!r} was not found for "
                f"process definition {process_instance.bpmn_process_definition_id}"
            )
        _upsert_task_model_from_payload(
            session,
            process_instance=process_instance,
            task_definition=task_definition,
            task_payload=task_payload,
            occurred_at=occurred_at,
        )


def _seed_runtime_definitions(
    session: Session,
    *,
    tenant_id: str,
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
    occurred_at: int,
) -> None:
    if process_instance.bpmn_process_definition_id is None:
        raise ValidationError(
            "Process instance is missing a BPMN process definition"
        )

    _sync_process_definition_from_workflow(
        session,
        process_instance=process_instance,
        serialized_workflow=_serialize_workflow_dict(workflow),
        occurred_at=occurred_at,
    )


def _upsert_task_definition(
    session: Session,
    *,
    tenant_id: str,
    process_definition_id: int,
    task_spec: object,
    occurred_at: int,
) -> TaskDefinitionModel:
    task_spec_payload = _WORKFLOW_SERIALIZER.to_dict(task_spec)
    if not isinstance(task_spec_payload, Mapping):
        raise ValidationError("Task spec serializer did not produce a dictionary")
    task_identifier = getattr(task_spec, "name", None)
    if not isinstance(task_identifier, str) or not task_identifier:
        raise ValidationError("Task spec is missing its serialized identifier")
    return _upsert_task_definition_from_payload(
        session,
        tenant_id=tenant_id,
        process_definition_id=process_definition_id,
        task_identifier=task_identifier,
        task_spec_payload=task_spec_payload,
        occurred_at=occurred_at,
    )


def _upsert_task_definition_from_payload(
    session: Session,
    *,
    tenant_id: str,
    process_definition_id: int,
    task_identifier: str,
    task_spec_payload: Mapping[str, Any],
    occurred_at: int,
) -> TaskDefinitionModel:
    task_definition = session.scalar(
        select(TaskDefinitionModel).where(
            TaskDefinitionModel.m8f_tenant_id == tenant_id,
            TaskDefinitionModel.bpmn_process_definition_id == process_definition_id,
            TaskDefinitionModel.bpmn_identifier == task_identifier,
        )
    )
    serialized_payload = dict(task_spec_payload)
    task_name = serialized_payload.get("bpmn_name")
    task_typename = serialized_payload.get("typename")
    if not isinstance(task_typename, str) or not task_typename:
        raise ValidationError(
            f"Task definition {task_identifier!r} is missing its typename"
        )
    if task_definition is None:
        task_definition = TaskDefinitionModel(
            m8f_tenant_id=tenant_id,
            bpmn_process_definition_id=process_definition_id,
            bpmn_identifier=task_identifier,
            bpmn_name=task_name if isinstance(task_name, str) else None,
            typename=task_typename,
            properties_json=serialized_payload,
            created_at_in_seconds=occurred_at,
            updated_at_in_seconds=occurred_at,
        )
        session.add(task_definition)
    else:
        task_definition.bpmn_name = task_name if isinstance(task_name, str) else None
        task_definition.typename = task_typename
        task_definition.properties_json = serialized_payload
        task_definition.updated_at_in_seconds = occurred_at

    session.flush()
    return task_definition


def _process_start_authorization_metadata(
    *,
    process_definition: BpmnProcessDefinitionModel,
    requested_bpmn_process_id: str | None,
) -> dict[str, object]:
    return {
        "bpmn_process_definition_id": process_definition.id,
        "process_model_identifier": process_definition.process_model_identifier,
        "bpmn_identifier": process_definition.bpmn_identifier,
        "bpmn_name": process_definition.bpmn_name,
        "requested_bpmn_process_id": requested_bpmn_process_id,
    }


def _materialize_ready_manual_tasks(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
    occurred_at: int,
) -> list[HumanTaskModel]:
    ready_tasks = workflow.get_tasks(state=TaskState.READY, manual=True)
    materialized: list[HumanTaskModel] = []
    for task in ready_tasks:
        materialized.append(
            _materialize_single_manual_task(
                session,
                process_instance=process_instance,
                workflow=workflow,
                task=task,
                occurred_at=occurred_at,
            )
        )
    return materialized


def _materialize_single_manual_task(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
    task: object,
    occurred_at: int,
) -> HumanTaskModel:
    task_id = str(task.id)
    if process_instance.bpmn_process is None:
        raise ValidationError("Process instance is missing a BPMN process")
    if process_instance.bpmn_process_definition_id is None:
        raise ValidationError(
            "Process instance is missing a BPMN process definition"
        )
    if process_instance.process_initiator_id is None:
        raise ValidationError("Process instance is missing a process initiator")

    task_definition = _upsert_task_definition(
        session,
        tenant_id=process_instance.m8f_tenant_id,
        process_definition_id=process_instance.bpmn_process_definition_id,
        task_spec=task.task_spec,
        occurred_at=occurred_at,
    )
    task_model = _upsert_task_model(
        session,
        process_instance=process_instance,
        task_definition=task_definition,
        task=task,
        occurred_at=occurred_at,
    )
    human_task = _upsert_human_task(
        session,
        process_instance=process_instance,
        task_model=task_model,
        task=task,
        task_definition=task_definition,
        occurred_at=occurred_at,
    )
    _sync_human_task_assignments(
        session,
        process_instance=process_instance,
        human_task=human_task,
        task=task,
    )
    task_model.future_task = _upsert_future_task(
        session,
        tenant_id=process_instance.m8f_tenant_id,
        guid=task_id,
        occurred_at=occurred_at,
    )
    session.flush()
    return human_task


def _upsert_future_task(
    session: Session,
    *,
    tenant_id: str,
    guid: str,
    occurred_at: int,
) -> FutureTaskModel:
    future_task = session.get(FutureTaskModel, guid)
    if future_task is None:
        future_task = FutureTaskModel(
            m8f_tenant_id=tenant_id,
            guid=guid,
            run_at_in_seconds=occurred_at,
            queued_to_run_at_in_seconds=occurred_at,
            completed=False,
            archived_for_process_instance_status=False,
            updated_at_in_seconds=occurred_at,
        )
        session.add(future_task)
    else:
        future_task.run_at_in_seconds = occurred_at
        future_task.queued_to_run_at_in_seconds = occurred_at
        future_task.completed = False
        future_task.archived_for_process_instance_status = False
        future_task.updated_at_in_seconds = occurred_at

    session.flush()
    return future_task


def _upsert_task_model(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    task_definition: TaskDefinitionModel,
    task: object,
    occurred_at: int,
) -> TaskModel:
    serialized_task = _WORKFLOW_SERIALIZER.to_dict(task)
    if not isinstance(serialized_task, Mapping):
        raise ValidationError("Task serializer did not produce a dictionary")
    return _upsert_task_model_from_payload(
        session,
        process_instance=process_instance,
        task_definition=task_definition,
        task_payload=serialized_task,
        occurred_at=occurred_at,
    )


def _upsert_task_model_from_payload(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    task_definition: TaskDefinitionModel,
    task_payload: Mapping[str, Any],
    occurred_at: int,
) -> TaskModel:
    task_guid = task_payload.get("id")
    if not isinstance(task_guid, str) or not task_guid:
        raise ValidationError("Serialized task is missing its guid")
    task_model = session.get(TaskModel, task_guid)
    properties_json = dict(task_payload)
    task_data = properties_json.pop("data", {})
    json_data_hash = JsonDataModel.create_or_update_from_payload(
        session,
        task_data if isinstance(task_data, Mapping) else {},
    )
    python_env_data_hash = JsonDataModel.create_or_update_from_payload(session, {})

    serialized_state = properties_json.get("state")
    if not isinstance(serialized_state, int):
        raise ValidationError(f"Serialized task {task_guid!r} is missing its state")
    task_state_name = TaskState.get_name(serialized_state)
    runtime_info = {
        "spiff_task_id": task_guid,
        "spiff_task_state": task_state_name,
        "manual": task_definition.properties_json.get("manual", False),
        "lane": task_definition.properties_json.get("lane"),
    }
    task_is_terminal = task_state_name in {"COMPLETED", "CANCELLED", "ERROR"}

    if task_model is None:
        task_model = TaskModel(
            m8f_tenant_id=process_instance.m8f_tenant_id,
            guid=task_guid,
            bpmn_process_id=process_instance.bpmn_process_id
            if process_instance.bpmn_process_id is not None
            else _raise_value_error("Process instance is missing a BPMN process"),
            process_instance_id=process_instance.id,
            task_definition_id=task_definition.id,
            state=task_state_name,
            properties_json=properties_json,
            json_data_hash=json_data_hash,
            python_env_data_hash=python_env_data_hash,
            runtime_info=runtime_info,
            start_in_seconds=float(occurred_at),
            end_in_seconds=float(occurred_at) if task_is_terminal else None,
        )
        session.add(task_model)
    else:
        task_model.bpmn_process_id = (
            process_instance.bpmn_process_id
            if process_instance.bpmn_process_id is not None
            else task_model.bpmn_process_id
        )
        task_model.process_instance_id = process_instance.id
        task_model.task_definition_id = task_definition.id
        task_model.state = task_state_name
        task_model.properties_json = properties_json
        task_model.json_data_hash = json_data_hash
        task_model.python_env_data_hash = python_env_data_hash
        task_model.runtime_info = runtime_info
        task_model.start_in_seconds = (
            float(occurred_at)
            if task_model.start_in_seconds is None
            else task_model.start_in_seconds
        )
        task_model.end_in_seconds = (
            float(occurred_at) if task_is_terminal else None
        )

    session.flush()
    return task_model


def _upsert_human_task(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    task_model: TaskModel,
    task: object,
    task_definition: TaskDefinitionModel,
    occurred_at: int,
) -> HumanTaskModel:
    human_task = session.scalar(
        select(HumanTaskModel).where(
            HumanTaskModel.m8f_tenant_id == process_instance.m8f_tenant_id,
            HumanTaskModel.process_instance_id == process_instance.id,
            HumanTaskModel.task_guid == task_model.guid,
        )
    )
    lane_name = getattr(task.task_spec, "lane", None)
    lane_group_id = _lane_group_id(session, lane_name)
    human_task_payload = _human_task_payload(task, task_definition)
    if human_task is None:
        human_task = HumanTaskModel(
            m8f_tenant_id=process_instance.m8f_tenant_id,
            process_instance_id=process_instance.id,
            task_id=task_model.guid,
            task_guid=task_model.guid,
            lane_assignment_id=lane_group_id,
            completed_by_user_id=None,
            actual_owner_id=None,
            form_file_name=None,
            ui_form_file_name=None,
            updated_at_in_seconds=occurred_at,
            created_at_in_seconds=occurred_at,
            task_name=task.task_spec.name,
            task_title=getattr(task.task_spec, "bpmn_name", None),
            task_type=task_definition.typename,
            task_status="READY",
            process_model_display_name=process_instance.process_model_display_name,
            bpmn_process_identifier=process_instance.process_model_identifier,
            lane_name=lane_name,
            json_metadata=human_task_payload,
            completed=False,
        )
        session.add(human_task)
    else:
        human_task.task_id = task_model.guid
        human_task.task_guid = task_model.guid
        human_task.lane_assignment_id = lane_group_id
        human_task.updated_at_in_seconds = occurred_at
        human_task.task_name = task.task_spec.name
        human_task.task_title = getattr(task.task_spec, "bpmn_name", None)
        human_task.task_type = task_definition.typename
        human_task.task_status = "READY"
        human_task.process_model_display_name = (
            process_instance.process_model_display_name
        )
        human_task.bpmn_process_identifier = process_instance.process_model_identifier
        human_task.lane_name = lane_name
        human_task.json_metadata = human_task_payload
        human_task.completed = False
        human_task.completed_by_user_id = None
        human_task.actual_owner_id = None

    process_instance.task_updated_at_in_seconds = occurred_at
    session.flush()
    return human_task


def _lane_group_id(session: Session, lane_name: str | None) -> int | None:
    if lane_name is None:
        return None
    if re.match(r"(process.?)initiator", lane_name, re.IGNORECASE):
        return None

    lane_group_id = resolve_lane_assignment_id(lane_name)
    lane_group = session.get(GroupModel, lane_group_id)
    if lane_group is None:
        lane_group = GroupModel(
            id=lane_group_id,
            name=lane_name,
            identifier=lane_name,
            source_is_open_id=False,
        )
        session.add(lane_group)
        session.flush()
    return lane_group.id


def _sync_human_task_assignments(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    human_task: HumanTaskModel,
    task: object,
) -> None:
    existing_assignments = {
        assignment.user_id for assignment in human_task.human_task_users
    }
    for user, added_by in _resolve_human_task_assignments(
        session,
        process_instance=process_instance,
        task=task,
    ):
        if user.id in existing_assignments:
            continue
        session.add(
            HumanTaskUserModel(
                m8f_tenant_id=process_instance.m8f_tenant_id,
                human_task_id=human_task.id,
                user_id=user.id,
                added_by=added_by.value,
            )
        )
    session.flush()


def _resolve_human_task_assignments(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    task: object,
) -> list[tuple[UserModel, HumanTaskUserAddedBy]]:
    lane_name = getattr(task.task_spec, "lane", None)
    if not lane_name or re.match(r"(process.?)initiator", lane_name, re.IGNORECASE):
        initiator = session.get(UserModel, process_instance.process_initiator_id)
        if initiator is None:
            raise NotFoundError(
                "Process initiator was not found for process instance "
                f"{process_instance.id}"
            )
        return [(initiator, HumanTaskUserAddedBy.process_initiator)]

    lane_owners = getattr(task, "data", {}).get("lane_owners", {})
    if not isinstance(lane_owners, dict) or lane_name not in lane_owners:
        raise NotFoundError(
            f"Task {task.task_spec.name} does not define lane owners for "
            f"lane {lane_name!r}"
        )

    resolved_users: list[tuple[UserModel, HumanTaskUserAddedBy]] = []
    seen_user_ids: set[int] = set()
    for identifier in lane_owners[lane_name]:
        for user in _find_users_by_identifier(
            session,
            tenant_id=process_instance.m8f_tenant_id,
            identifier=identifier,
        ):
            if user.id in seen_user_ids:
                continue
            seen_user_ids.add(user.id)
            resolved_users.append((user, HumanTaskUserAddedBy.lane_owner))

    if not resolved_users:
        raise NotFoundError(
            f"No users were resolved for lane {lane_name!r} on task "
            f"{task.task_spec.name}"
        )

    return resolved_users


def _find_users_by_identifier(
    session: Session,
    *,
    tenant_id: str,
    identifier: str,
) -> list[UserModel]:
    normalized = identifier.strip()
    candidates: list[UserModel] = []
    stmt = select(UserModel).where(
        or_(
            UserModel.username == normalized,
            UserModel.email == normalized,
            UserModel.service_id == normalized,
        )
    )
    candidates.extend(session.scalars(stmt).all())

    if "@" in normalized:
        username, _tenant_suffix = normalized.split("@", 1)
        stmt = select(UserModel).where(UserModel.username == username)
        candidates.extend(session.scalars(stmt).all())

    if tenant_id:
        tenant_identifiers = tenant_identifiers_for(session, tenant_id)
        if tenant_identifiers:
            candidates = [
                user
                for user in candidates
                if user_belongs_to_tenant(user, tenant_identifiers)
            ]

    return candidates


def _human_task_payload(
    task: object, task_definition: TaskDefinitionModel
) -> dict[str, Any]:
    lane_owners = getattr(task, "data", {}).get("lane_owners")
    payload: dict[str, Any] = {
        "task_definition_properties": task_definition.properties_json,
    }
    if lane_owners is not None:
        payload["lane_owners"] = lane_owners
    return payload


def _update_process_instance_status_from_workflow(
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
    *,
    occurred_at: int,
) -> None:
    if workflow.completed:
        process_instance.status = ProcessInstanceStatus.complete.value
        process_instance.end_in_seconds = occurred_at
        process_instance.updated_at_in_seconds = occurred_at
        if process_instance.bpmn_process is not None:
            process_instance.bpmn_process.end_in_seconds = float(occurred_at)
        _archive_completed_workflow_runtime_state(
            process_instance, occurred_at=occurred_at
        )
        return

    if workflow.get_tasks(state=TaskState.READY, manual=True):
        process_instance.status = ProcessInstanceStatus.user_input_required.value
    elif workflow.get_tasks(state=TaskState.WAITING):
        process_instance.status = ProcessInstanceStatus.waiting.value
    else:
        process_instance.status = ProcessInstanceStatus.running.value
    process_instance.end_in_seconds = None
    process_instance.updated_at_in_seconds = occurred_at
    process_instance.task_updated_at_in_seconds = occurred_at
    if process_instance.bpmn_process is not None:
        if process_instance.bpmn_process.start_in_seconds is None:
            process_instance.bpmn_process.start_in_seconds = float(
                process_instance.start_in_seconds or occurred_at
            )
        process_instance.bpmn_process.end_in_seconds = None


def _archive_completed_workflow_runtime_state(
    process_instance: ProcessInstanceModel,
    *,
    occurred_at: int,
) -> None:
    for task in process_instance.tasks:
        if task.future_task is None:
            continue
        task.future_task.completed = True
        task.future_task.archived_for_process_instance_status = True
        task.future_task.updated_at_in_seconds = occurred_at


def _get_ready_human_tasks(
    process_instance: ProcessInstanceModel,
) -> list[HumanTaskModel]:
    return [
        human_task
        for human_task in process_instance.human_tasks
        if not human_task.completed and human_task.task_status == "READY"
    ]


def _apply_metadata_to_workflow(
    workflow: BpmnWorkflow,
    metadata: Mapping[str, Any],
) -> None:
    if not metadata:
        return
    workflow.data.update(metadata)
    workflow.task_tree.data.update(metadata)


def _apply_metadata_to_task(
    workflow: BpmnWorkflow,
    task: object,
    metadata: Mapping[str, Any],
) -> None:
    if not metadata:
        return
    workflow.data.update(metadata)
    task.set_data(**metadata)


def _load_process_instance_metadata_payload(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for row in get_process_instance_metadata(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance_id,
    ):
        payload[row.key] = _coerce_metadata_value(row.value)
    return payload


def _coerce_metadata_value(value: str) -> Any:
    normalized = value.strip()
    lower = normalized.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in {"none", "null"}:
        return None
    try:
        return ast.literal_eval(normalized)
    except Exception:
        return value


def _hash_payload(payload: Mapping[str, Any]) -> str:
    normalized = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _coerce_xml_bytes(xml: str | bytes) -> bytes:
    if isinstance(xml, bytes):
        return xml
    return xml.encode("utf-8")


def _load_process_instance(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
) -> ProcessInstanceModel:
    process_instance = session.scalar(
        select(ProcessInstanceModel).where(
            ProcessInstanceModel.m8f_tenant_id == tenant_id,
            ProcessInstanceModel.id == process_instance_id,
        )
    )
    if process_instance is None:
        raise NotFoundError(
            "Process instance "
            f"{process_instance_id} was not found for tenant {tenant_id}"
        )
    return process_instance


def _resolve_timestamp(timestamp_in_seconds: int | None) -> int:
    return (
        timestamp_in_seconds if timestamp_in_seconds is not None else round(time.time())
    )


def _raise_value_error(message: str) -> None:
    raise ValidationError(message)
