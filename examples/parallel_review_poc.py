"""Parallel-review purchase order workflow walkthrough.

Run with:

    uv run python examples/parallel_review_poc.py

This is a non-interactive companion to ``examples/conditional_approval_poc.py``
that exercises BPMN shapes the conditional-approval POC does not cover:

* a parallel gateway (AND-split followed by AND-join),
* two script tasks — one before the split, one after the join,
* an exclusive gateway driven by a value computed by a script.

The walkthrough uses an in-memory SQLite database so it has no external
dependencies (no Postgres, no Docker). It seeds a tenant, a requester,
and two reviewers, then drives the public API end to end and prints what
happens at each step.

Set ``SCENARIO`` near the top of the file to try other outcomes.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from m8flow_bpmn_core import api  # noqa: E402
from m8flow_bpmn_core.db import build_engine, create_schema  # noqa: E402
from m8flow_bpmn_core.models.tenant import M8flowTenantModel  # noqa: E402
from m8flow_bpmn_core.models.user import UserModel  # noqa: E402
from m8flow_bpmn_core.services.authorization import (  # noqa: E402
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_USER,
    ensure_v1_role,
)

BPMN_PATH = REPO_ROOT / "tests" / "fixtures" / "parallel_review_poc.bpmn"
PROCESS_ID = "Process_parallel_review_poc"
TENANT_ID = "tenant-parallel-review"
TENANT_SLUG = "parallel-review"
SERVICE_URL = f"http://localhost:7002/realms/{TENANT_SLUG}"

LANE_OWNERS = {
    "Finance": ["finance_user"],
    "Compliance": ["compliance_user"],
}

ORDER_AMOUNT = "1250"
ORDER_VENDOR = "ACME Corp"
ORDER_DESCRIPTION = "Office supplies for Q3"


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    finance_decision: str
    compliance_decision: str


# Change this to "compliance_rejects" or "finance_rejects" to follow the
# rejection branches instead.
SCENARIO = Scenario(
    name="both_approved",
    finance_decision="Approved",
    compliance_decision="Approved",
)


def main() -> None:
    print("m8flow-bpmn-core parallel-review purchase-order POC")
    print()
    print(f"Scenario: {SCENARIO.name}")
    print(f"  finance_decision    = {SCENARIO.finance_decision!r}")
    print(f"  compliance_decision = {SCENARIO.compliance_decision!r}")
    print()

    engine = build_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)

    with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
        users = _seed(session)
        session.commit()

        _step(
            1,
            "Import the BPMN definition and start a process instance",
            "The script task before the first user task seeds lane_owners "
            "from the BPMN, then control hands off to the requester.",
        )
        definition = api.execute_command(
            session,
            api.ImportBpmnProcessDefinitionCommand(
                tenant_id=TENANT_ID,
                bpmn_identifier="parallel-review-poc",
                user_id=users["admin"].id,
                bpmn_name="Parallel Review POC",
                source_bpmn_xml=BPMN_PATH.read_text(encoding="utf-8"),
                properties_json={
                    "flow": "parallel_review",
                    "lane_owners": LANE_OWNERS,
                },
                created_at_in_seconds=90,
                updated_at_in_seconds=90,
            ),
        )
        process_instance = api.execute_command(
            session,
            api.InitializeProcessInstanceFromDefinitionCommand(
                tenant_id=TENANT_ID,
                bpmn_process_definition_id=definition.id,
                process_initiator_id=users["requester"].id,
                summary="Purchase order — parallel review walkthrough",
                process_version=1,
                started_at_in_seconds=100,
                bpmn_process_id=PROCESS_ID,
            ),
        )
        _show_instance("After init", session, process_instance.id)

        _step(
            2,
            "Requester submits the purchase order",
            "The submit task carries the order amount, vendor, and a note. "
            "After completion the next script task (Compute Total) runs and "
            "the parallel gateway fans out two review tasks.",
        )
        submit_task = _pending_task_for(session, users["requester"].id)
        api.execute_command(
            session,
            api.ClaimTaskCommand(
                tenant_id=TENANT_ID,
                human_task_id=submit_task.id,
                user_id=users["requester"].id,
            ),
        )
        api.execute_command(
            session,
            api.CompleteTaskCommand(
                tenant_id=TENANT_ID,
                human_task_id=submit_task.id,
                user_id=users["requester"].id,
                completed_at_in_seconds=110,
                task_payload={
                    "order_amount": ORDER_AMOUNT,
                    "vendor": ORDER_VENDOR,
                    "description": ORDER_DESCRIPTION,
                },
            ),
        )
        _show_instance("After submit", session, process_instance.id)
        _show_pending("After submit", session, users)

        _step(
            3,
            "Finance and Compliance review in parallel",
            "Both branches of the parallel gateway are live at the same "
            "time. We complete Finance first and confirm the workflow "
            "still waits at the join for Compliance.",
        )
        finance_task = _pending_task_for(session, users["finance_user"].id)
        api.execute_command(
            session,
            api.ClaimTaskCommand(
                tenant_id=TENANT_ID,
                human_task_id=finance_task.id,
                user_id=users["finance_user"].id,
            ),
        )
        api.execute_command(
            session,
            api.CompleteTaskCommand(
                tenant_id=TENANT_ID,
                human_task_id=finance_task.id,
                user_id=users["finance_user"].id,
                completed_at_in_seconds=120,
                task_payload={"finance_decision": SCENARIO.finance_decision},
            ),
        )
        _show_instance("After finance completes", session, process_instance.id)
        _show_pending("After finance completes", session, users)

        _step(
            4,
            "Compliance review completes",
            "This releases the join. The Determine Outcome script combines "
            "both decisions; the exclusive gateway routes to the matching "
            "notify task in the requester lane.",
        )
        compliance_task = _pending_task_for(session, users["compliance_user"].id)
        api.execute_command(
            session,
            api.ClaimTaskCommand(
                tenant_id=TENANT_ID,
                human_task_id=compliance_task.id,
                user_id=users["compliance_user"].id,
            ),
        )
        api.execute_command(
            session,
            api.CompleteTaskCommand(
                tenant_id=TENANT_ID,
                human_task_id=compliance_task.id,
                user_id=users["compliance_user"].id,
                completed_at_in_seconds=130,
                task_payload={
                    "compliance_decision": SCENARIO.compliance_decision,
                },
            ),
        )
        _show_instance("After compliance completes", session, process_instance.id)
        _show_pending("After compliance completes", session, users)

        _step(
            5,
            "Requester acknowledges the outcome",
            "Completing the notify task moves the process instance to its "
            "terminal state. The metadata trail and event log are intact.",
        )
        notify_task = _pending_task_for(session, users["requester"].id)
        api.execute_command(
            session,
            api.ClaimTaskCommand(
                tenant_id=TENANT_ID,
                human_task_id=notify_task.id,
                user_id=users["requester"].id,
            ),
        )
        api.execute_command(
            session,
            api.CompleteTaskCommand(
                tenant_id=TENANT_ID,
                human_task_id=notify_task.id,
                user_id=users["requester"].id,
                completed_at_in_seconds=140,
            ),
        )
        _show_instance("Final", session, process_instance.id)

        _print_metadata_summary(session, process_instance.id)
        _print_event_summary(session, process_instance.id)

    print()
    print("Walkthrough complete.")


def _step(number: int, title: str, description: str) -> None:
    print("-" * 80)
    print(f"Step {number}: {title}")
    print(description)


def _show_instance(label: str, session: Session, process_instance_id: int) -> None:
    instance = api.execute_query(
        session,
        api.GetProcessInstanceQuery(
            tenant_id=TENANT_ID,
            process_instance_id=process_instance_id,
        ),
    )
    print(f"  [{label}] status = {instance.status}")


def _show_pending(label: str, session: Session, users: dict[str, UserModel]) -> None:
    for who, user in users.items():
        tasks = api.execute_query(
            session,
            api.GetPendingTasksQuery(tenant_id=TENANT_ID, user_id=user.id),
        )
        names = [task.task_name for task in tasks]
        print(f"  [{label}] {who:>15} pending = {names}")


def _pending_task_for(session: Session, user_id: int):
    tasks = api.execute_query(
        session,
        api.GetPendingTasksQuery(tenant_id=TENANT_ID, user_id=user_id),
    )
    if not tasks:
        raise RuntimeError(f"Expected at least one pending task for user_id={user_id}")
    return tasks[0]


def _print_metadata_summary(session: Session, process_instance_id: int) -> None:
    rows = api.execute_query(
        session,
        api.GetProcessInstanceMetadataQuery(
            tenant_id=TENANT_ID,
            process_instance_id=process_instance_id,
        ),
    )
    print()
    print("Process metadata at end:")
    for row in rows:
        print(f"  {row.key:>22} = {row.value}")


def _print_event_summary(session: Session, process_instance_id: int) -> None:
    events = api.execute_query(
        session,
        api.GetProcessInstanceEventsQuery(
            tenant_id=TENANT_ID,
            process_instance_id=process_instance_id,
        ),
    )
    print()
    print("Process event log:")
    for event in events:
        print(f"  {event.event_type}")


def _seed(session: Session) -> dict[str, UserModel]:
    tenant = M8flowTenantModel(id=TENANT_ID, name="Parallel Review", slug=TENANT_SLUG)
    users = {
        "requester": UserModel(
            username="requester",
            email="requester@example.com",
            service=SERVICE_URL,
            service_id="requester-keycloak",
            display_name="Requester",
            created_at_in_seconds=1,
            updated_at_in_seconds=1,
        ),
        "finance_user": UserModel(
            username="finance_user",
            email="finance@example.com",
            service=SERVICE_URL,
            service_id="finance-keycloak",
            display_name="Finance User",
            created_at_in_seconds=1,
            updated_at_in_seconds=1,
        ),
        "admin": UserModel(
            username="admin",
            email="admin@example.com",
            service=SERVICE_URL,
            service_id="admin-keycloak",
            display_name="Admin",
            created_at_in_seconds=1,
            updated_at_in_seconds=1,
        ),
        "compliance_user": UserModel(
            username="compliance_user",
            email="compliance@example.com",
            service=SERVICE_URL,
            service_id="compliance-keycloak",
            display_name="Compliance User",
            created_at_in_seconds=1,
            updated_at_in_seconds=1,
        ),
    }
    session.add(tenant)
    session.add_all(users.values())
    session.flush()
    ensure_v1_role(
        session,
        tenant_id=TENANT_ID,
        role_name=ROLE_USER,
        user_ids=[users["requester"].id],
    )
    ensure_v1_role(
        session,
        tenant_id=TENANT_ID,
        role_name=ROLE_ADMIN,
        user_ids=[users["admin"].id],
    )
    ensure_v1_role(
        session,
        tenant_id=TENANT_ID,
        role_name=ROLE_MANAGER,
        user_ids=[
            users["finance_user"].id,
            users["compliance_user"].id,
        ],
    )
    return users


if __name__ == "__main__":
    main()
