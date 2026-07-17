from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from SpiffWorkflow.bpmn.script_engine.python_engine import PythonScriptEngine
from SpiffWorkflow.bpmn.serializer.workflow import BpmnWorkflowSerializer
from SpiffWorkflow.bpmn.specs.event_definitions.timer import TimerEventDefinition
from SpiffWorkflow.bpmn.workflow import BpmnWorkflow
from SpiffWorkflow.dmn.specs.model import DecisionTable
from SpiffWorkflow.spiff.parser.process import SpiffBpmnParser
from SpiffWorkflow.spiff.serializer.config import SPIFF_CONFIG
from SpiffWorkflow.util.task import TaskState
from sqlalchemy import or_, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from m8flow_bpmn_core.errors import (
    InvalidStateError,
    NotFoundError,
    ServiceTaskExecutionError,
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
from m8flow_bpmn_core.models.scheduler_job import (
    SchedulerJobModel,
    SchedulerJobType,
)
from m8flow_bpmn_core.models.task import TaskModel
from m8flow_bpmn_core.models.task_definition import TaskDefinitionModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.models.user_group_assignment import UserGroupAssignmentModel
from m8flow_bpmn_core.services.authorization import (
    PROCESS_START_COMMAND,
    require_command_authorization,
)
from m8flow_bpmn_core.services.process_instances import (
    create_process_instance,
    error_process_instance,
    get_process_instance_metadata,
    record_process_instance_event,
    upsert_process_instance_metadata,
)
from m8flow_bpmn_core.services.scheduler_jobs import (
    build_scheduler_job_key,
    delete_scheduler_job,
    upsert_scheduler_job,
)
from m8flow_bpmn_core.services.service_tasks import (
    ServiceTaskContext,
    ServiceTaskRequest,
    resolve_service_task_registry,
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
_FORCED_DUE_TIMER_EVENT_VALUE = "1970-01-01T00:00:00+00:00"


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
        service_task_context=_service_task_execution_context(
            tenant_id=tenant_id,
            process_instance_id=process_instance.id,
            process_definition_id=process_instance.bpmn_process_definition_id,
        ),
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
    _run_workflow_with_service_task_failure_handling(
        session,
        tenant_id=tenant_id,
        process_instance=process_instance,
        workflow=workflow,
        occurred_at=occurred_at,
        autonomous_failure_state_persistence=True,
    )

    return _finalize_initialized_process_instance_workflow(
        session,
        tenant_id=tenant_id,
        process_instance=process_instance,
        workflow=workflow,
        occurred_at=occurred_at,
    )


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
    process_instance, selected_process_id = _prepare_process_instance_from_definition(
        session,
        tenant_id=tenant_id,
        process_definition=process_definition,
        process_model_identifier=process_model_identifier,
        process_initiator_id=process_initiator_id,
        submission_metadata=submission_metadata,
        summary=summary,
        process_version=process_version,
        started_at_in_seconds=started_at_in_seconds,
        bpmn_process_id=bpmn_process_id,
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


def _initialize_process_instance_from_timer_start_definition(
    session: Session,
    *,
    tenant_id: str,
    process_definition_id: int,
    process_initiator_id: int,
    timer_start_task_spec_name: str,
    started_at_in_seconds: int | None = None,
) -> ProcessInstanceModel:
    ensure_user_belongs_to_tenant(
        session,
        tenant_id=tenant_id,
        user_id=process_initiator_id,
    )
    process_definition = _load_process_definition(
        session,
        tenant_id=tenant_id,
        bpmn_process_definition_id=process_definition_id,
    )
    process_model_identifier = (
        process_definition.process_model_identifier or str(process_definition.id)
    )
    process_instance, selected_process_id = _prepare_process_instance_from_definition(
        session,
        tenant_id=tenant_id,
        process_definition=process_definition,
        process_model_identifier=process_model_identifier,
        process_initiator_id=process_initiator_id,
        submission_metadata=None,
        summary=None,
        process_version=1,
        started_at_in_seconds=started_at_in_seconds,
        bpmn_process_id=None,
    )

    occurred_at = _resolve_timestamp(started_at_in_seconds)
    workflow = _build_workflow(
        bpmn_xml=process_definition.source_bpmn_xml,
        dmn_xml=process_definition.source_dmn_xml,
        bpmn_process_id=selected_process_id,
        service_task_context=_service_task_execution_context(
            tenant_id=tenant_id,
            process_instance_id=process_instance.id,
            process_definition_id=process_definition.id,
        ),
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
            process_instance_id=process_instance.id,
        ),
    )
    _run_workflow_with_service_task_failure_handling(
        session,
        tenant_id=tenant_id,
        process_instance=process_instance,
        workflow=workflow,
        occurred_at=occurred_at,
        autonomous_failure_state_persistence=True,
    )
    forced_due = _force_waiting_timer_start_task_due(
        workflow,
        timer_start_task_spec_name=timer_start_task_spec_name,
    )
    if forced_due:
        workflow.refresh_waiting_tasks()
        if not workflow.get_tasks(state=TaskState.READY, manual=True):
            _run_workflow_with_service_task_failure_handling(
                session,
                tenant_id=tenant_id,
                process_instance=process_instance,
                workflow=workflow,
                occurred_at=occurred_at,
                autonomous_failure_state_persistence=True,
            )

    return _finalize_initialized_process_instance_workflow(
        session,
        tenant_id=tenant_id,
        process_instance=process_instance,
        workflow=workflow,
        occurred_at=occurred_at,
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
    workflow = _restore_workflow(
        process_instance.workflow_state_json,
        service_task_context=_service_task_execution_context(
            tenant_id=tenant_id,
            process_instance_id=process_instance.id,
            process_definition_id=process_instance.bpmn_process_definition_id,
        ),
    )
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
    _run_workflow_with_service_task_failure_handling(
        session,
        tenant_id=tenant_id,
        process_instance=process_instance,
        workflow=workflow,
        occurred_at=occurred_at,
    )

    _persist_workflow_state(
        session,
        process_instance,
        workflow,
        occurred_at=occurred_at,
    )
    _sync_inactive_human_tasks(
        session,
        process_instance=process_instance,
        workflow=workflow,
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


def _prepare_process_instance_from_definition(
    session: Session,
    *,
    tenant_id: str,
    process_definition: BpmnProcessDefinitionModel,
    process_model_identifier: str,
    process_initiator_id: int,
    submission_metadata: Mapping[str, Any] | None,
    summary: str | None,
    process_version: int,
    started_at_in_seconds: int | None,
    bpmn_process_id: str | None,
) -> tuple[ProcessInstanceModel, str]:
    autonomous_baseline = _prepare_process_instance_baseline_in_independent_session(
        session,
        tenant_id=tenant_id,
        process_definition=process_definition,
        process_model_identifier=process_model_identifier,
        process_initiator_id=process_initiator_id,
        submission_metadata=submission_metadata,
        summary=summary,
        process_version=process_version,
        started_at_in_seconds=started_at_in_seconds,
        bpmn_process_id=bpmn_process_id,
    )
    if autonomous_baseline is not None:
        process_instance_id, selected_process_id = autonomous_baseline
        process_instance = _load_process_instance(
            session,
            tenant_id=tenant_id,
            process_instance_id=process_instance_id,
        )
        return process_instance, selected_process_id

    return _prepare_process_instance_from_definition_in_session(
        session,
        tenant_id=tenant_id,
        process_definition=process_definition,
        process_model_identifier=process_model_identifier,
        process_initiator_id=process_initiator_id,
        submission_metadata=submission_metadata,
        summary=summary,
        process_version=process_version,
        started_at_in_seconds=started_at_in_seconds,
        bpmn_process_id=bpmn_process_id,
    )


def _prepare_process_instance_baseline_in_independent_session(
    session: Session,
    *,
    tenant_id: str,
    process_definition: BpmnProcessDefinitionModel,
    process_model_identifier: str,
    process_initiator_id: int,
    submission_metadata: Mapping[str, Any] | None,
    summary: str | None,
    process_version: int,
    started_at_in_seconds: int | None,
    bpmn_process_id: str | None,
) -> tuple[int, str] | None:
    engine = _session_engine(session)
    if engine is None or process_definition.id is None:
        return None

    autonomous_session = Session(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    try:
        try:
            autonomous_process_definition = _load_process_definition(
                autonomous_session,
                tenant_id=tenant_id,
                bpmn_process_definition_id=process_definition.id,
            )
            ensure_user_belongs_to_tenant(
                autonomous_session,
                tenant_id=tenant_id,
                user_id=process_initiator_id,
            )
        except NotFoundError:
            autonomous_session.rollback()
            return None

        process_instance, selected_process_id = (
            _prepare_process_instance_from_definition_in_session(
                autonomous_session,
                tenant_id=tenant_id,
                process_definition=autonomous_process_definition,
                process_model_identifier=process_model_identifier,
                process_initiator_id=process_initiator_id,
                submission_metadata=submission_metadata,
                summary=summary,
                process_version=process_version,
                started_at_in_seconds=started_at_in_seconds,
                bpmn_process_id=bpmn_process_id,
            )
        )
        autonomous_session.commit()
        return process_instance.id, selected_process_id
    finally:
        autonomous_session.close()


def _prepare_process_instance_from_definition_in_session(
    session: Session,
    *,
    tenant_id: str,
    process_definition: BpmnProcessDefinitionModel,
    process_model_identifier: str,
    process_initiator_id: int,
    submission_metadata: Mapping[str, Any] | None,
    summary: str | None,
    process_version: int,
    started_at_in_seconds: int | None,
    bpmn_process_id: str | None,
) -> tuple[ProcessInstanceModel, str]:
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

    return process_instance, selected_process_id


def _finalize_initialized_process_instance_workflow(
    session: Session,
    *,
    tenant_id: str,
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
    occurred_at: int,
) -> ProcessInstanceModel:
    _persist_workflow_state(
        session,
        process_instance,
        workflow,
        occurred_at=occurred_at,
    )
    _sync_inactive_human_tasks(
        session,
        process_instance=process_instance,
        workflow=workflow,
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
        process_instance_id=process_instance.id,
        event_type=ProcessInstanceEventType.process_instance_created,
        timestamp=float(occurred_at),
        task_guid=ready_tasks[0].task_guid if ready_tasks else None,
        user_id=process_instance.process_initiator_id,
    )

    session.flush()
    return process_instance


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


def retry_errored_service_task_workflow_if_needed(
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

    timestamp = _resolve_timestamp(occurred_at)
    workflow = _restore_workflow(
        serialized_state,
        service_task_context=_service_task_execution_context(
            tenant_id=tenant_id,
            process_instance_id=process_instance.id,
            process_definition_id=process_instance.bpmn_process_definition_id,
        ),
    )
    metadata = _load_process_instance_metadata_payload(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance.id,
    )
    _apply_metadata_to_workflow(workflow, metadata)

    errored_service_task_ids = [
        task.id for task in _errored_service_tasks(workflow)
    ]
    if not errored_service_task_ids:
        return process_instance

    retry_task_data = dict(workflow.data)
    retry_task_data.update(metadata)
    for task_id in errored_service_task_ids:
        workflow.reset_from_task_id(task_id, data=retry_task_data)

    _run_workflow_with_service_task_failure_handling(
        session,
        tenant_id=tenant_id,
        process_instance=process_instance,
        workflow=workflow,
        occurred_at=timestamp,
        autonomous_failure_state_persistence=True,
    )
    _persist_workflow_state(
        session,
        process_instance,
        workflow,
        occurred_at=timestamp,
    )
    _sync_inactive_human_tasks(
        session,
        process_instance=process_instance,
        workflow=workflow,
        occurred_at=timestamp,
    )
    _materialize_ready_manual_tasks(
        session,
        process_instance=process_instance,
        workflow=workflow,
        occurred_at=timestamp,
    )
    _update_process_instance_status_from_workflow(
        process_instance,
        workflow,
        occurred_at=timestamp,
    )

    session.flush()
    return process_instance


def _refresh_waiting_process_instance_workflow(
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
    if process_instance.workflow_state_json is None:
        return process_instance
    if process_instance.has_terminal_status():
        return process_instance

    timestamp = _resolve_timestamp(occurred_at)
    workflow = _restore_workflow(
        process_instance.workflow_state_json,
        service_task_context=_service_task_execution_context(
            tenant_id=tenant_id,
            process_instance_id=process_instance.id,
            process_definition_id=process_instance.bpmn_process_definition_id,
        ),
    )
    workflow.refresh_waiting_tasks()
    _run_workflow_with_service_task_failure_handling(
        session,
        tenant_id=tenant_id,
        process_instance=process_instance,
        workflow=workflow,
        occurred_at=timestamp,
        autonomous_failure_state_persistence=True,
    )

    _persist_workflow_state(
        session,
        process_instance,
        workflow,
        occurred_at=timestamp,
    )
    _sync_inactive_human_tasks(
        session,
        process_instance=process_instance,
        workflow=workflow,
        occurred_at=timestamp,
    )
    _materialize_ready_manual_tasks(
        session,
        process_instance=process_instance,
        workflow=workflow,
        occurred_at=timestamp,
    )
    _update_process_instance_status_from_workflow(
        process_instance,
        workflow,
        occurred_at=timestamp,
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

    workflow = _restore_workflow(
        serialized_state,
        service_task_context=_service_task_execution_context(
            tenant_id=tenant_id,
            process_instance_id=process_instance.id,
            process_definition_id=process_instance.bpmn_process_definition_id,
        ),
    )
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


def _run_workflow_with_service_task_failure_handling(
    session: Session,
    *,
    tenant_id: str,
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
    occurred_at: int,
    autonomous_failure_state_persistence: bool = False,
) -> None:
    try:
        workflow.run_all(halt_on_manual=True)
    except ServiceTaskExecutionError:
        if autonomous_failure_state_persistence and (
            _persist_service_task_failure_state_in_independent_session(
                session,
                tenant_id=tenant_id,
                process_instance_id=process_instance.id,
                workflow=workflow,
                occurred_at=occurred_at,
            )
        ):
            raise
        _transition_process_instance_to_error_for_service_task_failure(
            session,
            tenant_id=tenant_id,
            process_instance=process_instance,
            workflow=workflow,
            occurred_at=occurred_at,
        )
        raise


def _persist_service_task_failure_state_in_independent_session(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
    workflow: BpmnWorkflow,
    occurred_at: int,
) -> bool:
    engine = _session_engine(session)
    if engine is None:
        return False

    autonomous_session = Session(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    try:
        try:
            process_instance = _load_process_instance(
                autonomous_session,
                tenant_id=tenant_id,
                process_instance_id=process_instance_id,
            )
        except NotFoundError:
            autonomous_session.rollback()
            return False

        _transition_process_instance_to_error_for_service_task_failure(
            autonomous_session,
            tenant_id=tenant_id,
            process_instance=process_instance,
            workflow=workflow,
            occurred_at=occurred_at,
            recovery_only=True,
        )
        autonomous_session.commit()
        return True
    except Exception:
        autonomous_session.rollback()
        return False
    finally:
        autonomous_session.close()


def _session_engine(session: Session) -> Engine | None:
    bind = session.get_bind()
    if isinstance(bind, Engine):
        return bind
    if isinstance(bind, Connection):
        return bind.engine
    return None


def _transition_process_instance_to_error_for_service_task_failure(
    session: Session,
    *,
    tenant_id: str,
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
    occurred_at: int,
    recovery_only: bool = False,
) -> None:
    errored_service_tasks = _errored_service_tasks(workflow)
    failed_task_guid = (
        str(errored_service_tasks[0].id) if errored_service_tasks else None
    )

    if recovery_only:
        _persist_workflow_recovery_state(
            session,
            process_instance,
            workflow,
        )
    else:
        _persist_workflow_state(
            session,
            process_instance,
            workflow,
            occurred_at=occurred_at,
        )
        _sync_inactive_human_tasks(
            session,
            process_instance=process_instance,
            workflow=workflow,
            occurred_at=occurred_at,
        )

    if failed_task_guid is not None:
        record_process_instance_event(
            session,
            tenant_id=tenant_id,
            process_instance_id=process_instance.id,
            event_type=ProcessInstanceEventType.task_failed,
            timestamp=float(occurred_at),
            task_guid=failed_task_guid,
            user_id=None,
        )

    if process_instance.start_in_seconds is None:
        process_instance.start_in_seconds = occurred_at
    if process_instance.bpmn_process is not None:
        if process_instance.bpmn_process.start_in_seconds is None:
            process_instance.bpmn_process.start_in_seconds = float(occurred_at)
        process_instance.bpmn_process.end_in_seconds = float(occurred_at)
    if process_instance.status == ProcessInstanceStatus.error.value:
        process_instance.status = ProcessInstanceStatus.running.value

    error_process_instance(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance.id,
        user_id=None,
        errored_at_in_seconds=occurred_at,
    )
    session.flush()


def _errored_service_tasks(workflow: BpmnWorkflow) -> list[object]:
    return [
        task
        for task in workflow.get_tasks(state=TaskState.ERROR)
        if type(getattr(task, "task_spec", object())).__name__ == "ServiceTask"
    ]


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
    return BpmnWorkflow(
        spec,
        subprocess_specs,
        script_engine=_service_task_script_engine(service_task_context),
    )


def _restore_workflow(
    serialized_state: str,
    *,
    service_task_context: ServiceTaskContext | None = None,
) -> BpmnWorkflow:
    workflow = _WORKFLOW_SERIALIZER.deserialize_json(serialized_state)
    workflow.script_engine = _service_task_script_engine(service_task_context)
    return workflow


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
    _sync_intermediate_timer_scheduler_job(
        session,
        process_instance=process_instance,
        workflow=workflow,
        occurred_at=occurred_at,
    )


def _persist_workflow_recovery_state(
    session: Session,
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
) -> None:
    serialized_state = _WORKFLOW_SERIALIZER.serialize_json(workflow)
    process_instance.spiff_serializer_version = _WORKFLOW_STATE_SERIALIZER_VERSION
    serialized_workflow = _serialize_workflow_dict(workflow)
    _sync_bpmn_process_from_workflow(
        session,
        process_instance=process_instance,
        serialized_workflow=serialized_workflow,
        serialized_state=serialized_state,
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
    ready_tasks = _current_ready_manual_tasks(
        session,
        process_instance=process_instance,
        workflow=workflow,
    )
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


def _sync_inactive_human_tasks(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
    occurred_at: int,
) -> None:
    ready_manual_task_guids = {
        str(task.id)
        for task in _current_ready_manual_tasks(
            session,
            process_instance=process_instance,
            workflow=workflow,
        )
    }
    human_tasks = session.scalars(
        select(HumanTaskModel).where(
            HumanTaskModel.m8f_tenant_id == process_instance.m8f_tenant_id,
            HumanTaskModel.process_instance_id == process_instance.id,
            HumanTaskModel.completed.is_(False),
        )
    ).all()
    for human_task in human_tasks:
        task_model = (
            session.get(TaskModel, human_task.task_guid)
            if human_task.task_guid is not None
            else None
        )
        if human_task.task_guid not in ready_manual_task_guids:
            _close_inactive_human_task(
                session,
                process_instance=process_instance,
                human_task=human_task,
                task_state_name=task_model.state if task_model is not None else None,
                occurred_at=occurred_at,
            )
            continue
        if task_model is None:
            continue
        if task_model.state == "READY":
            continue

        _close_inactive_human_task(
            session,
            process_instance=process_instance,
            human_task=human_task,
            task_state_name=task_model.state,
            occurred_at=occurred_at,
        )


def _current_ready_manual_tasks(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
) -> list[object]:
    ready_tasks: list[object] = []
    for task in workflow.get_tasks(state=TaskState.READY, manual=True):
        task_model = session.get(TaskModel, str(task.id))
        if task_model is None:
            continue
        if task_model.process_instance_id != process_instance.id:
            continue
        if task_model.state != "READY":
            continue
        ready_tasks.append(task)
    return ready_tasks


def _close_inactive_human_task(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    human_task: HumanTaskModel,
    task_state_name: str | None,
    occurred_at: int,
) -> None:
    if human_task.completed:
        return

    human_task.completed = True
    human_task.completed_by_user_id = None
    human_task.task_status = _inactive_human_task_status(task_state_name)
    human_task.updated_at_in_seconds = occurred_at

    task_model = human_task.task_model
    if task_model is not None and task_model.future_task is not None:
        task_model.future_task.completed = True
        task_model.future_task.updated_at_in_seconds = occurred_at

    event_type = _inactive_human_task_event_type(task_state_name)
    if event_type is not None:
        record_process_instance_event(
            session,
            tenant_id=process_instance.m8f_tenant_id,
            process_instance_id=process_instance.id,
            event_type=event_type,
            task_guid=human_task.task_guid,
            user_id=None,
            timestamp=float(occurred_at),
        )

    session.flush()


def _inactive_human_task_status(task_state_name: str | None) -> str:
    if task_state_name in {"CANCELLED", "COMPLETED", "ERROR", "TERMINATED"}:
        return task_state_name
    return "TERMINATED"


def _inactive_human_task_event_type(
    task_state_name: str | None,
) -> ProcessInstanceEventType | None:
    if task_state_name == "CANCELLED":
        return ProcessInstanceEventType.task_cancelled
    if task_state_name == "ERROR":
        return ProcessInstanceEventType.task_failed
    return None


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
    lane_owners = _task_lane_owners(
        session,
        process_instance=process_instance,
        task=task,
    )
    human_task_payload = _human_task_payload(
        task_definition,
        lane_owners=lane_owners,
    )
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
    lane_group = _lane_group(session, lane_name)
    if lane_group is None:
        return None
    return lane_group.id


def _lane_group(session: Session, lane_name: str | None) -> GroupModel | None:
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
    return lane_group


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

    lane_owners = _task_lane_owners(
        session,
        process_instance=process_instance,
        task=task,
    )
    _sync_lane_owner_group_assignments(
        session,
        tenant_id=process_instance.m8f_tenant_id,
        lane_owners=lane_owners,
    )
    preferred_identifiers: tuple[Any, ...] = ()
    if isinstance(lane_owners, Mapping):
        configured_identifiers = lane_owners.get(lane_name, ())
        if isinstance(configured_identifiers, (list, tuple, set, frozenset)):
            preferred_identifiers = tuple(configured_identifiers)
    resolved_users = _resolve_lane_group_users(
        session,
        tenant_id=process_instance.m8f_tenant_id,
        lane_name=lane_name,
        preferred_identifiers=preferred_identifiers,
    )

    if not resolved_users:
        if not isinstance(lane_owners, Mapping) or lane_name not in lane_owners:
            raise NotFoundError(
                f"Task {task.task_spec.name} does not define lane owners for "
                f"lane {lane_name!r} and no users belong to that lane group"
            )
        raise NotFoundError(
            f"No users were resolved for lane {lane_name!r} on task "
            f"{task.task_spec.name}"
        )

    return resolved_users


def _sync_lane_owner_group_assignments(
    session: Session,
    *,
    tenant_id: str,
    lane_owners: Mapping[str, Any] | None,
) -> None:
    if not isinstance(lane_owners, Mapping):
        return

    for lane_name, identifiers in lane_owners.items():
        if not isinstance(lane_name, str):
            continue
        lane_group = _lane_group(session, lane_name)
        if lane_group is None:
            continue
        for user in _resolved_lane_owner_users(
            session,
            tenant_id=tenant_id,
            identifiers=identifiers,
        ):
            assignment = session.scalar(
                select(UserGroupAssignmentModel).where(
                    UserGroupAssignmentModel.user_id == user.id,
                    UserGroupAssignmentModel.group_id == lane_group.id,
                )
            )
            if assignment is None:
                session.add(
                    UserGroupAssignmentModel(
                        user_id=user.id,
                        group_id=lane_group.id,
                    )
                )
    session.flush()


def _resolve_lane_group_users(
    session: Session,
    *,
    tenant_id: str,
    lane_name: str,
    preferred_identifiers: tuple[Any, ...] = (),
) -> list[tuple[UserModel, HumanTaskUserAddedBy]]:
    lane_group = _lane_group(session, lane_name)
    if lane_group is None:
        return []

    lane_group_users = _users_in_lane_group(
        session,
        tenant_id=tenant_id,
        group_id=lane_group.id,
    )
    users_by_id = {user.id: user for user in lane_group_users}

    resolved_users: list[tuple[UserModel, HumanTaskUserAddedBy]] = []
    seen_user_ids: set[int] = set()
    for user in _resolved_lane_owner_users(
        session,
        tenant_id=tenant_id,
        identifiers=preferred_identifiers,
    ):
        if user.id not in users_by_id or user.id in seen_user_ids:
            continue
        seen_user_ids.add(user.id)
        resolved_users.append((user, HumanTaskUserAddedBy.lane_owner))

    for user in sorted(lane_group_users, key=lambda item: (item.username, item.id)):
        if user.id in seen_user_ids:
            continue
        seen_user_ids.add(user.id)
        resolved_users.append((user, HumanTaskUserAddedBy.lane_assignment))

    return resolved_users


def _users_in_lane_group(
    session: Session,
    *,
    tenant_id: str,
    group_id: int,
) -> list[UserModel]:
    users = list(
        session.scalars(
            select(UserModel)
            .join(
                UserGroupAssignmentModel,
                UserGroupAssignmentModel.user_id == UserModel.id,
            )
            .where(UserGroupAssignmentModel.group_id == group_id)
        )
    )
    tenant_identifiers = tenant_identifiers_for(session, tenant_id)
    if tenant_identifiers:
        users = [
            user for user in users if user_belongs_to_tenant(user, tenant_identifiers)
        ]
    return users


def _resolved_lane_owner_users(
    session: Session,
    *,
    tenant_id: str,
    identifiers: Any,
) -> list[UserModel]:
    if not isinstance(identifiers, (list, tuple, set, frozenset)):
        return []

    resolved_users: list[UserModel] = []
    seen_user_ids: set[int] = set()
    for identifier in identifiers:
        if not isinstance(identifier, str):
            continue
        for user in _find_users_by_identifier(
            session,
            tenant_id=tenant_id,
            identifier=identifier,
        ):
            if user.id in seen_user_ids:
                continue
            seen_user_ids.add(user.id)
            resolved_users.append(user)
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
    task_definition: TaskDefinitionModel,
    *,
    lane_owners: Mapping[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_definition_properties": task_definition.properties_json,
    }
    if lane_owners is not None:
        payload["lane_owners"] = dict(lane_owners)
    return payload


def _task_lane_owners(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    task: object,
) -> Mapping[str, Any] | None:
    task_data = getattr(task, "data", None)
    if isinstance(task_data, Mapping):
        lane_owners = task_data.get("lane_owners")
        if isinstance(lane_owners, Mapping):
            return lane_owners

    process_definition = process_instance.bpmn_process_definition
    if process_definition is None and process_instance.bpmn_process_definition_id:
        process_definition = session.scalar(
            select(BpmnProcessDefinitionModel).where(
                BpmnProcessDefinitionModel.m8f_tenant_id
                == process_instance.m8f_tenant_id,
                BpmnProcessDefinitionModel.id
                == process_instance.bpmn_process_definition_id,
            )
        )
    if process_definition is None:
        return None

    definition_properties = process_definition.properties_json
    if not isinstance(definition_properties, Mapping):
        return None
    lane_owners = definition_properties.get("lane_owners")
    if not isinstance(lane_owners, Mapping):
        return None
    return lane_owners


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


def _sync_timer_start_scheduler_jobs_for_definition(
    session: Session,
    *,
    process_definition: BpmnProcessDefinitionModel,
    occurred_at: int,
) -> None:
    if process_definition.id is None:
        raise ValidationError("Process definition must be persisted before scheduling")

    timer_start_payloads = _collect_timer_start_payloads_for_definition(
        process_definition
    )
    expected_job_keys: set[str] = set()

    for payload in timer_start_payloads:
        qualifier = str(payload.get("task_spec_name") or "").strip()
        if not qualifier:
            raise ValidationError("Timer start payload is missing its task spec name")
        job_key = build_scheduler_job_key(
            job_type=SchedulerJobType.timer_start,
            bpmn_process_definition_id=process_definition.id,
            qualifier=qualifier,
        )
        expected_job_keys.add(job_key)
        upsert_scheduler_job(
            session,
            tenant_id=process_definition.m8f_tenant_id,
            job_key=job_key,
            job_type=SchedulerJobType.timer_start,
            bpmn_process_definition_id=process_definition.id,
            run_at_in_seconds=payload["run_at_in_seconds"],
            payload_json={
                "scheduled_from": "process_definition_import",
                "timer_task": payload,
            },
            updated_at_in_seconds=occurred_at,
            created_at_in_seconds=occurred_at,
        )

    for scheduler_job in session.scalars(
        select(SchedulerJobModel).where(
            SchedulerJobModel.m8f_tenant_id == process_definition.m8f_tenant_id,
            SchedulerJobModel.job_type == SchedulerJobType.timer_start.value,
            SchedulerJobModel.bpmn_process_definition_id == process_definition.id,
        )
    ).all():
        if scheduler_job.job_key in expected_job_keys:
            continue
        session.delete(scheduler_job)

    session.flush()


def _sync_intermediate_timer_scheduler_job(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
    occurred_at: int,
) -> None:
    if process_instance.id is None:
        raise ValidationError("Process instance must be persisted before scheduling")

    job_key = build_scheduler_job_key(
        job_type=SchedulerJobType.intermediate_timer,
        process_instance_id=process_instance.id,
    )
    waiting_timer_payloads = _collect_intermediate_timer_payloads(workflow)
    if not waiting_timer_payloads:
        delete_scheduler_job(
            session,
            tenant_id=process_instance.m8f_tenant_id,
            job_key=job_key,
        )
        return

    next_run_at_in_seconds = min(
        payload["run_at_in_seconds"] for payload in waiting_timer_payloads
    )
    upsert_scheduler_job(
        session,
        tenant_id=process_instance.m8f_tenant_id,
        job_key=job_key,
        job_type=SchedulerJobType.intermediate_timer,
        process_instance_id=process_instance.id,
        bpmn_process_definition_id=process_instance.bpmn_process_definition_id,
        run_at_in_seconds=next_run_at_in_seconds,
        payload_json={
            "scheduled_from": "workflow_runtime",
            "timer_tasks": waiting_timer_payloads,
        },
        updated_at_in_seconds=occurred_at,
        created_at_in_seconds=occurred_at,
    )


def _collect_timer_start_payloads_for_definition(
    process_definition: BpmnProcessDefinitionModel,
) -> list[dict[str, Any]]:
    source_bpmn_xml = process_definition.source_bpmn_xml
    if source_bpmn_xml is None:
        return []
    if not _definition_contains_timer_start_event(source_bpmn_xml):
        return []

    workflow = _build_workflow(
        bpmn_xml=source_bpmn_xml,
        dmn_xml=process_definition.source_dmn_xml,
        bpmn_process_id=None,
    )
    workflow.run_all(halt_on_manual=True)
    return _collect_timer_start_payloads(workflow)


def _definition_contains_timer_start_event(source_bpmn_xml: str | bytes) -> bool:
    root = ET.fromstring(_coerce_xml_bytes(source_bpmn_xml))
    namespace = {"bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL"}
    return bool(
        root.findall(
            ".//bpmn:startEvent[bpmn:timerEventDefinition]",
            namespace,
        )
    )


def _collect_intermediate_timer_payloads(
    workflow: BpmnWorkflow,
) -> list[dict[str, Any]]:
    return _collect_waiting_timer_payloads(
        workflow,
        start_event_type_name="exclude",
    )


def _collect_timer_start_payloads(
    workflow: BpmnWorkflow,
) -> list[dict[str, Any]]:
    return _collect_waiting_timer_payloads(
        workflow,
        start_event_type_name="only",
    )


def _collect_waiting_timer_payloads(
    workflow: BpmnWorkflow,
    *,
    start_event_type_name: str,
) -> list[dict[str, Any]]:
    timer_payloads: list[dict[str, Any]] = []
    for task in workflow.get_tasks(state=TaskState.WAITING):
        event_definition = getattr(task.task_spec, "event_definition", None)
        if not isinstance(event_definition, TimerEventDefinition):
            continue
        is_start_event = type(task.task_spec).__name__ == "StartEvent"
        if start_event_type_name == "exclude" and is_start_event:
            continue
        if start_event_type_name == "only" and not is_start_event:
            continue

        raw_event_value = _waiting_timer_event_value(task)
        timer_payloads.append(
            {
                "task_guid": str(task.id),
                "task_spec_name": getattr(task.task_spec, "name", None),
                "task_spec_type": type(task.task_spec).__name__,
                "event_definition_type": type(event_definition).__name__,
                "event_value": raw_event_value,
                "run_at_in_seconds": _timer_event_run_at_in_seconds(raw_event_value),
            }
        )

    timer_payloads.sort(
        key=lambda payload: (
            payload["run_at_in_seconds"],
            str(payload.get("task_guid") or ""),
        )
    )
    return timer_payloads


def _force_waiting_timer_start_task_due(
    workflow: BpmnWorkflow,
    *,
    timer_start_task_spec_name: str,
) -> bool:
    if _workflow_has_progressed_past_timer_start(workflow):
        return False

    for task in workflow.get_tasks(state=TaskState.WAITING):
        if type(task.task_spec).__name__ != "StartEvent":
            continue
        event_definition = getattr(task.task_spec, "event_definition", None)
        if not isinstance(event_definition, TimerEventDefinition):
            continue
        if getattr(task.task_spec, "name", None) != timer_start_task_spec_name:
            continue

        internal_data = getattr(task, "internal_data", None)
        if not isinstance(internal_data, Mapping):
            raise ValidationError("Waiting timer start task is missing internal data")
        internal_data["event_value"] = _forced_due_timer_event_value(
            internal_data.get("event_value")
        )
        return True

    raise ValidationError(
        "Timer start event "
        f"{timer_start_task_spec_name!r} was not found in waiting state"
    )


def _workflow_has_progressed_past_timer_start(
    workflow: BpmnWorkflow,
) -> bool:
    if workflow.completed:
        return True
    if workflow.get_tasks(state=TaskState.READY):
        return True
    return any(
        type(task.task_spec).__name__ != "StartEvent"
        for task in workflow.get_tasks(state=TaskState.WAITING)
    )


def _forced_due_timer_event_value(event_value: object) -> str | dict[str, Any]:
    if isinstance(event_value, str):
        return _FORCED_DUE_TIMER_EVENT_VALUE
    if isinstance(event_value, Mapping):
        forced_value = dict(event_value)
        forced_value["next"] = _FORCED_DUE_TIMER_EVENT_VALUE
        return forced_value
    raise ValidationError("Waiting timer task is missing its event_value")


def _waiting_timer_event_value(task: object) -> str | dict[str, Any]:
    internal_data = getattr(task, "internal_data", None)
    if not isinstance(internal_data, Mapping):
        raise ValidationError("Waiting timer task is missing internal timer data")

    event_value = internal_data.get("event_value")
    if isinstance(event_value, str):
        return event_value
    if isinstance(event_value, Mapping):
        return dict(event_value)
    raise ValidationError("Waiting timer task is missing its event_value")


def _timer_event_run_at_in_seconds(event_value: str | Mapping[str, Any]) -> int:
    if isinstance(event_value, Mapping):
        next_value = event_value.get("next")
        if not isinstance(next_value, str) or not next_value:
            raise ValidationError(
                "Recurring timer event payload is missing its next due timestamp"
            )
        event_value = next_value

    due_at = datetime.fromisoformat(event_value)
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=UTC)
    return math.ceil(due_at.timestamp())


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
                "Service task "
                f"{operation_name!r} parameter {name!r} is not a mapping"
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
