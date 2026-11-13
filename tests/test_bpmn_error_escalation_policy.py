"""M8Flow policy for uncaught error and escalation events (M8F-665, BPMN-04).

BPMN 2.0.2 leaves the process behaviour unspecified when no catcher matches.
M8Flow's policy, per M8F-665: an uncaught escalation is ignored and the flow
continues; an uncaught error end event ends its path like a plain end event.
Neither puts the instance into an error state.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_core import api
from m8flow_core.models.process_instance_event import ProcessInstanceEventModel
from m8flow_core.models.work_item import WorkItemModel
from test_bpmn_error_escalation_runtime import (
    REVIEW,
    _complete_review,
    _pending,
    _start,
    _status,
    _task_states,
)
from test_bpmn_error_escalation_standards import (
    ERROR,
    MAIN_START,
    _bpmn,
    _error_end,
    _escalation_then_continue,
    _flow,
    _markers,
    _process,
    _run,
    _script,
    _work,
)


def test_uncaught_escalation_is_ignored_and_the_flow_continues():
    workflow = _run(_process(_work(_escalation_then_continue())))
    assert workflow.completed
    assert _markers(workflow) == {"before_throw", "after_inner", "main_done"}


def test_uncaught_error_end_event_ends_its_path():
    workflow = _run(_process(_work(_error_end(ERROR("ErrA")))))
    assert workflow.completed
    assert _markers(workflow) == {"before_throw", "main_done"}


@pytest.mark.parametrize(
    ("thrower", "ran"),
    [
        pytest.param(
            _escalation_then_continue(),
            {"Work::Boom", "Work::AfterInner", "After"},
            id="escalation",
        ),
        pytest.param(
            _error_end(ERROR("ErrA")), {"Work::Boom", "After"}, id="error"
        ),
    ],
)
def test_uncaught_event_is_persisted_as_a_completed_instance(
    session: Session, thrower: str, ran: set[str]
) -> None:
    """Through the API: the instance, its tasks and its work item stay clean."""
    xml = _bpmn(
        f"{MAIN_START}{REVIEW}{_work(thrower)}"
        f'{_script("After", "main_done = True")}<bpmn:endEvent id="EndEvent" />'
        f'{_flow("f1", "Mark", "Review")}{_flow("f2", "Review", "Work")}'
        f'{_flow("f3", "Work", "After")}{_flow("f4", "After", "EndEvent")}'
    )
    tenant, user, instance = _start(session, xml)

    _complete_review(session, tenant, user)

    assert _status(session, tenant, instance) == api.ProcessInstanceStatus.complete
    assert _pending(session, tenant, user) == []
    states = _task_states(session, instance)
    for name in {"Review", "Work", *ran}:
        assert states[name] == {"COMPLETED"}, name
    assert all(state_set <= {"COMPLETED"} for state_set in states.values()), states
    work_items = session.scalars(
        select(WorkItemModel).where(WorkItemModel.process_instance_id == instance.id)
    ).all()
    assert [(item.completed, item.completed_by_user_id) for item in work_items] == [
        (True, user.id)
    ]
    event_types = set(
        session.scalars(
            select(ProcessInstanceEventModel.event_type).where(
                ProcessInstanceEventModel.process_instance_id == instance.id
            )
        )
    )
    assert {"process_instance_error", "task_failed"}.isdisjoint(event_types)
    assert "process_instance_completed" in event_types
