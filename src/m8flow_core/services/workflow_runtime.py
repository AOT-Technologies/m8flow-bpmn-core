#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import ast
import hashlib
import json
import logging
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from defusedxml import ElementTree
from SpiffWorkflow.bpmn.specs.event_definitions.timer import TimerEventDefinition
from SpiffWorkflow.bpmn.workflow import BpmnWorkflow
from SpiffWorkflow.util.task import TaskState
from sqlalchemy import select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from m8flow_core.errors import (
    CompensationExecutionError,
    InvalidStateError,
    NotFoundError,
    ServiceTaskExecutionError,
    ValidationError,
)
from m8flow_core.models.bpmn_process import BpmnProcessModel
from m8flow_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_core.models.future_task import FutureTaskModel
from m8flow_core.models.json_data import JsonDataModel
from m8flow_core.models.process_instance import (
    ProcessInstanceModel,
    ProcessInstanceStatus,
)
from m8flow_core.models.process_instance_event import (
    ProcessLifecycleEventType,
    TaskEventType,
)
from m8flow_core.models.process_model_bpmn_version import (
    ProcessModelBpmnVersionModel,
)
from m8flow_core.models.scheduler_job import (
    SchedulerJobModel,
    SchedulerJobType,
)
from m8flow_core.models.task import M8F_TERMINATED_TASK_STATE, TaskModel
from m8flow_core.models.task_definition import TaskDefinitionModel
from m8flow_core.models.work_item import WorkItemModel
from m8flow_core.services.authorization import (
    PROCESS_START_COMMAND,
    require_command_authorization,
)
from m8flow_core.services.call_activities import (
    CallActivityContext,
    CallActivityResolver,
)
from m8flow_core.services.compensation_journal import persist_compensation
from m8flow_core.services.compensation_runtime import (
    coordinator,
    execution_workflows,
    runtime_completed,
    runtime_task,
    runtime_tasks,
)
from m8flow_core.services.lane_assignment import (
    assign_work_item_potential_owners,
)
from m8flow_core.services.process_instances import (
    create_process_instance,
    error_process_instance,
    get_process_instance_metadata,
    record_process_instance_event,
    upsert_process_instance_metadata,
)
from m8flow_core.services.scheduler_jobs import (
    build_scheduler_job_key,
    delete_scheduler_job,
    upsert_scheduler_job,
)
from m8flow_core.services.signal_runtime import (
    RuntimeSignalDefinition,
)
from m8flow_core.services.tenant_users import (
    ensure_user_belongs_to_tenant,
)
from m8flow_core.services.work_items import (
    WorkItemState,
    close_work_item,
    prepare_work_item_for_ready_state,
)
from m8flow_core.services.workflow_engine import (
    _WORKFLOW_SERIALIZER as _WORKFLOW_SERIALIZER,
)
from m8flow_core.services.workflow_engine import (
    _WORKFLOW_STATE_SERIALIZER_VERSION as _WORKFLOW_STATE_SERIALIZER_VERSION,
)
from m8flow_core.services.workflow_engine import (
    _build_workflow as _build_workflow,
)
from m8flow_core.services.workflow_engine import (
    _coerce_xml_bytes as _coerce_xml_bytes,
)
from m8flow_core.services.workflow_engine import (
    _restore_workflow as _restore_workflow,
)
from m8flow_core.services.workflow_engine import (
    _RuntimeSpiffBpmnParser as _RuntimeSpiffBpmnParser,
)
from m8flow_core.services.workflow_engine import (
    _service_task_execution_context as _service_task_execution_context,
)
from m8flow_core.services.workflow_state import (
    _persist_workflow_recovery_state as _persist_workflow_recovery_state,
)
from m8flow_core.services.workflow_state import (
    _serialize_runtime_workflow as _serialize_runtime_workflow,
)
from m8flow_core.services.workflow_state import (
    _serialize_workflow_dict as _serialize_workflow_dict,
)
from m8flow_core.services.workflow_state import (
    _sync_bpmn_process_from_workflow as _sync_bpmn_process_from_workflow,
)

logger = logging.getLogger(__name__)

_FORCED_DUE_TIMER_EVENT_VALUE = "1970-01-01T00:00:00+00:00"


