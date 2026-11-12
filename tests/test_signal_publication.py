#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from sqlalchemy import delete, event, insert, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from m8flow_core import api
from m8flow_core.models import (
    M8flowTenantModel,
    ProcessInstanceModel,
    SignalDeliveryModel,
    SignalPublicationModel,
    SignalRegistryStateModel,
    SignalSubscriptionModel,
    UserModel,
)
from m8flow_core.services.authorization import ensure_v1_role
from m8flow_core.services.workflow_runtime import _restore_workflow

NOW = datetime(2040, 1, 1, tzinfo=UTC)


def model(body: str, process: str = "p", signal: str = "Notice") -> str:
    return f'''<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
      xmlns:spiffworkflow="http://spiffworkflow.org/bpmn/schema/1.0/core"
      targetNamespace="urn:signals">
      <signal id="sig" name="{signal}"><extensionElements>
      <spiffworkflow:variableName>received</spiffworkflow:variableName>
      </extensionElements></signal>
      <process id="{process}" isExecutable="true">{body}</process></definitions>'''


def flow(source: str, target: str) -> str:
    return (
        f'<sequenceFlow id="{source}_{target}" '
        f'sourceRef="{source}" targetRef="{target}"/>'
    )


def script(name: str, code: str) -> str:
    return (
        f'<scriptTask id="{name}" scriptFormat="python">'
        f"<script>{code}</script></scriptTask>"
    )


def waiter(process: str = "wait", signal: str = "Notice") -> str:
    return model(
        '<startEvent id="start"/>'
        + flow("start", "catch")
        + '<intermediateCatchEvent id="catch"><signalEventDefinition signalRef="sig"/>'
        + "</intermediateCatchEvent>"
        + flow("catch", "after")
        + script("after", "after_catch = True")
        + flow("after", "end")
        + '<endEvent id="end"/>',
        process,
        signal,
    )


def thrower(end: bool = False, gated: bool = False) -> str:
    start = '<startEvent id="start"/>'
    if gated:
        start += flow("start", "gate") + '<userTask id="gate"/>' + flow("gate", "throw")
    else:
        start += flow("start", "throw")
    if end:
        return model(
            start + '<endEvent id="throw"><signalEventDefinition signalRef="sig"/>'
            "</endEvent>",
            "throw_end",
        )
    return model(
        start
        + '<intermediateThrowEvent id="throw"><signalEventDefinition signalRef="sig"/>'
        + "</intermediateThrowEvent>"
        + flow("throw", "after")
        + script("after", "after_throw = True")
        + flow("after", "end")
        + '<endEvent id="end"/>',
        "thrower",
    )


def start_model(process: str = "signal_start") -> str:
    return model(
        '<startEvent id="signal_start">'
        '<signalEventDefinition signalRef="sig"/></startEvent>'
        + flow("signal_start", "after")
        + script("after", "started_by_signal = True")
        + flow("after", "end")
        + '<endEvent id="end"/>',
        process,
    )


@pytest.fixture()
def actor(session: Session) -> tuple[str, int]:
    return seed_actor(session, "signals")


def seed_actor(session: Session, tenant: str) -> tuple[str, int]:
    user = UserModel(
        username=f"admin-{tenant}",
        service=f"app/realms/{tenant}",
        service_id=f"admin-{tenant}",
        created_at=NOW,
        updated_at=NOW,
    )
    session.add_all([M8flowTenantModel(id=tenant, name=tenant, slug=tenant), user])
    session.flush()
    ensure_v1_role(session, tenant_id=tenant, role_name="admin", user_ids=[user.id])
    return tenant, user.id


def deploy(
    session: Session, actor: tuple[str, int], xml: str, identifier: str = "model"
):
    return api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=actor[0],
            bpmn_identifier=identifier,
            user_id=actor[1],
            source_bpmn_xml=xml,
            created_at=NOW,
        ),
    )


def start(session: Session, actor: tuple[str, int], definition):
    return api.execute_command(
        session,
        api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=actor[0],
            bpmn_process_definition_id=definition.id,
            process_initiator_id=actor[1],
            started_at=NOW,
        ),
    )


def publish(
    session: Session, actor: tuple[str, int], occurrence: str = "one", **kwargs
):
    return api.execute_command(
        session,
        api.PublishSignalCommand(
            tenant_id=actor[0],
            signal_name="Notice",
            user_id=actor[1],
            occurrence_id=occurrence,
            published_at=NOW,
            **kwargs,
        ),
    )


