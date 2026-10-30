"""Resume and retry transitions that re-enter the workflow runtime.

These live apart from ``process_instances`` because they call back into
``workflow_runtime``, which itself depends on ``process_instances``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from m8flow_core.errors import InvalidStateError
from m8flow_core.models.process_instance import (
    ProcessInstanceModel,
    ProcessInstanceStatus,
)
from m8flow_core.models.process_instance_event import ProcessLifecycleEventType
from m8flow_core.services.authorization import (
    PROCESS_RESUME_COMMAND,
    PROCESS_RETRY_COMMAND,
    require_command_authorization,
)
from m8flow_core.services.process_instances import (
    _delete_scheduled_process_retry_job,
    _load_process_instance,
    _reopen_process_instance_runtime_state,
    _resolve_timestamp,
    record_process_instance_event,
)
from m8flow_core.services.tenant_users import ensure_user_belongs_to_tenant
from m8flow_core.services.workflow_runtime import (
    retry_errored_service_task_workflow_if_needed,
    sync_open_work_item_potential_owners,
)


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
    sync_open_work_item_potential_owners(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance.id,
    )
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
    sync_open_work_item_potential_owners(
        session,
        tenant_id=tenant_id,
        process_instance_id=process_instance.id,
    )
    session.flush()
    return process_instance
