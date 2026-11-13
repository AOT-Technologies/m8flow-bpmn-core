from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from m8flow_core import api
from m8flow_core.models.tenant import M8flowTenantModel
from m8flow_core.models.user import UserModel
from m8flow_core.services.authorization import ROLE_ADMIN, ensure_v1_role

FIXTURES = Path(__file__).with_name("fixtures")


# Each fixture runs a sequential then a parallel script-task loop, and its
# final script raises unless both output collections hold [10, 20, 30]. A
# complete instance proves each loop ran once per instance and mapped its
# outputs (M8F-668).
@pytest.mark.parametrize(
    "fixture",
    [
        # Collection-driven: loopDataInputRef over [1, 2, 3].
        "simple-multi-instance.bpmn",
        # loopCardinality: a literal 3 (sequential) and an expression (parallel);
        # each instance gets its index in inputDataItem.
        "multi-instance-cardinality.bpmn",
    ],
)
def test_sequential_and_parallel_multi_instance_complete(
    session: Session, fixture: str
) -> None:
    tenant = M8flowTenantModel(id="mi-tenant", name="Loops", slug="mi-tenant")
    user = UserModel(
        username="looper",
        email="looper@example.com",
        service="http://localhost/realms/mi-tenant",
        service_id="looper",
        display_name="Looper",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add_all([tenant, user])
    session.flush()
    ensure_v1_role(
        session, tenant_id=tenant.id, role_name=ROLE_ADMIN, user_ids=[user.id]
    )

    definition = api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_identifier="multi-instance-model",
            user_id=user.id,
            source_bpmn_xml=(FIXTURES / fixture).read_text(encoding="utf-8"),
        ),
    )
    instance = api.execute_command(
        session,
        api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_process_definition_id=definition.id,
            process_initiator_id=user.id,
        ),
    )

    assert instance.status == api.ProcessInstanceStatus.complete


# --- M8F-640: iteration-local data, runtime counters, output order -----------

_DEFINITIONS = (
    '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
    'id="Defs_m8f640" targetNamespace="http://bpmn.io/schema/bpmn">'
    '<bpmn:process id="Process_m8f640" isExecutable="true">{body}</bpmn:process>'
    "</bpmn:definitions>"
)
# Names that belong to one iteration and must never reach the process scope.
_LOCAL_NAMES = (
    "it",
    "res",
    "loopCounter",
    "nrOfInstances",
    "nrOfActiveInstances",
    "nrOfCompletedInstances",
)
_NOTHING_LEAKED = (
    f"for name in {_LOCAL_NAMES!r}:\n"
    "    if name in locals(): raise Exception(name + ' leaked')"
)


def _loop(
    sequential: bool, *, condition: str | None = None, mapped: bool = True
) -> str:
    inner = "<bpmn:loopDataInputRef>items</bpmn:loopDataInputRef>"
    if mapped:
        inner += "<bpmn:loopDataOutputRef>out</bpmn:loopDataOutputRef>"
    inner += '<bpmn:inputDataItem id="it" name="it"/>'
    if mapped:
        inner += '<bpmn:outputDataItem id="res" name="res"/>'
    if condition is not None:
        inner += f"<bpmn:completionCondition>{condition}</bpmn:completionCondition>"
    flag = ' isSequential="true"' if sequential else ""
    return (
        f"<bpmn:multiInstanceLoopCharacteristics{flag}>{inner}"
        "</bpmn:multiInstanceLoopCharacteristics>"
    )


def _script(task_id: str, script: str, loop: str = "") -> str:
    return (
        f'<bpmn:scriptTask id="{task_id}">{loop}'
        f"<bpmn:script>{script}</bpmn:script></bpmn:scriptTask>"
    )


def _chain(*tasks: str) -> str:
    ids = ["StartEvent_1"]
    ids += [task.split('id="', 1)[1].split('"', 1)[0] for task in tasks]
    ids.append("EndEvent_1")
    flows = "".join(
        f'<bpmn:sequenceFlow id="f{n}" sourceRef="{a}" targetRef="{b}"/>'
        for n, (a, b) in enumerate(zip(ids, ids[1:], strict=False))
    )
    body = (
        '<bpmn:startEvent id="StartEvent_1"/>'
        + "".join(tasks)
        + '<bpmn:endEvent id="EndEvent_1"/>'
        + flows
    )
    return _DEFINITIONS.format(body=body)


