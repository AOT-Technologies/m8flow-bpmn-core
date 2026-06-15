"""End-to-end test for the parallel-review POC.

Exercises BPMN shapes that the conditional-approval POC does not cover:

* a parallel gateway (AND-split followed by AND-join),
* two script tasks (one before the split, one after the join),
* an exclusive gateway driven by a value computed by a script.

The flow is a purchase-order approval with three lanes:

    Start
      -> ScriptTask: set lane owners
      -> UserTask: Submit Purchase Order (Requester)
      -> ScriptTask: Compute Total With Tax
      -> ParallelGateway (split)
          -> UserTask: Finance Review (Finance)
          -> UserTask: Compliance Review (Compliance)
      -> ParallelGateway (join)
      -> ScriptTask: Determine Outcome
      -> ExclusiveGateway (final decision)
          -> UserTask: Notify Approved -> End
          -> UserTask: Notify Rejected -> End
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel

EXAMPLE_BPMN_PATH = (
    Path(__file__).with_name("fixtures") / "parallel_review_poc.bpmn"
)
PROCESS_ID = "Process_parallel_review_poc"
LANE_OWNERS = {
    "Finance": ["finance_user"],
    "Compliance": ["compliance_user"],
}


@dataclass(frozen=True, slots=True)
class ParallelReviewScenario:
    name: str
    finance_decision: str
    compliance_decision: str
    expected_final_decision: str
    expected_notify_task: str


SCENARIOS = [
    ParallelReviewScenario(
        name="both_approved",
        finance_decision="Approved",
        compliance_decision="Approved",
        expected_final_decision="Approved",
        expected_notify_task="Activity_notify_approved",
    ),
    ParallelReviewScenario(
        name="compliance_rejects",
        finance_decision="Approved",
        compliance_decision="Rejected",
        expected_final_decision="Rejected",
        expected_notify_task="Activity_notify_rejected",
    ),
    ParallelReviewScenario(
        name="finance_rejects",
        finance_decision="Rejected",
        compliance_decision="Approved",
        expected_final_decision="Rejected",
        expected_notify_task="Activity_notify_rejected",
    ),
]


@dataclass(frozen=True, slots=True)
class ParallelReviewContext:
    tenant: M8flowTenantModel
    users: dict[str, UserModel]
    process_instance_id: int


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_parallel_review_workflow_completes_through_both_reviewers(
    session: Session,
    scenario: ParallelReviewScenario,
) -> None:
    context = _seed_parallel_review_workflow(session, scenario)

    # Step 1: requester sees the submit task as pending.
    submit_tasks = api.execute_query(
        session,
        api.GetPendingTasksQuery(
            tenant_id=context.tenant.id,
            user_id=context.users["requester"].id,
        ),
    )
    assert [t.task_name for t in submit_tasks] == ["Activity_submit_order"]
    submit_task = submit_tasks[0]

    # Step 2: requester claims and completes the submit task with order_amount.
    api.execute_command(
        session,
        api.ClaimTaskCommand(
            tenant_id=context.tenant.id,
            human_task_id=submit_task.id,
            user_id=context.users["requester"].id,
        ),
    )
    api.execute_command(
        session,
        api.CompleteTaskCommand(
            tenant_id=context.tenant.id,
            human_task_id=submit_task.id,
            user_id=context.users["requester"].id,
            completed_at_in_seconds=110,
            task_payload={
                "order_amount": "1000",
                "vendor": "ACME Corp",
                "description": "Office supplies",
            },
        ),
    )

    # Step 3: the parallel split fans out — both reviewers see their task.
    finance_tasks = api.execute_query(
        session,
        api.GetPendingTasksQuery(
            tenant_id=context.tenant.id,
            user_id=context.users["finance_user"].id,
        ),
    )
    compliance_tasks = api.execute_query(
        session,
        api.GetPendingTasksQuery(
            tenant_id=context.tenant.id,
            user_id=context.users["compliance_user"].id,
        ),
    )
    assert [t.task_name for t in finance_tasks] == ["Activity_finance_review"]
    assert [t.task_name for t in compliance_tasks] == ["Activity_compliance_review"]
    finance_task = finance_tasks[0]
    compliance_task = compliance_tasks[0]

    # Step 4: finance reviewer completes their review independently.
    api.execute_command(
        session,
        api.ClaimTaskCommand(
            tenant_id=context.tenant.id,
            human_task_id=finance_task.id,
            user_id=context.users["finance_user"].id,
        ),
    )
    api.execute_command(
        session,
        api.CompleteTaskCommand(
            tenant_id=context.tenant.id,
            human_task_id=finance_task.id,
            user_id=context.users["finance_user"].id,
            completed_at_in_seconds=120,
            task_payload={"finance_decision": scenario.finance_decision},
        ),
    )

    # After finance completes, the workflow must wait — compliance is still pending.
    instance_mid = api.execute_query(
        session,
        api.GetProcessInstanceQuery(
            tenant_id=context.tenant.id,
            process_instance_id=context.process_instance_id,
        ),
    )
    assert instance_mid.status == api.ProcessInstanceStatus.user_input_required
    assert instance_mid.end_in_seconds is None

    # Step 5: compliance reviewer completes their review — this releases the join.
    api.execute_command(
        session,
        api.ClaimTaskCommand(
            tenant_id=context.tenant.id,
            human_task_id=compliance_task.id,
            user_id=context.users["compliance_user"].id,
        ),
    )
    api.execute_command(
        session,
        api.CompleteTaskCommand(
            tenant_id=context.tenant.id,
            human_task_id=compliance_task.id,
            user_id=context.users["compliance_user"].id,
            completed_at_in_seconds=130,
            task_payload={"compliance_decision": scenario.compliance_decision},
        ),
    )

    # Step 6: after the join, the determine_outcome script ran and the exclusive
    # gateway routed to the matching notify task in the requester lane.
    notify_tasks = api.execute_query(
        session,
        api.GetPendingTasksQuery(
            tenant_id=context.tenant.id,
            user_id=context.users["requester"].id,
        ),
    )
    assert [t.task_name for t in notify_tasks] == [scenario.expected_notify_task]
    notify_task = notify_tasks[0]

    # Step 7: complete the notify task to terminate the workflow.
    api.execute_command(
        session,
        api.ClaimTaskCommand(
            tenant_id=context.tenant.id,
            human_task_id=notify_task.id,
            user_id=context.users["requester"].id,
        ),
    )
    api.execute_command(
        session,
        api.CompleteTaskCommand(
            tenant_id=context.tenant.id,
            human_task_id=notify_task.id,
            user_id=context.users["requester"].id,
            completed_at_in_seconds=140,
        ),
    )

    final_instance = api.execute_query(
        session,
        api.GetProcessInstanceQuery(
            tenant_id=context.tenant.id,
            process_instance_id=context.process_instance_id,
        ),
    )
    assert final_instance.status == api.ProcessInstanceStatus.complete
    assert final_instance.end_in_seconds == 140

    # Metadata captures every payload + every value the script tasks computed.
    metadata = api.execute_query(
        session,
        api.GetProcessInstanceMetadataQuery(
            tenant_id=context.tenant.id,
            process_instance_id=context.process_instance_id,
        ),
    )
    metadata_map = {row.key: row.value for row in metadata}
    assert metadata_map["order_amount"] == "1000"
    assert metadata_map["finance_decision"] == scenario.finance_decision
    assert metadata_map["compliance_decision"] == scenario.compliance_decision


def test_parallel_review_join_does_not_advance_until_both_branches_complete(
    session: Session,
) -> None:
    """Completing only one branch leaves the workflow waiting at the join."""
    scenario = SCENARIOS[0]
    context = _seed_parallel_review_workflow(session, scenario)

    submit_task = api.execute_query(
        session,
        api.GetPendingTasksQuery(
            tenant_id=context.tenant.id,
            user_id=context.users["requester"].id,
        ),
    )[0]
    api.execute_command(
        session,
        api.ClaimTaskCommand(
            tenant_id=context.tenant.id,
            human_task_id=submit_task.id,
            user_id=context.users["requester"].id,
        ),
    )
    api.execute_command(
        session,
        api.CompleteTaskCommand(
            tenant_id=context.tenant.id,
            human_task_id=submit_task.id,
            user_id=context.users["requester"].id,
            completed_at_in_seconds=110,
            task_payload={"order_amount": "1000"},
        ),
    )

    # Complete only the finance branch.
    finance_task = api.execute_query(
        session,
        api.GetPendingTasksQuery(
            tenant_id=context.tenant.id,
            user_id=context.users["finance_user"].id,
        ),
    )[0]
    api.execute_command(
        session,
        api.ClaimTaskCommand(
            tenant_id=context.tenant.id,
            human_task_id=finance_task.id,
            user_id=context.users["finance_user"].id,
        ),
    )
    api.execute_command(
        session,
        api.CompleteTaskCommand(
            tenant_id=context.tenant.id,
            human_task_id=finance_task.id,
            user_id=context.users["finance_user"].id,
            completed_at_in_seconds=120,
            task_payload={"finance_decision": "Approved"},
        ),
    )

    # Compliance task is still pending; requester sees no notification yet.
    requester_tasks = api.execute_query(
        session,
        api.GetPendingTasksQuery(
            tenant_id=context.tenant.id,
            user_id=context.users["requester"].id,
        ),
    )
    assert requester_tasks == []

    compliance_pending = api.execute_query(
        session,
        api.GetPendingTasksQuery(
            tenant_id=context.tenant.id,
            user_id=context.users["compliance_user"].id,
        ),
    )
    assert [t.task_name for t in compliance_pending] == ["Activity_compliance_review"]


def _seed_parallel_review_workflow(
    session: Session,
    scenario: ParallelReviewScenario,
) -> ParallelReviewContext:
    bpmn_xml = EXAMPLE_BPMN_PATH.read_text(encoding="utf-8")
    service_url = "http://localhost:7002/realms/parallel-review"
    tenant = M8flowTenantModel(
        id="tenant-parallel-review",
        name="Parallel Review Tenant",
        slug="parallel-review",
    )
    users = {
        "requester": UserModel(
            username="requester",
            email="requester@example.com",
            service=service_url,
            service_id="requester-keycloak",
            display_name="Requester",
            created_at_in_seconds=1,
            updated_at_in_seconds=1,
        ),
        "finance_user": UserModel(
            username="finance_user",
            email="finance@example.com",
            service=service_url,
            service_id="finance-keycloak",
            display_name="Finance User",
            created_at_in_seconds=1,
            updated_at_in_seconds=1,
        ),
        "compliance_user": UserModel(
            username="compliance_user",
            email="compliance@example.com",
            service=service_url,
            service_id="compliance-keycloak",
            display_name="Compliance User",
            created_at_in_seconds=1,
            updated_at_in_seconds=1,
        ),
    }
    session.add(tenant)
    session.add_all(users.values())
    session.flush()

    definition = api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_identifier="parallel-review-poc",
            bpmn_name="Parallel Review POC",
            source_bpmn_xml=bpmn_xml,
            properties_json={
                "flow": "parallel_review",
                "scenario_name": scenario.name,
                "lane_owners": LANE_OWNERS,
            },
            created_at_in_seconds=90,
            updated_at_in_seconds=90,
        ),
    )

    process_instance = api.execute_command(
        session,
        api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_process_definition_id=definition.id,
            process_initiator_id=users["requester"].id,
            summary=f"Parallel review — {scenario.name}",
            process_version=1,
            started_at_in_seconds=100,
            bpmn_process_id=PROCESS_ID,
        ),
    )
    assert process_instance.status == api.ProcessInstanceStatus.user_input_required
    assert process_instance.workflow_state_json is not None

    return ParallelReviewContext(
        tenant=tenant,
        users=users,
        process_instance_id=process_instance.id,
    )
