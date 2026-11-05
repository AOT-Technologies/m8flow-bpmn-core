from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from m8flow_core import api
from m8flow_core.models import M8flowTenantModel, UserModel
from m8flow_core.services.authorization import ROLE_ADMIN, ensure_v1_role
from m8flow_core.services.compensation_runtime import coordinator
from m8flow_core.services.workflow_runtime import (
    _build_workflow,
    _restore_workflow,
    _serialize_runtime_workflow,
)


def definitions(body: str) -> str:
    return f"""<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
        xmlns:spiff="http://spiffworkflow.org/bpmn/schema/1.0/core"
        targetNamespace="urn:compensation">{body}</definitions>"""


def flow(source: str, target: str) -> str:
    return (
        f'<sequenceFlow id="f_{source}_{target}" '
        f'sourceRef="{source}" targetRef="{target}"/>'
    )


def binding(source: str, handler: str) -> str:
    return f'''<boundaryEvent id="b_{source}" attachedToRef="{source}"
      cancelActivity="false">
      <compensateEventDefinition/></boundaryEvent>
      <association id="assoc_{source}" sourceRef="b_{source}" targetRef="{handler}"/>'''


def saga(
    *,
    wait: bool = True,
    target: str | None = None,
    handler_type: str = "userTask",
    end: bool = False,
) -> str:
    nodes = '<startEvent id="start"/>'
    previous = "start"
    for name in ("inventory", "payment", "shipment"):
        nodes += (
            f'<scriptTask id="{name}"><script>receipt = "{name}"</script></scriptTask>'
        )
        nodes += flow(previous, name) + binding(name, "undo_" + name)
        if handler_type == "scriptTask":
            nodes += (
                f'<scriptTask id="undo_{name}" isForCompensation="true">'
                f'<script>undone = "{name}"</script></scriptTask>'
            )
        else:
            nodes += (
                f'<{handler_type} id="undo_{name}" name="Undo {name}" '
                'isForCompensation="true"/>'
            )
        previous = name
    event = "endEvent" if end else "intermediateThrowEvent"
    ref = f' activityRef="{target}"' if target else ""
    nodes += (
        f'<{event} id="compensate"><compensateEventDefinition '
        f'waitForCompletion="{str(wait).lower()}"{ref}/></{event}>'
    )
    nodes += flow(previous, "compensate")
    if not end:
        nodes += '<userTask id="after" name="After compensation"/><endEvent id="end"/>'
        nodes += flow("compensate", "after") + flow("after", "end")
    return definitions(f'<process id="saga" isExecutable="true">{nodes}</process>')


def start(
    session: Session,
    xml: str,
    *,
    properties: dict | None = None,
    dmn: str | None = None,
) -> tuple[int, int]:
    tenant = M8flowTenantModel(id="comp", name="Compensation", slug="comp")
    user = UserModel(
        username="operator",
        service="comp",
        service_id="operator",
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
            tenant_id="comp",
            bpmn_identifier="saga",
            user_id=user.id,
            source_bpmn_xml=xml,
            source_dmn_xml=dmn,
            properties_json=properties,
        ),
    )
    instance = api.execute_command(
        session,
        api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id="comp",
            bpmn_process_definition_id=definition.id,
            process_initiator_id=user.id,
        ),
    )
    return instance.id, user.id


def pending(session: Session) -> list:
    return api.execute_query(session, api.GetPendingTasksQuery("comp"))


def finish(session: Session, user_id: int, name: str) -> None:
    item = next(item for item in pending(session) if item.task_title == name)
    api.execute_command(session, api.ClaimTaskCommand("comp", item.id, user_id))
    api.execute_command(session, api.CompleteTaskCommand("comp", item.id, user_id))


def records(session: Session, instance_id: int) -> list:
    return api.execute_query(
        session, api.GetProcessInstanceCompensationsQuery("comp", instance_id)
    )


