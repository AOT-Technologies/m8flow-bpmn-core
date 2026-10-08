from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from SpiffWorkflow.util.task import TaskState
from sqlalchemy import Select, exists, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from m8flow_bpmn_core.errors import AuthorizationError, InvalidStateError, NotFoundError
from m8flow_bpmn_core.models.future_task import FutureTaskModel
from m8flow_bpmn_core.models.process_instance import (
    ProcessInstanceModel,
    ProcessInstanceStatus,
)
from m8flow_bpmn_core.models.process_instance_event import (
    ProcessLifecycleEventType,
    TaskEventType,
)
from m8flow_bpmn_core.models.user_group_assignment import UserGroupAssignmentModel
from m8flow_bpmn_core.models.work_item import WorkItemModel
from m8flow_bpmn_core.models.work_item_user import WorkItemUserModel
from m8flow_bpmn_core.services.authorization import (
    TASK_CLAIM_COMMAND,
    TASK_COMPLETE_COMMAND,
    require_command_authorization,
)
from m8flow_bpmn_core.services.process_instances import (
    record_process_instance_event,
    upsert_process_instance_metadata,
)
from m8flow_bpmn_core.services.tenant_users import ensure_user_belongs_to_tenant
from m8flow_bpmn_core.services.work_items import complete_work_item
from m8flow_bpmn_core.services.workflow_runtime import advance_process_instance_workflow


def get_pending_tasks(
    session: Session, *, tenant_id: str, user_id: int | None = None
) -> list[WorkItemModel]:
    if user_id is not None:
        ensure_user_belongs_to_tenant(session, tenant_id=tenant_id, user_id=user_id)
    stmt: Select[tuple[WorkItemModel]] = select(WorkItemModel).where(
        WorkItemModel.m8f_tenant_id == tenant_id,
        WorkItemModel.completed.is_(False),
    )
    if user_id is not None:
        stmt = stmt.where(
            exists(
                select(1).where(
                    WorkItemUserModel.work_item_id == WorkItemModel.id,
                    WorkItemUserModel.m8f_tenant_id == tenant_id,
                    WorkItemUserModel.user_id == user_id,
                )
            )
        )
    return list(session.scalars(stmt.order_by(WorkItemModel.id)).all())


def assign_pending_tasks_for_user(
    session: Session, *, tenant_id: str, user_id: int
) -> list[WorkItemModel]:
    ensure_user_belongs_to_tenant(session, tenant_id=tenant_id, user_id=user_id)
    group_ids = select(UserGroupAssignmentModel.group_id).where(
        UserGroupAssignmentModel.user_id == user_id
    )
    assignment_exists = exists(
        select(1).where(
            WorkItemUserModel.work_item_id == WorkItemModel.id,
            WorkItemUserModel.user_id == user_id,
        )
    )
    items = list(
        session.scalars(
            select(WorkItemModel)
            .where(
                WorkItemModel.m8f_tenant_id == tenant_id,
                WorkItemModel.completed.is_(False),
                WorkItemModel.lane_assignment_id.in_(group_ids),
                ~assignment_exists,
            )
            .order_by(WorkItemModel.id)
        ).all()
    )
    for item in items:
        session.add(
            WorkItemUserModel(
                m8f_tenant_id=tenant_id,
                work_item_id=item.id,
                user_id=user_id,
                added_by="lane_assignment",
            )
        )
    session.flush()
    return items


def claim_task(
    session: Session,
    *,
    tenant_id: str,
    work_item_id: int,
    user_id: int,
    added_by: str = "manual",
) -> WorkItemModel:
    ensure_user_belongs_to_tenant(session, tenant_id=tenant_id, user_id=user_id)
    item = _load_work_item(session, tenant_id=tenant_id, work_item_id=work_item_id)
    require_command_authorization(
        session,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        command_key=TASK_CLAIM_COMMAND,
        resource_type="task",
        resource_id=item.task_guid or str(item.id),
        metadata=_task_authorization_metadata(item),
    )
    if item.completed:
        raise InvalidStateError("Cannot claim a completed task")
    if not _user_is_assigned(session, tenant_id, item.id, user_id):
        raise AuthorizationError("User is not assigned to this task")
    if item.actual_owner_id is not None and item.actual_owner_id != user_id:
        raise AuthorizationError("Task is already claimed by another user")
    claimed_at = datetime.now(UTC)
    # Compare-and-set the canonical row.  This closes the race between the
    # ownership check above and the claim write when two workers claim the
    # same work item concurrently.
    claim_result = session.execute(
        update(WorkItemModel)
        .where(
            WorkItemModel.id == item.id,
            WorkItemModel.m8f_tenant_id == tenant_id,
            WorkItemModel.completed.is_(False),
            (WorkItemModel.actual_owner_id.is_(None))
            | (WorkItemModel.actual_owner_id == user_id),
        )
        .values(
            actual_owner_id=user_id,
            task_status="CLAIMED",
            updated_at=claimed_at,
        )
    )
    if not isinstance(claim_result, CursorResult) or claim_result.rowcount != 1:
        raise InvalidStateError("Task claim lost a concurrent update")
    session.refresh(item)
    process_instance = session.get(ProcessInstanceModel, item.process_instance_id)
    if process_instance is not None:
        process_instance.task_updated_at = claimed_at
    session.flush()
    return item


