from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from m8flow_core import api
from m8flow_core.models import M8flowTenantModel, UserModel
from m8flow_core.services.authorization import ROLE_ADMIN, ensure_v1_role


def _complete_pending(
    connection: Connection, tenant_id: str, user_id: int, expected: str
) -> None:
    with Session(bind=connection) as session:
        [item] = api.execute_query(session, api.GetPendingTasksQuery(tenant_id))
        assert item.task_name == expected
        item_id = item.id
    api.execute_command(connection, api.ClaimTaskCommand(tenant_id, item_id, user_id))
    api.execute_command(
        connection, api.CompleteTaskCommand(tenant_id, item_id, user_id)
    )


def test_postgres_compensation_survives_restart_and_connection_dispatch(
    postgres_engine,
):
    tenant_id = "compensation-" + uuid4().hex
    source = Path(__file__).parents[1] / "tests/fixtures/compensation_saga.bpmn"
    with Session(postgres_engine) as session:
        now = datetime.now(UTC)
        session.add(M8flowTenantModel(id=tenant_id, name=tenant_id, slug=tenant_id))
        user = UserModel(
            username=tenant_id,
            service=tenant_id,
            service_id=tenant_id,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        session.flush()
        ensure_v1_role(
            session, tenant_id=tenant_id, role_name=ROLE_ADMIN, user_ids=[user.id]
        )
        definition = api.execute_command(
            session,
            api.ImportBpmnProcessDefinitionCommand(
                tenant_id, tenant_id, user.id, source.read_text(encoding="utf-8")
            ),
        )
        instance = api.execute_command(
            session,
            api.InitializeProcessInstanceFromDefinitionCommand(
                tenant_id, definition.id, user.id
            ),
        )
        instance_id, user_id = instance.id, user.id
        session.commit()

    journal = api.GetProcessInstanceCompensationsQuery(tenant_id, instance_id)
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        _complete_pending(connection, tenant_id, user_id, "undo_shipment")
        assert api.execute_query(connection, journal)[-1].status == "completed"
        transaction.rollback()

    with postgres_engine.begin() as connection:
        before = api.execute_query(connection, journal)
        assert [record.status for record in before] == ["queued", "queued", "running"]
        shipment_guid = before[-1].handler_task_guid
        _complete_pending(connection, tenant_id, user_id, "undo_shipment")

    restarted_engine = create_engine(postgres_engine.url)
    try:
        with restarted_engine.begin() as connection:
            recovered = api.execute_query(connection, journal)
            assert [record.status for record in recovered] == [
                "queued",
                "running",
                "completed",
            ]
            assert recovered[-1].handler_task_guid == shipment_guid
            for name in ("undo_payment", "undo_inventory"):
                _complete_pending(connection, tenant_id, user_id, name)

        with Session(restarted_engine) as session:
            records = api.execute_query(session, journal)
            assert [record.status for record in records] == ["completed"] * 3
            assert [record.attempts for record in records] == [1] * 3
            assert all(record.created_at.tzinfo is not None for record in records)
            events = api.execute_query(
                session, api.GetProcessInstanceEventsQuery(tenant_id, instance_id)
            )
            assert sum(
                event.event_type == "compensation_completed" for event in events
            ) == 3
            instance = api.execute_query(
                session, api.GetProcessInstanceQuery(tenant_id, instance_id)
            )
            assert instance.status == "complete"
    finally:
        restarted_engine.dispose()
