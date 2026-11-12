#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

"""Broadcast one BPMN signal to three waiting instances and a Signal Start.

Run: uv run python examples/signal_broadcast.py
Uses an in-memory SQLite database, with no external services.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_core import api
from m8flow_core.db import build_engine, create_schema
from m8flow_core.models import M8flowTenantModel, SignalPublicationModel, UserModel
from m8flow_core.services.authorization import ensure_v1_role

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def main(*, interactive: bool = True) -> None:
    engine = build_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    tenant_id = "signal-example"
    timestamp = datetime.now(UTC)
    with Session(engine) as session:
        user = UserModel(
            username="signal-admin",
            service=f"app/realms/{tenant_id}",
            service_id="signal-admin",
            created_at=timestamp,
            updated_at=timestamp,
        )
        session.add_all(
            [
                M8flowTenantModel(id=tenant_id, name="Signal example", slug=tenant_id),
                user,
            ]
        )
        session.flush()
        ensure_v1_role(
            session, tenant_id=tenant_id, role_name="admin", user_ids=[user.id]
        )
        definitions = {}
        for name in ("waiting", "start", "throw"):
            definitions[name] = api.execute_command(
                session,
                api.ImportBpmnProcessDefinitionCommand(
                    tenant_id=tenant_id,
                    bpmn_identifier=f"signal-{name}",
                    user_id=user.id,
                    source_bpmn_xml=(FIXTURES / f"signal_{name}.bpmn").read_text(),
                ),
            )
        if interactive:
            input("Press Enter to start three listeners. ")
        for _ in range(3):
            instance = api.execute_command(
                session,
                api.InitializeProcessInstanceFromDefinitionCommand(
                    tenant_id,
                    definitions["waiting"].id,
                    user.id,
                ),
            )
            assert instance.status == "waiting"
            print(f"Waiting instance: {instance.id}")
        session.commit()
        print(f"Signal Start definition: {definitions['start'].id}")

        if interactive:
            input("Press Enter to run the throwing process and publish the signal. ")
        emitter = api.execute_command(
            session,
            api.InitializeProcessInstanceFromDefinitionCommand(
                tenant_id,
                definitions["throw"].id,
                user.id,
            ),
        )
        assert emitter.status == "complete"
        assert json.loads(emitter.workflow_state_json)["data"]["after_throw"] is True
        print(f"Throwing instance {emitter.id} completed after publishing.")
        publication = session.scalars(
            select(SignalPublicationModel).where(
                SignalPublicationModel.m8f_tenant_id == tenant_id,
                SignalPublicationModel.source_process_instance_id == emitter.id,
            )
        ).one()
        queued = api.list_signal_deliveries(
            session, tenant_id=tenant_id, occurrence_id=publication.occurrence_id
        )
        assert len(queued) == 4 and all(row.status == "pending" for row in queued)
        for row in queued:
            target = (
                f"instance {row.process_instance_id}"
                if row.process_instance_id is not None
                else "Signal Start"
            )
            print(f"Pending receipt {row.id} for {target}")
        session.commit()

    if interactive:
        input("Press Enter to run the delivery loop. ")
    # The host owns the runner and its transaction boundary.
    with Session(engine) as session:
        receipts = api.execute_command(
            session, api.RunSignalDeliveriesCommand(tenant_id)
        )
        assert len(receipts) == 4
        assert all(receipt.status == "delivered" for receipt in receipts)
        session.commit()
        instances = api.execute_query(session, api.ListProcessInstancesQuery(tenant_id))
        assert len(instances) == 5
        assert all(instance.status == "complete" for instance in instances)
        assert api.run_signal_deliveries(session, tenant_id=tenant_id) == []
        print("Delivered once to three waiting instances and one Signal Start.")
        print(
            "All five instances are complete; repeating the runner does nothing."
        )
    engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--non-interactive", action="store_true", help="run all steps without prompts"
    )
    main(interactive=not parser.parse_args().non_interactive)
