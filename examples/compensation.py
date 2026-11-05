"""Inventory/payment/shipment compensation, with a durable restart.

Run with ``uv run python examples/compensation.py``. No external services needed.
"""

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy.orm import Session

from m8flow_core import api
from m8flow_core.db import build_engine, create_schema
from m8flow_core.models import M8flowTenantModel, UserModel
from m8flow_core.services.authorization import ROLE_ADMIN, ensure_v1_role

FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/compensation_saga.bpmn"
TENANT = "compensation-demo"


def finish(session: Session, user_id: int, expected: str) -> None:
    items = api.execute_query(session, api.GetPendingTasksQuery(TENANT, user_id))
    assert [item.task_name for item in items] == [expected]
    item = items[0]
    api.execute_command(session, api.ClaimTaskCommand(TENANT, item.id, user_id))
    api.execute_command(session, api.CompleteTaskCommand(TENANT, item.id, user_id))
    print(f"Completed {expected}")


def main() -> None:
    with TemporaryDirectory(prefix="m8flow-compensation-") as directory:
        url = f"sqlite+pysqlite:///{Path(directory) / 'saga.db'}"
        engine = build_engine(url)
        create_schema(engine)
        with Session(engine) as session:
            now = datetime.now(UTC)
            session.add(M8flowTenantModel(id=TENANT, name="Saga demo", slug=TENANT))
            user = UserModel(
                username="operator",
                service=TENANT,
                service_id="operator",
                created_at=now,
                updated_at=now,
            )
            session.add(user)
            session.flush()
            ensure_v1_role(
                session, tenant_id=TENANT, role_name=ROLE_ADMIN, user_ids=[user.id]
            )
            definition = api.execute_command(
                session,
                api.ImportBpmnProcessDefinitionCommand(
                    TENANT,
                    "compensation-demo",
                    user.id,
                    FIXTURE.read_text(encoding="utf-8"),
                ),
            )
            instance = api.execute_command(
                session,
                api.InitializeProcessInstanceFromDefinitionCommand(
                    TENANT, definition.id, user.id
                ),
            )
            instance_id, user_id = instance.id, user.id
            print("Reserved inventory, charged payment, arranged shipment.")
            finish(session, user_id, "undo_shipment")
            session.commit()
        engine.dispose()

        print("Restarting the engine after committing shipment undo...")
        engine = build_engine(url)
        try:
            with Session(engine) as session:
                finish(session, user_id, "undo_payment")
                session.commit()
                finish(session, user_id, "undo_inventory")
                session.commit()
                records = api.execute_query(
                    session,
                    api.GetProcessInstanceCompensationsQuery(TENANT, instance_id),
                )
                assert all(
                    record.status == "completed" and record.attempts == 1
                    for record in records
                )
                assert (
                    api.execute_query(
                        session, api.GetProcessInstanceQuery(TENANT, instance_id)
                    ).status
                    == "complete"
                )
                print(
                    "All undo handlers completed once; "
                    "committed shipment undo was preserved."
                )
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