def run(session: Session, tenant: str, **kwargs):
    return api.execute_command(
        session,
        api.RunSignalDeliveriesCommand(
            tenant_id=tenant,
            now=NOW,
            **kwargs,
        ),
    )


def data(instance):
    return _restore_workflow(instance.workflow_state_json).data


@pytest.mark.parametrize("end", [False, True])
def test_unmatched_throw_completes_and_actually_publishes(session, actor, end):
    instance = start(session, actor, deploy(session, actor, thrower(end)))
    assert instance.status == "complete"
    if not end:
        assert data(instance)["after_throw"] is True
    rows = list(session.scalars(select(SignalPublicationModel)))
    assert len(rows) == 1
    assert rows[0].source_process_instance_id == instance.id
    assert (
        api.list_signal_deliveries(
            session, tenant_id=actor[0], occurrence_id=rows[0].occurrence_id
        )
        == []
    )
    assert all(
        e.event_type != "process_instance_error"
        for e in instance.process_instance_events
    )


def test_broadcast_three_instances_and_signal_start_exactly_once(session, actor):
    definition = deploy(session, actor, waiter(), "waiting")
    instances = [start(session, actor, definition) for _ in range(3)]
    deploy(session, actor, start_model(), "started")
    emitter = start(session, actor, deploy(session, actor, thrower(), "publisher"))
    assert emitter.status == "complete"
    session.commit()
    receipts = run(session, actor[0])
    assert len(receipts) == 4
    assert {r.status for r in receipts} == {"delivered"}
    session.commit()
    session.expire_all()
    for instance in instances:
        assert instance.status == "complete"
        assert data(instance)["after_catch"] is True
    started = session.scalar(
        select(ProcessInstanceModel).where(
            ProcessInstanceModel.process_model_identifier == "started",
        )
    )
    assert started.status == "complete"
    assert data(started)["started_by_signal"] is True
    assert run(session, actor[0]) == []
    assert len(list(session.scalars(select(ProcessInstanceModel)))) == 5


def test_signal_reaches_called_child_after_restoring_parent(session, actor):
    parent = deploy(
        session,
        actor,
        model(
            '<startEvent id="start"/>'
            + flow("start", "call")
            + '<callActivity id="call" calledElement="wait"/>'
            + flow("call", "end")
            + '<endEvent id="end"/>'
        ),
    )
    instance = api.execute_command(
        session,
        api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=actor[0],
            bpmn_process_definition_id=parent.id,
            process_initiator_id=actor[1],
            started_at=NOW,
            call_activity_resolver=lambda _: api.CallActivityDefinition(
                process_id="wait", bpmn_xml=waiter()
            ),
        ),
    )
    assert instance.status == "waiting"
    session.commit()
    session.expunge_all()

    publish(session, actor)
    receipts = run(session, actor[0])
    assert [(receipt.status, receipt.process_instance_id) for receipt in receipts] == [
        ("delivered", instance.id)
    ]
    restored = session.get(ProcessInstanceModel, instance.id)
    assert restored.status == "complete"


def test_external_idempotency_payload_and_no_late_replay(session, actor):
    first = publish(session, actor, payload={"value": 42})
    instance = start(session, actor, deploy(session, actor, waiter()))
    assert publish(session, actor, payload={"value": 42}).id == first.id
    assert run(session, actor[0]) == []
    assert instance.status == "waiting"
    with pytest.raises(api.ValidationError):
        publish(session, actor, payload={"value": 43})
    publish(session, actor, "two", payload={"value": 43})
    assert run(session, actor[0])[0].status == "delivered"
    assert data(instance)["received"] == {"value": 43}


def test_tenant_isolation_and_signal_name_matching(session, actor):
    other = seed_actor(session, "other")
    foreign = start(session, other, deploy(session, other, waiter()))
    different = start(
        session,
        actor,
        deploy(session, actor, waiter("different", "notice"), "different"),
    )
    publish(session, actor)
    assert run(session, actor[0]) == []
    assert foreign.status == different.status == "waiting"
    with pytest.raises(api.NotFoundError):
        api.get_signal_publication(session, tenant_id=other[0], occurrence_id="one")
    with pytest.raises(api.AuthorizationError):
        publish(session, (other[0], actor[1]))


