"""Internal work-item state transitions over the legacy human-task row."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


class WorkItemState(StrEnum):
    READY = "READY"
    CLAIMED = "CLAIMED"
    COMPLETED = "COMPLETED"
    TERMINATED = "TERMINATED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


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


def reopen_work_item(work_item: Any, *, occurred_at: int) -> None:
    work_item.completed = False
    work_item.completed_by_user_id = None
    work_item.actual_owner_id = None
    work_item.task_status = WorkItemState.READY.value
    work_item.updated_at_in_seconds = occurred_at


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
