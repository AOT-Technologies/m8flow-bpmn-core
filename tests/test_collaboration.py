from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_core import api
from m8flow_core.models.bpmn_process_definition import BpmnProcessDefinitionModel
from m8flow_core.models.process_instance import ProcessInstanceModel
from m8flow_core.models.scheduler_job import SchedulerJobModel
from m8flow_core.models.task_definition import TaskDefinitionModel
from m8flow_core.models.tenant import M8flowTenantModel
from m8flow_core.models.user import UserModel
from m8flow_core.services import collaboration_delivery, tasks
from m8flow_core.services.authorization import ROLE_ADMIN, ensure_v1_role

SOURCE = (
    Path(__file__).parent / "fixtures/order-fulfilment-collaboration.bpmn"
).read_text(encoding="utf-8")
NOW = datetime(2031, 1, 1, tzinfo=UTC)


def _tenant(session: Session, tenant_id: str = "orders") -> int:
    user = UserModel(
        username=tenant_id,
        email=f"{tenant_id}@example.test",
        service=f"https://example.test/realms/{tenant_id}",
        service_id=tenant_id,
        realm_identifier=tenant_id,
    )
    session.add_all(
        [M8flowTenantModel(id=tenant_id, slug=tenant_id, name=tenant_id), user]
    )
    session.flush()
    ensure_v1_role(
        session, tenant_id=tenant_id, role_name=ROLE_ADMIN, user_ids=[user.id]
    )
    return user.id


def _deploy(
    session: Session, user_id: int, *, tenant_id: str = "orders", source: str = SOURCE
) -> BpmnProcessDefinitionModel:
    return api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant_id,
            bpmn_identifier="order-model",
            user_id=user_id,
            source_bpmn_xml=source,
        ),
    )


def _start(
    db,
    definition,
    user_id,
    process_id,
    *,
    key="order-123",
    message_start=False,
    tenant_id="orders",
) -> ProcessInstanceModel:
    return api.execute_command(
        db,
        api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=tenant_id,
            bpmn_process_definition_id=definition.id,
            process_initiator_id=user_id,
            bpmn_process_id=process_id,
            collaboration_correlation_key=key,
            submission_metadata={"start_fulfilment": str(message_start)},
            started_at=NOW,
        ),
    )


def _complete(db, instance, user_id, *, tenant_id="orders") -> None:
    items = api.execute_query(
        db, api.GetPendingTasksQuery(tenant_id=tenant_id, user_id=user_id)
    )
    item = next(item for item in items if item.process_instance_id == instance.id)
    api.execute_command(db, api.ClaimTaskCommand(tenant_id, item.id, user_id))
    api.execute_command(
        db, api.CompleteTaskCommand(tenant_id, item.id, user_id, completed_at=NOW)
    )


def _jobs(session):
    return list(
        session.scalars(select(SchedulerJobModel).order_by(SchedulerJobModel.id))
    )


def _instances(session):
    return list(
        session.scalars(select(ProcessInstanceModel).order_by(ProcessInstanceModel.id))
    )


def test_deploy_preserves_pools_and_flows_and_validates_references(session):
    user_id = _tenant(session)
    definition = _deploy(session, user_id)
    metadata = definition.collaboration_metadata
    assert definition.bpmn_identifier == metadata["id"] == "OrderCollaboration"
    assert definition.source_bpmn_xml == SOURCE
    assert metadata["executable_process_ids"] == ["Order", "Fulfilment"]
    assert metadata["participants"][2]["process_ref"] is None
    assert metadata["message_flows"][0]["message_ref"] == "NewOrder"
    assert metadata["message_flows"][1]["routed_message_ref"] == "ConfirmOrder"
    assert metadata["message_flows"][2]["target_kind"] is None
    assert _deploy(session, user_id).id == definition.id
    with pytest.raises(api.ValidationError, match="unknown endpoint"):
        _deploy(
            session,
            user_id,
            source=SOURCE.replace('targetRef="ExternalPool"', 'targetRef="Missing"'),
        )
    with pytest.raises(api.ValidationError, match="matching message references"):
        _deploy(
            session,
            user_id,
            source=SOURCE.replace(
                'messageRef="orders:NewOrder"/>', 'messageRef="ConfirmOrder"/>', 1
            ),
        )
    with pytest.raises(api.ValidationError, match="process id is required"):
        _start(session, definition, user_id, None)
    assert _instances(session) == []
    assert len(list(session.scalars(select(BpmnProcessDefinitionModel)))) == 1


