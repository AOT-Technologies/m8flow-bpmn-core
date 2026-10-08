"""Claim-state transitions for persisted workflow work items."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_bpmn_core.models.work_item import WorkItemModel


class WorkItemState(StrEnum):
    READY = "READY"
    CLAIMED = "CLAIMED"
    COMPLETED = "COMPLETED"
    TERMINATED = "TERMINATED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


def ensure_work_item(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
    task_guid: str,
    lane_assignment_id: int | None = None,
    occurred_at: datetime | None = None,
) -> WorkItemModel:
    item = session.scalar(
        select(WorkItemModel).where(
            WorkItemModel.m8f_tenant_id == tenant_id,
            WorkItemModel.task_guid == task_guid,
        )
    )
    if item is None:
        item = WorkItemModel(
            m8f_tenant_id=tenant_id,
            process_instance_id=process_instance_id,
            task_guid=task_guid,
            lane_assignment_id=lane_assignment_id,
            task_status=WorkItemState.READY.value,
            completed=False,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        session.add(item)
    elif lane_assignment_id is not None:
        item.lane_assignment_id = lane_assignment_id
    return item


def claim_work_item(
    item: WorkItemModel, *, user_id: int, occurred_at: datetime
) -> None:
    item.actual_owner_id = user_id
    item.task_status = WorkItemState.CLAIMED.value
    item.updated_at = occurred_at


def complete_work_item(
    item: WorkItemModel, *, user_id: int, occurred_at: datetime
) -> None:
    item.completed = True
    item.completed_by_user_id = user_id
    item.actual_owner_id = user_id
    item.task_status = WorkItemState.COMPLETED.value
    item.updated_at = occurred_at


def close_work_item(
    item: WorkItemModel,
    *,
    state: WorkItemState,
    occurred_at: datetime,
    user_id: int | None = None,
) -> None:
    item.completed = True
    item.completed_by_user_id = user_id
    item.task_status = state.value
    item.updated_at = occurred_at
    if user_id is not None:
        item.actual_owner_id = user_id


def reopen_work_item(item: WorkItemModel, *, occurred_at: datetime) -> None:
    item.completed = False
    item.completed_by_user_id = None
    item.actual_owner_id = None
    item.task_status = WorkItemState.READY.value
    item.updated_at = occurred_at


def prepare_work_item_for_ready_state(
    item: Any, *, occurred_at: datetime
) -> None:
    item.task_status = WorkItemState.READY.value
    item.completed = False
    item.completed_by_user_id = None
    item.actual_owner_id = None
    item.updated_at = occurred_at
