from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from m8flow_core import api
from m8flow_core.models import M8flowTenantModel, UserModel
from m8flow_core.services.authorization import ROLE_ADMIN, ensure_v1_role


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
        [item] = api.execute_query(session, api.GetPendingTasksQuery(tenant_id))
        assert item.task_name == "undo_shipment"
        api.execute_command(session, api.ClaimTaskCommand(tenant_id, item.id, user_id))
        api.execute_command(
            session, api.CompleteTaskCommand(tenant_id, item.id, user_id)
        )
        session.commit()
    with postgres_engine.begin() as connection:
        for name in ("undo_payment", "undo_inventory"):
            [item] = api.execute_query(connection, api.GetPendingTasksQuery(tenant_id))
            with Session(bind=connection) as session:
                assert session.merge(item).task_name == name
            api.execute_command(
                connection, api.ClaimTaskCommand(tenant_id, item.id, user_id)
            )
            api.execute_command(
                connection, api.CompleteTaskCommand(tenant_id, item.id, user_id)
            )
        records = api.execute_query(
            connection, api.GetProcessInstanceCompensationsQuery(tenant_id, instance_id)
        )
        assert [record.status for record in records] == ["completed"] * 3
        assert [record.attempts for record in records] == [1] * 3
        assert all(record.created_at.tzinfo is not None for record in records)