def test_reverse_order_survives_new_session(engine) -> None:
    with Session(engine) as session:
        instance_id, user_id = start(session, saga())
        assert [item.task_title for item in pending(session)] == ["Undo shipment"]
        finish(session, user_id, "Undo shipment")
        session.commit()
    with Session(engine) as session:
        assert [item.task_title for item in pending(session)] == ["Undo payment"]
        before = records(session, instance_id)
        assert [r.status for r in before] == ["queued", "running", "completed"]
        assert [r.completion_sequence for r in before] == [1, 2, 3]
        shipment_guid = before[-1].handler_task_guid
        finish(session, user_id, "Undo payment")
        finish(session, user_id, "Undo inventory")
        assert [item.task_title for item in pending(session)] == ["After compensation"]
        finish(session, user_id, "After compensation")
        assert records(session, instance_id)[-1].handler_task_guid == shipment_guid
        assert all(
            r.status == "completed" and r.attempts == 1
            for r in records(session, instance_id)
        )
        events = api.execute_query(
            session, api.GetProcessInstanceEventsQuery("comp", instance_id)
        )
        assert len([e for e in events if e.event_type == "compensation_completed"]) == 3
        assert (
            api.execute_query(
                session, api.GetProcessInstanceQuery("comp", instance_id)
            ).status
            == "complete"
        )
        session.commit()


@pytest.mark.parametrize("wait", [True, False])
def test_wait_modes(session, wait) -> None:
    instance_id, user_id = start(session, saga(wait=wait))
    names = {item.task_title for item in pending(session)}
    assert names == (
        {"Undo shipment"} if wait else {"Undo shipment", "After compensation"}
    )
    if not wait:
        finish(session, user_id, "After compensation")
        assert (
            api.execute_query(
                session, api.GetProcessInstanceQuery("comp", instance_id)
            ).status
            == "user_input_required"
        )
    for name in ("shipment", "payment", "inventory"):
        finish(session, user_id, "Undo " + name)
    if wait:
        finish(session, user_id, "After compensation")
    assert (
        api.execute_query(
            session, api.GetProcessInstanceQuery("comp", instance_id)
        ).status
        == "complete"
    )


@pytest.mark.parametrize("end", [False, True])
def test_targeted_compensation_and_end_event(session, end) -> None:
    instance_id, user_id = start(session, saga(target="payment", end=end))
    assert [item.task_title for item in pending(session)] == ["Undo payment"]
    finish(session, user_id, "Undo payment")
    assert [r.status for r in records(session, instance_id)] == [
        "available",
        "completed",
        "available",
    ]


def test_compensation_rollback_is_caller_owned(engine) -> None:
    with Session(engine) as session:
        instance_id, user_id = start(session, saga())
        session.commit()
        finish(session, user_id, "Undo shipment")
        session.rollback()
    with Session(engine) as session:
        assert [r.status for r in records(session, instance_id)] == [
            "queued",
            "queued",
            "running",
        ]
        assert [item.task_title for item in pending(session)] == ["Undo shipment"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda x: x.replace('targetRef="undo_inventory"', 'targetRef="missing"'),
        lambda x: x.replace('isForCompensation="true"', 'isForCompensation="false"', 1),
        lambda x: x.replace('waitForCompletion="true"', 'waitForCompletion="maybe"'),
        lambda x: x.replace(
            "<compensateEventDefinition waitForCompletion",
            '<compensateEventDefinition activityRef="missing" waitForCompletion',
        ),
        lambda x: x.replace(
            '<scriptTask id="inventory">',
            '<scriptTask id="inventory"><multiInstanceLoopCharacteristics/>',
        ),
        lambda x: x.replace("</process>", flow("undo_inventory", "end") + "</process>"),
    ],
)
def test_import_rejects_invalid_bindings(session, mutation) -> None:
    with pytest.raises(api.ValidationError):
        start(session, mutation(saga()))


def test_scope_default_recursion_and_explicit_precedence(session) -> None:
    inner = (
        (
            '<startEvent id="is"/><scriptTask id="a"><script>'
            "receipt=7</script></scriptTask>"
        )
        + binding("a", "undo_a")
        + '<userTask id="undo_a" name="Undo inner" isForCompensation="true"/>'
        + '<endEvent id="ie"/>'
        + flow("is", "a")
        + flow("a", "ie")
    )
    xml = definitions(
        '<process id="saga" isExecutable="true"><startEvent id="s"/>'
        f'<subProcess id="sub">{inner}</subProcess>'
        '<endEvent id="c"><compensateEventDefinition/></endEvent>'
        + flow("s", "sub")
        + flow("sub", "c")
        + "</process>"
    )
    instance_id, user_id = start(session, xml)
    assert [item.task_title for item in pending(session)] == ["Undo inner"]
    finish(session, user_id, "Undo inner")
    assert all(r.status == "completed" for r in records(session, instance_id))