def _run(session: Session, xml: str) -> str:
    """Import and start ``xml`` as an admin; return the instance status."""
    tenant = M8flowTenantModel(id="mi-640", name="Loops", slug="mi-640")
    user = UserModel(
        username="looper640",
        email="looper640@example.com",
        service="http://localhost/realms/mi-640",
        service_id="looper640",
        display_name="Looper",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add_all([tenant, user])
    session.flush()
    ensure_v1_role(
        session, tenant_id=tenant.id, role_name=ROLE_ADMIN, user_ids=[user.id]
    )
    definition = api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_identifier="m8f640",
            user_id=user.id,
            source_bpmn_xml=xml,
        ),
    )
    instance = api.execute_command(
        session,
        api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_process_definition_id=definition.id,
            process_initiator_id=user.id,
        ),
    )
    return str(instance.status)


_PREP = _script("prep", "items = ['a', 'b', 'c']")


@pytest.mark.parametrize(
    ("sequential", "expected"),
    [
        (True, [[0, 3, 1, 0], [1, 3, 1, 1], [2, 3, 1, 2]]),
        (False, [[0, 3, 3, 0], [1, 3, 2, 1], [2, 3, 1, 2]]),
    ],
    ids=["sequential", "parallel"],
)
def test_each_iteration_sees_its_runtime_counters(
    session: Session, sequential: bool, expected: list[list[int]]
) -> None:
    work = _script(
        "work",
        "res = [loopCounter, nrOfInstances,"
        " nrOfActiveInstances, nrOfCompletedInstances]",
        _loop(sequential),
    )
    check = _script(
        "chk",
        f"if out != {expected!r}: raise Exception('out was ' + repr(out))\n"
        + _NOTHING_LEAKED,
    )
    assert _run(session, _chain(_PREP, work, check)) == "complete"


@pytest.mark.parametrize("sequential", [True, False], ids=["sequential", "parallel"])
def test_unmapped_loop_keeps_the_input_item_local(
    session: Session, sequential: bool
) -> None:
    # Without an output mapping, other variables still flow back (last write
    # wins), but the input item and counters stay in their iteration.
    work = _script("work", "seen = it", _loop(sequential, mapped=False))
    check = _script(
        "chk",
        "if seen != 'c': raise Exception('seen was ' + repr(seen))\n" + _NOTHING_LEAKED,
    )
    assert _run(session, _chain(_PREP, work, check)) == "complete"


def test_parallel_completion_condition_reads_the_counters(session: Session) -> None:
    work = _script(
        "work",
        "res = it.upper()",
        _loop(False, condition="nrOfCompletedInstances &gt;= 2"),
    )
    check = _script(
        "chk", "if out != ['A', 'B']: raise Exception('out was ' + repr(out))"
    )
    assert _run(session, _chain(_PREP, work, check)) == "complete"


def test_cardinality_loop_replaces_a_stale_output_list(session: Session) -> None:
    loops = [
        _script(
            f"work_{kind}",
            "res = idx * 10",
            f"<bpmn:multiInstanceLoopCharacteristics{flag}>"
            "<bpmn:loopCardinality>3</bpmn:loopCardinality>"
            f"<bpmn:loopDataOutputRef>out_{kind}</bpmn:loopDataOutputRef>"
            '<bpmn:inputDataItem id="idx" name="idx"/>'
            '<bpmn:outputDataItem id="res" name="res"/>'
            "</bpmn:multiInstanceLoopCharacteristics>",
        )
        for kind, flag in (("seq", ' isSequential="true"'), ("par", ""))
    ]
    stale = _script("stale", "out_seq = ['stale']\nout_par = ['stale']")
    check = _script(
        "chk",
        "if (out_seq, out_par) != ([0, 10, 20], [0, 10, 20]):"
        " raise Exception(repr((out_seq, out_par)))",
    )
    assert _run(session, _chain(stale, *loops, check)) == "complete"


