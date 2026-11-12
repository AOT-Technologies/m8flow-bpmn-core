from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_core import api
from m8flow_core.models import (
    M8flowTenantModel,
    SchedulerJobModel,
    SignalPublicationModel,
    UserModel,
)
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


def transaction_xml(*, normal_end: bool = False, nested: bool = False) -> str:
    end_definition = "" if normal_end else "<cancelEventDefinition/>"
    active_work = '<userTask id="active" name="Ship order"/>'
    if nested:
        active_work = f"""
        <subProcess id="active">
          <startEvent id="shipping_start"/>
          <userTask id="pack" name="Pack order"/>
          {binding("pack", "undo_pack")}
          <userTask id="undo_pack" name="Unpack order" isForCompensation="true"/>
          <userTask id="ship" name="Ship order"/>
          <endEvent id="shipping_end"/>
          {flow("shipping_start", "pack")}{flow("pack", "ship")}
          {flow("ship", "shipping_end")}
        </subProcess>"""
    transaction = f"""
      <transaction id="order">
        <startEvent id="tx_start"/>
        <scriptTask id="reserve"><script>reserved = True</script></scriptTask>
        <scriptTask id="charge"><script>charged = True</script></scriptTask>
        {binding("reserve", "undo_reserve")}
        {binding("charge", "undo_charge")}
        <scriptTask id="undo_reserve" isForCompensation="true">
          <script>assert operator_fixed, 'Repair required'</script>
        </scriptTask>
        <userTask id="undo_charge" name="Refund charge" isForCompensation="true"/>
        <parallelGateway id="split"/>
        <userTask id="cancel_request" name="Cancel order"/>
        {active_work}
        <endEvent id="cancel">{end_definition}</endEvent>
        <endEvent id="shipped"/>
        {flow("tx_start", "reserve")}{flow("reserve", "charge")}
        {flow("charge", "split")}
        {flow("split", "cancel_request")}{flow("split", "active")}
        {flow("cancel_request", "cancel")}{flow("active", "shipped")}
      </transaction>"""
    return definitions(f"""
      <process id="saga" isExecutable="true">
        <startEvent id="start"/>{transaction}
        <boundaryEvent id="boundary" attachedToRef="order">
          <cancelEventDefinition/>
        </boundaryEvent>
        <userTask id="after_cancel" name="After cancel"/>
        <userTask id="after_success" name="After success"/>
        <endEvent id="end"/>
        {flow("start", "order")}{flow("order", "after_success")}
        {flow("boundary", "after_cancel")}{flow("after_cancel", "end")}
        {flow("after_success", "end")}
      </process>""")


@pytest.mark.parametrize(
    "nested", [False, True], ids=["direct-work", "active-subprocess"]
)
def test_transaction_cancel_waits_for_compensation_and_retries_after_reload(
    engine, nested
):
    with Session(engine) as session:
        instance_id, user_id = start(session, transaction_xml(nested=nested))
        if nested:
            assert {item.task_title for item in pending(session)} == {
                "Cancel order",
                "Pack order",
            }
            finish(session, user_id, "Pack order")
        assert {item.task_title for item in pending(session)} == {
            "Cancel order",
            "Ship order",
        }
        finish(session, user_id, "Cancel order")
        if nested:
            assert [item.task_title for item in pending(session)] == ["Unpack order"]
            finish(session, user_id, "Unpack order")
        assert [item.task_title for item in pending(session)] == ["Refund charge"]
        with pytest.raises(
            api.CompensationExecutionError, match="undo_reserve"
        ) as failure:
            finish(session, user_id, "Refund charge")
        assert failure.value.compensation_recovery_flushed
        expected_statuses = {"reserve": "failed", "charge": "completed"}
        if nested:
            expected_statuses["pack"] = "completed"
        assert {
            record.activity_id: record.status
            for record in records(session, instance_id)
        } == expected_statuses
        session.commit()

    with Session(engine) as session:
        assert pending(session) == []
        api.execute_command(
            session,
            api.UpsertProcessInstanceMetadataCommand(
                "comp",
                instance_id,
                "operator_fixed",
                "true",
                datetime.now(UTC),
            ),
        )
        api.execute_command(
            session, api.RetryProcessInstanceCommand("comp", instance_id, user_id)
        )
        assert [item.task_title for item in pending(session)] == ["After cancel"]
        expected_attempts = [("reserve", 2), ("charge", 1)]
        if nested:
            expected_attempts.append(("pack", 1))
        assert [
            (record.activity_id, record.attempts)
            for record in records(session, instance_id)
        ] == expected_attempts
        session.commit()

    with Session(engine) as session:
        assert [item.task_title for item in pending(session)] == ["After cancel"]
        finish(session, user_id, "After cancel")
        assert pending(session) == []
        assert (
            api.execute_query(
                session, api.GetProcessInstanceQuery("comp", instance_id)
            ).status
            == "complete"
        )


def test_transaction_normal_completion_skips_compensation(session):
    instance_id, user_id = start(session, transaction_xml(normal_end=True))
    finish(session, user_id, "Cancel order")
    finish(session, user_id, "Ship order")
    assert [item.task_title for item in pending(session)] == ["After success"]
    assert all(record.status == "available" for record in records(session, instance_id))


@pytest.mark.parametrize(
    "before, after",
    [
        ('attachedToRef="order"', 'attachedToRef="active"'),
        (
            'id="boundary" attachedToRef="order"',
            'id="boundary" attachedToRef="order" cancelActivity="false"',
        ),
        ('<transaction id="order">', '<subProcess id="order">'),
        ('<endEvent id="cancel">', '<intermediateThrowEvent id="cancel">'),
    ],
)
def test_invalid_transaction_cancel_models(before, after):
    xml = transaction_xml().replace(before, after)
    if after.startswith("<subProcess"):
        xml = xml.replace("</transaction>", "</subProcess>")
    if after.startswith("<intermediateThrowEvent"):
        xml = xml.replace(
            "<cancelEventDefinition/></endEvent>",
            "<cancelEventDefinition/></intermediateThrowEvent>",
        )
    with pytest.raises(api.ValidationError, match="Cancel"):
        api.validate_compensation_bpmn(xml)


