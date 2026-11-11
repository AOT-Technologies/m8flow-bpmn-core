"""M8Flow policy for uncaught error and escalation events (M8F-665, BPMN-04).

BPMN 2.0.2 leaves the process behaviour unspecified when no catcher matches.
M8Flow's policy, per M8F-665: an uncaught escalation is ignored and the flow
continues; an uncaught error end event ends its path like a plain end event.
Neither puts the instance into an error state.
"""

from __future__ import annotations

from test_bpmn_error_escalation_standards import (
    ERROR,
    _error_end,
    _escalation_then_continue,
    _markers,
    _process,
    _run,
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