def test_nonconnector_failure_retries_only_failed_handler(session) -> None:
    xml = saga(handler_type="scriptTask", end=True).replace(
        '<script>undone = "payment"</script>', '<script>assert repair == "yes"</script>'
    )
    with pytest.raises(api.CompensationExecutionError):
        start(session, xml)
    instance = api.execute_query(session, api.ListErrorProcessInstancesQuery("comp"))[0]
    assert [r.status for r in records(session, instance.id)] == [
        "queued",
        "failed",
        "completed",
    ]
    assert records(session, instance.id)[-1].attempts == 1
    user_id = instance.process_initiator_id
    api.execute_command(
        session,
        api.UpsertProcessInstanceMetadataCommand(
            "comp", instance.id, "repair", "yes", datetime.now(UTC)
        ),
    )
    api.execute_command(
        session, api.RetryProcessInstanceCommand("comp", instance.id, user_id)
    )
    assert [r.status for r in records(session, instance.id)] == ["completed"] * 3
    assert [r.attempts for r in records(session, instance.id)] == [1, 2, 1]


def test_empty_and_repeated_throw() -> None:
    xml = saga(handler_type="scriptTask").replace(
        '<userTask id="after" name="After compensation"/>',
        (
            '<intermediateThrowEvent id="after"><compensateEventDefinition/>'
            "</intermediateThrowEvent>"
        ),
    )
    workflow = _build_workflow(bpmn_xml=xml, dmn_xml=None, bpmn_process_id="saga")
    coordinator(workflow).run()
    restored = _restore_workflow(_serialize_runtime_workflow(workflow))
    coordinator(restored).run()
    assert restored.completed
    assert len(coordinator(restored).handlers) == 3
    assert len(coordinator(restored).state["runs"]) == 2


def test_tenant_scoped_query_and_wrong_dispatcher(session) -> None:
    instance_id, _ = start(session, saga())
    with pytest.raises(api.NotFoundError):
        api.execute_query(
            session, api.GetProcessInstanceCompensationsQuery("other", instance_id)
        )
    with pytest.raises(TypeError):
        api.execute_command(
            session, api.GetProcessInstanceCompensationsQuery("comp", instance_id)
        )


def test_timer_in_handler_subprocess(session) -> None:
    xml = saga(end=True)
    xml = xml.replace(
        '<userTask id="undo_shipment" name="Undo shipment" isForCompensation="true"/>',
        '<subProcess id="undo_shipment" isForCompensation="true"><startEvent id="hs"/>'
        '<intermediateCatchEvent id="timer"><timerEventDefinition>'
        "<timeDate>2099-01-01T00:00:00Z</timeDate>"
        "</timerEventDefinition></intermediateCatchEvent><userTask "
        'id="confirm" name="Confirm undo"/>'
        '<endEvent id="he"/>'
        + flow("hs", "timer")
        + flow("timer", "confirm")
        + flow("confirm", "he")
        + "</subProcess>",
    )
    instance_id, _ = start(session, xml)
    assert not pending(session)
    assert (
        api.execute_query(
            session, api.GetProcessInstanceQuery("comp", instance_id)
        ).status
        == "waiting"
    )
    from sqlalchemy import select

    from m8flow_core.models.scheduler_job import SchedulerJobModel

    job = session.scalar(select(SchedulerJobModel))
    assert job is not None
    assert (
        api.run_due_scheduler_jobs(
            session, now=datetime(2099, 1, 1, tzinfo=UTC) + timedelta(seconds=1)
        )
        == 1
    )
    assert [item.task_title for item in pending(session)] == ["Confirm undo"]


