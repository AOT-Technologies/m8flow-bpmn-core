from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from m8flow_bpmn_core.services.work_items import (
    WorkItemState,
    claim_work_item,
    close_work_item,
    complete_work_item,
    ensure_work_item,
    reopen_work_item,
)


def _work_item() -> SimpleNamespace:
    return SimpleNamespace(
        task_guid="task-guid",
        actual_owner_id=None,
        completed_by_user_id=None,
        task_status=WorkItemState.READY.value,
        completed=False,
        updated_at=None,
    )


def _companion() -> SimpleNamespace:
    return SimpleNamespace(
        task_guid="task-guid",
        lane_assignment_id=None,
        actual_owner_id=None,
        completed_by_user_id=None,
        task_status=WorkItemState.READY.value,
        completed=False,
        updated_at=None,
    )


class _Session:
    def __init__(self) -> None:
        self.added: list[object] = []

    def flush(self) -> None:
        return None

    def get(self, model, identifier):
        return None

    def add(self, value: object) -> None:
        self.added.append(value)


def test_ensure_work_item_creates_missing_work_item() -> None:
    session = _Session()

    # The in-memory stub models the canonical lookup contract.  Database
    # sessions provide the scalar query used by ensure_work_item.
    session.scalar = lambda query: None
    companion = ensure_work_item(
        session,
        tenant_id="tenant-a",
        process_instance_id=22,
        task_guid="task-guid",
    )

    assert companion.m8f_tenant_id == "tenant-a"
    assert companion.process_instance_id == 22
    assert session.added == [companion]


def test_work_item_claim_and_complete_transitions() -> None:
    work_item = _work_item()
    work_item.work_item = _companion()

    claim_work_item(
        work_item,
        user_id=7,
        occurred_at=datetime.fromtimestamp(100, UTC),
    )
    assert work_item.actual_owner_id == 7
    assert work_item.task_status == WorkItemState.CLAIMED.value
    assert work_item.completed is False
    assert work_item.updated_at.timestamp() == 100

    complete_work_item(
        work_item,
        user_id=7,
        occurred_at=datetime.fromtimestamp(110, UTC),
    )
    assert work_item.completed is True
    assert work_item.completed_by_user_id == 7
    assert work_item.task_status == WorkItemState.COMPLETED.value
    assert work_item.updated_at.timestamp() == 110


def test_work_item_close_and_reopen_transitions() -> None:
    work_item = _work_item()
    work_item.work_item = _companion()

    close_work_item(
        work_item,
        state=WorkItemState.TERMINATED,
        occurred_at=datetime.fromtimestamp(200, UTC),
        user_id=9,
    )
    assert work_item.completed is True
    assert work_item.actual_owner_id == 9
    assert work_item.completed_by_user_id == 9
    assert work_item.task_status == WorkItemState.TERMINATED.value

    reopen_work_item(
        work_item,
        occurred_at=datetime.fromtimestamp(210, UTC),
    )
    assert work_item.completed is False
    assert work_item.actual_owner_id is None
    assert work_item.completed_by_user_id is None
    assert work_item.task_status == WorkItemState.READY.value
    assert work_item.updated_at.timestamp() == 210