def test_signal_registry_rejects_cross_tenant_references(session):
    if session.connection().dialect.name == "sqlite":
        session.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
    actor = seed_actor(session, "signals")
    other = seed_actor(session, "other")
    own_publication = publish(session, actor)
    foreign_publication = publish(session, other)
    own_definition = deploy(session, actor, waiter())
    foreign_definition = deploy(session, other, waiter())
    own_instance = start(session, actor, own_definition)
    foreign_instance = start(session, other, foreign_definition)
    subscriptions = {
        row.m8f_tenant_id: row.id
        for row in session.scalars(select(SignalSubscriptionModel))
    }
    subscription = {
        "m8f_tenant_id": actor[0],
        "subscription_key": "direct",
        "signal_name": "Notice",
        "kind": "catch",
        "process_instance_id": own_instance.id,
        "definition_id": own_definition.id,
        "task_spec_name": "catch",
        "created_at": NOW,
    }
    delivery = {
        "m8f_tenant_id": actor[0],
        "publication_id": own_publication.id,
        "subscription_id": subscriptions[actor[0]],
        "process_instance_id": own_instance.id,
        "retry_at": NOW,
    }
    invalid_inserts = [
        insert(SignalPublicationModel).values(
            m8f_tenant_id=actor[0],
            occurrence_id="direct",
            signal_name="Notice",
            payload_json=None,
            published_at=NOW,
            source_process_instance_id=source_id,
        )
        for source_id in (
            foreign_instance.id,
            max(own_instance.id, foreign_instance.id) + 1,
        )
    ]
    invalid_inserts.extend(
        [
            insert(SignalSubscriptionModel).values(
                subscription | {"process_instance_id": foreign_instance.id}
            ),
            insert(SignalSubscriptionModel).values(
                subscription | {"definition_id": foreign_definition.id}
            ),
            insert(SignalDeliveryModel).values(
                delivery | {"publication_id": foreign_publication.id}
            ),
            insert(SignalDeliveryModel).values(
                delivery | {"subscription_id": subscriptions[other[0]]}
            ),
            insert(SignalDeliveryModel).values(
                delivery | {"process_instance_id": foreign_instance.id}
            ),
        ]
    )
    for statement in invalid_inserts:
        with pytest.raises(IntegrityError, match="(?i)foreign key"):
            with session.begin_nested():
                session.execute(statement)


@pytest.mark.parametrize(
    ("kind", "has_definition", "has_instance"),
    [
        ("start", False, False),
        ("start", True, True),
        ("catch", True, False),
        ("boundary", True, False),
        ("event_subprocess", True, False),
    ],
)
def test_subscription_kind_requires_matching_references(
    session, actor, kind, has_definition, has_instance
):
    definition = deploy(session, actor, waiter())
    instance = start(session, actor, definition)
    with pytest.raises(IntegrityError, match="m8f_signal_subscription_references"):
        with session.begin_nested():
            session.execute(
                insert(SignalSubscriptionModel).values(
                    m8f_tenant_id=actor[0],
                    subscription_key="invalid",
                    signal_name="Notice",
                    kind=kind,
                    process_instance_id=instance.id if has_instance else None,
                    definition_id=definition.id if has_definition else None,
                    task_spec_name="invalid",
                    created_at=NOW,
                )
            )


def test_one_shot_reserved_and_suspension_holds_delivery(session, actor):
    instance = start(session, actor, deploy(session, actor, waiter()))
    api.execute_command(
        session, api.SuspendProcessInstanceCommand(actor[0], instance.id, actor[1])
    )
    publish(session, actor)
    publish(session, actor, "two")
    assert (
        len(
            api.list_signal_deliveries(session, tenant_id=actor[0], occurrence_id="one")
        )
        == 1
    )
    assert (
        api.list_signal_deliveries(session, tenant_id=actor[0], occurrence_id="two")
        == []
    )
    assert run(session, actor[0]) == []
    api.execute_command(
        session, api.ResumeProcessInstanceCommand(actor[0], instance.id, actor[1])
    )
    assert run(session, actor[0])[0].status == "delivered"
    assert instance.status == "complete"


def test_retired_recipient_is_skipped(session, actor):
    instance = start(session, actor, deploy(session, actor, waiter()))
    publish(session, actor)
    api.execute_command(
        session, api.TerminateProcessInstanceCommand(actor[0], instance.id, actor[1])
    )
    assert run(session, actor[0])[0].status == "skipped"
    assert instance.status == "terminated"


def test_latest_start_definition_and_captured_version(session, actor):
    old = deploy(session, actor, start_model("old"), "versions")
    publish(session, actor)
    new = deploy(session, actor, start_model("new"), "versions")
    publish(session, actor, "two")
    assert len(run(session, actor[0])) == 2
    instances = list(session.scalars(select(ProcessInstanceModel)))
    assert {i.bpmn_process_definition_id for i in instances} == {old.id, new.id}


