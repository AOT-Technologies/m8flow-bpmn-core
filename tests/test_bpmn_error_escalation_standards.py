"""BPMN 2.0 error and escalation catch semantics (M8F-665, gap tracker BPMN-04).

Standards set: which catcher handles an event, by scope and by code. Uncaught
behaviour is product policy and lives in test_bpmn_error_escalation_policy.py.
"""

from __future__ import annotations

import pytest
from SpiffWorkflow.util.task import TaskState

from m8flow_core.services.workflow_runtime import _build_workflow

MARKERS = {
    "main_done",
    "before_throw",
    "inner_handled",
    "outer_handled",
    "handler_saw",
    "after_inner",
}


def _flow(flow_id: str, source: str, target: str) -> str:
    return (
        f'<bpmn:sequenceFlow id="{flow_id}" sourceRef="{source}" '
        f'targetRef="{target}" />'
    )


def _script(task_id: str, code: str) -> str:
    return (
        f'<bpmn:scriptTask id="{task_id}"><bpmn:script>{code}</bpmn:script>'
        "</bpmn:scriptTask>"
    )


def _bpmn(body: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions
  xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  id="Defs" targetNamespace="http://m8flow.example/events">
  <bpmn:error id="ErrA" name="ErrA" errorCode="A" />
  <bpmn:error id="ErrB" name="ErrB" errorCode="B" />
  <bpmn:escalation id="EscA" name="EscA" escalationCode="A" />
  <bpmn:process id="P" isExecutable="true">{body}</bpmn:process>
</bpmn:definitions>"""


def ERROR(ref: str | None) -> str:  # noqa: N802
    if ref is None:
        return "<bpmn:errorEventDefinition />"
    return f'<bpmn:errorEventDefinition errorRef="{ref}" />'


ESCALATION = '<bpmn:escalationEventDefinition escalationRef="EscA" />'


def _boundary(
    boundary_id: str,
    attached_to: str,
    definition: str,
    marker: str,
    *,
    cancel_activity: bool = True,
    code: str | None = None,
) -> str:
    """Boundary event whose path runs ``code`` (default: ``<marker> = True``)."""
    flag = "" if cancel_activity else ' cancelActivity="false"'
    return (
        f'<bpmn:boundaryEvent id="{boundary_id}" attachedToRef="{attached_to}"{flag}>'
        f"{definition}</bpmn:boundaryEvent>"
        f'{_script(boundary_id + "H", code or marker + " = True")}'
        f'<bpmn:endEvent id="{boundary_id}E" />'
        f'{_flow(boundary_id + "1", boundary_id, boundary_id + "H")}'
        f'{_flow(boundary_id + "2", boundary_id + "H", boundary_id + "E")}'
    )


def _handler(
    handler_id: str, definition: str, marker: str, *, interrupting: bool = True
) -> str:
    flag = "" if interrupting else ' isInterrupting="false"'
    return (
        f'<bpmn:subProcess id="{handler_id}" triggeredByEvent="true">'
        f'<bpmn:startEvent id="{handler_id}S"{flag}>{definition}</bpmn:startEvent>'
        f'{_script(handler_id + "H", marker + " = True; handler_saw = before_throw")}'
        f'<bpmn:endEvent id="{handler_id}E" />'
        f'{_flow(handler_id + "1", handler_id + "S", handler_id + "H")}'
        f'{_flow(handler_id + "2", handler_id + "H", handler_id + "E")}'
        "</bpmn:subProcess>"
    )


def _work(thrower: str, extra: str = "") -> str:
    """Embedded subprocess Work that runs ``thrower`` (id ``Boom``) first."""
    return (
        '<bpmn:subProcess id="Work"><bpmn:startEvent id="WorkStart" />'
        f'{thrower}{_flow("k1", "WorkStart", "Boom")}{extra}</bpmn:subProcess>'
    )


def _error_end(definition: str) -> str:
    return f'<bpmn:endEvent id="Boom">{definition}</bpmn:endEvent>'


def _escalation_then_continue() -> str:
    return (
        f'<bpmn:intermediateThrowEvent id="Boom">{ESCALATION}'
        "</bpmn:intermediateThrowEvent>"
        f'{_script("AfterInner", "after_inner = True")}<bpmn:endEvent id="WorkEnd" />'
        f'{_flow("k3", "Boom", "AfterInner")}{_flow("k4", "AfterInner", "WorkEnd")}'
    )


MAIN_START = (
    '<bpmn:startEvent id="StartEvent" />'
    f'{_script("Mark", "before_throw = True")}{_flow("f0", "StartEvent", "Mark")}'
)


def _process(work: str, outer: str = "") -> str:
    """StartEvent -> Mark (before_throw) -> Work -> After (main_done) -> EndEvent."""
    return _bpmn(
        f"{MAIN_START}{work}{outer}"
        f'{_script("After", "main_done = True")}<bpmn:endEvent id="EndEvent" />'
        f'{_flow("f1", "Mark", "Work")}{_flow("f2", "Work", "After")}'
        f'{_flow("f3", "After", "EndEvent")}'
    )


def _wrapped(work_with_inner_boundary: str, outer: str) -> str:
    """Wrap[ Work + inner boundary on Work ] with ``outer`` attached to Wrap."""
    wrap = (
        '<bpmn:subProcess id="Wrap"><bpmn:startEvent id="WrapStart" />'
        f'{work_with_inner_boundary}<bpmn:endEvent id="WrapEnd" />'
        f'{_flow("w1", "WrapStart", "Work")}{_flow("w2", "Work", "WrapEnd")}'
        "</bpmn:subProcess>"
    )
    return _bpmn(
        f"{MAIN_START}{wrap}{outer}"
        f'{_script("After", "main_done = True")}<bpmn:endEvent id="EndEvent" />'
        f'{_flow("f1", "Mark", "Wrap")}{_flow("f2", "Wrap", "After")}'
        f'{_flow("f3", "After", "EndEvent")}'
    )


def _run(xml: str):
    workflow = _build_workflow(bpmn_xml=xml, dmn_xml=None, bpmn_process_id=None)
    workflow.run_all(halt_on_manual=True)
    return workflow


def _markers(workflow) -> set[str]:
    return MARKERS & set(workflow.data)


# --- boundary events -----------------------------------------------------------


def test_non_matching_inner_boundary_is_skipped():
    xml = _wrapped(
        _work(_error_end(ERROR("ErrB")))
        + _boundary("InnerB", "Work", ERROR("ErrA"), "inner_handled"),
        _boundary("OuterB", "Wrap", ERROR("ErrB"), "outer_handled"),
    )
    assert _markers(_run(xml)) == {"before_throw", "outer_handled"}


def test_nearest_matching_boundary_wins():
    xml = _wrapped(
        _work(_error_end(ERROR("ErrB")))
        + _boundary("InnerB", "Work", ERROR("ErrB"), "inner_handled"),
        _boundary("OuterB", "Wrap", ERROR("ErrB"), "outer_handled"),
    )
    assert _markers(_run(xml)) == {"before_throw", "inner_handled", "main_done"}


def test_catch_all_error_boundary_catches_any_code():
    xml = _process(
        _work(_error_end(ERROR("ErrB"))),
        _boundary("OuterB", "Work", ERROR(None), "outer_handled"),
    )
    assert _markers(_run(xml)) == {"before_throw", "outer_handled"}


@pytest.mark.parametrize(
    ("thrower", "catcher"),
    [
        pytest.param(
            _escalation_then_continue(), ERROR("ErrA"), id="escalation-vs-error"
        ),
        pytest.param(
            _error_end(ERROR("ErrA")), ESCALATION, id="error-vs-escalation"
        ),
    ],
)
def test_error_and_escalation_are_not_cross_caught(thrower, catcher):
    catcher_xml = _boundary("OuterB", "Work", catcher, "outer_handled")
    xml = _process(_work(thrower), catcher_xml)
    assert "outer_handled" not in _markers(_run(xml))


def test_escalation_boundary_catches_and_interrupts_the_activity():
    xml = _process(
        _work(_escalation_then_continue()),
        _boundary("OuterB", "Work", ESCALATION, "outer_handled"),
    )
    assert _markers(_run(xml)) == {"before_throw", "outer_handled"}


def test_non_interrupting_escalation_boundary_runs_beside_the_activity():
    xml = _process(
        _work(_escalation_then_continue()),
        _boundary(
            "OuterB", "Work", ESCALATION, "outer_handled", cancel_activity=False
        ),
    )
    workflow = _run(xml)
    assert workflow.completed
    assert _markers(workflow) == {
        "before_throw",
        "after_inner",
        "main_done",
        "outer_handled",
    }


@pytest.mark.parametrize(
    ("thrower", "catcher"),
    [
        pytest.param(_error_end(ERROR("ErrB")), ERROR("ErrB"), id="error"),
        pytest.param(_escalation_then_continue(), ESCALATION, id="escalation"),
    ],
)
def test_process_data_is_kept_on_the_boundary_path(thrower, catcher):
    """QA #845 and #846: the boundary path sees data set before the throw."""
    code = "outer_handled = True; handler_saw = before_throw"
    xml = _process(
        _work(thrower),
        _boundary("OuterB", "Work", catcher, "outer_handled", code=code),
    )
    workflow = _run(xml)
    assert workflow.completed
    assert _markers(workflow) == {"before_throw", "outer_handled", "handler_saw"}
    assert workflow.data["handler_saw"] is True


# --- event subprocesses --------------------------------------------------------


def test_error_event_subprocess_does_not_run_when_nothing_is_thrown():
    xml = _bpmn(
        f'{MAIN_START}<bpmn:endEvent id="EndEvent" />{_flow("f1", "Mark", "EndEvent")}'
        + _handler("Outer", ERROR("ErrA"), "outer_handled")
    )
    assert _markers(_run(xml)) == {"before_throw"}


def test_error_event_subprocess_catches_and_interrupts_its_scope():
    xml = _process(
        _work(_error_end(ERROR("ErrB"))),
        _handler("Outer", ERROR("ErrB"), "outer_handled"),
    )
    workflow = _run(xml)
    assert workflow.completed
    assert _markers(workflow) == {"before_throw", "outer_handled", "handler_saw"}


def test_non_matching_error_event_subprocess_does_not_catch():
    xml = _process(
        _work(_error_end(ERROR("ErrB"))),
        _handler("Outer", ERROR("ErrA"), "outer_handled"),
    )
    assert "outer_handled" not in _markers(_run(xml))


@pytest.mark.parametrize(
    ("thrower", "catcher"),
    [
        pytest.param(
            _escalation_then_continue(), ERROR("ErrA"), id="escalation-vs-error"
        ),
        pytest.param(
            _error_end(ERROR("ErrA")), ESCALATION, id="error-vs-escalation"
        ),
    ],
)
def test_event_subprocess_does_not_catch_the_other_event_type(thrower, catcher):
    """Same code ``A`` on both, but an error catcher never takes an escalation."""
    xml = _process(_work(thrower), _handler("Outer", catcher, "outer_handled"))
    workflow = _run(xml)
    assert workflow.completed
    assert {"outer_handled", "handler_saw"}.isdisjoint(_markers(workflow))


def test_non_interrupting_escalation_event_subprocess_runs_beside_main_path():
    xml = _process(
        _work(_escalation_then_continue()),
        _handler("Outer", ESCALATION, "outer_handled", interrupting=False),
    )
    workflow = _run(xml)
    assert workflow.completed
    assert _markers(workflow) == {
        "before_throw",
        "after_inner",
        "main_done",
        "outer_handled",
        "handler_saw",
    }


def test_event_subprocess_in_inner_scope_wins_over_outer_one():
    xml = _process(
        _work(
            _error_end(ERROR("ErrB")),
            extra=_handler("Inner", ERROR("ErrB"), "inner_handled"),
        ),
        _handler("Outer", ERROR("ErrB"), "outer_handled"),
    )
    workflow = _run(xml)
    assert _markers(workflow) == {
        "before_throw",
        "inner_handled",
        "handler_saw",
        "main_done",
    }


# --- the interrupted activity's recorded state --------------------------------


def _state(workflow, bpmn_id: str) -> str:
    [task] = [
        task
        for task in workflow.get_tasks()
        if task.workflow is workflow and task.task_spec.bpmn_id == bpmn_id
    ]
    return TaskState.get_name(task.state)


@pytest.mark.parametrize(
    ("thrower", "catcher", "work_state"),
    [
        pytest.param(
            _error_end(ERROR("ErrB")),
            _handler("Outer", ERROR("ErrB"), "outer_handled"),
            "CANCELLED",
            id="error-event-subprocess",
        ),
        pytest.param(
            _escalation_then_continue(),
            _handler("Outer", ESCALATION, "outer_handled"),
            "CANCELLED",
            id="escalation-event-subprocess",
        ),
        pytest.param(
            _error_end(ERROR("ErrB")),
            _boundary("OuterB", "Work", ERROR("ErrB"), "outer_handled"),
            "CANCELLED",
            id="error-boundary",
        ),
        pytest.param(
            _escalation_then_continue(),
            _boundary("OuterB", "Work", ESCALATION, "outer_handled"),
            "CANCELLED",
            id="escalation-boundary",
        ),
        pytest.param(
            _escalation_then_continue(),
            _handler("Outer", ESCALATION, "outer_handled", interrupting=False),
            "COMPLETED",
            id="non-interrupting-event-subprocess",
        ),
        pytest.param(
            _escalation_then_continue(),
            _boundary(
                "OuterB", "Work", ESCALATION, "outer_handled", cancel_activity=False
            ),
            "COMPLETED",
            id="non-interrupting-boundary",
        ),
    ],
)
def test_interrupted_subprocess_is_recorded_as_cancelled(thrower, catcher, work_state):
    """An interrupted activity ends CANCELLED, not COMPLETED: cancelling it used to
    end its subprocess, whose completion callback revived the activity."""
    workflow = _run(_process(_work(thrower), catcher))

    assert workflow.completed
    assert _state(workflow, "Work") == work_state


# --- runtime exceptions are not BPMN errors ------------------------------------


@pytest.mark.parametrize(
    "catcher",
    [
        pytest.param(
            _boundary("OuterB", "Work", ERROR(None), "outer_handled"), id="boundary"
        ),
        pytest.param(
            _handler("Outer", ERROR(None), "outer_handled"), id="event-subprocess"
        ),
    ],
)
def test_script_exception_is_not_caught_as_a_bpmn_error(catcher):
    thrower = (
        f'{_script("Boom", "x = 1 / 0")}<bpmn:endEvent id="WorkEnd" />'
        f'{_flow("k3", "Boom", "WorkEnd")}'
    )
    xml = _process(_work(thrower), catcher)
    with pytest.raises(Exception, match="division by zero"):
        _run(xml)
