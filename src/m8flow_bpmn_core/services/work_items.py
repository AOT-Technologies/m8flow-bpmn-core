"""Internal work-item state transitions over the legacy human-task row."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from m8flow_bpmn_core.models.human_task import HumanTaskModel
from m8flow_bpmn_core.models.work_item import WorkItemModel


class WorkItemState(StrEnum):
    READY = "READY"
    CLAIMED = "CLAIMED"
    COMPLETED = "COMPLETED"
    TERMINATED = "TERMINATED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


def ensure_work_item(session: Session, human_task: HumanTaskModel) -> WorkItemModel:
    """Return the normalized row paired with a legacy human-task row.

    The migration creates these rows for existing data.  This helper also
    covers newly materialized tasks and hosts that create a human task through
    the compatibility model, so future writes do not leave the normalized
    table incomplete.
    """
    if human_task.id is None:
        session.flush()

    # Query by the shared primary key instead of touching the relationship.
    # Loading that collection while a workflow is materializing assignments
    # can trigger an autoflush before all potential-owner rows are reconciled.
    companion = session.get(WorkItemModel, human_task.id)
    if companion is None:
        companion = WorkItemModel(
            id=human_task.id,
            m8f_tenant_id=human_task.m8f_tenant_id,
            process_instance_id=human_task.process_instance_id,
            task_guid=human_task.task_guid,
            task_id=human_task.task_id,
            lane_assignment_id=human_task.lane_assignment_id,
            completed_by_user_id=human_task.completed_by_user_id,
            actual_owner_id=human_task.actual_owner_id,
            task_status=human_task.task_status,
            completed=human_task.completed,
            created_at_in_seconds=human_task.created_at_in_seconds,
            updated_at_in_seconds=human_task.updated_at_in_seconds,
            created_at=human_task.created_at,
            updated_at=human_task.updated_at,
        )
        session.add(companion)
    return companion


def _sync_companion_work_item(work_item: Any) -> None:
    """Mirror state onto the normalized row when one is loaded."""
    companion = getattr(work_item, "work_item", None)
    if companion is None or companion is work_item:
        return

    for field in (
        "task_id",
        "task_guid",
        "lane_assignment_id",
        "completed_by_user_id",
        "actual_owner_id",
        "task_status",
        "completed",
        "updated_at_in_seconds",
    ):
        if hasattr(work_item, field) and hasattr(companion, field):
            setattr(companion, field, getattr(work_item, field))


def claim_work_item(
    work_item: Any,
    *,
    user_id: int,
    occurred_at: int,
) -> None:
    work_item.task_id = work_item.task_id or work_item.task_guid
    work_item.actual_owner_id = user_id
    work_item.task_status = WorkItemState.CLAIMED.value
    work_item.updated_at_in_seconds = occurred_at
    _sync_companion_work_item(work_item)


def complete_work_item(
    work_item: Any,
    *,
    user_id: int,
    occurred_at: int,
) -> None:
    work_item.completed = True
    work_item.completed_by_user_id = user_id
    work_item.actual_owner_id = user_id
    work_item.task_status = WorkItemState.COMPLETED.value
    work_item.task_id = work_item.task_id or work_item.task_guid
    work_item.updated_at_in_seconds = occurred_at
    _sync_companion_work_item(work_item)


def close_work_item(
    work_item: Any,
    *,
    state: WorkItemState,
    occurred_at: int,
    user_id: int | None = None,
) -> None:
    work_item.completed = True
    work_item.completed_by_user_id = user_id
    work_item.task_status = state.value
    work_item.updated_at_in_seconds = occurred_at
    if user_id is not None:
        work_item.actual_owner_id = user_id
    _sync_companion_work_item(work_item)


def reopen_work_item(work_item: Any, *, occurred_at: int) -> None:
    work_item.completed = False
    work_item.completed_by_user_id = None
    work_item.actual_owner_id = None
    work_item.task_status = WorkItemState.READY.value
    work_item.updated_at_in_seconds = occurred_at
    _sync_companion_work_item(work_item)


def prepare_work_item_for_ready_state(
    work_item: Any,
    *,
    occurred_at: int,
) -> None:
    work_item.task_status = WorkItemState.READY.value
    work_item.completed = False
    work_item.completed_by_user_id = None
    work_item.actual_owner_id = None
    work_item.updated_at_in_seconds = occurred_at
    _sync_companion_work_item(work_item)