@pytest.mark.parametrize("recovery", ["immediate", "scheduled", "rollback"])
def test_connector_failure_retry_keeps_progress_and_idempotency_key(engine, recovery):
    class UndoConnector:
        connector_key = "undo"

        def __init__(self):
            self.fail = True
            self.keys = []

        def list_commands(self):
            return (api.ServiceTaskCommandDefinition("undo", "Run"),)

        def execute(self, request):
            self.keys.append(
                request.context.metadata["compensation"]["idempotency_key"]
            )
            if self.fail:
                raise RuntimeError("Payment provider unavailable")
            return api.ServiceTaskResult(payload={"refunded": True})

    xml = saga(end=True).replace(
        '<userTask id="undo_payment" name="Undo payment" isForCompensation="true"/>',
        '<serviceTask id="undo_payment" isForCompensation="true"><extensionElements>'
        '<spiff:serviceTaskOperator id="undo/Run" resultVariable="refund">'
        "<spiff:parameters/></spiff:serviceTaskOperator>"
        "</extensionElements></serviceTask>",
    )
    connector = UndoConnector()
    with api.service_task_registry_scope(
        api.ServiceTaskRegistry(connectors=(connector,))
    ):
        with Session(engine) as session:
            instance_id, user_id = start(session, xml)
            session.commit()
            with pytest.raises(api.ServiceTaskExecutionError):
                finish(session, user_id, "Undo shipment")
            assert [r.status for r in records(session, instance_id)] == [
                "queued",
                "failed",
                "completed",
            ]
            if recovery == "rollback":
                session.rollback()
            else:
                session.commit()
        connector.fail = False
        with Session(engine) as session:
            if recovery == "rollback":
                finish(session, user_id, "Undo shipment")
            elif recovery == "scheduled":
                api.execute_command(
                    session,
                    api.ScheduleProcessInstanceRetryCommand(
                        "comp", instance_id, user_id, datetime.now(UTC)
                    ),
                )
                api.run_due_scheduler_jobs(
                    session, now=datetime.now(UTC) + timedelta(seconds=1)
                )
            else:
                api.execute_command(
                    session,
                    api.RetryProcessInstanceCommand("comp", instance_id, user_id),
                )
            assert len(connector.keys) == 2 and connector.keys[0] == connector.keys[1]
            assert [item.task_title for item in pending(session)] == ["Undo inventory"]
            assert records(session, instance_id)[-1].attempts == 1
            finish(session, user_id, "Undo inventory")
            session.commit()


def test_lifecycle_suspend_resume_and_terminate(session):
    instance_id, user_id = start(session, saga(wait=False))
    api.execute_command(
        session, api.SuspendProcessInstanceCommand("comp", instance_id, user_id)
    )
    with pytest.raises(api.InvalidStateError, match="suspended"):
        finish(session, user_id, "Undo shipment")
    api.execute_command(
        session, api.ResumeProcessInstanceCommand("comp", instance_id, user_id)
    )
    assert {item.task_title for item in pending(session)} == {
        "Undo shipment",
        "After compensation",
    }
    api.execute_command(
        session, api.TerminateProcessInstanceCommand("comp", instance_id, user_id)
    )
    assert not pending(session)
    assert all(r.status == "cancelled" for r in records(session, instance_id))


def test_connection_transaction_owns_start_and_journal(engine):
    from sqlalchemy import func, select

    from m8flow_core.models.compensation import CompensationRecordModel

    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection) as session:
            instance_id, _ = start(session, saga())
            result = api.execute_query(
                connection,
                api.GetProcessInstanceCompensationsQuery("comp", instance_id),
            )
            assert len(result) == 3
        transaction.rollback()
    with engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(CompensationRecordModel))
            == 0
        )


def test_completed_subprocess_explicit_handler_takes_precedence(session):
    inner = (
        (
            '<startEvent id="is"/><scriptTask id="a"><script>'
            "receipt=7</script></scriptTask>"
        )
        + binding("a", "undo_a")
        + '<userTask id="undo_a" name="Undo inner" isForCompensation="true"/>'
        + '<endEvent id="ie"/>'
        + flow("is", "a")
        + flow("a", "ie")
    )
    xml = definitions(
        '<process id="saga" isExecutable="true"><startEvent id="s"/>'
        f'<subProcess id="sub">{inner}</subProcess>'
        + binding("sub", "undo_sub")
        + '<userTask id="undo_sub" name="Undo subprocess" isForCompensation="true"/>'
        '<endEvent id="c"><compensateEventDefinition/></endEvent>'
        + flow("s", "sub")
        + flow("sub", "c")
        + "</process>"
    )
    instance_id, user_id = start(session, xml)
    assert [item.task_title for item in pending(session)] == ["Undo subprocess"]
    finish(session, user_id, "Undo subprocess")
    assert [(r.activity_id, r.status) for r in records(session, instance_id)] == [
        ("a", "available"),
        ("sub", "completed"),
    ]