def initialize_process_instance_workflow(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
    bpmn_xml: str | bytes,
    dmn_xml: str | bytes | None = None,
    bpmn_process_id: str | None = None,
    started_at: datetime | None = None,
    call_activity_resolver: CallActivityResolver | None = None,
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

    occurred_at = _resolve_timestamp(started_at)
    workflow = _build_workflow(
        bpmn_xml=bpmn_xml,
        dmn_xml=dmn_xml,
        bpmn_process_id=bpmn_process_id,
        service_task_context=_service_task_execution_context(
            tenant_id=tenant_id,
            process_instance_id=process_instance.id,
            process_definition_id=process_instance.bpmn_process_definition_id,
        ),
        call_activity_resolver=call_activity_resolver,
        call_activity_context=CallActivityContext(
            tenant_id=tenant_id,
            called_element="",
            parent_process_instance_id=process_instance.id,
            parent_process_definition_id=process_instance.bpmn_process_definition_id,
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
    started_at: datetime | None = None,
    bpmn_process_id: str | None = None,
    call_activity_resolver: CallActivityResolver | None = None,
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
    process_model_identifier = process_definition.process_model_identifier or str(
        process_definition.id
    )
    require_command_authorization(
        session,
        tenant_id=tenant_id,
        actor_user_id=process_initiator_id,
        command_key=PROCESS_START_COMMAND,
        resource_type="process_model",
        resource_id=process_model_identifier,
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
        started_at=started_at,
        bpmn_process_id=bpmn_process_id,
    )

    return initialize_process_instance_workflow(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance.id,
        bpmn_xml=process_definition.source_bpmn_xml,
        dmn_xml=process_definition.source_dmn_xml,
        bpmn_process_id=selected_process_id,
        started_at=started_at,
        call_activity_resolver=call_activity_resolver,
    )


def _initialize_process_instance_from_timer_start_definition(
    session: Session,
    *,
    tenant_id: str,
    process_definition_id: int,
    process_initiator_id: int,
    timer_start_task_spec_name: str,
    started_at: datetime | None = None,
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
    process_model_identifier = process_definition.process_model_identifier or str(
        process_definition.id
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
        started_at=started_at,
        bpmn_process_id=None,
    )

    occurred_at = _resolve_timestamp(started_at)
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
        for tree in execution_workflows(workflow):
            tree.refresh_timers()
        if not runtime_tasks(workflow, state=TaskState.READY, manual=True):
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
    completed_at: datetime | None = None,
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
    if (
        process_instance.status == ProcessInstanceStatus.suspended.value
        and "m8f_compensation" in process_instance.workflow_state_json
    ):
        raise InvalidStateError("Cannot advance a suspended compensation workflow")

    occurred_at = _resolve_timestamp(completed_at)
    workflow = _restore_workflow(
        process_instance.workflow_state_json,
        service_task_context=_service_task_execution_context(
            tenant_id=tenant_id,
            process_instance_id=process_instance.id,
            process_definition_id=process_instance.bpmn_process_definition_id,
        ),
    )
    completed_task = runtime_task(workflow, UUID(completed_task_guid))
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
    _sync_inactive_work_items(
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
    started_at: datetime | None,
    bpmn_process_id: str | None,
) -> tuple[ProcessInstanceModel, str]:
    # Publications, receipts and signal instances share the caller's transaction.
    if "signalEventDefinition" in (process_definition.source_bpmn_xml or ""):
        return _prepare_process_instance_from_definition_in_session(
            session,
            tenant_id=tenant_id,
            process_definition=process_definition,
            process_model_identifier=process_model_identifier,
            process_initiator_id=process_initiator_id,
            submission_metadata=submission_metadata,
            summary=summary,
            process_version=process_version,
            started_at=started_at,
            bpmn_process_id=bpmn_process_id,
        )
    autonomous_baseline = _prepare_process_instance_baseline_in_independent_session(
        session,
        tenant_id=tenant_id,
        process_definition=process_definition,
        process_model_identifier=process_model_identifier,
        process_initiator_id=process_initiator_id,
        submission_metadata=submission_metadata,
        summary=summary,
        process_version=process_version,
        started_at=started_at,
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
        started_at=started_at,
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
    started_at: datetime | None,
    bpmn_process_id: str | None,
) -> tuple[int, str] | None:
    if "compensateEventDefinition" in (process_definition.source_bpmn_xml or ""):
        return None
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
                started_at=started_at,
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
    started_at: datetime | None,
    bpmn_process_id: str | None,
) -> tuple[ProcessInstanceModel, str]:
    if process_definition.source_bpmn_xml is None:
        raise ValidationError("Process definition does not include stored BPMN XML")

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
        created_at=started_at,
        updated_at=started_at,
    )
    # Keep the inserted ORM row attached through a caller rollback. A lazy
    # reload after the flush can leave a second identity for a rolled-back row.
    process_instance.bpmn_process = bpmn_process
    process_instance.bpmn_version_control_type = (
        process_definition.bpmn_version_control_type
    )
    process_instance.bpmn_version_control_identifier = (
        process_definition.bpmn_version_control_identifier
    )
    process_instance.last_milestone_bpmn_name = (
        process_definition.bpmn_name or process_definition.bpmn_identifier
    )

    metadata_timestamp = _resolve_timestamp(started_at)
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
            updated_at=metadata_timestamp,
            created_at=metadata_timestamp,
        )

    return process_instance, selected_process_id


def _finalize_initialized_process_instance_workflow(
    session: Session,
    *,
    tenant_id: str,
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
    occurred_at: datetime,
) -> ProcessInstanceModel:
    _persist_workflow_state(
        session,
        process_instance,
        workflow,
        occurred_at=occurred_at,
    )
    _sync_inactive_work_items(
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
    process_instance.started_at = occurred_at
    _update_process_instance_status_from_workflow(
        process_instance,
        workflow,
        occurred_at=occurred_at,
    )

    session.flush()

    ready_tasks = _get_ready_work_items(process_instance)
    record_process_instance_event(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance.id,
        event_type=ProcessLifecycleEventType.process_instance_created,
        occurred_at=occurred_at,
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
    occurred_at: datetime,
) -> None:
    if process_instance.bpmn_process_definition_id is None:
        raise ValidationError("Process instance is missing a BPMN process definition")

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
    occurred_at: datetime | None = None,
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

    compensation_retry = coordinator(workflow) and coordinator(workflow).retry(metadata)
    errored_service_task_ids = [task.id for task in _errored_service_tasks(workflow)]
    if not errored_service_task_ids and not compensation_retry:
        return process_instance

    retry_task_data = dict(workflow.data)
    retry_task_data.update(metadata)
    if coordinator(workflow):
        # Preserve compensation throw occurrences and detached handler trees.
        # A branch reset can otherwise remove a throw that still owns undo work.
        for task_id in errored_service_task_ids:
            task = runtime_task(workflow, task_id)
            task.data.update(metadata)
            task._set_state(TaskState.READY)
    elif not compensation_retry:
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
    _sync_inactive_work_items(
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


def sync_open_work_item_potential_owners(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
) -> None:
    """Re-apply lane assignment to the instance's open work items.

    Users who now qualify are added; existing potential owners and the claim
    state are left as they are.
    """
    process_instance = _load_process_instance(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance_id,
    )
    if process_instance.workflow_state_json is None:
        return
    open_work_items = [
        work_item
        for work_item in process_instance.work_items
        if not work_item.completed and work_item.task_guid is not None
    ]
    if not open_work_items:
        return

    workflow = _restore_workflow(
        process_instance.workflow_state_json,
        service_task_context=_service_task_execution_context(
            tenant_id=tenant_id,
            process_instance_id=process_instance.id,
            process_definition_id=process_instance.bpmn_process_definition_id,
        ),
    )
    ready_tasks = {
        str(task.id): task
        for task in runtime_tasks(workflow, state=TaskState.READY, manual=True)
    }
    definition = process_instance.bpmn_process_definition
    for work_item in open_work_items:
        task = ready_tasks.get(work_item.task_guid)
        if task is None:
            continue
        assign_work_item_potential_owners(
            session,
            process_instance=process_instance,
            work_item=work_item,
            lane_name=getattr(task.task_spec, "lane", None),
            task_data=getattr(task, "data", None),
            definition_properties=(
                definition.properties_json if definition is not None else None
            ),
        )
    session.flush()


def _refresh_waiting_process_instance_workflow(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
    occurred_at: datetime | None = None,
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
    if coordinator(workflow):
        for task in runtime_tasks(workflow, state=TaskState.WAITING):
            if isinstance(
                getattr(task.task_spec, "event_definition", None), TimerEventDefinition
            ):
                value = _waiting_timer_event_value(task)
                if _timer_event_run_at_in_seconds(value) <= timestamp.timestamp():
                    task.internal_data["event_value"] = _forced_due_timer_event_value(
                        value
                    )
    for tree in execution_workflows(workflow):
        tree.refresh_timers()
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
    _sync_inactive_work_items(
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
    occurred_at: datetime | None = None,
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
    process_instance.workflow_engine_version = _WORKFLOW_STATE_SERIALIZER_VERSION
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
    occurred_at: datetime,
    autonomous_failure_state_persistence: bool = False,
) -> None:
    from m8flow_core.services.signal_registry import bind_signal_runtime

    bind_signal_runtime(session, process_instance, workflow, occurred_at)
    try:
        if comp := coordinator(workflow):
            comp.run()
        else:
            workflow.run_all(halt_on_manual=True)
    except (ServiceTaskExecutionError, CompensationExecutionError):
        if (
            autonomous_failure_state_persistence
            and not coordinator(workflow)
            and not any(
                isinstance(
                    getattr(task_spec, "event_definition", None),
                    RuntimeSignalDefinition,
                )
                for spec in [workflow.spec, *workflow.subprocess_specs.values()]
                for task_spec in spec.task_specs.values()
            )
            and _persist_service_task_failure_state_in_independent_session(
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
    occurred_at: datetime,
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
    occurred_at: datetime,
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
        _sync_inactive_work_items(
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
            event_type=TaskEventType.task_failed,
            occurred_at=occurred_at,
            task_guid=failed_task_guid,
            user_id=None,
        )

    if process_instance.started_at is None:
        process_instance.started_at = occurred_at
    if process_instance.bpmn_process is not None:
        if process_instance.bpmn_process.started_at is None:
            process_instance.bpmn_process.started_at = occurred_at
        process_instance.bpmn_process.ended_at = occurred_at
    if process_instance.status == ProcessInstanceStatus.error.value:
        process_instance.status = ProcessInstanceStatus.running.value

    error_process_instance(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance.id,
        user_id=None,
        errored_at=occurred_at,
    )
    session.flush()


def _errored_service_tasks(workflow: BpmnWorkflow) -> list[object]:
    return [
        task
        for task in runtime_tasks(workflow, state=TaskState.ERROR)
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
            tenant_id,
            {
                "bpmn_process_definition_id": process_definition.id,
                "process_identifier": process_identifier,
            },
        ),
        started_at=None,
        ended_at=None,
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
    occurred_at: datetime,
) -> ProcessModelBpmnVersionModel:
    if bpmn_xml_text is None:
        raise ValidationError("Cannot create a BPMN version snapshot without BPMN XML")

    normalized_bpmn_xml = (
        bpmn_xml_text.decode("utf-8")
        if isinstance(bpmn_xml_text, bytes)
        else bpmn_xml_text
    )
    bpmn_xml_hash = hashlib.sha256(normalized_bpmn_xml.encode("utf-8")).hexdigest()
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
            created_at=occurred_at,
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


def _persist_workflow_state(
    session: Session,
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
    *,
    occurred_at: datetime,
) -> None:
    from m8flow_core.services.signal_registry import sync_instance_signals

    sync_instance_signals(session, process_instance, workflow, occurred_at)
    serialized_state = _serialize_runtime_workflow(workflow)
    process_instance.workflow_engine_version = _WORKFLOW_STATE_SERIALIZER_VERSION
    serialized_workflow = _serialize_workflow_dict(workflow)
    if comp := coordinator(workflow):
        persist_compensation(session, process_instance, comp, occurred_at)
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


def _cancel_compensation(
    session: Session, process_instance: ProcessInstanceModel, occurred_at: datetime
) -> None:
    state = process_instance.workflow_state_json
    if state is None or "m8f_compensation" not in json.loads(state):
        return
    workflow = _restore_workflow(state)
    comp = coordinator(workflow)
    if comp is None:
        raise InvalidStateError("Compensation snapshot is missing its journal")
    for tree in execution_workflows(workflow):
        if not tree.completed:
            tree.cancel()
    for run in comp.state["runs"].values():
        if run["status"] in {"running", "failed"}:
            run["status"] = "cancelled"
    for record in comp.state["records"].values():
        if record["status"] not in {"completed", "cancelled"}:
            record["status"] = "cancelled"
    _persist_workflow_state(
        session, process_instance, workflow, occurred_at=occurred_at
    )


def _sync_process_definition_from_workflow(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    serialized_workflow: Mapping[str, Any],
    occurred_at: datetime,
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
    process_definition.updated_at = occurred_at

    _sync_serialized_task_specs(
        session,
        serialized_spec,
        tenant_id=process_instance.m8f_tenant_id,
        process_definition_id=process_definition.id,
        occurred_at=occurred_at,
    )
    subprocess_specs = serialized_workflow.get("subprocess_specs")
    if isinstance(subprocess_specs, Mapping):
        for subprocess_name, subprocess_spec in subprocess_specs.items():
            if isinstance(subprocess_spec, Mapping):
                _sync_serialized_task_specs(
                    session,
                    subprocess_spec,
                    tenant_id=process_instance.m8f_tenant_id,
                    process_definition_id=process_definition.id,
                    occurred_at=occurred_at,
                    task_identifier_prefix=str(subprocess_name),
                )


def _sync_serialized_task_specs(
    session: Session,
    serialized_spec: Mapping[str, Any],
    *,
    tenant_id: str,
    process_definition_id: int,
    occurred_at: datetime,
    task_identifier_prefix: str | None = None,
) -> None:
    task_specs = serialized_spec.get("task_specs")
    if not isinstance(task_specs, Mapping):
        raise ValidationError("Serialized workflow spec is missing task specs")
    for task_name, task_spec_payload in task_specs.items():
        if isinstance(task_spec_payload, Mapping):
            task_identifier = str(task_name)
            database_identifier = (
                f"{task_identifier_prefix}::{task_identifier}"
                if task_identifier_prefix is not None
                else task_identifier
            )
            persisted_payload = dict(task_spec_payload)
            persisted_payload["__m8f_task_identifier"] = task_identifier
            _upsert_task_definition_from_payload(
                session,
                tenant_id=tenant_id,
                process_definition_id=process_definition_id,
                task_identifier=database_identifier,
                task_spec_payload=persisted_payload,
                occurred_at=occurred_at,
            )


def _sync_task_models_from_workflow(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    serialized_workflow: Mapping[str, Any],
    occurred_at: datetime,
) -> None:
    if process_instance.bpmn_process_definition_id is None:
        raise ValidationError("Process instance is missing a BPMN process definition")

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

    serialized_task_rows = list(_iter_serialized_task_rows(serialized_workflow))
    if not serialized_task_rows:
        raise ValidationError("Serialized workflow is missing task rows")
    for _task_guid, task_spec_identifier, task_payload in serialized_task_rows:
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


def _iter_serialized_task_rows(
    serialized_workflow: Mapping[str, Any],
    *,
    identifier_prefix: str | None = None,
) -> Sequence[tuple[str, str, Mapping[str, Any]]]:
    rows: list[tuple[str, str, Mapping[str, Any]]] = []
    serialized_tasks = serialized_workflow.get("tasks")
    if isinstance(serialized_tasks, Mapping):
        for task_guid, task_payload in serialized_tasks.items():
            if not isinstance(task_payload, Mapping):
                continue
            task_spec_name = task_payload.get("task_spec")
            if not isinstance(task_spec_name, str):
                raise ValidationError(
                    f"Serialized task {task_guid!r} is missing its task spec identifier"
                )
            task_definition_identifier = (
                f"{identifier_prefix}::{task_spec_name}"
                if identifier_prefix is not None
                else task_spec_name
            )
            rows.append((str(task_guid), task_definition_identifier, task_payload))
    subprocesses = serialized_workflow.get("subprocesses")
    if isinstance(subprocesses, Mapping):
        for subprocess in subprocesses.values():
            if isinstance(subprocess, Mapping):
                subprocess_name = subprocess.get("spec")
                if not isinstance(subprocess_name, str):
                    raise ValidationError(
                        "Serialized subprocess is missing its spec identifier"
                    )
                rows.extend(
                    _iter_serialized_task_rows(
                        subprocess,
                        identifier_prefix=subprocess_name,
                    )
                )
    return rows


def _upsert_task_definition(
    session: Session,
    *,
    tenant_id: str,
    process_definition_id: int,
    task_spec: object,
    task_identifier_prefix: str | None = None,
    occurred_at: datetime,
) -> TaskDefinitionModel:
    task_spec_payload = _WORKFLOW_SERIALIZER.to_dict(task_spec)
    if not isinstance(task_spec_payload, Mapping):
        raise ValidationError("Task spec serializer did not produce a dictionary")
    source_task_identifier = getattr(task_spec, "name", None)
    if not isinstance(source_task_identifier, str) or not source_task_identifier:
        raise ValidationError("Task spec is missing its serialized identifier")
    task_identifier = (
        f"{task_identifier_prefix}::{source_task_identifier}"
        if task_identifier_prefix is not None
        else source_task_identifier
    )
    serialized_payload = dict(task_spec_payload)
    serialized_payload["__m8f_task_identifier"] = source_task_identifier
    return _upsert_task_definition_from_payload(
        session,
        tenant_id=tenant_id,
        process_definition_id=process_definition_id,
        task_identifier=task_identifier,
        task_spec_payload=serialized_payload,
        occurred_at=occurred_at,
    )


def _task_definition_identifier_prefix(workflow: object | None) -> str | None:
    if (
        getattr(workflow, "parent_workflow", None) is None
        and getattr(workflow, "_m8f_compensation_context", None) is None
    ):
        return None
    spec = getattr(workflow, "spec", None)
    spec_name = getattr(spec, "name", None)
    return spec_name if isinstance(spec_name, str) and spec_name else None


def _upsert_task_definition_from_payload(
    session: Session,
    *,
    tenant_id: str,
    process_definition_id: int,
    task_identifier: str,
    task_spec_payload: Mapping[str, Any],
    occurred_at: datetime,
) -> TaskDefinitionModel:
    task_definition = session.scalar(
        select(TaskDefinitionModel).where(
            TaskDefinitionModel.m8f_tenant_id == tenant_id,
            TaskDefinitionModel.bpmn_process_definition_id == process_definition_id,
            TaskDefinitionModel.bpmn_identifier == task_identifier,
        )
    )
    serialized_payload = dict(task_spec_payload)
    serialized_payload.setdefault("__m8f_task_identifier", task_identifier)
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
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        session.add(task_definition)
    else:
        task_definition.bpmn_name = task_name if isinstance(task_name, str) else None
        task_definition.typename = task_typename
        task_definition.properties_json = serialized_payload
        task_definition.updated_at = occurred_at

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
    occurred_at: datetime,
) -> list[WorkItemModel]:
    ready_tasks = _current_ready_manual_tasks(
        session,
        process_instance=process_instance,
        workflow=workflow,
    )
    materialized: list[WorkItemModel] = []
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


def _sync_inactive_work_items(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
    occurred_at: datetime,
) -> None:
    ready_manual_task_guids = {
        str(task.id)
        for task in _current_ready_manual_tasks(
            session,
            process_instance=process_instance,
            workflow=workflow,
        )
    }
    work_items = session.scalars(
        select(WorkItemModel).where(
            WorkItemModel.m8f_tenant_id == process_instance.m8f_tenant_id,
            WorkItemModel.process_instance_id == process_instance.id,
            WorkItemModel.completed.is_(False),
        )
    ).all()
    for work_item in work_items:
        task_model = (
            session.get(TaskModel, work_item.task_guid)
            if work_item.task_guid is not None
            else None
        )
        if work_item.task_guid not in ready_manual_task_guids:
            _close_inactive_work_item(
                session,
                process_instance=process_instance,
                work_item=work_item,
                task_state_name=task_model.state if task_model is not None else None,
                occurred_at=occurred_at,
            )
            continue
        if task_model is None:
            continue
        if task_model.state == TaskState.get_name(TaskState.READY):
            continue

        _close_inactive_work_item(
            session,
            process_instance=process_instance,
            work_item=work_item,
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
    for task in runtime_tasks(workflow, state=TaskState.READY, manual=True):
        task_model = session.get(TaskModel, str(task.id))
        if task_model is None:
            continue
        if task_model.process_instance_id != process_instance.id:
            continue
        if task_model.state != TaskState.get_name(TaskState.READY):
            continue
        ready_tasks.append(task)
    return ready_tasks


def _close_inactive_work_item(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    work_item: WorkItemModel,
    task_state_name: str | None,
    occurred_at: datetime,
) -> None:
    if work_item.completed:
        return

    close_work_item(
        work_item,
        state=WorkItemState(_inactive_work_item_status(task_state_name)),
        occurred_at=occurred_at,
    )

    task_model = work_item.task_model
    if task_model is not None and task_model.future_task is not None:
        task_model.future_task.completed = True
        task_model.future_task.updated_at = occurred_at

    event_type = _inactive_work_item_event_type(task_state_name)
    if event_type is not None:
        record_process_instance_event(
            session,
            tenant_id=process_instance.m8f_tenant_id,
            process_instance_id=process_instance.id,
            event_type=event_type,
            task_guid=work_item.task_guid,
            user_id=None,
            occurred_at=occurred_at,
        )

    session.flush()


def _inactive_work_item_status(task_state_name: str | None) -> str:
    if task_state_name in {
        TaskState.get_name(TaskState.CANCELLED),
        TaskState.get_name(TaskState.COMPLETED),
        TaskState.get_name(TaskState.ERROR),
        M8F_TERMINATED_TASK_STATE,
    }:
        return task_state_name
    return WorkItemState.TERMINATED.value


def _inactive_work_item_event_type(
    task_state_name: str | None,
) -> ProcessLifecycleEventType | TaskEventType | None:
    if task_state_name == TaskState.get_name(TaskState.CANCELLED):
        return TaskEventType.task_cancelled
    if task_state_name == TaskState.get_name(TaskState.ERROR):
        return TaskEventType.task_failed
    return None


def _materialize_single_manual_task(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
    task: object,
    occurred_at: datetime,
) -> WorkItemModel:
    task_id = str(task.id)
    if process_instance.bpmn_process is None:
        raise ValidationError("Process instance is missing a BPMN process")
    if process_instance.bpmn_process_definition_id is None:
        raise ValidationError("Process instance is missing a BPMN process definition")
    if process_instance.process_initiator_id is None:
        raise ValidationError("Process instance is missing a process initiator")

    task_definition = _upsert_task_definition(
        session,
        tenant_id=process_instance.m8f_tenant_id,
        process_definition_id=process_instance.bpmn_process_definition_id,
        task_spec=task.task_spec,
        task_identifier_prefix=_task_definition_identifier_prefix(
            getattr(task, "workflow", None)
        ),
        occurred_at=occurred_at,
    )
    task_model = _upsert_task_model(
        session,
        process_instance=process_instance,
        task_definition=task_definition,
        task=task,
        occurred_at=occurred_at,
    )
    work_item = _upsert_work_item(
        session,
        process_instance=process_instance,
        task_model=task_model,
        task=task,
        task_definition=task_definition,
        occurred_at=occurred_at,
    )
    definition = process_instance.bpmn_process_definition
    assign_work_item_potential_owners(
        session,
        process_instance=process_instance,
        work_item=work_item,
        lane_name=getattr(task.task_spec, "lane", None),
        task_data=getattr(task, "data", None),
        definition_properties=(
            definition.properties_json if definition is not None else None
        ),
    )
    task_model.future_task = _upsert_future_task(
        session,
        tenant_id=process_instance.m8f_tenant_id,
        guid=task_id,
        occurred_at=occurred_at,
    )
    session.flush()
    return work_item


def _upsert_future_task(
    session: Session,
    *,
    tenant_id: str,
    guid: str,
    occurred_at: datetime,
) -> FutureTaskModel:
    future_task = session.get(FutureTaskModel, guid)
    if future_task is None:
        future_task = FutureTaskModel(
            m8f_tenant_id=tenant_id,
            guid=guid,
            run_at=occurred_at,
            queued_to_run_at=occurred_at,
            completed=False,
            archived_for_process_instance_status=False,
            updated_at=occurred_at,
        )
        session.add(future_task)
    else:
        future_task.run_at = occurred_at
        future_task.queued_to_run_at = occurred_at
        future_task.completed = False
        future_task.archived_for_process_instance_status = False
        future_task.updated_at = occurred_at

    session.flush()
    return future_task


def _upsert_task_model(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    task_definition: TaskDefinitionModel,
    task: object,
    occurred_at: datetime,
) -> TaskModel:
    serialized_task = _WORKFLOW_SERIALIZER.to_dict(task)
    if not isinstance(serialized_task, Mapping):
        raise ValidationError("Task serializer did not produce a dictionary")
    if comp := coordinator(task.workflow.top_workflow):
        serialized_task = comp.encode_payload(dict(serialized_task))
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
    occurred_at: datetime,
) -> TaskModel:
    task_guid = task_payload.get("id")
    if not isinstance(task_guid, str) or not task_guid:
        raise ValidationError("Serialized task is missing its guid")
    task_model = session.get(TaskModel, task_guid)
    properties_json = dict(task_payload)
    task_data = properties_json.pop("data", {})
    json_data_hash = JsonDataModel.create_or_update_from_payload(
        session,
        process_instance.m8f_tenant_id,
        task_data if isinstance(task_data, Mapping) else {},
    )
    python_env_data_hash = JsonDataModel.create_or_update_from_payload(
        session, process_instance.m8f_tenant_id, {}
    )

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
    task_is_terminal = task_state_name in {
        TaskState.get_name(TaskState.COMPLETED),
        TaskState.get_name(TaskState.CANCELLED),
        TaskState.get_name(TaskState.ERROR),
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
            state=task_state_name,
            properties_json=properties_json,
            json_data_hash=json_data_hash,
            python_env_data_hash=python_env_data_hash,
            runtime_info=runtime_info,
            started_at=occurred_at,
            ended_at=occurred_at if task_is_terminal else None,
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
        task_model.started_at = (
            occurred_at if task_model.started_at is None else task_model.started_at
        )
        task_model.ended_at = occurred_at if task_is_terminal else None

    session.flush()
    return task_model


def _upsert_work_item(
    session: Session,
    *,
    process_instance: ProcessInstanceModel,
    task_model: TaskModel,
    task: object,
    task_definition: TaskDefinitionModel,
    occurred_at: datetime,
) -> WorkItemModel:
    work_item = session.scalar(
        select(WorkItemModel).where(
            WorkItemModel.m8f_tenant_id == process_instance.m8f_tenant_id,
            WorkItemModel.process_instance_id == process_instance.id,
            WorkItemModel.task_guid == task_model.guid,
        )
    )
    if work_item is None:
        work_item = WorkItemModel(
            m8f_tenant_id=process_instance.m8f_tenant_id,
            process_instance_id=process_instance.id,
            task_guid=task_model.guid,
            completed_by_user_id=None,
            actual_owner_id=None,
            task_status=WorkItemState.READY.value,
            updated_at=occurred_at,
            created_at=occurred_at,
            completed=False,
        )
        session.add(work_item)
    else:
        work_item.task_guid = task_model.guid
        prepare_work_item_for_ready_state(
            work_item,
            occurred_at=occurred_at,
        )

    process_instance.task_updated_at = occurred_at
    session.flush()
    return work_item


def _update_process_instance_status_from_workflow(
    process_instance: ProcessInstanceModel,
    workflow: BpmnWorkflow,
    *,
    occurred_at: datetime,
) -> None:
    if runtime_completed(workflow):
        process_instance.status = ProcessInstanceStatus.complete.value
        process_instance.ended_at = occurred_at
        process_instance.updated_at = occurred_at
        if process_instance.bpmn_process is not None:
            process_instance.bpmn_process.ended_at = occurred_at
        _archive_completed_workflow_runtime_state(
            process_instance, occurred_at=occurred_at
        )
        return

    if runtime_tasks(workflow, state=TaskState.READY, manual=True):
        process_instance.status = ProcessInstanceStatus.user_input_required.value
    elif runtime_tasks(workflow, state=TaskState.WAITING):
        process_instance.status = ProcessInstanceStatus.waiting.value
    else:
        process_instance.status = ProcessInstanceStatus.running.value
    process_instance.ended_at = None
    process_instance.updated_at = occurred_at
    process_instance.task_updated_at = occurred_at
    if process_instance.bpmn_process is not None:
        if process_instance.bpmn_process.started_at is None:
            process_instance.bpmn_process.started_at = (
                process_instance.started_at or occurred_at
            )
        process_instance.bpmn_process.ended_at = None


def _archive_completed_workflow_runtime_state(
    process_instance: ProcessInstanceModel,
    *,
    occurred_at: datetime,
) -> None:
    for task in process_instance.tasks:
        if task.future_task is None:
            continue
        task.future_task.completed = True
        task.future_task.archived_for_process_instance_status = True
        task.future_task.updated_at = occurred_at


def _sync_timer_start_scheduler_jobs_for_definition(
    session: Session,
    *,
    process_definition: BpmnProcessDefinitionModel,
    occurred_at: datetime,
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
            run_at=datetime.fromtimestamp(float(payload["run_at_in_seconds"]), UTC),
            payload_json={
                "scheduled_from": "process_definition_import",
                "timer_task": payload,
            },
            updated_at=occurred_at,
            created_at=occurred_at,
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
    occurred_at: datetime,
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
        run_at=datetime.fromtimestamp(float(next_run_at_in_seconds), UTC),
        payload_json={
            "scheduled_from": "workflow_runtime",
            "timer_tasks": waiting_timer_payloads,
        },
        updated_at=occurred_at,
        created_at=occurred_at,
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
    root = ElementTree.fromstring(_coerce_xml_bytes(source_bpmn_xml))
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
    for task in runtime_tasks(workflow, state=TaskState.WAITING):
        event_definition = getattr(task.task_spec, "event_definition", None)
        if not isinstance(event_definition, TimerEventDefinition):
            continue
        # Only the top-level process's start events start new instances. An
        # event subprocess's timer start runs inside a live instance, so it is
        # scheduled like an intermediate timer.
        is_start_event = (
            type(task.task_spec).__name__ == "StartEvent" and task.workflow is workflow
        )
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

    for task in runtime_tasks(workflow, state=TaskState.WAITING):
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
    if runtime_completed(workflow):
        return True
    if runtime_tasks(workflow, state=TaskState.READY):
        return True
    return any(
        type(task.task_spec).__name__ != "StartEvent"
        for task in runtime_tasks(workflow, state=TaskState.WAITING)
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


def _get_ready_work_items(
    process_instance: ProcessInstanceModel,
) -> list[WorkItemModel]:
    return [
        work_item
        for work_item in process_instance.work_items
        if (
            not work_item.completed
            and work_item.task_status == WorkItemState.READY.value
        )
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
    from m8flow_core.services.signal_registry import lock_signal_instance

    if process_instance.workflow_state_json is not None:
        payload = json.loads(process_instance.workflow_state_json)
        if "m8f_compensation" not in payload and process_instance.bpmn_version_id:
            version = session.scalar(
                select(ProcessModelBpmnVersionModel).where(
                    ProcessModelBpmnVersionModel.id == process_instance.bpmn_version_id,
                    ProcessModelBpmnVersionModel.m8f_tenant_id == tenant_id,
                )
            )
            if (
                version
                and "compensateEventDefinition" in version.bpmn_xml_file_contents
            ):
                raise InvalidStateError(
                    "Legacy compensation state has no completion journal; start a "
                    "new instance"
                )
    return lock_signal_instance(session, process_instance)


def _resolve_timestamp(timestamp: datetime | None) -> datetime:
    return timestamp or datetime.now(UTC)


def _raise_value_error(message: str) -> None:
    raise ValidationError(message)
