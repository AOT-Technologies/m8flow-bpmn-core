from __future__ import annotations

from types import SimpleNamespace

from m8flow_bpmn_core.services.work_items import (
    WorkItemState,
    claim_work_item,
    close_work_item,
    complete_work_item,
    reopen_work_item,
)


def _work_item() -> SimpleNamespace:
    return SimpleNamespace(
        task_id=None,
        task_guid="task-guid",
        actual_owner_id=None,
        completed_by_user_id=None,
        task_status=WorkItemState.READY.value,
        completed=False,
        updated_at_in_seconds=None,
    )


def test_work_item_claim_and_complete_transitions_preserve_legacy_fields() -> None:
    work_item = _work_item()

    claim_work_item(work_item, user_id=7, occurred_at=100)
    assert work_item.task_id == "task-guid"
    assert work_item.actual_owner_id == 7
    assert work_item.task_status == WorkItemState.CLAIMED.value
    assert work_item.completed is False
    assert work_item.updated_at_in_seconds == 100

    complete_work_item(work_item, user_id=7, occurred_at=110)
    assert work_item.completed is True
    assert work_item.completed_by_user_id == 7
    assert work_item.task_status == WorkItemState.COMPLETED.value
    assert work_item.updated_at_in_seconds == 110


def test_work_item_close_and_reopen_transitions() -> None:
    work_item = _work_item()

    close_work_item(
        work_item,
        state=WorkItemState.TERMINATED,
        occurred_at=200,
        user_id=9,
    )
    assert work_item.completed is True
    assert work_item.actual_owner_id == 9
    assert work_item.completed_by_user_id == 9
    assert work_item.task_status == WorkItemState.TERMINATED.value

    reopen_work_item(work_item, occurred_at=210)
    assert work_item.completed is False
    assert work_item.actual_owner_id is None
    assert work_item.completed_by_user_id is None
    assert work_item.task_status == WorkItemState.READY.value
    assert work_item.updated_at_in_seconds == 210