@pytest.mark.parametrize(
    "black_box",
    [True, False],
    ids=["black-box-pool", "non-executable-pool"],
)
def test_deploy_rejects_collaboration_with_only_one_executable_participant(
    session, black_box
):
    user_id = _tenant(session)
    source = SOURCE.replace(
        'id="Fulfilment" isExecutable="true"',
        'id="Fulfilment" isExecutable="false"',
    )
    if black_box:
        source = source.replace(' processRef="Fulfilment"', "")
    with pytest.raises(
        api.ValidationError, match="two executable participant processes"
    ):
        _deploy(session, user_id, source=source)
    assert session.scalars(select(BpmnProcessDefinitionModel)).all() == []


def test_collaboration_message_then_signal_advances_the_same_participant(session):
    user_id = _tenant(session)
    source = (
        SOURCE.replace(
            '<bpmn:collaboration id="OrderCollaboration">',
            '<bpmn:signal id="Release" name="Release fulfilment"/>'
            '<bpmn:collaboration id="OrderCollaboration">',
        )
        .replace(
            '<bpmn:endEvent id="FulfilmentComplete"/>',
            '<bpmn:intermediateCatchEvent id="ReleaseCatch">'
            '<bpmn:signalEventDefinition signalRef="orders:Release"/>'
            "</bpmn:intermediateCatchEvent>"
            '<bpmn:endEvent id="FulfilmentComplete"/>'
            '<bpmn:sequenceFlow id="Released" sourceRef="ReleaseCatch"'
            ' targetRef="FulfilmentComplete"/>',
        )
        .replace(
            'sourceRef="FulfilmentCatch" targetRef="FulfilmentComplete"',
            'sourceRef="FulfilmentCatch" targetRef="ReleaseCatch"',
        )
    )
    definition = _deploy(session, user_id, source=source)
    fulfilment = _start(session, definition, user_id, "Fulfilment")
    _complete(session, fulfilment, user_id)
    order = _start(session, definition, user_id, "Order")
    _complete(session, order, user_id)
    assert api.run_due_scheduler_jobs(session, tenant_id="orders", now=NOW) == 1
    assert fulfilment.status == "waiting"
    api.execute_command(
        session,
        api.PublishSignalCommand(
            tenant_id="orders",
            signal_name="Release fulfilment",
            user_id=user_id,
            occurrence_id="release-order-123",
            published_at=NOW,
        ),
    )
    deliveries = api.execute_command(
        session, api.RunSignalDeliveriesCommand(tenant_id="orders", now=NOW)
    )
    assert [
        (delivery.process_instance_id, delivery.status) for delivery in deliveries
    ] == [(fulfilment.id, "delivered")]
    assert fulfilment.status == "complete"


def test_message_starts_exact_event_and_is_delivered_once(session):
    user_id = _tenant(session)
    definition = _deploy(session, user_id)
    sender = _start(session, definition, user_id, "Order", key=None, message_start=True)
    _complete(session, sender, user_id)
    assert sender.status == "complete"
    assert len(_jobs(session)) == 1
    assert api.run_due_scheduler_jobs(session, tenant_id="orders", now=NOW) == 1
    order, fulfilment = _instances(session)
    assert order.id == sender.id
    assert fulfilment.status == "complete"
    assert fulfilment.process_initiator_id == user_id
    assert (
        fulfilment.collaboration_correlation_key == order.collaboration_correlation_key
    )
    assert fulfilment.bpmn_version_id == order.bpmn_version_id
    states = {
        task.task_definition.properties_json["__m8f_task_identifier"]: task.state
        for task in fulfilment.tasks
    }
    assert states["FulfilmentMessageStart"] == "COMPLETED"
    assert states.get("UnrelatedMessageStart") != "COMPLETED"
    assert states.get("FulfilmentStart") != "COMPLETED"
    assert api.run_due_scheduler_jobs(session, now=NOW) == 0


def test_message_waits_until_exact_correlated_catch_is_ready(session):
    user_id = _tenant(session)
    definition = _deploy(session, user_id)
    sender = _start(session, definition, user_id, "Order")
    recipient = _start(session, definition, user_id, "Fulfilment")
    wrong_key = _start(session, definition, user_id, "Fulfilment", key="ORDER-123")
    _complete(session, wrong_key, user_id)
    _complete(session, sender, user_id)
    assert api.run_due_scheduler_jobs(session, tenant_id="orders", now=NOW) == 0
    assert _jobs(session)[0].payload_json["delivery_status"] == "waiting_for_recipient"
    _complete(session, recipient, user_id)
    assert recipient.status == "waiting"
    assert (
        api.run_due_scheduler_jobs(
            session, tenant_id="orders", now=NOW + timedelta(seconds=5)
        )
        == 1
    )
    assert recipient.status == "complete"
    assert wrong_key.status == "waiting"
    assert definition.bpmn_identifier == "OrderCollaboration"
    identifiers = set(session.scalars(select(TaskDefinitionModel.bpmn_identifier)))
    assert {"Order::Start", "Fulfilment::Start"} <= identifiers


