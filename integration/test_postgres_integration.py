from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.application import (
    ClaimTaskCommand,
    CompleteTaskCommand,
    GetPendingTasksCommand,
    GetProcessInstanceCommand,
    execute_command,
)
from m8flow_bpmn_core.models import (
    BpmnProcessDefinitionModel,
    BpmnProcessModel,
    FutureTaskModel,
    HumanTaskModel,
    HumanTaskUserModel,
    M8flowTenantModel,
    ProcessInstanceModel,
    TaskDefinitionModel,
    TaskModel,
    UserModel,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
CONDITIONAL_APPROVAL_BPMN_PATH = FIXTURE_DIR / "conditional-approval.bpmn"
CONDITIONAL_APPROVAL_DMN_PATH = FIXTURE_DIR / "check_eligibility.dmn"
CONDITIONAL_APPROVAL_PROCESS_ID = "Process_conditional_approval_8qpy9gh"
CONDITIONAL_APPROVAL_LANE_OWNERS = {
    "Manager": ["manager@m8flow", "reviewer@m8flow"],
    "Finance": ["james@m8flow"],
}


def test_postgres_supports_transaction_control_and_pending_tasks(
    postgres_engine,
) -> None:
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        session = Session(
            bind=connection,
            autoflush=False,
            expire_on_commit=False,
        )
        try:
            tenant, user, process_instance, human_task = _seed_runtime_rows(
                session
            )

            pending_tasks = execute_command(
                connection,
                GetPendingTasksCommand(
                    tenant_id=tenant.id,
                    user_id=user.id,
                ),
            )
            assert [task.id for task in pending_tasks] == [human_task.id]

            claimed_task = execute_command(
                connection,
                ClaimTaskCommand(
                    tenant_id=tenant.id,
                    human_task_id=human_task.id,
                    user_id=user.id,
                ),
            )
            assert claimed_task.actual_owner_id == user.id
            assert claimed_task.task_status == "CLAIMED"

            completed_task = execute_command(
                connection,
                CompleteTaskCommand(
                    tenant_id=tenant.id,
                    human_task_id=human_task.id,
                    user_id=user.id,
                ),
            )
            assert completed_task.completed is True
            assert completed_task.task_status == "COMPLETED"

            current_process_instance = execute_command(
                connection,
                GetProcessInstanceCommand(
                    tenant_id=tenant.id,
                    process_instance_id=process_instance.id,
                ),
            )
            assert current_process_instance.status == "running"
        finally:
            try:
                transaction.rollback()
            finally:
                session.close()

    with Session(bind=postgres_engine) as verify_session:
        with pytest.raises(LookupError):
            execute_command(
                verify_session,
                GetProcessInstanceCommand(
                    tenant_id=tenant.id,
                    process_instance_id=process_instance.id,
                ),
            )


def test_postgres_runs_conditional_approval_workflow_end_to_end(
    postgres_engine,
) -> None:
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        session = Session(
            bind=connection,
            autoflush=False,
            expire_on_commit=False,
        )
        try:
            tenant, users = _seed_conditional_approval_users(session)
            bpmn_xml = CONDITIONAL_APPROVAL_BPMN_PATH.read_text(encoding="utf-8")
            dmn_xml = CONDITIONAL_APPROVAL_DMN_PATH.read_text(encoding="utf-8")

            definition = api.execute_command(
                connection,
                api.ImportBpmnProcessDefinitionCommand(
                    tenant_id=tenant.id,
                    bpmn_identifier="conditional-approval-poc",
                    bpmn_name="Conditional Approval POC",
                    source_bpmn_xml=bpmn_xml,
                    source_dmn_xml=dmn_xml,
                    properties_json={
                        "version": 1,
                        "flow": "conditional_approval",
                        "source_bpmn_fixture": CONDITIONAL_APPROVAL_BPMN_PATH.name,
                        "lane_owners": CONDITIONAL_APPROVAL_LANE_OWNERS,
                    },
                    bpmn_version_control_type="git",
                    bpmn_version_control_identifier="main",
                    created_at_in_seconds=90,
                    updated_at_in_seconds=90,
                ),
            )
            assert definition.source_bpmn_xml == bpmn_xml
            assert definition.source_dmn_xml == dmn_xml

            process_instance = api.execute_command(
                connection,
                api.InitializeProcessInstanceFromDefinitionCommand(
                    tenant_id=tenant.id,
                    bpmn_process_definition_id=definition.id,
                    process_initiator_id=users["requester"].id,
                    submission_metadata={
                        "expense_date": "2026-04-01",
                        "expense_type": "Travel",
                        "amount": "1500",
                        "description": "Trip to LA",
                    },
                    summary="Postgres conditional approval smoke test",
                    process_version=1,
                    started_at_in_seconds=100,
                    bpmn_process_id=CONDITIONAL_APPROVAL_PROCESS_ID,
                ),
            )
            assert process_instance.status == (
                api.ProcessInstanceStatus.user_input_required
            )
            assert process_instance.workflow_state_json is not None

            submit_tasks = api.execute_command(
                connection,
                api.GetPendingTasksCommand(
                    tenant_id=tenant.id,
                    user_id=users["requester"].id,
                ),
            )
            assert len(submit_tasks) == 1
            submit_task = submit_tasks[0]
            assert submit_task.task_name == "Activity_0qoxmh9"
            assert submit_task.task_title == "Submit Expense Claim"
            assert submit_task.lane_name is None
            assert submit_task.lane_assignment_id is None
            assert submit_task.task_status == "READY"
            assert submit_task.json_metadata is not None
            assert submit_task.json_metadata["lane_owners"] == (
                CONDITIONAL_APPROVAL_LANE_OWNERS
            )

            api.execute_command(
                connection,
                api.ClaimTaskCommand(
                    tenant_id=tenant.id,
                    human_task_id=submit_task.id,
                    user_id=users["requester"].id,
                ),
            )
            api.execute_command(
                connection,
                api.CompleteTaskCommand(
                    tenant_id=tenant.id,
                    human_task_id=submit_task.id,
                    user_id=users["requester"].id,
                    completed_at_in_seconds=110,
                ),
            )
            api.execute_command(
                connection,
                api.UpsertProcessInstanceMetadataCommand(
                    tenant_id=tenant.id,
                    process_instance_id=process_instance.id,
                    key="decision",
                    value="Approved",
                    updated_at_in_seconds=112,
                ),
            )

            process_instance = api.execute_command(
                connection,
                api.GetProcessInstanceCommand(
                    tenant_id=tenant.id,
                    process_instance_id=process_instance.id,
                ),
            )
            assert process_instance.status == (
                api.ProcessInstanceStatus.user_input_required
            )
            assert process_instance.end_in_seconds is None

            manager_tasks = api.execute_command(
                connection,
                api.GetPendingTasksCommand(
                    tenant_id=tenant.id,
                    user_id=users["manager"].id,
                ),
            )
            reviewer_tasks = api.execute_command(
                connection,
                api.GetPendingTasksCommand(
                    tenant_id=tenant.id,
                    user_id=users["reviewer"].id,
                ),
            )
            assert len(manager_tasks) == 1
            assert len(reviewer_tasks) == 1
            assert manager_tasks[0].id == reviewer_tasks[0].id
            manager_task = manager_tasks[0]
            assert manager_task.task_name == "Activity_0b1dd0g"
            assert manager_task.task_title == "Review Expense Claim"
            assert manager_task.lane_name == "Manager"
            assert manager_task.lane_assignment_id == api.resolve_lane_assignment_id(
                "Manager"
            )
            assert manager_task.json_metadata is not None
            assert manager_task.json_metadata["lane_owners"] == (
                CONDITIONAL_APPROVAL_LANE_OWNERS
            )

            api.execute_command(
                connection,
                api.ClaimTaskCommand(
                    tenant_id=tenant.id,
                    human_task_id=manager_task.id,
                    user_id=users["manager"].id,
                ),
            )
            api.execute_command(
                connection,
                api.CompleteTaskCommand(
                    tenant_id=tenant.id,
                    human_task_id=manager_task.id,
                    user_id=users["manager"].id,
                    completed_at_in_seconds=120,
                ),
            )

            process_instance = api.execute_command(
                connection,
                api.GetProcessInstanceCommand(
                    tenant_id=tenant.id,
                    process_instance_id=process_instance.id,
                ),
            )
            assert process_instance.status == (
                api.ProcessInstanceStatus.user_input_required
            )
            assert process_instance.end_in_seconds is None

            finance_tasks = api.execute_command(
                connection,
                api.GetPendingTasksCommand(
                    tenant_id=tenant.id,
                    user_id=users["finance"].id,
                ),
            )
            assert len(finance_tasks) == 1
            finance_task = finance_tasks[0]
            assert finance_task.task_name == "Activity_1uha89x"
            assert finance_task.task_title == "Review Expense Claim (Finance)"
            assert finance_task.lane_name == "Finance"
            assert finance_task.lane_assignment_id == api.resolve_lane_assignment_id(
                "Finance"
            )
            assert finance_task.json_metadata is not None
            assert finance_task.json_metadata["lane_owners"] == (
                CONDITIONAL_APPROVAL_LANE_OWNERS
            )

            api.execute_command(
                connection,
                api.UpsertProcessInstanceMetadataCommand(
                    tenant_id=tenant.id,
                    process_instance_id=process_instance.id,
                    key="finance_decision",
                    value="Approved",
                    updated_at_in_seconds=123,
                ),
            )
            api.execute_command(
                connection,
                api.ClaimTaskCommand(
                    tenant_id=tenant.id,
                    human_task_id=finance_task.id,
                    user_id=users["finance"].id,
                ),
            )
            api.execute_command(
                connection,
                api.CompleteTaskCommand(
                    tenant_id=tenant.id,
                    human_task_id=finance_task.id,
                    user_id=users["finance"].id,
                    completed_at_in_seconds=130,
                ),
            )

            process_instance = api.execute_command(
                connection,
                api.GetProcessInstanceCommand(
                    tenant_id=tenant.id,
                    process_instance_id=process_instance.id,
                ),
            )
            assert process_instance.status == api.ProcessInstanceStatus.complete
            assert process_instance.end_in_seconds == 130

            metadata_rows = api.execute_command(
                connection,
                api.GetProcessInstanceMetadataCommand(
                    tenant_id=tenant.id,
                    process_instance_id=process_instance.id,
                ),
            )
            assert {
                item.key: item.value for item in metadata_rows
            } == {
                "amount": "1500",
                "description": "Trip to LA",
                "decision": "Approved",
                "expense_date": "2026-04-01",
                "expense_type": "Travel",
                "finance_decision": "Approved",
            }

            events = api.execute_command(
                connection,
                api.GetProcessInstanceEventsCommand(
                    tenant_id=tenant.id,
                    process_instance_id=process_instance.id,
                ),
            )
            assert [event.event_type for event in events] == [
                "process_instance_created",
                "task_completed",
                "task_completed",
                "task_completed",
                "process_instance_completed",
            ]
        finally:
            try:
                transaction.rollback()
            finally:
                session.close()

    with Session(bind=postgres_engine) as verify_session:
        with pytest.raises(LookupError):
            api.execute_command(
                verify_session,
                api.GetProcessInstanceCommand(
                    tenant_id=tenant.id,
                    process_instance_id=process_instance.id,
                ),
            )


def _seed_runtime_rows(
    session: Session,
) -> tuple[M8flowTenantModel, UserModel, ProcessInstanceModel, HumanTaskModel]:
    tenant = M8flowTenantModel(
        id=f"tenant-{uuid4().hex[:8]}",
        name="Postgres Tenant",
        slug=f"postgres-tenant-{uuid4().hex[:8]}",
    )
    user = UserModel(
        username=f"alice-{uuid4().hex[:8]}",
        email=f"alice-{uuid4().hex[:8]}@example.com",
    )
    session.add_all([tenant, user])
    session.flush()

    definition = BpmnProcessDefinitionModel(
        m8f_tenant_id=tenant.id,
        single_process_hash="def-single",
        full_process_model_hash="def-full",
        bpmn_identifier="postgres-smoke-test",
        bpmn_name="Postgres Smoke Test",
        properties_json={"version": 1},
        bpmn_version_control_type="git",
        bpmn_version_control_identifier="main",
        created_at_in_seconds=900,
        updated_at_in_seconds=900,
    )
    session.add(definition)
    session.flush()

    bpmn_process = BpmnProcessModel(
        m8f_tenant_id=tenant.id,
        guid=f"process-{uuid4().hex[:8]}",
        bpmn_process_definition_id=definition.id,
        top_level_process_id=None,
        direct_parent_process_id=None,
        properties_json={"root": "task-root"},
        json_data_hash="process-json-a",
    )
    session.add(bpmn_process)
    session.flush()

    task_definition = TaskDefinitionModel(
        m8f_tenant_id=tenant.id,
        bpmn_process_definition_id=definition.id,
        bpmn_identifier="approve_invoice",
        bpmn_name="Approve Invoice",
        typename="UserTask",
        properties_json={"allowGuest": False},
        created_at_in_seconds=950,
        updated_at_in_seconds=950,
    )
    session.add(task_definition)
    session.flush()

    process_instance = ProcessInstanceModel(
        m8f_tenant_id=tenant.id,
        process_model_identifier="postgres-smoke-test",
        process_model_display_name="Postgres Smoke Test",
        process_initiator_id=user.id,
        bpmn_process_definition_id=definition.id,
        bpmn_process_id=bpmn_process.id,
        status="running",
        process_version=1,
        created_at_in_seconds=1_000,
        updated_at_in_seconds=1_000,
    )
    session.add(process_instance)
    session.flush()

    task = TaskModel(
        m8f_tenant_id=tenant.id,
        guid=f"task-{uuid4().hex[:8]}",
        bpmn_process_id=bpmn_process.id,
        process_instance_id=process_instance.id,
        task_definition_id=task_definition.id,
        state="READY",
        properties_json={"task_spec": "Approve Invoice"},
        json_data_hash="json-hash-a",
        python_env_data_hash="env-hash-a",
    )
    session.add(task)
    session.flush()

    future_task = FutureTaskModel(
        m8f_tenant_id=tenant.id,
        guid=task.guid,
        run_at_in_seconds=1_050,
        queued_to_run_at_in_seconds=1_025,
        updated_at_in_seconds=1_050,
    )
    session.add(future_task)
    session.flush()

    human_task = HumanTaskModel(
        m8f_tenant_id=tenant.id,
        process_instance_id=process_instance.id,
        task_guid=task.guid,
        lane_assignment_id=None,
        completed_by_user_id=None,
        actual_owner_id=None,
        task_name="approve_invoice",
        task_title="Approve Invoice",
        task_type="User Task",
        task_status="READY",
        process_model_display_name=process_instance.process_model_display_name,
        bpmn_process_identifier=process_instance.process_model_identifier,
        lane_name="process initiator",
        json_metadata={"priority": "high"},
        completed=False,
    )
    session.add(human_task)
    session.flush()

    session.add(
        HumanTaskUserModel(
            m8f_tenant_id=tenant.id,
            human_task_id=human_task.id,
            user_id=user.id,
            added_by="process_initiator",
        )
    )
    session.flush()

    return tenant, user, process_instance, human_task


def _seed_conditional_approval_users(
    session: Session,
) -> tuple[M8flowTenantModel, dict[str, UserModel]]:
    tenant = M8flowTenantModel(
        id="tenant-conditional-approval-postgres",
        name="Conditional Approval Postgres",
        slug="conditional-approval-postgres",
    )
    users = {
        "manager": UserModel(
            username="manager@m8flow",
            email="manager@example.com",
        ),
        "reviewer": UserModel(
            username="reviewer@m8flow",
            email="reviewer@example.com",
        ),
        "finance": UserModel(
            username="james@m8flow",
            email="james@example.com",
        ),
        "requester": UserModel(
            username="requester@m8flow",
            email="requester@example.com",
        ),
    }
    session.add(tenant)
    session.add_all(users.values())
    session.flush()
    return tenant, users