def test_active_subprocess_is_not_compensated_from_parent(session):
    inner = (
        (
            '<startEvent id="is"/><scriptTask id="a"><script>'
            "receipt=7</script></scriptTask>"
        )
        + binding("a", "undo_a")
        + '<userTask id="undo_a" name="Undo inner" isForCompensation="true"/>'
        + '<userTask id="hold" name="Hold subprocess"/><endEvent id="ie"/>'
        + flow("is", "a")
        + flow("a", "hold")
        + flow("hold", "ie")
    )
    xml = definitions(
        '<process id="saga" isExecutable="true"><startEvent id="s"/>'
        '<parallelGateway id="split"/>'
        f'<subProcess id="sub">{inner}</subProcess>'
        '<intermediateCatchEvent id="delay"><timerEventDefinition>'
        "<timeDate>2099-01-01T00:00:00Z</timeDate></timerEventDefinition>"
        "</intermediateCatchEvent>"
        '<endEvent id="c"><compensateEventDefinition/></endEvent>'
        '<endEvent id="end"/>'
        + flow("s", "split")
        + flow("split", "sub")
        + flow("sub", "end")
        + flow("split", "delay")
        + flow("delay", "c")
        + "</process>"
    )
    instance_id, _ = start(session, xml)
    api.run_due_scheduler_jobs(
        session, now=datetime(2099, 1, 1, tzinfo=UTC) + timedelta(seconds=1)
    )
    assert [item.task_title for item in pending(session)] == ["Hold subprocess"]
    assert [(r.activity_id, r.status) for r in records(session, instance_id)] == [
        ("a", "available")
    ]


def test_sequence_flow_cycle_records_distinct_completions(session):
    xml = definitions(
        '<process id="saga" isExecutable="true"><startEvent id="s"/>'
        '<scriptTask id="init"><script>i=0</script></scriptTask>'
        '<scriptTask id="a"><script>i+=1</script></scriptTask>'
        + binding("a", "undo")
        + '<userTask id="undo" name="Undo occurrence" isForCompensation="true"/>'
        '<exclusiveGateway id="choice" default="done"/>'
        '<endEvent id="c"><compensateEventDefinition/></endEvent>'
        + flow("s", "init")
        + flow("init", "a")
        + flow("a", "choice")
        + '<sequenceFlow id="again" sourceRef="choice" targetRef="a">'
        "<conditionExpression>i &lt; 3</conditionExpression></sequenceFlow>"
        '<sequenceFlow id="done" sourceRef="choice" targetRef="c"/></process>'
    )
    instance_id, user_id = start(session, xml)
    initial = records(session, instance_id)
    assert len({r.source_task_guid for r in initial}) == 3
    assert [r.status for r in initial] == ["queued", "queued", "running"]
    for _ in range(3):
        finish(session, user_id, "Undo occurrence")
    assert [r.attempts for r in records(session, instance_id)] == [1, 1, 1]


def test_timer_cancelled_and_unreached_activities_have_no_subscription(session):
    xml = definitions(
        '<process id="saga" isExecutable="true"><startEvent id="s"/>'
        '<scriptTask id="done"><script>receipt=1</script></scriptTask>'
        + binding("done", "undo_done")
        + '<userTask id="undo_done" name="Undo done" isForCompensation="true"/>'
        '<userTask id="active"/>'
        + binding("active", "undo_active")
        + '<userTask id="undo_active" isForCompensation="true"/>'
        '<userTask id="skipped"/>'
        + binding("skipped", "undo_skipped")
        + '<userTask id="undo_skipped" isForCompensation="true"/>'
        '<boundaryEvent id="timeout" attachedToRef="active" cancelActivity="true">'
        "<timerEventDefinition><timeDate>2099-01-01T00:00:00Z</timeDate>"
        "</timerEventDefinition></boundaryEvent>"
        '<endEvent id="c"><compensateEventDefinition/></endEvent>'
        + flow("s", "done")
        + flow("done", "active")
        + flow("active", "c")
        + flow("timeout", "c")
        + "</process>"
    )
    instance_id, _ = start(session, xml)
    api.run_due_scheduler_jobs(
        session, now=datetime(2099, 1, 1, tzinfo=UTC) + timedelta(seconds=1)
    )
    assert [(r.activity_id, r.status) for r in records(session, instance_id)] == [
        ("done", "running")
    ]
    assert [item.task_title for item in pending(session)] == ["Undo done"]


def test_human_forward_completion_registers_subscription(session):
    xml = saga(end=True).replace(
        '<scriptTask id="inventory"><script>'
        'receipt = "inventory"</script></scriptTask>',
        '<userTask id="inventory" name="Reserve inventory"/>',
    )
    instance_id, user_id = start(session, xml)
    assert not records(session, instance_id)
    finish(session, user_id, "Reserve inventory")
    assert [r.activity_id for r in records(session, instance_id)] == [
        "inventory",
        "payment",
        "shipment",
    ]