def test_ambiguous_catch_defers_and_ignores_suspended_instance(session):
    user_id = _tenant(session)
    definition = _deploy(session, user_id)
    first = _start(session, definition, user_id, "Fulfilment")
    second = _start(session, definition, user_id, "Fulfilment")
    sender = _start(session, definition, user_id, "Order")
    for instance in (first, second, sender):
        _complete(session, instance, user_id)

    assert api.run_due_scheduler_jobs(session, now=NOW) == 0
    assert _jobs(session)[0].payload_json["delivery_status"] == "ambiguous_recipient"
    api.execute_command(
        session,
        api.SuspendProcessInstanceCommand(
            tenant_id="orders", process_instance_id=second.id, user_id=user_id
        ),
    )
    assert api.run_due_scheduler_jobs(session, now=NOW + timedelta(seconds=5)) == 1
    assert first.status == "complete"
    assert second.status == "suspended"


def test_correlation_is_scoped_to_tenant_and_deployment_version(session):
    user_id = _tenant(session)
    other_user = _tenant(session, "other")
    definition = _deploy(session, user_id)
    next_version = _deploy(
        session, user_id, source=SOURCE.replace('name="Order"', 'name="Order 2"')
    )
    other_tenant = _deploy(session, other_user, tenant_id="other")
    sender = _start(session, definition, user_id, "Order")
    wrong_version = _start(session, next_version, user_id, "Fulfilment")
    wrong_tenant = _start(
        session, other_tenant, other_user, "Fulfilment", tenant_id="other"
    )
    _complete(session, wrong_version, user_id)
    _complete(session, wrong_tenant, other_user, tenant_id="other")
    _complete(session, sender, user_id)

    assert api.run_due_scheduler_jobs(session, now=NOW) == 0
    target = _start(session, definition, user_id, "Fulfilment")
    _complete(session, target, user_id)
    assert api.run_due_scheduler_jobs(session, now=NOW + timedelta(seconds=5)) == 1
    assert target.status == "complete"
    assert wrong_version.status == wrong_tenant.status == "waiting"


def test_sender_and_delivery_rollback_with_caller_transactions(session, engine):
    user_id = _tenant(session)
    definition = _deploy(session, user_id)
    sender = _start(session, definition, user_id, "Order", message_start=True)
    session.commit()
    with engine.connect() as connection:
        transaction = connection.begin()
        _complete(connection, sender, user_id)
        assert connection.execute(select(SchedulerJobModel.id)).scalar_one()
        transaction.rollback()
    session.expire_all()
    assert _jobs(session) == []
    assert sender.status == "user_input_required"

    _complete(session, sender, user_id)
    session.commit()
    assert api.run_due_scheduler_jobs(session, now=NOW) == 1
    assert len(_instances(session)) == 2
    session.rollback()
    assert len(_instances(session)) == 1
    assert len(_jobs(session)) == 1
    assert api.run_due_scheduler_jobs(session, now=NOW) == 1
    assert len(_instances(session)) == 2


def test_failed_message_start_can_retry_without_duplicate_instances(
    session, monkeypatch
):
    user_id = _tenant(session)
    definition = _deploy(session, user_id)
    sender = _start(session, definition, user_id, "Order", message_start=True)
    _complete(session, sender, user_id)
    start = collaboration_delivery._start_collaboration_participant

    def fail_after_start(*args, **kwargs):
        start(*args, **kwargs)
        raise api.ValidationError("simulated failure before delivery is acknowledged")

    with monkeypatch.context() as patch:
        patch.setattr(
            collaboration_delivery, "_start_collaboration_participant", fail_after_start
        )
        with pytest.raises(api.ValidationError, match="simulated failure"):
            api.run_due_scheduler_jobs(session, now=NOW)
    assert len(_instances(session)) == 1
    assert not _jobs(session)[0].is_locked
    assert api.run_due_scheduler_jobs(session, now=NOW) == 1
    assert len(_instances(session)) == 2
    assert _jobs(session) == []


