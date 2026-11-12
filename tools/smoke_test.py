#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

"""Release smoke test: import the package and run a BPMN workflow end to end.

Run it against an installed wheel, from outside the source tree, so the import
resolves to the installed distribution rather than ``src/``:

    python -I tools/smoke_test.py tests/fixtures/parallel_review_poc.bpmn \
        --expect-installed

The script uses an in-memory SQLite database. It seeds a tenant and users,
imports the BPMN definition, starts a process instance, claims and completes
every human task, and checks that the instance reaches ``complete`` with an
event history. It exits non-zero on any failure.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

PROCESS_ID = "Process_parallel_review_poc"
REALM_BASE_URL = "http://localhost:7002/realms"
LANE_OWNERS = {
    "Finance": ["finance_user"],
    "Compliance": ["compliance_user"],
}


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    process_instance_id: int
    status: str
    completed_tasks: list[str]
    event_count: int


def seed(session, tenant_id: str) -> dict:
    """Create a tenant and the four users the parallel-review workflow needs.

    Tenant membership is derived from the realm in each user's service URL.
    """
    from m8flow_core.models.tenant import M8flowTenantModel
    from m8flow_core.models.user import UserModel
    from m8flow_core.services.authorization import (
        ROLE_ADMIN,
        ROLE_MANAGER,
        ROLE_USER,
        ensure_v1_role,
    )

    created = datetime.fromtimestamp(1, UTC)
    session.add(M8flowTenantModel(id=tenant_id, name=tenant_id, slug=tenant_id))
    users = {
        name: UserModel(
            username=name,
            email=f"{name}@example.com",
            service=f"{REALM_BASE_URL}/{tenant_id}",
            service_id=f"{name}-id",
            display_name=name,
            created_at=created,
            updated_at=created,
        )
        for name in ("admin", "requester", "finance_user", "compliance_user")
    }
    session.add_all(users.values())
    session.flush()
    ensure_v1_role(
        session, tenant_id=tenant_id, role_name=ROLE_ADMIN, user_ids=[users["admin"].id]
    )
    ensure_v1_role(
        session,
        tenant_id=tenant_id,
        role_name=ROLE_USER,
        user_ids=[users["requester"].id],
    )
    ensure_v1_role(
        session,
        tenant_id=tenant_id,
        role_name=ROLE_MANAGER,
        user_ids=[users["finance_user"].id, users["compliance_user"].id],
    )
    return users


def import_definition(session, tenant_id: str, admin_id: int, bpmn_xml: str):
    from m8flow_core import api

    now = datetime.now(UTC)
    return api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant_id,
            bpmn_identifier="parallel-review-smoke",
            user_id=admin_id,
            bpmn_name="Parallel Review Smoke Test",
            source_bpmn_xml=bpmn_xml,
            properties_json={"lane_owners": LANE_OWNERS},
            created_at=now,
            updated_at=now,
        ),
    )


def run_workflow(session, tenant_id: str, users: dict, definition_id: int):
    """Start an instance and complete every task until none are pending."""
    from m8flow_core import api

    instance = api.execute_command(
        session,
        api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=tenant_id,
            bpmn_process_definition_id=definition_id,
            process_initiator_id=users["requester"].id,
            summary="Release smoke test",
            process_version=1,
            started_at=datetime.now(UTC),
            bpmn_process_id=PROCESS_ID,
        ),
    )
    payloads = {
        "requester": {"order_amount": "1250", "vendor": "ACME", "description": "x"},
        "finance_user": {"finance_decision": "Approved"},
        "compliance_user": {"compliance_decision": "Approved"},
    }
    completed: list[str] = []
    progressed = True
    while progressed:
        progressed = False
        for name, user in users.items():
            tasks = api.execute_query(
                session,
                api.GetPendingTasksQuery(tenant_id=tenant_id, user_id=user.id),
            )
            for task in tasks:
                api.execute_command(
                    session,
                    api.ClaimTaskCommand(
                        tenant_id=tenant_id, work_item_id=task.id, user_id=user.id
                    ),
                )
                api.execute_command(
                    session,
                    api.CompleteTaskCommand(
                        tenant_id=tenant_id,
                        work_item_id=task.id,
                        user_id=user.id,
                        completed_at=datetime.now(UTC),
                        task_payload=payloads.get(name),
                    ),
                )
                completed.append(task.task_name)
                progressed = True

    final = api.execute_query(
        session,
        api.GetProcessInstanceQuery(
            tenant_id=tenant_id, process_instance_id=instance.id
        ),
    )
    events = api.execute_query(
        session,
        api.GetProcessInstanceEventsQuery(
            tenant_id=tenant_id, process_instance_id=instance.id
        ),
    )
    return WorkflowResult(
        process_instance_id=instance.id,
        status=str(final.status),
        completed_tasks=completed,
        event_count=len(events),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bpmn", type=Path, help="Path to parallel_review_poc.bpmn")
    parser.add_argument(
        "--expect-installed",
        action="store_true",
        help="Fail if m8flow_core is imported from a source checkout.",
    )
    args = parser.parse_args(argv)

    from sqlalchemy.orm import Session

    import m8flow_core
    from m8flow_core.db import build_engine, create_schema

    location = Path(m8flow_core.__file__).resolve()
    print(f"m8flow_core {m8flow_core.__version__} imported from {location.parent}")
    if args.expect_installed and "site-packages" not in location.parts:
        print("FAIL: m8flow_core was not imported from site-packages")
        return 1

    engine = build_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    tenant_id = "smoke"
    with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
        users = seed(session, tenant_id)
        definition = import_definition(
            session, tenant_id, users["admin"].id, args.bpmn.read_text("utf-8")
        )
        result = run_workflow(session, tenant_id, users, definition.id)
        session.commit()

    print(f"completed tasks: {result.completed_tasks}")
    print(f"final status: {result.status}, events recorded: {result.event_count}")
    if result.status != "complete" or not result.event_count:
        print("FAIL: workflow did not complete with an event history")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
