from __future__ import annotations

import time
from collections.abc import Mapping

from SpiffWorkflow.util.task import TaskState
from sqlalchemy import Select, exists, select
from sqlalchemy.orm import Session

from m8flow_bpmn_core.errors import (
    AuthorizationError,
    InvalidStateError,
    NotFoundError,
)
from m8flow_bpmn_core.models.future_task import FutureTaskModel
from m8flow_bpmn_core.models.human_task import HumanTaskModel
from m8flow_bpmn_core.models.human_task_user import HumanTaskUserModel
from m8flow_bpmn_core.models.process_instance import (
    ProcessInstanceModel,
    ProcessInstanceStatus,
)
from m8flow_bpmn_core.models.process_instance_event import ProcessInstanceEventType
from m8flow_bpmn_core.models.user_group_assignment import UserGroupAssignmentModel
from m8flow_bpmn_core.services.authorization import (
    TASK_CLAIM_COMMAND,
    TASK_COMPLETE_COMMAND,
    require_command_authorization,
)
from m8flow_bpmn_core.services.process_instances import (
    record_process_instance_event,
    upsert_process_instance_metadata,
)
from m8flow_bpmn_core.services.tenant_users import (
    ensure_user_belongs_to_tenant,
)
from m8flow_bpmn_core.services.work_items import (
    claim_work_item,
    complete_work_item,
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


def assign_pending_tasks_for_user(
    session: Session,
    *,
    tenant_id: str,
    user_id: int,
) -> list[HumanTaskModel]:
    """Add a user to pending tasks for the lane groups they belong to.

    This is intended for hosts to call after synchronizing directory
    membership. It creates potential-owner rows only; the user must still
    claim a task before it can be completed. Repeated calls are idempotent.
    """
    ensure_user_belongs_to_tenant(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
    )

    user_group_ids = select(UserGroupAssignmentModel.group_id).where(
        UserGroupAssignmentModel.user_id == user_id,
    )
    existing_assignment = exists(
        select(1).where(
            HumanTaskUserModel.m8f_tenant_id == tenant_id,
            HumanTaskUserModel.human_task_id == HumanTaskModel.id,
            HumanTaskUserModel.user_id == user_id,
        )
    )
    tasks = list(
        session.scalars(
            select(HumanTaskModel)
            .where(
                HumanTaskModel.m8f_tenant_id == tenant_id,
                HumanTaskModel.completed.is_(False),
                HumanTaskModel.lane_assignment_id.in_(user_group_ids),
                ~existing_assignment,
            )
            .order_by(HumanTaskModel.id)
        ).all()
    )
    for human_task in tasks:
        session.add(
            HumanTaskUserModel(
                m8f_tenant_id=tenant_id,
                human_task_id=human_task.id,
                user_id=user_id,
                added_by="lane_assignment",
            )
        )
    session.flush()
    return tasks


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
    require_command_authorization(
        session,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        command_key=TASK_CLAIM_COMMAND,
        target_uri=f"/tasks/{human_task.id}",
        target_id=human_task.id,
        metadata=_task_authorization_metadata(human_task),
    )
    if human_task.completed:
        raise InvalidStateError("Cannot claim a completed task")

    if not _user_is_assigned_to_task(
        session,
        tenant_id=tenant_id,
        human_task_id=human_task_id,
        user_id=user_id,
    ):
        raise AuthorizationError("User is not assigned to this task")
    if (
        human_task.actual_owner_id is not None
        and human_task.actual_owner_id != user_id
    ):
        raise AuthorizationError("Task is already claimed by another user")

    claimed_at = round(time.time())
    claim_work_item(
        human_task,
        user_id=user_id,
        occurred_at=claimed_at,
    )
    process_instance = session.get(ProcessInstanceModel, human_task.process_instance_id)
    if process_instance is not None:
        process_instance.task_updated_at_in_seconds = claimed_at
    session.flush()
    return human_task


def complete_task(
    session: Session,
    *,
    tenant_id: str,
    human_task_id: int,
    user_id: int,
    completed_at_in_seconds: int | None = None,
    task_payload: Mapping[str, object] | None = None,
) -> HumanTaskModel:
    ensure_user_belongs_to_tenant(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    human_task = _load_human_task(
        session, tenant_id=tenant_id, human_task_id=human_task_id
    )
    require_command_authorization(
        session,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        command_key=TASK_COMPLETE_COMMAND,
        target_uri=f"/tasks/{human_task.id}",
        target_id=human_task.id,
        metadata=_task_authorization_metadata(human_task),
    )
    if human_task.completed:
        raise InvalidStateError("Task is already completed")

    if not _user_is_assigned_to_task(
        session,
        tenant_id=tenant_id,
        human_task_id=human_task_id,
        user_id=user_id,
    ):
        raise AuthorizationError("User is not assigned to this task")
    if human_task.actual_owner_id is None:
        raise InvalidStateError("Task must be claimed before completion")
    if human_task.actual_owner_id != user_id:
        raise AuthorizationError("User does not own this task")

    completed_at = (
        completed_at_in_seconds
        if completed_at_in_seconds is not None
        else round(time.time())
    )
    _persist_task_payload(
        session,
        tenant_id=tenant_id,
        process_instance_id=human_task.process_instance_id,
        task_payload=task_payload,
        completed_at_in_seconds=completed_at,
    )

    complete_work_item(
        human_task,
        user_id=user_id,
        occurred_at=completed_at,
    )

    if human_task.task_model is not None:
        human_task.task_model.state = TaskState.get_name(TaskState.COMPLETED)
        human_task.task_model.end_in_seconds = float(completed_at)

    if human_task.task_guid is not None:
        future_task = session.get(FutureTaskModel, human_task.task_guid)
        if future_task is not None:
            future_task.completed = True

    process_instance = session.get(ProcessInstanceModel, human_task.process_instance_id)
    if process_instance is not None:
        process_instance.task_updated_at_in_seconds = completed_at
    if (
        process_instance is not None
        and process_instance.workflow_state_json is not None
    ):
        process_instance = advance_process_instance_workflow(
            session,
            tenant_id=tenant_id,
            process_instance_id=human_task.process_instance_id,
            completed_task_guid=human_task.task_guid or human_task.task_model.guid,
            completed_at_in_seconds=completed_at,
        )
        record_process_instance_event(
            session,
            tenant_id=tenant_id,
            process_instance_id=human_task.process_instance_id,
            event_type=ProcessInstanceEventType.task_completed,
            task_guid=human_task.task_guid,
            user_id=user_id,
            timestamp=float(completed_at),
        )
        if process_instance.status == ProcessInstanceStatus.complete.value:
            record_process_instance_event(
                session,
                tenant_id=tenant_id,
                process_instance_id=human_task.process_instance_id,
                event_type=ProcessInstanceEventType.process_instance_completed,
                task_guid=human_task.task_guid,
                user_id=user_id,
                timestamp=float(completed_at),
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
        raise NotFoundError(
            f"Human task {human_task_id} was not found for tenant {tenant_id}"
        )
    return human_task


def _user_is_assigned_to_task(
    session: Session, *, tenant_id: str, human_task_id: int, user_id: int
) -> bool:
    assignment = session.scalar(
        select(HumanTaskUserModel).where(
            HumanTaskUserModel.m8f_tenant_id == tenant_id,
            HumanTaskUserModel.human_task_id == human_task_id,
            HumanTaskUserModel.user_id == user_id,
        )
    )
    return assignment is not None


def _persist_task_payload(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
    task_payload: Mapping[str, object] | None,
    completed_at_in_seconds: int,
) -> None:
    if not task_payload:
        return

    for key, value in task_payload.items():
        upsert_process_instance_metadata(
            session,
            tenant_id=tenant_id,
            process_instance_id=process_instance_id,
            key=str(key),
            value=str(value),
            updated_at_in_seconds=completed_at_in_seconds,
            created_at_in_seconds=completed_at_in_seconds,
        )


def _task_authorization_metadata(
    human_task: HumanTaskModel,
) -> dict[str, object]:
    return {
        "human_task_id": human_task.id,
        "process_instance_id": human_task.process_instance_id,
        "task_guid": human_task.task_guid,
        "task_name": human_task.task_name,
        "task_status": human_task.task_status,
        "actual_owner_id": human_task.actual_owner_id,
        "lane_name": human_task.lane_name,
        "lane_assignment_id": human_task.lane_assignment_id,
        "completed": human_task.completed,
    }
