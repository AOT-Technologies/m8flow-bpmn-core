from __future__ import annotations

import time

from sqlalchemy import Select, exists, select
from sqlalchemy.orm import Session

from m8flow_bpmn_core.models.future_task import FutureTaskModel
from m8flow_bpmn_core.models.human_task import HumanTaskModel
from m8flow_bpmn_core.models.human_task_user import HumanTaskUserModel
from m8flow_bpmn_core.models.process_instance import (
    ProcessInstanceModel,
    ProcessInstanceStatus,
)
from m8flow_bpmn_core.models.process_instance_event import ProcessInstanceEventType
from m8flow_bpmn_core.services.process_instances import record_process_instance_event
from m8flow_bpmn_core.services.tenant_users import (
    ensure_user_belongs_to_tenant,
)
from m8flow_bpmn_core.services.workflow_runtime import (
    advance_process_instance_workflow,
)


def get_pending_tasks(
    session: Session, *, tenant_id: str, user_id: int | None = None
) -> list[HumanTaskModel]:
    if user_id is not None:
        ensure_user_belongs_to_tenant(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
        )

    stmt: Select[tuple[HumanTaskModel]] = select(HumanTaskModel).where(
        HumanTaskModel.m8f_tenant_id == tenant_id,
        HumanTaskModel.completed.is_(False),
    )

    if user_id is not None:
        stmt = stmt.where(
            exists(
                select(1).where(
                    HumanTaskUserModel.m8f_tenant_id == tenant_id,
                    HumanTaskUserModel.human_task_id == HumanTaskModel.id,
                    HumanTaskUserModel.user_id == user_id,
                )
            )
        )

    stmt = stmt.order_by(HumanTaskModel.id)
    return list(session.scalars(stmt).all())


def claim_task(
    session: Session,
    *,
    tenant_id: str,
    human_task_id: int,
    user_id: int,
    added_by: str = "manual",
) -> HumanTaskModel:
    ensure_user_belongs_to_tenant(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    human_task = _load_human_task(
        session, tenant_id=tenant_id, human_task_id=human_task_id
    )
    if human_task.completed:
        raise ValueError("Cannot claim a completed task")

    assignment = session.scalar(
        select(HumanTaskUserModel).where(
            HumanTaskUserModel.m8f_tenant_id == tenant_id,
            HumanTaskUserModel.human_task_id == human_task_id,
            HumanTaskUserModel.user_id == user_id,
        )
    )
    if assignment is None:
        session.add(
            HumanTaskUserModel(
                m8f_tenant_id=tenant_id,
                human_task_id=human_task_id,
                user_id=user_id,
                added_by=added_by,
            )
        )

    human_task.actual_owner_id = user_id
    human_task.task_status = "CLAIMED"
    if human_task.task_model is not None and human_task.task_model.state != "COMPLETED":
        human_task.task_model.state = "CLAIMED"
    session.flush()
    return human_task


def complete_task(
    session: Session,
    *,
    tenant_id: str,
    human_task_id: int,
    user_id: int,
    completed_at_in_seconds: int | None = None,
) -> HumanTaskModel:
    ensure_user_belongs_to_tenant(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    human_task = _load_human_task(
        session, tenant_id=tenant_id, human_task_id=human_task_id
    )
    if human_task.completed:
        raise ValueError("Task is already completed")

    if not _user_can_complete_task(
        session, tenant_id=tenant_id, human_task_id=human_task_id, user_id=user_id
    ):
        raise PermissionError("User is not assigned to this task")

    human_task.completed = True
    human_task.completed_by_user_id = user_id
    human_task.actual_owner_id = user_id
    human_task.task_status = "COMPLETED"

    if human_task.task_model is not None:
        human_task.task_model.state = "COMPLETED"
        human_task.task_model.end_in_seconds = (
            completed_at_in_seconds
            if completed_at_in_seconds is not None
            else round(time.time())
        )

    if human_task.task_guid is not None:
        future_task = session.get(FutureTaskModel, human_task.task_guid)
        if future_task is not None:
            future_task.completed = True

    process_instance = session.get(ProcessInstanceModel, human_task.process_instance_id)
    if (
        process_instance is not None
        and process_instance.workflow_state_json is not None
    ):
        process_instance = advance_process_instance_workflow(
            session,
            tenant_id=tenant_id,
            process_instance_id=human_task.process_instance_id,
            completed_task_guid=human_task.task_guid or human_task.task_model.guid,
            completed_at_in_seconds=completed_at_in_seconds,
        )
        completed_at = float(
            completed_at_in_seconds
            if completed_at_in_seconds is not None
            else round(time.time())
        )
        record_process_instance_event(
            session,
            tenant_id=tenant_id,
            process_instance_id=human_task.process_instance_id,
            event_type=ProcessInstanceEventType.task_completed,
            task_guid=human_task.task_guid,
            user_id=user_id,
            timestamp=completed_at,
        )
        if process_instance.status == ProcessInstanceStatus.complete.value:
            record_process_instance_event(
                session,
                tenant_id=tenant_id,
                process_instance_id=human_task.process_instance_id,
                event_type=ProcessInstanceEventType.process_instance_completed,
                task_guid=human_task.task_guid,
                user_id=user_id,
                timestamp=completed_at,
            )

    session.flush()
    return human_task


def _load_human_task(
    session: Session, *, tenant_id: str, human_task_id: int
) -> HumanTaskModel:
    human_task = session.scalar(
        select(HumanTaskModel).where(
            HumanTaskModel.m8f_tenant_id == tenant_id,
            HumanTaskModel.id == human_task_id,
        )
    )
    if human_task is None:
        raise LookupError(
            f"Human task {human_task_id} was not found for tenant {tenant_id}"
        )
    return human_task


def _user_can_complete_task(
    session: Session, *, tenant_id: str, human_task_id: int, user_id: int
) -> bool:
    assignment = session.scalar(
        select(HumanTaskUserModel).where(
            HumanTaskUserModel.m8f_tenant_id == tenant_id,
            HumanTaskUserModel.human_task_id == human_task_id,
            HumanTaskUserModel.user_id == user_id,
        )
    )
    if assignment is not None:
        return True

    human_task = session.scalar(
        select(HumanTaskModel).where(
            HumanTaskModel.m8f_tenant_id == tenant_id,
            HumanTaskModel.id == human_task_id,
            HumanTaskModel.actual_owner_id == user_id,
        )
    )
    return human_task is not None