def test_transaction_cancel_removes_waiting_timer(session):
    xml = transaction_xml().replace(
        '<endEvent id="shipped"/>',
        '<endEvent id="shipped"/><endEvent id="timed_out"/>'
        '<boundaryEvent id="timeout" attachedToRef="active">'
        "<timerEventDefinition><timeDuration>PT1H</timeDuration></timerEventDefinition>"
        "</boundaryEvent>" + flow("timeout", "timed_out"),
    )
    instance_id, user_id = start(session, xml)
    jobs = select(SchedulerJobModel).where(
        SchedulerJobModel.process_instance_id == instance_id
    )
    assert session.scalar(jobs) is not None
    finish(session, user_id, "Cancel order")
    assert session.scalar(jobs) is None
    assert [item.task_title for item in pending(session)] == ["Refund charge"]


def test_cancel_boundary_only_catches_its_own_transaction(session):
    transactions = []
    for name in ("one", "two"):
        transactions.append(f'''
          <transaction id="{name}">
            <startEvent id="start_{name}"/>
            <userTask id="request_{name}" name="Cancel {name}"/>
            <endEvent id="cancel_{name}"><cancelEventDefinition/></endEvent>
            {flow(f"start_{name}", f"request_{name}")}
            {flow(f"request_{name}", f"cancel_{name}")}
          </transaction>
          <boundaryEvent id="boundary_{name}" attachedToRef="{name}">
            <cancelEventDefinition/>
          </boundaryEvent>
          <userTask id="after_{name}" name="After {name}"/>
          {flow("fork", name)}{flow(f"boundary_{name}", f"after_{name}")}
          {flow(name, "end")}{flow(f"after_{name}", "end")}
        ''')
    xml = definitions(f"""
      <process id="saga" isExecutable="true">
        <startEvent id="start"/><parallelGateway id="fork"/><endEvent id="end"/>
        {flow("start", "fork")}{"".join(transactions)}
      </process>""")
    _, user_id = start(session, xml)
    finish(session, user_id, "Cancel one")
    assert {item.task_title for item in pending(session)} == {"After one", "Cancel two"}
    finish(session, user_id, "Cancel two")
    assert {item.task_title for item in pending(session)} == {"After one", "After two"}


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


@pytest.mark.parametrize(
    "suffix", ["", "_start", "_end", "_flow_0", "_flow_1", "_lanes", "_lane"]
)
def test_compensation_helper_ids_avoid_existing_bpmn_ids(session, suffix):
    base = "__m8f_compensation_undo_shipment"
    xml = saga(end=True, target="shipment").replace(
        '<startEvent id="start"/>',
        f'<textAnnotation id="{base}{suffix}"><text>Shipping</text></textAnnotation>'
        '<laneSet id="lanes"><lane id="shipping" name="Shipping">'
        "<flowNodeRef>undo_shipment</flowNodeRef></lane></laneSet>"
        '<startEvent id="start"/>',
    )
    instance_id, user_id = start(
        session, xml, properties={"lane_owners": {"Shipping": ["operator"]}}
    )
    [item] = api.execute_query(session, api.GetPendingTasksQuery("comp", user_id))
    assert item.lane_name == "Shipping"
    instance = api.execute_query(
        session, api.GetProcessInstanceQuery("comp", instance_id)
    )
    workflow = _restore_workflow(instance.workflow_state_json)
    comp = coordinator(workflow)
    assert comp is not None
    [handler] = comp.handlers.values()
    assert handler.spec.name == base + "_1"
    finish(session, user_id, "Undo shipment")
    assert records(session, instance_id)[-1].status == "completed"


def test_compensation_helper_ids_avoid_earlier_helpers(session):
    xml = saga(end=True).replace("undo_payment", "undo_inventory_start")
    instance_id, user_id = start(session, xml)
    instance = api.execute_query(
        session, api.GetProcessInstanceQuery("comp", instance_id)
    )
    comp = coordinator(_restore_workflow(instance.workflow_state_json))
    assert comp is not None
    templates = comp.state["templates"]
    assert templates["undo_inventory_start"]["spec"]["name"] == (
        "__m8f_compensation_undo_inventory_start_1"
    )
    for name in ("shipment", "payment", "inventory"):
        finish(session, user_id, "Undo " + name)
    assert all(record.status == "completed" for record in records(session, instance_id))


def test_compensation_and_qualified_signal_end_survive_reload(engine):
    xml = (
        saga(target="shipment")
        .replace(
            'targetNamespace="urn:compensation">',
            'xmlns:tns="urn:compensation" targetNamespace="urn:compensation">'
            '<signal id="notice" name="Notice"/>',
        )
        .replace(
            '<endEvent id="end"/>',
            '<endEvent id="end"><signalEventDefinition signalRef="tns:notice"/>'
            "</endEvent>",
        )
    )
    with Session(engine) as session:
        instance_id, user_id = start(session, xml)
        finish(session, user_id, "Undo shipment")
        session.commit()

    with Session(engine) as session:
        finish(session, user_id, "After compensation")
        publication = session.scalars(select(SignalPublicationModel)).one()
        assert publication.signal_name == "Notice"
        assert publication.source_process_instance_id == instance_id
        assert records(session, instance_id)[-1].status == "completed"


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