def test_registry_backfills_existing_workflows_without_running_them(session, actor):
    instance = start(session, actor, deploy(session, actor, waiter()))
    session.execute(delete(SignalRegistryStateModel))
    session.execute(delete(SignalSubscriptionModel))
    publish(session, actor)
    assert instance.status == "waiting"
    assert run(session, actor[0])[0].status == "delivered"


def test_delivery_rollback_restores_wait_and_receipt(session, actor):
    instance = start(session, actor, deploy(session, actor, waiter()))
    publish(session, actor)
    session.commit()
    run(session, actor[0])
    session.rollback()
    assert instance.status == "waiting"
    receipts = api.list_signal_deliveries(
        session, tenant_id=actor[0], occurrence_id="one"
    )
    assert receipts[0].status == "pending"
    assert run(session, actor[0])[0].status == "delivered"


def test_signal_binding_validates_timestamp_before_the_first_throw(session, actor):
    definition = deploy(session, actor, thrower(gated=True))
    with pytest.raises(api.ValidationError, match="timezone-aware"):
        api.execute_command(
            session,
            api.InitializeProcessInstanceFromDefinitionCommand(
                tenant_id=actor[0],
                bpmn_process_definition_id=definition.id,
                process_initiator_id=actor[1],
                started_at=datetime(2040, 1, 1),
            ),
        )


def test_invalid_inputs(session, actor):
    for kwargs in [{"payload": object()}, {"payload": float("nan")}]:
        with pytest.raises(api.ValidationError):
            publish(session, actor, **kwargs)
    with pytest.raises(api.ValidationError):
        run(session, actor[0], limit=0)
    with pytest.raises(api.ValidationError):
        api.execute_command(
            session,
            api.PublishSignalCommand(
                actor[0],
                "Notice",
                actor[1],
                "bad-time",
                published_at=datetime(2040, 1, 1),
            ),
        )


def handler_model(kind: str, interrupt: bool, nested: bool = False) -> str:
    main = '<startEvent id="start"/>' + flow("start", "main")
    main += '<userTask id="main"/>' + flow("main", "end") + '<endEvent id="end"/>'
    side = (
        '<userTask id="side"/>' + flow("side", "side_end") + '<endEvent id="side_end"/>'
    )
    flag = str(interrupt).lower()
    if kind == "boundary":
        main += (
            '<boundaryEvent id="boundary" attachedToRef="main" '
            f'cancelActivity="{flag}">'
        )
        main += '<signalEventDefinition signalRef="sig"/></boundaryEvent>'
        main += flow("boundary", "side") + side
    else:
        main += '<subProcess id="handler" triggeredByEvent="true">'
        main += f'<startEvent id="handler_start" isInterrupting="{flag}">'
        main += '<signalEventDefinition signalRef="sig"/></startEvent>'
        main += flow("handler_start", "side") + side + "</subProcess>"
    if nested:
        main = (
            '<startEvent id="outer_start"/>'
            + flow("outer_start", "nested")
            + '<subProcess id="nested">'
            + main
            + "</subProcess>"
            + flow("nested", "outer_end")
            + '<endEvent id="outer_end"/>'
        )
    return model(main, f"{kind}_{flag}")


def pending_names(session, actor):
    return [
        w.task_name
        for w in api.get_pending_tasks(
            session,
            tenant_id=actor[0],
            user_id=actor[1],
        )
    ]


@pytest.mark.parametrize("kind", ["boundary", "event_subprocess"])
@pytest.mark.parametrize("interrupt", [True, False])
@pytest.mark.parametrize("nested", [True, False])
def test_signal_handlers_interrupt_repeat_and_materialize_human_tasks(
    session,
    actor,
    kind,
    interrupt,
    nested,
):
    instance = start(
        session, actor, deploy(session, actor, handler_model(kind, interrupt, nested))
    )
    assert pending_names(session, actor) == ["main"]
    publish(session, actor)
    rows = run(session, actor[0])
    assert [r.status for r in rows] == ["delivered"]
    names = pending_names(session, actor)
    assert names.count("side") == 1
    assert names.count("main") == (0 if interrupt else 1)
    session.commit()
    session.expire_all()
    publish(session, actor, "two")
    rows = run(session, actor[0])
    names = pending_names(session, actor)
    if interrupt:
        assert rows == []
        assert names == ["side"]
    else:
        assert [r.status for r in rows] == ["delivered"]
        assert names.count("side") == 2
        assert names.count("main") == 1
    assert instance.status == "user_input_required"
    for _ in range(4):
        pending = api.get_pending_tasks(session, tenant_id=actor[0], user_id=actor[1])
        if not pending:
            break
        item = pending[0]
        api.execute_command(session, api.ClaimTaskCommand(actor[0], item.id, actor[1]))
        api.execute_command(
            session, api.CompleteTaskCommand(actor[0], item.id, actor[1])
        )
    assert instance.status == "complete"