def test_reentered_activity_starts_a_new_output_collection(session: Session) -> None:
    body = (
        '<bpmn:startEvent id="StartEvent_1"/>'
        + _script("prep", "items = ['a', 'b']\nrounds = 0")
        + _script("work", "res = it.upper() + str(rounds)", _loop(True))
        + _script("count", "rounds = rounds + 1")
        + '<bpmn:exclusiveGateway id="again" default="f_done"/>'
        + _script(
            "chk", "if out != ['A1', 'B1']: raise Exception('out was ' + repr(out))"
        )
        + '<bpmn:endEvent id="EndEvent_1"/>'
        '<bpmn:sequenceFlow id="f0" sourceRef="StartEvent_1" targetRef="prep"/>'
        '<bpmn:sequenceFlow id="f1" sourceRef="prep" targetRef="work"/>'
        '<bpmn:sequenceFlow id="f2" sourceRef="work" targetRef="count"/>'
        '<bpmn:sequenceFlow id="f3" sourceRef="count" targetRef="again"/>'
        '<bpmn:sequenceFlow id="f_loop" sourceRef="again" targetRef="work">'
        "<bpmn:conditionExpression>rounds &lt; 2</bpmn:conditionExpression>"
        "</bpmn:sequenceFlow>"
        '<bpmn:sequenceFlow id="f_done" sourceRef="again" targetRef="chk"/>'
        '<bpmn:sequenceFlow id="f4" sourceRef="chk" targetRef="EndEvent_1"/>'
    )
    assert _run(session, _DEFINITIONS.format(body=body)) == "complete"


def test_nested_loops_keep_their_own_counters(session: Session) -> None:
    inner = (
        "<bpmn:multiInstanceLoopCharacteristics>"
        "<bpmn:loopDataInputRef>row</bpmn:loopDataInputRef>"
        "<bpmn:loopDataOutputRef>row_out</bpmn:loopDataOutputRef>"
        '<bpmn:inputDataItem id="cell" name="cell"/>'
        '<bpmn:outputDataItem id="cell_out" name="cell_out"/>'
        "</bpmn:multiInstanceLoopCharacteristics>"
    )
    outer = (
        '<bpmn:multiInstanceLoopCharacteristics isSequential="true">'
        "<bpmn:loopDataInputRef>grid</bpmn:loopDataInputRef>"
        "<bpmn:loopDataOutputRef>grid_out</bpmn:loopDataOutputRef>"
        '<bpmn:inputDataItem id="row" name="row"/>'
        '<bpmn:outputDataItem id="row_summary" name="row_summary"/>'
        "</bpmn:multiInstanceLoopCharacteristics>"
    )
    expected = [
        [0, 2, [[0, 2, 1], [1, 2, 2]]],
        [1, 2, [[0, 3, 3], [1, 3, 4], [2, 3, 5]]],
    ]
    body = (
        '<bpmn:startEvent id="StartEvent_1"/>'
        + _script("prep", "grid = [[1, 2], [3, 4, 5]]")
        + f'<bpmn:subProcess id="per_row">{outer}'
        '<bpmn:startEvent id="row_start"/>'
        + _script("per_cell", "cell_out = [loopCounter, nrOfInstances, cell]", inner)
        + _script("summ", "row_summary = [loopCounter, nrOfInstances, row_out]")
        + '<bpmn:endEvent id="row_end"/>'
        '<bpmn:sequenceFlow id="r0" sourceRef="row_start" targetRef="per_cell"/>'
        '<bpmn:sequenceFlow id="r1" sourceRef="per_cell" targetRef="summ"/>'
        '<bpmn:sequenceFlow id="r2" sourceRef="summ" targetRef="row_end"/>'
        "</bpmn:subProcess>"
        + _script(
            "chk", f"if grid_out != {expected!r}: raise Exception(repr(grid_out))"
        )
        + '<bpmn:endEvent id="EndEvent_1"/>'
        '<bpmn:sequenceFlow id="f0" sourceRef="StartEvent_1" targetRef="prep"/>'
        '<bpmn:sequenceFlow id="f1" sourceRef="prep" targetRef="per_row"/>'
        '<bpmn:sequenceFlow id="f2" sourceRef="per_row" targetRef="chk"/>'
        '<bpmn:sequenceFlow id="f3" sourceRef="chk" targetRef="EndEvent_1"/>'
    )
    assert _run(session, _DEFINITIONS.format(body=body)) == "complete"


def test_saved_state_keeps_spiff_typenames(session: Session) -> None:
    # Snapshots written before M8F-640 must keep restoring, and new ones must
    # stay readable by the same names.
    from m8flow_core.models.process_instance import ProcessInstanceModel

    review = '<bpmn:userTask id="review">' + _loop(False) + "</bpmn:userTask>"
    assert _run(session, _chain(_PREP, review)) == "user_input_required"
    (instance,) = session.query(ProcessInstanceModel).all()
    state = instance.workflow_state_json or ""
    assert '"typename": "ParallelMultiInstanceTask"' in state
    assert "RuntimeParallelMultiInstanceTask" not in state