def test_legacy_compensation_recovery_is_rejected(session):
    import json

    instance_id, _ = start(session, saga())
    instance = api.execute_query(
        session, api.GetProcessInstanceQuery("comp", instance_id)
    )
    state = json.loads(instance.workflow_state_json)
    state.pop("m8f_compensation")
    from m8flow_core.models.json_data import JsonDataModel
    from m8flow_core.models.process_instance import WORKFLOW_STATE_JSON_DATA_KEY

    stored = JsonDataModel.get_for_tenant(
        session, "comp", instance.bpmn_process.json_data_hash
    )
    payload = dict(stored.data)
    payload[WORKFLOW_STATE_JSON_DATA_KEY] = json.dumps(state)
    instance.bpmn_process.json_data_hash = JsonDataModel.create_or_update_from_payload(
        session, "comp", payload
    )
    session.flush()
    from m8flow_core.services.workflow_runtime import (
        repair_process_instance_runtime_representation,
    )

    with pytest.raises(api.InvalidStateError, match="Legacy compensation"):
        repair_process_instance_runtime_representation(
            session, tenant_id="comp", process_instance_id=instance_id
        )


def test_completed_scope_data_is_retained_and_handler_outputs_are_isolated(session):
    inner = (
        '<startEvent id="is"/><scriptTask id="a"><script>'
        "from datetime import datetime; "
        'receipt=datetime.fromisoformat("2040-01-01T00:00:00+00:00")</script></scriptTask>'
        + binding("a", "undo_a")
        + '<scriptTask id="undo_a" isForCompensation="true">'
        "<script>assert receipt.year == 2040; "
        'receipt="handler-local"</script></scriptTask>'
        '<endEvent id="ie"/>' + flow("is", "a") + flow("a", "ie")
    )
    xml = definitions(
        '<process id="saga" isExecutable="true"><startEvent id="s"/>'
        f'<subProcess id="sub">{inner}</subProcess>'
        '<scriptTask id="change"><script>receipt="current"</script></scriptTask>'
        '<intermediateThrowEvent id="c">'
        "<compensateEventDefinition/></intermediateThrowEvent>"
        '<scriptTask id="check"><script>'
        'assert receipt == "current"</script></scriptTask>'
        '<endEvent id="e"/>'
        + flow("s", "sub")
        + flow("sub", "change")
        + flow("change", "c")
        + flow("c", "check")
        + flow("check", "e")
        + "</process>"
    )
    instance_id, _ = start(session, xml)
    assert all(r.status == "completed" for r in records(session, instance_id))


@pytest.mark.parametrize("handler_type", ["manualTask", "task"])
def test_human_handler_preserves_lane_assignment_and_tenant_check(
    session, handler_type
):
    xml = saga(handler_type=handler_type, end=True, target="shipment").replace(
        '<startEvent id="start"/>',
        '<laneSet id="lanes"><lane id="shipping" name="Shipping">'
        "<flowNodeRef>undo_shipment</flowNodeRef></lane></laneSet>"
        '<startEvent id="start"/>',
    )
    instance_id, user_id = start(
        session, xml, properties={"lane_owners": {"Shipping": ["operator"]}}
    )
    [item] = api.execute_query(session, api.GetPendingTasksQuery("comp", user_id))
    assert item.lane_name == "Shipping"
    foreign = UserModel(
        username="foreign",
        service="another-tenant",
        service_id="foreign",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(foreign)
    session.flush()
    with pytest.raises(api.AuthorizationError, match="does not belong to tenant"):
        api.execute_command(session, api.ClaimTaskCommand("comp", item.id, foreign.id))
    finish(session, user_id, "Undo shipment")
    assert records(session, instance_id)[-1].status == "completed"


def test_business_rule_handler_uses_dmn(session):
    xml = (
        saga(end=True, target="shipment")
        .replace(
            '<userTask id="undo_shipment" name="Undo shipment" '
            'isForCompensation="true"/>',
            '<businessRuleTask id="undo_shipment" isForCompensation="true">'
            "<extensionElements><spiff:calledDecisionId>check_eligibility"
            "</spiff:calledDecisionId>"
            "</extensionElements></businessRuleTask>",
        )
        .replace('receipt = "shipment"', "amount = 400")
    )
    dmn = (Path(__file__).parent / "fixtures/check_eligibility.dmn").read_text(
        encoding="utf-8"
    )
    instance_id, _ = start(session, xml, dmn=dmn)
    assert records(session, instance_id)[-1].status == "completed"
    assert not pending(session)