def test_local_catchers_and_remote_delivery_share_one_publication(session, actor):
    remote = start(session, actor, deploy(session, actor, waiter(), "remote"))
    body = '<startEvent id="s"/>' + flow("s", "split") + '<parallelGateway id="split"/>'
    for number in range(2):
        catcher, after, end = f"c{number}", f"a{number}", f"e{number}"
        body += (
            flow("split", catcher) + f'<intermediateCatchEvent id="{catcher}">'
            '<signalEventDefinition signalRef="sig"/></intermediateCatchEvent>'
        )
        body += (
            flow(catcher, after)
            + script(after, f"caught_{number} = True")
            + flow(after, end)
            + f'<endEvent id="{end}"/>'
        )
    body += flow("split", "gate") + '<userTask id="gate"/>' + flow("gate", "throw")
    body += (
        '<intermediateThrowEvent id="throw">'
        '<signalEventDefinition signalRef="sig"/></intermediateThrowEvent>'
        + flow("throw", "end")
        + '<endEvent id="end"/>'
    )
    local = start(session, actor, deploy(session, actor, model(body, "local"), "local"))
    session.commit()
    item = api.get_pending_tasks(session, tenant_id=actor[0], user_id=actor[1])[0]
    api.execute_command(session, api.ClaimTaskCommand(actor[0], item.id, actor[1]))
    api.execute_command(session, api.CompleteTaskCommand(actor[0], item.id, actor[1]))
    assert local.status == "complete"
    assert remote.status == "waiting"
    publications = list(session.scalars(select(SignalPublicationModel)))
    assert len(publications) == 1
    receipts = api.list_signal_deliveries(
        session, tenant_id=actor[0], occurrence_id=publications[0].occurrence_id
    )
    assert [r.status for r in receipts].count("delivered") == 2
    assert len(run(session, actor[0])) == 1
    assert remote.status == "complete"


def test_recipient_script_failure_consumed_once_and_does_not_block_others(
    session, actor
):
    bad_xml = waiter("bad").replace(
        "after_catch = True", 'raise RuntimeError("recipient failure")'
    )
    bad = start(session, actor, deploy(session, actor, bad_xml, "bad"))
    good = start(session, actor, deploy(session, actor, waiter("good"), "good"))
    publish(session, actor)
    assert {r.status for r in run(session, actor[0])} == {"delivered"}
    assert bad.status == "error"
    assert good.status == "complete"
    assert run(session, actor[0]) == []


def test_connection_rollback_owns_publication_and_consumption(engine, session, actor):
    instance = start(session, actor, deploy(session, actor, waiter()))
    instance_id = instance.id
    session.commit()
    with engine.connect() as connection:
        transaction = connection.begin()
        api.execute_command(
            connection,
            api.PublishSignalCommand(
                actor[0],
                "Notice",
                actor[1],
                "connection",
                published_at=NOW,
            ),
        )
        rows = api.run_signal_deliveries(connection, tenant_id=actor[0], now=NOW)
        assert rows[0].status == "delivered"
        transaction.rollback()
    with Session(engine) as check:
        assert check.get(ProcessInstanceModel, instance_id).status == "waiting"
        with pytest.raises(api.NotFoundError):
            api.execute_query(
                check, api.GetSignalPublicationQuery(actor[0], "connection")
            )


def test_pending_deliveries_survive_session_restart(engine, session, actor):
    instance = start(session, actor, deploy(session, actor, waiter()))
    instance_id = instance.id
    publish(session, actor)
    session.commit()
    with Session(engine) as restarted:
        receipts = run(restarted, actor[0])
        assert [r.status for r in receipts] == ["delivered"]
        restarted.commit()
    with Session(engine) as restarted:
        assert run(restarted, actor[0]) == []
        assert restarted.get(ProcessInstanceModel, instance_id).status == "complete"


