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

from m8flow_bpmn_core.models.bpmn_process import BpmnProcessModel
from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_bpmn_core.models.future_task import FutureTaskModel
from m8flow_bpmn_core.models.human_task import HumanTaskModel
from m8flow_bpmn_core.models.human_task_user import (
    HumanTaskUserAddedBy,
    HumanTaskUserModel,
)
from m8flow_bpmn_core.models.process_instance import (
    ProcessInstanceModel,
    ProcessInstanceStatus,
)
from m8flow_bpmn_core.models.process_instance_event import ProcessInstanceEventType
from m8flow_bpmn_core.models.task import TaskModel
from m8flow_bpmn_core.models.task_definition import TaskDefinitionModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.services.process_instances import (
    create_process_instance,
    get_process_instance_metadata,
    record_process_instance_event,
    upsert_process_instance_metadata,
)
from m8flow_bpmn_core.services.tenant_users import (
    tenant_identifiers_for,
    user_belongs_to_tenant,
)

_WORKFLOW_SERIALIZER = BpmnWorkflowSerializer(
    registry=BpmnWorkflowSerializer.configure(SPIFF_CONFIG)
)


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
    """Return a stable lane identifier without a groups table."""
    normalized_lane = lane_name.strip().lower()
    digest = hashlib.sha256(normalized_lane.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


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
        raise ValueError("Cannot initialize a terminal process instance")
    if process_instance.workflow_state_json is not None:
        raise ValueError("Process instance workflow already initialized")

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

    _persist_workflow_state(process_instance, workflow)
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
    process_definition = _load_process_definition(
        session,
        tenant_id=tenant_id,
        bpmn_process_definition_id=bpmn_process_definition_id,
    )
    if process_definition.source_bpmn_xml is None:
        raise ValueError(
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
        process_model_identifier=process_definition.bpmn_identifier,
        process_model_display_name=(
            process_definition.bpmn_name or process_definition.bpmn_identifier
        ),
        process_initiator_id=process_initiator_id,
        bpmn_process_definition_id=process_definition.id,
        bpmn_process_id=bpmn_process.id,
        summary=summary,
        process_version=process_version,
        created_at_in_seconds=started_at_in_seconds,
        updated_at_in_seconds=started_at_in_seconds,
    )

    metadata_timestamp = _resolve_timestamp(started_at_in_seconds)
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

    _persist_workflow_state(process_instance, workflow)
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
        raise LookupError(
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
    for bpmn_process in process_definition.bpmn_processes:
        if bpmn_process.properties_json.get("root") == process_identifier:
            return bpmn_process

    bpmn_process = BpmnProcessModel(
        m8f_tenant_id=tenant_id,
        guid=None,
        bpmn_process_definition_id=process_definition.id,
        top_level_process_id=None,
        direct_parent_process_id=None,
        properties_json={"root": process_identifier},
        json_data_hash=_hash_payload(
            {
                "bpmn_process_definition_id": process_definition.id,
                "process_identifier": process_identifier,
            }
        ),
    )
    session.add(bpmn_process)
    session.flush()
    return bpmn_process


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
            raise ValueError(
                "A BPMN process id must be supplied when the definition contains "
                "multiple executable processes"
            )
        return process_ids[0]
    if bpmn_process_id not in process_ids:
        raise ValueError(
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
            raise ValueError(
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
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
) -> None:
    process_instance.workflow_state_json = _WORKFLOW_SERIALIZER.serialize_json(workflow)


def _seed_runtime_definitions(
    session: Session,
    *,
    tenant_id: str,
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
    occurred_at: int,
) -> None:
    if process_instance.bpmn_process_definition_id is None:
        raise ValueError("Process instance is missing a BPMN process definition")

    for task_spec in workflow.spec.task_specs.values():
        bpmn_identifier = getattr(task_spec, "bpmn_id", None)
        if not bpmn_identifier:
            continue
        _upsert_task_definition(
            session,
            tenant_id=tenant_id,
            process_definition_id=process_instance.bpmn_process_definition_id,
            task_spec=task_spec,
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
    bpmn_identifier = getattr(task_spec, "bpmn_id", None)
    if not bpmn_identifier:
        raise ValueError("Task spec is missing a BPMN identifier")

    task_definition = session.scalar(
        select(TaskDefinitionModel).where(
            TaskDefinitionModel.m8f_tenant_id == tenant_id,
            TaskDefinitionModel.bpmn_process_definition_id == process_definition_id,
            TaskDefinitionModel.bpmn_identifier == bpmn_identifier,
        )
    )
    task_properties = _task_definition_properties(task_spec)
    if task_definition is None:
        task_definition = TaskDefinitionModel(
            m8f_tenant_id=tenant_id,
            bpmn_process_definition_id=process_definition_id,
            bpmn_identifier=bpmn_identifier,
            bpmn_name=getattr(task_spec, "bpmn_name", None),
            typename=task_spec.__class__.__name__,
            properties_json=task_properties,
            created_at_in_seconds=occurred_at,
            updated_at_in_seconds=occurred_at,
        )
        session.add(task_definition)
    else:
        task_definition.bpmn_name = getattr(task_spec, "bpmn_name", None)
        task_definition.typename = task_spec.__class__.__name__
        task_definition.properties_json = task_properties
        task_definition.updated_at_in_seconds = occurred_at

    session.flush()
    return task_definition


def _task_definition_properties(task_spec: object) -> dict[str, Any]:
    extensions = getattr(task_spec, "extensions", {})
    properties: dict[str, Any] = {
        "description": getattr(task_spec, "description", None),
        "lane": getattr(task_spec, "lane", None),
        "manual": getattr(task_spec, "manual", False),
        "extensions": extensions,
    }
    script = getattr(task_spec, "script", None)
    if script is not None:
        properties["script"] = script
    return properties


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
        raise ValueError("Process instance is missing a BPMN process")
    if process_instance.bpmn_process_definition_id is None:
        raise ValueError("Process instance is missing a BPMN process definition")
    if process_instance.process_initiator_id is None:
        raise ValueError("Process instance is missing a process initiator")

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
    task_guid = str(task.id)
    task_model = session.get(TaskModel, task_guid)
    properties_json = {
        "task_spec": task.task_spec.name,
        "task_spec_type": task.task_spec.__class__.__name__,
        "lane": getattr(task.task_spec, "lane", None),
        "extensions": getattr(task.task_spec, "extensions", {}),
        "task_definition_properties": task_definition.properties_json,
    }
    json_data_hash = _hash_payload(
        {
            "task_guid": task_guid,
            "task_spec": task.task_spec.name,
            "task_data": getattr(task, "data", {}),
        }
    )
    python_env_data_hash = _hash_payload(
        {
            "process_instance_id": process_instance.id,
            "task_guid": task_guid,
            "task_spec": task.task_spec.name,
        }
    )
    runtime_info = {
        "spiff_task_id": task_guid,
        "spiff_task_state": TaskState.get_name(task.state),
        "manual": task.task_spec.manual,
        "lane": getattr(task.task_spec, "lane", None),
    }

    if task_model is None:
        task_model = TaskModel(
            m8f_tenant_id=process_instance.m8f_tenant_id,
            guid=task_guid,
            bpmn_process_id=process_instance.bpmn_process_id
            if process_instance.bpmn_process_id is not None
            else _raise_value_error("Process instance is missing a BPMN process"),
            process_instance_id=process_instance.id,
            task_definition_id=task_definition.id,
            state=TaskState.get_name(task.state),
            properties_json=properties_json,
            json_data_hash=json_data_hash,
            python_env_data_hash=python_env_data_hash,
            runtime_info=runtime_info,
            start_in_seconds=occurred_at,
            end_in_seconds=None,
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
        task_model.state = TaskState.get_name(task.state)
        task_model.properties_json = properties_json
        task_model.json_data_hash = json_data_hash
        task_model.python_env_data_hash = python_env_data_hash
        task_model.runtime_info = runtime_info
        task_model.start_in_seconds = (
            occurred_at
            if task_model.start_in_seconds is None
            else task_model.start_in_seconds
        )
        task_model.end_in_seconds = None

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
    human_task_payload = _human_task_payload(task, task_definition)
    if human_task is None:
        human_task = HumanTaskModel(
            m8f_tenant_id=process_instance.m8f_tenant_id,
            process_instance_id=process_instance.id,
            task_guid=task_model.guid,
            lane_assignment_id=(
                resolve_lane_assignment_id(lane_name) if lane_name else None
            ),
            completed_by_user_id=None,
            actual_owner_id=None,
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
        human_task.task_guid = task_model.guid
        human_task.lane_assignment_id = (
            resolve_lane_assignment_id(lane_name) if lane_name else None
        )
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

    session.flush()
    return human_task


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
            raise LookupError(
                "Process initiator was not found for process instance "
                f"{process_instance.id}"
            )
        return [(initiator, HumanTaskUserAddedBy.process_initiator)]

    lane_owners = getattr(task, "data", {}).get("lane_owners", {})
    if not isinstance(lane_owners, dict) or lane_name not in lane_owners:
        raise LookupError(
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
        raise LookupError(
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
        raise LookupError(
            "Process instance "
            f"{process_instance_id} was not found for tenant {tenant_id}"
        )
    return process_instance


def _resolve_timestamp(timestamp_in_seconds: int | None) -> int:
    return (
        timestamp_in_seconds if timestamp_in_seconds is not None else round(time.time())
    )


def _raise_value_error(message: str) -> None:
    raise ValueError(message)
