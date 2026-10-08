from __future__ import annotations

from datetime import UTC, datetime

from SpiffWorkflow.util.task import TaskState
from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_bpmn_core.errors import InvalidStateError, NotFoundError
from m8flow_bpmn_core.models.process_instance import (
    ProcessInstanceModel,
    ProcessInstanceStatus,
)
from m8flow_bpmn_core.models.process_instance_event import (
    ProcessInstanceEventModel,
    ProcessLifecycleEventType,
    TaskEventType,
)
from m8flow_bpmn_core.models.process_instance_metadata import (
    ProcessInstanceMetadataModel,
)
from m8flow_bpmn_core.models.scheduler_job import (
    SchedulerJobModel,
    SchedulerJobType,
)
from m8flow_bpmn_core.models.task import M8F_TERMINATED_TASK_STATE
from m8flow_bpmn_core.services.authorization import (
    PROCESS_RESUME_COMMAND,
    PROCESS_RETRY_COMMAND,
    PROCESS_SUSPEND_COMMAND,
    PROCESS_TERMINATE_COMMAND,
    require_command_authorization,
)
from m8flow_bpmn_core.services.scheduler_jobs import (
    build_scheduler_job_key,
    delete_scheduler_job,
    upsert_scheduler_job,
)
from m8flow_bpmn_core.services.tenant_users import (
    ensure_user_belongs_to_tenant,
)
from m8flow_bpmn_core.services.work_items import (
    WorkItemState,
    close_work_item,
    reopen_work_item,
)