def test_collaboration_timers_target_participants_and_are_not_rearmed_on_reimport(
    session,
):
    user_id = _tenant(session)
    timer = """
    <bpmn:startEvent id="{prefix}Timer">
      <bpmn:timerEventDefinition><bpmn:timeDate>2031-01-01T00:00:00Z</bpmn:timeDate>
      </bpmn:timerEventDefinition>
    </bpmn:startEvent>
    <bpmn:sequenceFlow id="{prefix}TimerFlow" sourceRef="{prefix}Timer"
                       targetRef="{target}"/>
    """
    source = SOURCE.replace(
        '<bpmn:startEvent id="OrderStart"/>',
        '<bpmn:startEvent id="OrderStart"/>'
        + timer.format(prefix="Order", target="ApproveOrder"),
    ).replace(
        '<bpmn:startEvent id="FulfilmentStart"/>',
        '<bpmn:startEvent id="FulfilmentStart"/>'
        + timer.format(prefix="Fulfilment", target="FulfilmentComplete"),
    )
    definition = _deploy(session, user_id, source=source)
    assert {
        job.payload_json["timer_task"]["bpmn_process_id"] for job in _jobs(session)
    } == {
        "Order",
        "Fulfilment",
    }
    assert api.run_due_scheduler_jobs(session, now=NOW) == 2
    assert {instance.status for instance in _instances(session)} == {
        "user_input_required",
        "complete",
    }
    assert _deploy(session, user_id, source=source).id == definition.id
    assert _jobs(session) == []


def test_concurrent_task_completion_and_delivery_keep_both_branches(
    session, engine, monkeypatch
):
    source = (
        SOURCE.replace(
            '<bpmn:userTask id="PrepareFulfilment"',
            '<bpmn:parallelGateway id="FulfilmentSplit"/>'
            '<bpmn:parallelGateway id="FulfilmentJoin"/>'
            '<bpmn:userTask id="PrepareFulfilment"',
        )
        .replace(
            'sourceRef="FulfilmentStart" targetRef="PrepareFulfilment"/>',
            'sourceRef="FulfilmentStart" targetRef="FulfilmentSplit"/>'
            '<bpmn:sequenceFlow id="SplitPreparation" sourceRef="FulfilmentSplit"'
            ' targetRef="PrepareFulfilment"/>'
            '<bpmn:sequenceFlow id="SplitCatch" sourceRef="FulfilmentSplit"'
            ' targetRef="FulfilmentCatch"/>'
            '<bpmn:sequenceFlow id="Joined" sourceRef="FulfilmentJoin"'
            ' targetRef="FulfilmentComplete"/>',
        )
        .replace(
            'sourceRef="PrepareFulfilment" targetRef="FulfilmentCatch"',
            'sourceRef="PrepareFulfilment" targetRef="FulfilmentJoin"',
        )
        .replace(
            'sourceRef="FulfilmentCatch" targetRef="FulfilmentComplete"',
            'sourceRef="FulfilmentCatch" targetRef="FulfilmentJoin"',
        )
    )
    user_id = _tenant(session)
    definition = _deploy(session, user_id, source=source)
    sender = _start(session, definition, user_id, "Order")
    recipient = _start(session, definition, user_id, "Fulfilment")
    _complete(session, sender, user_id)
    item_id = recipient.work_items[0].id
    api.execute_command(session, api.ClaimTaskCommand("orders", item_id, user_id))
    session.commit()

    delivering = Event()
    completing = Event()
    advance = collaboration_delivery._advance_waiting_workflow
    lock = tasks.lock_collaboration_instance

    def pause_delivery(*args, **kwargs):
        delivering.set()
        assert completing.wait(5)
        return advance(*args, **kwargs)

    def signal_completion(*args, **kwargs):
        completing.set()
        return lock(*args, **kwargs)

    def deliver():
        with Session(engine) as worker, worker.begin():
            return api.run_due_scheduler_jobs(worker, now=NOW)

    def complete():
        assert delivering.wait(5)
        with Session(engine) as worker, worker.begin():
            api.execute_command(
                worker,
                api.CompleteTaskCommand("orders", item_id, user_id, completed_at=NOW),
            )

    monkeypatch.setattr(
        collaboration_delivery, "_advance_waiting_workflow", pause_delivery
    )
    monkeypatch.setattr(tasks, "lock_collaboration_instance", signal_completion)
    with ThreadPoolExecutor(max_workers=2) as executor:
        delivery = executor.submit(deliver)
        completion = executor.submit(complete)
        assert delivery.result(timeout=15) == 1
        completion.result(timeout=15)
    session.expire_all()
    assert recipient.status == "complete"
    assert _jobs(session) == []