def test_failure_before_consumption_retries_without_losing_other_recipients(
    session,
    actor,
    monkeypatch,
):
    from m8flow_core.services import signals

    definition = deploy(session, actor, waiter())
    instances = [start(session, actor, definition) for _ in range(2)]
    publish(session, actor)
    original = signals._inject_deliveries

    def fail_one(db, instance, *args):
        if instance.id == instances[0].id:
            raise RuntimeError("temporary delivery failure")
        return original(db, instance, *args)

    monkeypatch.setattr(signals, "_inject_deliveries", fail_one)
    rows = run(session, actor[0])
    assert [r.status for r in rows] == ["pending", "delivered"]
    assert rows[0].attempts == 1
    assert rows[0].last_error == "temporary delivery failure"
    assert instances[0].status == "waiting"
    assert instances[1].status == "complete"
    monkeypatch.setattr(signals, "_inject_deliveries", original)
    assert run(session, actor[0]) == []
    rows = api.run_signal_deliveries(
        session,
        tenant_id=actor[0],
        now=NOW + timedelta(seconds=30),
    )
    assert [r.status for r in rows] == ["delivered"]
    assert rows[0].attempts == 2


def test_newly_produced_signals_wait_for_next_bounded_batch(session, actor):
    xml = start_model().replace(
        '<endEvent id="end"/>',
        '<endEvent id="end"><signalEventDefinition signalRef="sig"/></endEvent>',
    )
    deploy(session, actor, xml)
    publish(session, actor)
    assert len(run(session, actor[0], limit=1)) == 1
    assert len(list(session.scalars(select(ProcessInstanceModel)))) == 1
    assert len(run(session, actor[0], limit=1)) == 1
    assert len(list(session.scalars(select(ProcessInstanceModel)))) == 2


def test_one_shot_reservation_survives_unrelated_workflow_persistence(session, actor):
    from m8flow_core.services.workflow_runtime import (
        repair_process_instance_runtime_representation,
    )

    instance = start(session, actor, deploy(session, actor, waiter()))
    publish(session, actor)
    repair_process_instance_runtime_representation(
        session,
        tenant_id=actor[0],
        process_instance_id=instance.id,
    )
    publish(session, actor, "two")
    assert (
        api.list_signal_deliveries(session, tenant_id=actor[0], occurrence_id="two")
        == []
    )
    assert run(session, actor[0])[0].status == "delivered"