def create_process_instance(
    session: Session,
    *,
    tenant_id: str,
    process_model_identifier: str,
    process_model_display_name: str,
    process_initiator_id: int,
    bpmn_process_definition_id: int,
    bpmn_process_id: int,
    summary: str | None = None,
    process_version: int = 1,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> ProcessInstanceModel:
    ensure_user_belongs_to_tenant(
        session,
        tenant_id=tenant_id,
        user_id=process_initiator_id,
    )
    occurred_at = _resolve_timestamp(created_at)
    process_instance = ProcessInstanceModel(
        m8f_tenant_id=tenant_id,
        process_model_identifier=process_model_identifier,
        process_model_display_name=process_model_display_name,
        summary=summary,
        process_initiator_id=process_initiator_id,
        bpmn_process_definition_id=bpmn_process_definition_id,
        bpmn_process_id=bpmn_process_id,
        status=ProcessInstanceStatus.not_started.value,
        created_at=occurred_at,
        updated_at=(
            updated_at
            if updated_at is not None
            else occurred_at
        ),
    )
    session.add(process_instance)
    session.flush()
    return process_instance


def get_process_instance(
    session: Session, *, tenant_id: str, process_instance_id: int
) -> ProcessInstanceModel:
    return _load_process_instance(
        session, tenant_id=tenant_id, process_instance_id=process_instance_id
    )


def list_process_instances(
    session: Session,
    *,
    tenant_id: str,
    status: ProcessInstanceStatus | str | None = None,
) -> list[ProcessInstanceModel]:
    stmt = select(ProcessInstanceModel).where(
        ProcessInstanceModel.m8f_tenant_id == tenant_id
    )
    if status is not None:
        stmt = stmt.where(ProcessInstanceModel.status == _normalize_status(status))
    stmt = stmt.order_by(ProcessInstanceModel.id)
    return list(session.scalars(stmt).all())


def list_error_process_instances(
    session: Session,
    *,
    tenant_id: str,
) -> list[ProcessInstanceModel]:
    return list_process_instances(
        session,
        tenant_id=tenant_id,
        status=ProcessInstanceStatus.error,
    )


def list_suspended_process_instances(
    session: Session,
    *,
    tenant_id: str,
) -> list[ProcessInstanceModel]:
    return list_process_instances(
        session,
        tenant_id=tenant_id,
        status=ProcessInstanceStatus.suspended,
    )


def list_terminated_process_instances(
    session: Session,
    *,
    tenant_id: str,
) -> list[ProcessInstanceModel]:
    return list_process_instances(
        session,
        tenant_id=tenant_id,
        status=ProcessInstanceStatus.terminated,
    )


def record_process_instance_event(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
    event_type: (
        ProcessLifecycleEventType | TaskEventType | str
    ),
    occurred_at: datetime | None = None,
    task_guid: str | None = None,
    user_id: int | None = None,
) -> ProcessInstanceEventModel:
    if user_id is not None:
        ensure_user_belongs_to_tenant(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
        )
    _load_process_instance(
        session, tenant_id=tenant_id, process_instance_id=process_instance_id
    )
    event = ProcessInstanceEventModel(
        m8f_tenant_id=tenant_id,
        process_instance_id=process_instance_id,
        task_guid=task_guid,
        event_type=event_type,
        occurred_at=occurred_at or datetime.now(UTC),
        user_id=user_id,
    )
    session.add(event)
    session.flush()
    return event


def get_process_instance_events(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
) -> list[ProcessInstanceEventModel]:
    _load_process_instance(
        session, tenant_id=tenant_id, process_instance_id=process_instance_id
    )
    stmt = (
        select(ProcessInstanceEventModel)
        .where(
            ProcessInstanceEventModel.m8f_tenant_id == tenant_id,
            ProcessInstanceEventModel.process_instance_id == process_instance_id,
        )
        .order_by(
            ProcessInstanceEventModel.occurred_at,
            ProcessInstanceEventModel.id,
        )
    )
    return list(session.scalars(stmt).all())


def upsert_process_instance_metadata(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
    key: str,
    value: str,
    updated_at: datetime,
    created_at: datetime | None = None,
) -> ProcessInstanceMetadataModel:
    _load_process_instance(
        session, tenant_id=tenant_id, process_instance_id=process_instance_id
    )
    metadata = session.scalar(
        select(ProcessInstanceMetadataModel).where(
            ProcessInstanceMetadataModel.m8f_tenant_id == tenant_id,
            ProcessInstanceMetadataModel.process_instance_id == process_instance_id,
            ProcessInstanceMetadataModel.key == key,
        )
    )
    if metadata is None:
        metadata = ProcessInstanceMetadataModel(
            m8f_tenant_id=tenant_id,
            process_instance_id=process_instance_id,
            key=key,
            value=value,
            updated_at=updated_at,
            created_at=(
                created_at
                if created_at is not None
                else updated_at
            ),
        )
        session.add(metadata)
    else:
        metadata.value = value
        metadata.updated_at = updated_at
        if created_at is not None:
            metadata.created_at = created_at

    session.flush()
    return metadata


def suspend_process_instance(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
    user_id: int,
    suspended_at: datetime | None = None,
) -> ProcessInstanceModel:
    ensure_user_belongs_to_tenant(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    process_instance = _load_process_instance(
        session, tenant_id=tenant_id, process_instance_id=process_instance_id
    )
    require_command_authorization(
        session,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        command_key=PROCESS_SUSPEND_COMMAND,
        resource_type="process_instance",
        resource_id=process_instance.id,
    )
    if process_instance.status == ProcessInstanceStatus.suspended.value:
        return process_instance
    if process_instance.has_terminal_status():
        raise InvalidStateError("Cannot suspend a terminal process instance")

    occurred_at = _resolve_timestamp(suspended_at)
    process_instance.status = ProcessInstanceStatus.suspended.value
    process_instance.updated_at = occurred_at
    record_process_instance_event(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance_id,
        event_type=ProcessLifecycleEventType.process_instance_suspended,
        occurred_at=occurred_at,
        user_id=user_id,
    )
    session.flush()
    return process_instance


def error_process_instance(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
    user_id: int | None = None,
    errored_at: datetime | None = None,
) -> ProcessInstanceModel:
    if user_id is not None:
        ensure_user_belongs_to_tenant(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
        )
    process_instance = _load_process_instance(
        session, tenant_id=tenant_id, process_instance_id=process_instance_id
    )
    if process_instance.status == ProcessInstanceStatus.error.value:
        return process_instance
    if process_instance.status == ProcessInstanceStatus.complete.value:
        raise InvalidStateError(
            "Cannot mark a completed process instance as errored"
        )
    if process_instance.status == ProcessInstanceStatus.terminated.value:
        raise InvalidStateError(
            "Cannot mark a terminated process instance as errored"
        )

    occurred_at = _resolve_timestamp(errored_at)
    process_instance.status = ProcessInstanceStatus.error.value
    process_instance.ended_at = occurred_at
    process_instance.updated_at = occurred_at
    _close_process_instance_runtime_state(
        session,
        process_instance,
        occurred_at=occurred_at,
        user_id=user_id,
    )
    record_process_instance_event(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance_id,
        event_type=ProcessLifecycleEventType.process_instance_error,
        occurred_at=occurred_at,
        user_id=user_id,
    )
    session.flush()
    return process_instance


def resume_process_instance(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
    user_id: int,
    resumed_at: datetime | None = None,
) -> ProcessInstanceModel:
    ensure_user_belongs_to_tenant(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    process_instance = _load_process_instance(
        session, tenant_id=tenant_id, process_instance_id=process_instance_id
    )
    require_command_authorization(
        session,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        command_key=PROCESS_RESUME_COMMAND,
        resource_type="process_instance",
        resource_id=process_instance.id,
    )
    if process_instance.status == ProcessInstanceStatus.running.value:
        return process_instance
    if process_instance.has_terminal_status():
        raise InvalidStateError("Cannot resume a terminal process instance")
    if process_instance.status != ProcessInstanceStatus.suspended.value:
        raise InvalidStateError("Only suspended process instances can be resumed")

    occurred_at = _resolve_timestamp(resumed_at)
    process_instance.status = ProcessInstanceStatus.running.value
    process_instance.updated_at = occurred_at
    record_process_instance_event(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance_id,
        event_type=ProcessLifecycleEventType.process_instance_resumed,
        occurred_at=occurred_at,
        user_id=user_id,
    )
    session.flush()
    return process_instance


def retry_process_instance(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
    user_id: int,
    retried_at: datetime | None = None,
) -> ProcessInstanceModel:
    from m8flow_bpmn_core.services.workflow_runtime import (
        retry_errored_service_task_workflow_if_needed,
    )

    ensure_user_belongs_to_tenant(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    process_instance = _load_process_instance(
        session, tenant_id=tenant_id, process_instance_id=process_instance_id
    )
    require_command_authorization(
        session,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        command_key=PROCESS_RETRY_COMMAND,
        resource_type="process_instance",
        resource_id=process_instance.id,
    )
    if process_instance.status != ProcessInstanceStatus.error.value:
        raise InvalidStateError("Only errored process instances can be retried")

    occurred_at = _resolve_timestamp(retried_at)
    process_instance.status = ProcessInstanceStatus.running.value
    process_instance.ended_at = None
    process_instance.updated_at = occurred_at
    _reopen_process_instance_runtime_state(
        session,
        process_instance,
        occurred_at=occurred_at,
    )
    retry_errored_service_task_workflow_if_needed(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance.id,
        occurred_at=occurred_at,
    )
    _delete_scheduled_process_retry_job(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance.id,
    )
    record_process_instance_event(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance_id,
        event_type=ProcessLifecycleEventType.process_instance_retried,
        occurred_at=occurred_at,
        user_id=user_id,
    )
    retry_errored_service_task_workflow_if_needed(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance.id,
        occurred_at=occurred_at,
    )
    session.flush()
    return process_instance


def schedule_process_instance_retry(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
    user_id: int,
    retry_at: datetime,
    scheduled_at: datetime | None = None,
) -> SchedulerJobModel:
    ensure_user_belongs_to_tenant(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    process_instance = _load_process_instance(
        session, tenant_id=tenant_id, process_instance_id=process_instance_id
    )
    require_command_authorization(
        session,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        command_key=PROCESS_RETRY_COMMAND,
        resource_type="process_instance",
        resource_id=process_instance.id,
    )
    if process_instance.status != ProcessInstanceStatus.error.value:
        raise InvalidStateError(
            "Only errored process instances can be scheduled for retry"
        )

    occurred_at = _resolve_timestamp(scheduled_at)
    return upsert_scheduler_job(
        session,
        tenant_id=tenant_id,
        job_key=_process_retry_scheduler_job_key(
            process_instance_id=process_instance.id
        ),
        job_type=SchedulerJobType.process_retry,
        process_instance_id=process_instance.id,
        bpmn_process_definition_id=process_instance.bpmn_process_definition_id,
        run_at=retry_at,
        payload_json={
            "requested_by_user_id": user_id,
            "scheduled_at": occurred_at.isoformat(),
        },
        updated_at=occurred_at,
        created_at=occurred_at,
    )


def terminate_process_instance(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
    user_id: int,
    terminated_at: datetime | None = None,
) -> ProcessInstanceModel:
    ensure_user_belongs_to_tenant(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    process_instance = _load_process_instance(
        session, tenant_id=tenant_id, process_instance_id=process_instance_id
    )
    require_command_authorization(
        session,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        command_key=PROCESS_TERMINATE_COMMAND,
        resource_type="process_instance",
        resource_id=process_instance.id,
    )
    if process_instance.status == ProcessInstanceStatus.terminated.value:
        return process_instance
    if process_instance.status in (
        ProcessInstanceStatus.complete.value,
        ProcessInstanceStatus.error.value,
    ):
        raise InvalidStateError(
            "Cannot terminate a completed or errored process instance"
        )

    occurred_at = _resolve_timestamp(terminated_at)
    process_instance.status = ProcessInstanceStatus.terminated.value
    process_instance.ended_at = occurred_at
    process_instance.updated_at = occurred_at
    _close_process_instance_runtime_state(
        session,
        process_instance,
        occurred_at=occurred_at,
        user_id=user_id,
    )

    record_process_instance_event(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance_id,
        event_type=ProcessLifecycleEventType.process_instance_terminated,
        occurred_at=occurred_at,
        user_id=user_id,
    )
    session.flush()
    return process_instance


def _close_process_instance_runtime_state(
    session: Session,
    process_instance: ProcessInstanceModel,
    *,
    occurred_at: datetime,
    user_id: int | None,
) -> None:
    process_instance.task_updated_at = occurred_at
    for task in process_instance.tasks:
        if task.state != TaskState.get_name(TaskState.COMPLETED):
            task.state = M8F_TERMINATED_TASK_STATE
        task.ended_at = occurred_at
        if task.future_task is not None:
            task.future_task.completed = True
            task.future_task.archived_for_process_instance_status = True
            task.future_task.updated_at = occurred_at

    for work_item in process_instance.work_items:
        if work_item.completed:
            continue
        work_item = session.merge(work_item)
        close_work_item(
            work_item,
            state=WorkItemState.TERMINATED,
            occurred_at=occurred_at,
            user_id=user_id,
        )


def _reopen_process_instance_runtime_state(
    session: Session,
    process_instance: ProcessInstanceModel,
    *,
    occurred_at: datetime,
) -> None:
    process_instance.task_updated_at = occurred_at
    for task in process_instance.tasks:
        if task.state != M8F_TERMINATED_TASK_STATE:
            continue
        task.state = TaskState.get_name(TaskState.READY)
        task.started_at = None
        task.ended_at = None
        if task.future_task is not None:
            task.future_task.completed = False
            task.future_task.archived_for_process_instance_status = False
            task.future_task.updated_at = occurred_at

    for work_item in process_instance.work_items:
        if (
            work_item.task_status != WorkItemState.TERMINATED.value
            and not work_item.completed
        ):
            continue
        reopen_work_item(work_item, occurred_at=occurred_at)


def _delete_scheduled_process_retry_job(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
) -> None:
    delete_scheduler_job(
        session,
        tenant_id=tenant_id,
        job_key=_process_retry_scheduler_job_key(
            process_instance_id=process_instance_id
        ),
    )


def get_process_instance_metadata(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
) -> list[ProcessInstanceMetadataModel]:
    _load_process_instance(
        session, tenant_id=tenant_id, process_instance_id=process_instance_id
    )
    stmt = (
        select(ProcessInstanceMetadataModel)
        .where(
            ProcessInstanceMetadataModel.m8f_tenant_id == tenant_id,
            ProcessInstanceMetadataModel.process_instance_id == process_instance_id,
        )
        .order_by(
            ProcessInstanceMetadataModel.key,
            ProcessInstanceMetadataModel.id,
        )
    )
    return list(session.scalars(stmt).all())


def _load_process_instance(
    session: Session, *, tenant_id: str, process_instance_id: int
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


def _normalize_status(status: ProcessInstanceStatus | str) -> str:
    return ProcessInstanceStatus(status).value


def _process_retry_scheduler_job_key(*, process_instance_id: int) -> str:
    return build_scheduler_job_key(
        job_type=SchedulerJobType.process_retry,
        process_instance_id=process_instance_id,
    )


def _resolve_timestamp(timestamp: datetime | None) -> datetime:
    return timestamp or datetime.now(UTC)
