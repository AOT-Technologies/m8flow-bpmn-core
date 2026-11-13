"""Deploy two BPMN pools and deliver modeled messages with the scheduler."""

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from m8flow_core import api
from m8flow_core.db import build_engine, create_schema
from m8flow_core.models.process_instance import ProcessInstanceModel
from m8flow_core.models.tenant import M8flowTenantModel
from m8flow_core.models.user import UserModel
from m8flow_core.services.authorization import ROLE_ADMIN, ensure_v1_role

TENANT = "order-demo"
FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "order-fulfilment-collaboration.bpmn"
)


def main() -> None:
    engine = build_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)

    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            user = UserModel(
                username="operator",
                email="operator@example.test",
                service=f"https://example.test/realms/{TENANT}",
                service_id="operator",
                realm_identifier=TENANT,
            )
            session.add_all(
                [M8flowTenantModel(id=TENANT, name="Orders", slug=TENANT), user]
            )
            session.flush()
            ensure_v1_role(
                session, tenant_id=TENANT, role_name=ROLE_ADMIN, user_ids=[user.id]
            )
            deployment = api.execute_command(
                session,
                api.ImportBpmnProcessDefinitionCommand(
                    tenant_id=TENANT,
                    bpmn_identifier="order-fulfilment",
                    user_id=user.id,
                    source_bpmn_xml=FIXTURE.read_bytes(),
                ),
            )

        def start(
            participant: str, key: str, *, starts_fulfilment: bool = False
        ) -> ProcessInstanceModel:
            return api.execute_command(
                session,
                api.InitializeProcessInstanceFromDefinitionCommand(
                    tenant_id=TENANT,
                    bpmn_process_definition_id=deployment.id,
                    process_initiator_id=user.id,
                    bpmn_process_id=participant,
                    collaboration_correlation_key=key,
                    submission_metadata={"start_fulfilment": str(starts_fulfilment)},
                ),
            )

        def complete(instance: ProcessInstanceModel) -> None:
            item = next(
                item
                for item in api.get_pending_tasks(
                    session, tenant_id=TENANT, user_id=user.id
                )
                if item.process_instance_id == instance.id
            )
            api.execute_command(session, api.ClaimTaskCommand(TENANT, item.id, user.id))
            api.execute_command(
                session, api.CompleteTaskCommand(TENANT, item.id, user.id)
            )

        with session.begin():
            complete(start("Order", "order-100", starts_fulfilment=True))
        with session.begin():
            assert api.run_due_scheduler_jobs(session, tenant_id=TENANT) == 1
            instances = api.execute_query(
                session, api.ListProcessInstancesQuery(TENANT)
            )
            assert len(instances) == 2 and all(
                item.status == "complete" for item in instances
            )
            print(
                "Order 100 started Fulfilment through a Message Start Event."
            )

        with session.begin():
            fulfilment = start("Fulfilment", "order-101")
            complete(fulfilment)
            complete(start("Order", "order-101"))
            assert fulfilment.status == "waiting"
        with session.begin():
            assert (
                api.run_due_scheduler_jobs(
                    session, tenant_id=TENANT, now=datetime.now(UTC)
                )
                == 1
            )
            assert fulfilment.status == "complete"
            print(
                "Order 101 resumed Fulfilment through a Message Catch Event."
            )

    engine.dispose()


if __name__ == "__main__":
    main()