def test_manager_can_publish_user_cannot_and_payload_types_are_distinct(session, actor):
    user = UserModel(
        username="member",
        service=f"app/realms/{actor[0]}",
        service_id="member",
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(user)
    session.flush()
    ensure_v1_role(session, tenant_id=actor[0], role_name="user", user_ids=[user.id])
    with pytest.raises(api.AuthorizationError):
        publish(session, (actor[0], user.id))
    ensure_v1_role(session, tenant_id=actor[0], role_name="manager", user_ids=[user.id])
    publish(session, (actor[0], user.id), payload=True)
    with pytest.raises(api.ValidationError):
        publish(session, (actor[0], user.id), payload=1)


def test_upgrade_preserves_running_workflow_and_backfills_registry(
    session,
    actor,
    engine,
    monkeypatch,
):
    from alembic import command
    from alembic.config import Config

    from m8flow_core.settings import get_settings

    instance = start(session, actor, deploy(session, actor, waiter()))
    session.commit()
    monkeypatch.setenv(
        "M8FLOW_DATABASE_URL", engine.url.render_as_string(hide_password=False)
    )
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        command.stamp(config, "head")
        command.downgrade(config, "k2l3m4n5o6p7")
        command.upgrade(config, "head")
        session.expire_all()
        assert instance.status == "waiting"
        publish(session, actor)
        assert run(session, actor[0])[0].status == "delivered"
        assert instance.status == "complete"
    finally:
        get_settings.cache_clear()


def test_qualified_signal_references_match_names_across_documents(session, actor):
    xml = waiter().replace('id="sig"', 'id="another_xml_id"')
    xml = xml.replace('signalRef="sig"', 'signalRef="tns:another_xml_id"')
    xml = xml.replace(
        'targetNamespace="urn:signals"',
        'xmlns:tns="urn:signals" targetNamespace="urn:signals"',
    )
    receiver = start(session, actor, deploy(session, actor, xml, "receiver"))
    start(session, actor, deploy(session, actor, thrower(), "sender"))
    assert run(session, actor[0])[0].status == "delivered"
    assert receiver.status == "complete"


def test_concurrent_identical_publications_create_one_occurrence(
    engine, session, actor
):
    start(session, actor, deploy(session, actor, waiter()))
    session.commit()

    def worker(_number):
        with Session(engine) as worker_session:
            publication = publish(worker_session, actor)
            publication_id = publication.id
            worker_session.commit()
            return publication_id

    with ThreadPoolExecutor(max_workers=4) as pool:
        identifiers = list(pool.map(worker, range(4)))
    assert len(set(identifiers)) == 1
    assert (
        len(
            api.list_signal_deliveries(
                session,
                tenant_id=actor[0],
                occurrence_id="one",
            )
        )
        == 1
    )


@pytest.mark.parametrize("commit_first", [True, False], ids=["commit", "rollback"])
def test_concurrent_runners_create_one_signal_start(
    engine, session, actor, commit_first
):
    deploy(session, actor, start_model())
    publish(session, actor)
    session.commit()
    contender_reached_registry = Event()

    def compete():
        with Session(engine) as contender:
            connection = contender.connection()

            @event.listens_for(connection, "before_cursor_execute")
            def reached_registry(
                _connection, _cursor, statement, _parameters, _context, _many
            ):
                if statement.startswith("UPDATE signal_registry_state"):
                    contender_reached_registry.set()

            rows = run(contender, actor[0])
            result = [(row.status, row.attempts) for row in rows]
            contender.commit()
            return result

    with ThreadPoolExecutor(max_workers=1) as pool, Session(engine) as first:
        rows = run(first, actor[0])
        assert [(row.status, row.attempts) for row in rows] == [("delivered", 1)]
        competing = pool.submit(compete)
        assert contender_reached_registry.wait(timeout=5)
        if commit_first:
            first.commit()
        else:
            first.rollback()
        assert competing.result(timeout=10) == (
            [] if commit_first else [("delivered", 1)]
        )

    assert len(list(session.scalars(select(ProcessInstanceModel)))) == 1
    receipts = api.list_signal_deliveries(
        session, tenant_id=actor[0], occurrence_id="one"
    )
    assert [(row.status, row.attempts) for row in receipts] == [("delivered", 1)]
    assert run(session, actor[0]) == []


def service_task() -> str:
    return """<serviceTask id="after"><extensionElements>
      <spiffworkflow:serviceTaskOperator id="demo/Run" resultVariable="service_result">
        <spiffworkflow:parameters/>
      </spiffworkflow:serviceTaskOperator>
      </extensionElements></serviceTask>"""


class RetryConnector:
    connector_key = "demo"
    fail = True

    def list_commands(self):
        return (
            api.ServiceTaskCommandDefinition(
                connector_key="demo",
                command_name="Run",
            ),
        )

    def execute(self, request):
        if self.fail:
            raise RuntimeError("connector unavailable")
        return api.ServiceTaskResult(payload={"ok": True})


def test_recipient_service_failure_retries_without_consuming_signal_again(
    session, actor
):
    xml = waiter().replace(script("after", "after_catch = True"), service_task())
    instance = start(session, actor, deploy(session, actor, xml))
    publish(session, actor)
    connector = RetryConnector()
    with api.service_task_registry_scope(
        api.ServiceTaskRegistry(connectors=(connector,))
    ):
        rows = run(session, actor[0])
        assert rows[0].status == "delivered"
        assert rows[0].last_error
        assert instance.status == "error"
        session.commit()
        connector.fail = False
        api.execute_command(
            session,
            api.RetryProcessInstanceCommand(
                actor[0],
                instance.id,
                actor[1],
                retried_at=NOW,
            ),
        )
    assert instance.status == "complete"
    assert run(session, actor[0]) == []
    assert (
        api.list_signal_deliveries(
            session,
            tenant_id=actor[0],
            occurrence_id="one",
        )[0].attempts
        == 1
    )


def test_publisher_failure_rollback_keeps_publication_and_workflow_atomic(
    session, actor
):
    receiver = start(session, actor, deploy(session, actor, waiter(), "receiver"))
    xml = thrower().replace(script("after", "after_throw = True"), service_task())
    definition = deploy(session, actor, xml, "sender")
    session.commit()
    connector = RetryConnector()
    with api.service_task_registry_scope(
        api.ServiceTaskRegistry(connectors=(connector,))
    ):
        with pytest.raises(api.ServiceTaskExecutionError):
            start(session, actor, definition)
    session.rollback()
    assert receiver.status == "waiting"
    assert list(session.scalars(select(SignalPublicationModel))) == []


def test_signal_start_creation_and_receipt_roll_back_together(session, actor):
    deploy(session, actor, start_model())
    publish(session, actor)
    session.commit()
    run(session, actor[0])
    session.commit()
    assert len(list(session.scalars(select(ProcessInstanceModel)))) == 1
    publish(session, actor, "two")
    session.commit()
    [delivery] = run(session, actor[0])
    assert delivery.status == "delivered"
    rolled_back = session.get(ProcessInstanceModel, delivery.process_instance_id)
    assert rolled_back is not None
    rolled_back_process = rolled_back.bpmn_process
    session.rollback()
    assert inspect(rolled_back_process).transient
    assert len(list(session.scalars(select(ProcessInstanceModel)))) == 1
    assert (
        api.list_signal_deliveries(
            session,
            tenant_id=actor[0],
            occurrence_id="two",
        )[0].status
        == "pending"
    )
    run(session, actor[0])
    assert len(list(session.scalars(select(ProcessInstanceModel)))) == 2


def test_signal_does_not_replay_to_later_catch_in_same_instance(session, actor):
    xml = waiter().replace(
        flow("after", "end"),
        flow("after", "later")
        + '<intermediateCatchEvent id="later">'
        + '<signalEventDefinition signalRef="sig"/></intermediateCatchEvent>'
        + flow("later", "end"),
    )
    instance = start(session, actor, deploy(session, actor, xml))
    publish(session, actor)
    run(session, actor[0])
    assert instance.status == "waiting"
    assert run(session, actor[0]) == []
    publish(session, actor, "two")
    run(session, actor[0])
    assert instance.status == "complete"


def test_retry_reactivates_waiting_signal_subscriptions(session, actor):
    instance = start(session, actor, deploy(session, actor, waiter()))
    api.execute_command(
        session,
        api.ErrorProcessInstanceCommand(
            actor[0],
            instance.id,
            actor[1],
            errored_at=NOW,
        ),
    )
    publish(session, actor)
    assert run(session, actor[0]) == []
    api.execute_command(
        session,
        api.RetryProcessInstanceCommand(
            actor[0],
            instance.id,
            actor[1],
            retried_at=NOW,
        ),
    )
    publish(session, actor, "two")
    assert run(session, actor[0])[0].status == "delivered"
    assert instance.status == "complete"


def test_loop_throws_are_distinct_signal_occurrences(session, actor):
    body = '<startEvent id="start"/>' + flow("start", "init")
    body += script("init", "count = 0") + flow("init", "throw")
    body += '<intermediateThrowEvent id="throw">'
    body += '<signalEventDefinition signalRef="sig"/></intermediateThrowEvent>'
    body += flow("throw", "increment") + script("increment", "count += 1")
    body += flow("increment", "choice")
    body += '<exclusiveGateway id="choice" default="choice_end"/>'
    body += '<sequenceFlow id="again" sourceRef="choice" targetRef="throw">'
    body += "<conditionExpression>count &lt; 2</conditionExpression></sequenceFlow>"
    body += flow("choice", "end") + '<endEvent id="end"/>'
    instance = start(session, actor, deploy(session, actor, model(body, "loop")))
    assert instance.status == "complete"
    publications = list(session.scalars(select(SignalPublicationModel)))
    assert len(publications) == 2
    assert len({p.occurrence_id for p in publications}) == 2


def test_legacy_signal_end_state_restores_publication_without_restarting(
    session, actor
):
    from SpiffWorkflow.bpmn.specs.event_definitions.simple import NoneEventDefinition

    from m8flow_core.services.workflow_runtime import (
        _WORKFLOW_SERIALIZER,
        _persist_workflow_recovery_state,
    )

    instance = start(
        session, actor, deploy(session, actor, thrower(end=True, gated=True))
    )
    workflow = _WORKFLOW_SERIALIZER.deserialize_json(instance.workflow_state_json)
    workflow.spec.task_specs["throw"].event_definition = NoneEventDefinition()
    _persist_workflow_recovery_state(session, instance, workflow)
    session.commit()
    item = api.get_pending_tasks(session, tenant_id=actor[0], user_id=actor[1])[0]
    api.execute_command(session, api.ClaimTaskCommand(actor[0], item.id, actor[1]))
    api.execute_command(session, api.CompleteTaskCommand(actor[0], item.id, actor[1]))
    assert instance.status == "complete"
    assert len(list(session.scalars(select(SignalPublicationModel)))) == 1


@pytest.mark.parametrize("invalid", ["missing-name", "missing-reference"])
def test_invalid_signal_definition_is_rejected_before_storage(session, actor, invalid):
    from m8flow_core.models import BpmnProcessDefinitionModel

    xml = waiter()
    if invalid == "missing-name":
        xml = xml.replace('name="Notice"', 'name=""')
    else:
        xml = xml.replace('signalRef="sig"', 'signalRef="missing"')
    with pytest.raises(api.ValidationError):
        deploy(session, actor, xml)
    assert list(session.scalars(select(BpmnProcessDefinitionModel))) == []