def complete_task(
    session: Session,
    *,
    tenant_id: str,
    work_item_id: int,
    user_id: int,
    completed_at: datetime | None = None,
    task_payload: Mapping[str, object] | None = None,
) -> WorkItemModel:
    ensure_user_belongs_to_tenant(session, tenant_id=tenant_id, user_id=user_id)
    item = _load_work_item(session, tenant_id=tenant_id, work_item_id=work_item_id)
    require_command_authorization(
        session,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        command_key=TASK_COMPLETE_COMMAND,
        resource_type="task",
        resource_id=item.task_guid or str(item.id),
        metadata=_task_authorization_metadata(item),
    )
    if item.completed:
        raise InvalidStateError("Task is already completed")
    if not _user_is_assigned(session, tenant_id, item.id, user_id):
        raise AuthorizationError("User is not assigned to this task")
    if item.actual_owner_id is None:
        raise InvalidStateError("Task must be claimed before completion")
    if item.actual_owner_id != user_id:
        raise AuthorizationError("User does not own this task")
    completed_at = completed_at or datetime.now(UTC)
    _persist_task_payload(
        session,
        tenant_id=tenant_id,
        process_instance_id=item.process_instance_id,
        task_payload=task_payload,
        completed_at=completed_at,
    )
    complete_work_item(item, user_id=user_id, occurred_at=completed_at)
    task_model = item.task_model
    if task_model is not None:
        task_model.state = TaskState.get_name(TaskState.COMPLETED)
        task_model.ended_at = completed_at
        completed_guid = task_model.guid
    else:
        completed_guid = item.task_guid
    if completed_guid is not None:
        future_task = session.get(FutureTaskModel, completed_guid)
        if future_task is not None:
            future_task.completed = True
    process_instance = session.get(ProcessInstanceModel, item.process_instance_id)
    if process_instance is not None:
        process_instance.task_updated_at = completed_at
        if process_instance.workflow_state_json is not None:
            process_instance = advance_process_instance_workflow(
                session,
                tenant_id=tenant_id,
                process_instance_id=item.process_instance_id,
                completed_task_guid=completed_guid or "",
                completed_at=completed_at,
            )
            record_process_instance_event(
                session,
                tenant_id=tenant_id,
                process_instance_id=item.process_instance_id,
                event_type=TaskEventType.task_completed,
                task_guid=item.task_guid,
                user_id=user_id,
                occurred_at=completed_at,
            )
            if process_instance.status == ProcessInstanceStatus.complete.value:
                record_process_instance_event(
                    session,
                    tenant_id=tenant_id,
                    process_instance_id=item.process_instance_id,
                    event_type=ProcessLifecycleEventType.process_instance_completed,
                    task_guid=item.task_guid,
                    user_id=user_id,
                    occurred_at=completed_at,
                )
    session.flush()
    return item


def _load_work_item(
    session: Session, *, tenant_id: str, work_item_id: int
) -> WorkItemModel:
    item = session.scalar(
        select(WorkItemModel).where(
            WorkItemModel.m8f_tenant_id == tenant_id,
            WorkItemModel.id == work_item_id,
        )
    )
    if item is None:
        raise NotFoundError(
            f"Work item {work_item_id} was not found for tenant {tenant_id}"
        )
    return item


def _user_is_assigned(
    session: Session, tenant_id: str, work_item_id: int, user_id: int
) -> bool:
    return session.scalar(
        select(WorkItemUserModel).where(
            WorkItemUserModel.m8f_tenant_id == tenant_id,
            WorkItemUserModel.work_item_id == work_item_id,
            WorkItemUserModel.user_id == user_id,
        )
    ) is not None


def _persist_task_payload(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
    task_payload: Mapping[str, object] | None,
    completed_at: datetime,
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
            updated_at=completed_at,
            created_at=completed_at,
        )


def _task_authorization_metadata(item: WorkItemModel) -> dict[str, object]:
    return {
        "work_item_id": item.id,
        "process_instance_id": item.process_instance_id,
        "task_guid": item.task_guid,
        "task_name": item.task_name,
        "task_status": item.task_status,
        "actual_owner_id": item.actual_owner_id,
        "lane_name": item.lane_name,
        "lane_assignment_id": item.lane_assignment_id,
        "completed": item.completed,
    }
