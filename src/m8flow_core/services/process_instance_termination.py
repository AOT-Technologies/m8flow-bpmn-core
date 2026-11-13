#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

"""Process termination that also cancels pending compensation in the runtime."""

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
    PROCESS_TERMINATE_COMMAND,
    require_command_authorization,
)
from m8flow_core.services.process_instances import (
    _close_process_instance_runtime_state,
    _load_process_instance,
    _resolve_timestamp,
    record_process_instance_event,
)
from m8flow_core.services.signal_registry import retire_instance_signals
from m8flow_core.services.tenant_users import ensure_user_belongs_to_tenant
from m8flow_core.services.workflow_runtime import _cancel_compensation


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
        session, tenant_id=tenant_id, process_instance_id=process_instance_id, lock=True
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
    _cancel_compensation(session, process_instance, occurred_at)
    retire_instance_signals(session, process_instance)
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
