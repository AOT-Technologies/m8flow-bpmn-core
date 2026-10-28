from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from m8flow_core import api
from m8flow_core.application import (
    ClaimTaskCommand,
    CompleteTaskCommand,
    GetPendingTasksQuery,
    GetProcessInstanceQuery,
    execute_command,
    execute_query,
)
from m8flow_core.models import (
    BpmnProcessDefinitionModel,
    BpmnProcessModel,
    FutureTaskModel,
    M8flowTenantModel,
    ProcessInstanceModel,
    TaskDefinitionModel,
    TaskModel,
    UserModel,
    WorkItemModel,
    WorkItemUserAddedBy,
    WorkItemUserModel,
)
from m8flow_core.services.authorization import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_USER,
    ensure_v1_role,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
CONDITIONAL_APPROVAL_BPMN_PATH = FIXTURE_DIR / "conditional-approval.bpmn"
CONDITIONAL_APPROVAL_DMN_PATH = FIXTURE_DIR / "check_eligibility.dmn"
VALIDATION_BPMN_PATH = FIXTURE_DIR / "invoice_approval_poc.bpmn"
CONDITIONAL_APPROVAL_PROCESS_ID = "Process_conditional_approval_8qpy9gh"
CONDITIONAL_APPROVAL_LANE_OWNERS = {
    "Manager": ["manager", "reviewer"],
    "Finance": ["james"],
}
INITIATOR_LANE = "Requester"
FOREIGN_TENANT_MESSAGE = "does not belong to tenant"


@dataclass(frozen=True, slots=True)
class TenantValidationContext:
    tenant: M8flowTenantModel
    foreign_tenant: M8flowTenantModel
    tenant_user: UserModel
    tenant_observer: UserModel
    foreign_user: UserModel
    process_instance: ProcessInstanceModel
    foreign_process_instance: ProcessInstanceModel
    work_item: WorkItemModel
    foreign_work_item: WorkItemModel


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
            tenant, user, process_instance, work_item = _seed_runtime_rows(
                session
            )

            pending_tasks = execute_query(
                connection,
                GetPendingTasksQuery(
                    tenant_id=tenant.id,
                    user_id=user.id,
                ),
            )
            assert [task.id for task in pending_tasks] == [work_item.id]

            # Derived work item properties lazy-load the task row, so read
            # them through a Session bound to the same connection.
            [session_task] = execute_query(
                session,
                GetPendingTasksQuery(
                    tenant_id=tenant.id,
                    user_id=user.id,
                ),
            )
            assert session_task.task_name == "approve_invoice"
            assert session_task.lane_name == INITIATOR_LANE
            assert _assignment_summary(session_task) == [
                (user.username, WorkItemUserAddedBy.process_initiator.value)
            ]

            claimed_task = execute_command(
                connection,
                ClaimTaskCommand(
                    tenant_id=tenant.id,
                    work_item_id=work_item.id,
                    user_id=user.id,
                ),
            )
            assert claimed_task.actual_owner_id == user.id
            assert claimed_task.task_status == "CLAIMED"

            completed_task = execute_command(
                connection,
                CompleteTaskCommand(
                    tenant_id=tenant.id,
                    work_item_id=work_item.id,
                    user_id=user.id,
                ),
            )
            assert completed_task.completed is True
            assert completed_task.task_status == "COMPLETED"

            current_process_instance = execute_query(
                connection,
                GetProcessInstanceQuery(
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
        with pytest.raises(api.NotFoundError):
            execute_query(
                verify_session,
                GetProcessInstanceQuery(
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
                session,
                api.ImportBpmnProcessDefinitionCommand(
                    tenant_id=tenant.id,
                    bpmn_identifier="conditional-approval-poc",
                    user_id=users["admin"].id,
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
                    created_at=datetime.fromtimestamp(90, UTC),
                    updated_at=datetime.fromtimestamp(90, UTC),
                ),
            )
            assert definition.source_bpmn_xml == bpmn_xml
            assert definition.source_dmn_xml == dmn_xml

            process_instance = api.execute_command(
                session,
                api.InitializeProcessInstanceFromDefinitionCommand(
                    tenant_id=tenant.id,
                    bpmn_process_definition_id=definition.id,
                    process_initiator_id=users["requester"].id,
                    summary="Postgres conditional approval smoke test",
                    process_version=1,
                    started_at=datetime.fromtimestamp(100, UTC),
                    bpmn_process_id=CONDITIONAL_APPROVAL_PROCESS_ID,
                ),
            )
            assert process_instance.status == (
                api.ProcessInstanceStatus.user_input_required
            )
            persisted_process_instance = api.execute_query(
                session,
                api.GetProcessInstanceQuery(
                    tenant_id=tenant.id,
                    process_instance_id=process_instance.id,
                ),
            )
            assert persisted_process_instance.workflow_state_json is not None

            submit_tasks = api.execute_query(
                session,
                api.GetPendingTasksQuery(
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
            assert _assignment_summary(submit_task) == [
                ("requester", WorkItemUserAddedBy.process_initiator.value)
            ]

            api.execute_command(
                session,
                api.ClaimTaskCommand(
                    tenant_id=tenant.id,
                    work_item_id=submit_task.id,
                    user_id=users["requester"].id,
                ),
            )
            api.execute_command(
                session,
                api.CompleteTaskCommand(
                    tenant_id=tenant.id,
                    work_item_id=submit_task.id,
                    user_id=users["requester"].id,
                    completed_at=datetime.fromtimestamp(110, UTC),
                    task_payload={
                        "expense_date": "2026-04-01",
                        "expense_type": "Travel",
                        "amount": "1500",
                        "description": "Trip to LA",
                    },
                ),
            )

            process_instance = api.execute_query(
                session,
                api.GetProcessInstanceQuery(
                    tenant_id=tenant.id,
                    process_instance_id=process_instance.id,
                ),
            )
            assert process_instance.status == (
                api.ProcessInstanceStatus.user_input_required
            )
            assert process_instance.ended_at is None

            manager_tasks = api.execute_query(
                session,
                api.GetPendingTasksQuery(
                    tenant_id=tenant.id,
                    user_id=users["manager"].id,
                ),
            )
            reviewer_tasks = api.execute_query(
                session,
                api.GetPendingTasksQuery(
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
                "Manager", tenant.id
            )
            assert manager_task.json_metadata is not None
            assert manager_task.json_metadata["lane_owners"] == (
                CONDITIONAL_APPROVAL_LANE_OWNERS
            )
            assert _assignment_summary(manager_task) == [
                ("manager", WorkItemUserAddedBy.lane_owner.value),
                ("reviewer", WorkItemUserAddedBy.lane_owner.value),
            ]

            api.execute_command(
                session,
                api.ClaimTaskCommand(
                    tenant_id=tenant.id,
                    work_item_id=manager_task.id,
                    user_id=users["manager"].id,
                ),
            )
            api.execute_command(
                session,
                api.CompleteTaskCommand(
                    tenant_id=tenant.id,
                    work_item_id=manager_task.id,
                    user_id=users["manager"].id,
                    completed_at=datetime.fromtimestamp(120, UTC),
                    task_payload={"decision": "Approved"},
                ),
            )

            process_instance = api.execute_query(
                session,
                api.GetProcessInstanceQuery(
                    tenant_id=tenant.id,
                    process_instance_id=process_instance.id,
                ),
            )
            assert process_instance.status == (
                api.ProcessInstanceStatus.user_input_required
            )
            assert process_instance.ended_at is None

            finance_tasks = api.execute_query(
                session,
                api.GetPendingTasksQuery(
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
                "Finance", tenant.id
            )
            assert finance_task.json_metadata is not None
            assert finance_task.json_metadata["lane_owners"] == (
                CONDITIONAL_APPROVAL_LANE_OWNERS
            )
            assert _assignment_summary(finance_task) == [
                ("james", WorkItemUserAddedBy.lane_owner.value)
            ]

            api.execute_command(
                session,
                api.ClaimTaskCommand(
                    tenant_id=tenant.id,
                    work_item_id=finance_task.id,
                    user_id=users["finance"].id,
                ),
            )
            api.execute_command(
                session,
                api.CompleteTaskCommand(
                    tenant_id=tenant.id,
                    work_item_id=finance_task.id,
                    user_id=users["finance"].id,
                    completed_at=datetime.fromtimestamp(130, UTC),
                    task_payload={"finance_decision": "Approved"},
                ),
            )

            process_instance = api.execute_query(
                session,
                api.GetProcessInstanceQuery(
                    tenant_id=tenant.id,
                    process_instance_id=process_instance.id,
                ),
            )
            assert process_instance.status == api.ProcessInstanceStatus.complete
            assert process_instance.ended_at == datetime.fromtimestamp(130, UTC)

            metadata_rows = api.execute_query(
                session,
                api.GetProcessInstanceMetadataQuery(
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

            events = api.execute_query(
                session,
                api.GetProcessInstanceEventsQuery(
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
        with pytest.raises(api.NotFoundError):
            api.execute_query(
                verify_session,
                api.GetProcessInstanceQuery(
                    tenant_id=tenant.id,
                    process_instance_id=process_instance.id,
                ),
            )


def test_postgres_rejects_cross_tenant_workflow_and_task_actions(
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
            context = _seed_tenant_validation_context(session)

            tenant_instances = api.execute_query(
                connection,
                api.ListProcessInstancesQuery(tenant_id=context.tenant.id),
            )
            assert [item.id for item in tenant_instances] == [
                context.process_instance.id
            ]

            foreign_instances = api.execute_query(
                connection,
                api.ListProcessInstancesQuery(tenant_id=context.foreign_tenant.id),
            )
            assert [item.id for item in foreign_instances] == [
                context.foreign_process_instance.id
            ]

            with pytest.raises(api.AuthorizationError, match=FOREIGN_TENANT_MESSAGE):
                api.execute_command(
                    connection,
                    api.InitializeProcessInstanceFromDefinitionCommand(
                        tenant_id=context.tenant.id,
                        bpmn_process_definition_id=(
                            context.process_instance.bpmn_process_definition_id
                        ),
                        process_initiator_id=context.foreign_user.id,
                        summary="Cross-tenant workflow start should fail",
                        process_version=1,
                        started_at=datetime.fromtimestamp(100, UTC),
                        bpmn_process_id="invoice_approval_poc",
                    ),
                )

            pending_tasks = api.execute_query(
                connection,
                api.GetPendingTasksQuery(
                    tenant_id=context.tenant.id,
                    user_id=context.tenant_user.id,
                ),
            )
            assert [task.id for task in pending_tasks] == [context.work_item.id]

            observer_pending_tasks = api.execute_query(
                connection,
                api.GetPendingTasksQuery(
                    tenant_id=context.tenant.id,
                    user_id=context.tenant_observer.id,
                ),
            )
            assert observer_pending_tasks == []

            foreign_pending_tasks = api.execute_query(
                connection,
                api.GetPendingTasksQuery(
                    tenant_id=context.foreign_tenant.id,
                    user_id=context.foreign_user.id,
                ),
            )
            assert [task.id for task in foreign_pending_tasks] == [
                context.foreign_work_item.id
            ]

            with pytest.raises(api.AuthorizationError, match=FOREIGN_TENANT_MESSAGE):
                api.execute_query(
                    connection,
                    api.GetPendingTasksQuery(
                        tenant_id=context.tenant.id,
                        user_id=context.foreign_user.id,
                    ),
                )

            with pytest.raises(api.AuthorizationError, match=FOREIGN_TENANT_MESSAGE):
                api.execute_command(
                    connection,
                    api.ClaimTaskCommand(
                        tenant_id=context.tenant.id,
                        work_item_id=context.work_item.id,
                        user_id=context.foreign_user.id,
                    ),
                )

            claimed_task = api.execute_command(
                connection,
                api.ClaimTaskCommand(
                    tenant_id=context.tenant.id,
                    work_item_id=context.work_item.id,
                    user_id=context.tenant_user.id,
                ),
            )
            assert claimed_task.actual_owner_id == context.tenant_user.id
            assert claimed_task.task_status == "CLAIMED"

            with pytest.raises(api.AuthorizationError, match=FOREIGN_TENANT_MESSAGE):
                api.execute_command(
                    connection,
                    api.CompleteTaskCommand(
                        tenant_id=context.tenant.id,
                        work_item_id=context.work_item.id,
                        user_id=context.foreign_user.id,
                        completed_at=datetime.fromtimestamp(120, UTC),
                    ),
                )

            completed_task = api.execute_command(
                connection,
                api.CompleteTaskCommand(
                    tenant_id=context.tenant.id,
                    work_item_id=context.work_item.id,
                    user_id=context.tenant_user.id,
                    completed_at=datetime.fromtimestamp(130, UTC),
                ),
            )
            assert completed_task.completed is True
            assert completed_task.task_status == "COMPLETED"
        finally:
            try:
                transaction.rollback()
            finally:
                session.close()


def _assignment_summary(work_item: WorkItemModel) -> list[tuple[str, str | None]]:
    return sorted(
        (assignment.user.username, assignment.added_by)
        for assignment in work_item.work_item_users
    )


def _seed_runtime_rows(
    session: Session,
) -> tuple[M8flowTenantModel, UserModel, ProcessInstanceModel, WorkItemModel]:
    tenant = M8flowTenantModel(
        id=f"tenant-{uuid4().hex[:8]}",
        name="Postgres Tenant",
        slug=f"postgres-tenant-{uuid4().hex[:8]}",
    )
    service_url = f"http://localhost:7002/realms/{tenant.slug}"
    user = UserModel(
        username=f"alice-{uuid4().hex[:8]}",
        email=f"alice-{uuid4().hex[:8]}@example.com",
        service=service_url,
        service_id="alice-keycloak",
        display_name="Alice",
        created_at=datetime.fromtimestamp(1, UTC),
        updated_at=datetime.fromtimestamp(1, UTC),
    )
    session.add_all([tenant, user])
    session.flush()
    ensure_v1_role(
        session,
        tenant_id=tenant.id,
        role_name=ROLE_USER,
        user_ids=[user.id],
    )

    definition = BpmnProcessDefinitionModel(
        m8f_tenant_id=tenant.id,
        process_xml_digest="postgres-smoke-test-digest",
        bpmn_identifier="postgres-smoke-test",
        bpmn_name="Postgres Smoke Test",
        properties_json={"version": 1, "initiator_lanes": [INITIATOR_LANE]},
        bpmn_version_control_type="git",
        bpmn_version_control_identifier="main",
        created_at=datetime.fromtimestamp(900, UTC),
        updated_at=datetime.fromtimestamp(900, UTC),
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
        created_at=datetime.fromtimestamp(950, UTC),
        updated_at=datetime.fromtimestamp(950, UTC),
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
        created_at=datetime.fromtimestamp(1_000, UTC),
        updated_at=datetime.fromtimestamp(1_000, UTC),
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
        properties_json={"task_spec": "Approve Invoice", "lane": INITIATOR_LANE},
        json_data_hash="json-hash-a",
        python_env_data_hash="env-hash-a",
    )
    session.add(task)
    session.flush()

    future_task = FutureTaskModel(
        m8f_tenant_id=tenant.id,
        guid=task.guid,
        run_at=datetime.fromtimestamp(1_050, UTC),
        queued_to_run_at=datetime.fromtimestamp(1_025, UTC),
        updated_at=datetime.fromtimestamp(1_050, UTC),
    )
    session.add(future_task)
    session.flush()

    work_item = WorkItemModel(
        m8f_tenant_id=tenant.id,
        process_instance_id=process_instance.id,
        task_guid=task.guid,
        lane_assignment_id=None,
        completed_by_user_id=None,
        actual_owner_id=None,
        task_status="READY",
        completed=False,
    )
    session.add(work_item)
    session.flush()

    session.add(
        WorkItemUserModel(
            m8f_tenant_id=tenant.id,
            work_item_id=work_item.id,
            user_id=user.id,
            added_by=WorkItemUserAddedBy.process_initiator,
        )
    )
    session.flush()

    return tenant, user, process_instance, work_item


def _seed_conditional_approval_users(
    session: Session,
) -> tuple[M8flowTenantModel, dict[str, UserModel]]:
    tenant = M8flowTenantModel(
        id="tenant-conditional-approval-postgres",
        name="Conditional Approval Postgres",
        slug="conditional-approval-postgres",
    )
    service_url = f"http://localhost:7002/realms/{tenant.slug}"
    users = {
        "admin": UserModel(
            username="admin",
            email="admin@example.com",
            service=service_url,
            service_id="admin-keycloak",
            display_name="Admin",
            created_at=datetime.fromtimestamp(1, UTC),
            updated_at=datetime.fromtimestamp(1, UTC),
        ),
        "manager": UserModel(
            username="manager",
            email="manager@example.com",
            service=service_url,
            service_id="manager-keycloak",
            display_name="Manager",
            created_at=datetime.fromtimestamp(1, UTC),
            updated_at=datetime.fromtimestamp(1, UTC),
        ),
        "reviewer": UserModel(
            username="reviewer",
            email="reviewer@example.com",
            service=service_url,
            service_id="reviewer-keycloak",
            display_name="Reviewer",
            created_at=datetime.fromtimestamp(1, UTC),
            updated_at=datetime.fromtimestamp(1, UTC),
        ),
        "finance": UserModel(
            username="james",
            email="james@example.com",
            service=service_url,
            service_id="finance-keycloak",
            display_name="Finance",
            created_at=datetime.fromtimestamp(1, UTC),
            updated_at=datetime.fromtimestamp(1, UTC),
        ),
        "requester": UserModel(
            username="requester",
            email="requester@example.com",
            service=service_url,
            service_id="requester-keycloak",
            display_name="Requester",
            created_at=datetime.fromtimestamp(1, UTC),
            updated_at=datetime.fromtimestamp(1, UTC),
        ),
    }
    session.add(tenant)
    session.add_all(users.values())
    session.flush()
    ensure_v1_role(
        session,
        tenant_id=tenant.id,
        role_name=ROLE_USER,
        user_ids=[users["requester"].id],
    )
    ensure_v1_role(
        session,
        tenant_id=tenant.id,
        role_name=ROLE_ADMIN,
        user_ids=[users["admin"].id],
    )
    ensure_v1_role(
        session,
        tenant_id=tenant.id,
        role_name=ROLE_MANAGER,
        user_ids=[
            users["manager"].id,
            users["reviewer"].id,
            users["finance"].id,
        ],
    )
    return tenant, users


def _seed_tenant_validation_context(
    session: Session,
) -> TenantValidationContext:
    tenant = M8flowTenantModel(
        id=f"tenant-validation-postgres-{uuid4().hex[:8]}",
        name="Tenant Validation Postgres",
        slug=f"tenant-validation-postgres-{uuid4().hex[:8]}",
    )
    foreign_tenant = M8flowTenantModel(
        id=f"tenant-validation-foreign-postgres-{uuid4().hex[:8]}",
        name="Tenant Validation Foreign Postgres",
        slug=f"tenant-validation-foreign-postgres-{uuid4().hex[:8]}",
    )
    tenant_service = f"http://localhost:7002/realms/{tenant.slug}"
    foreign_service = f"http://localhost:7002/realms/{foreign_tenant.slug}"
    tenant_user = UserModel(
        username=f"tenant-user-{uuid4().hex[:8]}",
        email=f"tenant-user-{uuid4().hex[:8]}@example.com",
        service=tenant_service,
        service_id=f"tenant-user-{uuid4().hex[:8]}-keycloak",
        display_name="Tenant User",
        created_at=datetime.fromtimestamp(1, UTC),
        updated_at=datetime.fromtimestamp(1, UTC),
    )
    tenant_observer = UserModel(
        username=f"tenant-observer-{uuid4().hex[:8]}",
        email=f"tenant-observer-{uuid4().hex[:8]}@example.com",
        service=tenant_service,
        service_id=f"tenant-observer-{uuid4().hex[:8]}-keycloak",
        display_name="Tenant Observer",
        created_at=datetime.fromtimestamp(1, UTC),
        updated_at=datetime.fromtimestamp(1, UTC),
    )
    foreign_user = UserModel(
        username=tenant_user.username,
        email=f"foreign-user-{uuid4().hex[:8]}@example.com",
        service=foreign_service,
        service_id=f"foreign-user-{uuid4().hex[:8]}-keycloak",
        display_name="Foreign User",
        created_at=datetime.fromtimestamp(1, UTC),
        updated_at=datetime.fromtimestamp(1, UTC),
    )
    session.add_all(
        [tenant, foreign_tenant, tenant_user, tenant_observer, foreign_user]
    )
    session.flush()
    ensure_v1_role(
        session,
        tenant_id=tenant.id,
        role_name=ROLE_USER,
        user_ids=[tenant_user.id],
    )

    definition = BpmnProcessDefinitionModel(
        m8f_tenant_id=tenant.id,
        process_xml_digest="validation-digest",
        bpmn_identifier="tenant-validation-process",
        bpmn_name="Tenant Validation Process",
        source_bpmn_xml=VALIDATION_BPMN_PATH.read_text(encoding="utf-8"),
        source_dmn_xml=None,
        properties_json={"version": 1},
        bpmn_version_control_type="git",
        bpmn_version_control_identifier="main",
        created_at=datetime.fromtimestamp(900, UTC),
        updated_at=datetime.fromtimestamp(900, UTC),
    )
    session.add(definition)
    session.flush()

    bpmn_process = BpmnProcessModel(
        m8f_tenant_id=tenant.id,
        guid=f"validation-process-{uuid4().hex[:8]}",
        bpmn_process_definition_id=definition.id,
        top_level_process_id=None,
        direct_parent_process_id=None,
        properties_json={"root": "approve_invoice"},
        json_data_hash=f"validation-process-json-{uuid4().hex[:8]}",
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
        created_at=datetime.fromtimestamp(950, UTC),
        updated_at=datetime.fromtimestamp(950, UTC),
    )
    session.add(task_definition)
    session.flush()

    process_instance = ProcessInstanceModel(
        m8f_tenant_id=tenant.id,
        process_model_identifier="tenant-validation-process",
        process_model_display_name="Tenant Validation Process",
        process_initiator_id=tenant_user.id,
        bpmn_process_definition_id=definition.id,
        bpmn_process_id=bpmn_process.id,
        status="running",
        created_at=datetime.fromtimestamp(1_000, UTC),
        updated_at=datetime.fromtimestamp(1_000, UTC),
    )
    session.add(process_instance)
    session.flush()

    foreign_definition = BpmnProcessDefinitionModel(
        m8f_tenant_id=foreign_tenant.id,
        process_xml_digest="validation-digest-foreign",
        bpmn_identifier="tenant-validation-process-foreign",
        bpmn_name="Tenant Validation Foreign Process",
        source_bpmn_xml=VALIDATION_BPMN_PATH.read_text(encoding="utf-8"),
        source_dmn_xml=None,
        properties_json={"version": 1},
        bpmn_version_control_type="git",
        bpmn_version_control_identifier="main",
        created_at=datetime.fromtimestamp(901, UTC),
        updated_at=datetime.fromtimestamp(901, UTC),
    )
    session.add(foreign_definition)
    session.flush()

    foreign_bpmn_process = BpmnProcessModel(
        m8f_tenant_id=foreign_tenant.id,
        guid=f"validation-process-{uuid4().hex[:8]}",
        bpmn_process_definition_id=foreign_definition.id,
        top_level_process_id=None,
        direct_parent_process_id=None,
        properties_json={"root": "approve_invoice"},
        json_data_hash=f"validation-process-json-{uuid4().hex[:8]}",
    )
    session.add(foreign_bpmn_process)
    session.flush()

    foreign_task_definition = TaskDefinitionModel(
        m8f_tenant_id=foreign_tenant.id,
        bpmn_process_definition_id=foreign_definition.id,
        bpmn_identifier="approve_invoice",
        bpmn_name="Approve Invoice",
        typename="UserTask",
        properties_json={"allowGuest": False},
        created_at=datetime.fromtimestamp(951, UTC),
        updated_at=datetime.fromtimestamp(951, UTC),
    )
    session.add(foreign_task_definition)
    session.flush()

    foreign_process_instance = ProcessInstanceModel(
        m8f_tenant_id=foreign_tenant.id,
        process_model_identifier="tenant-validation-process-foreign",
        process_model_display_name="Tenant Validation Foreign Process",
        process_initiator_id=foreign_user.id,
        bpmn_process_definition_id=foreign_definition.id,
        bpmn_process_id=foreign_bpmn_process.id,
        status="running",
        created_at=datetime.fromtimestamp(1_001, UTC),
        updated_at=datetime.fromtimestamp(1_001, UTC),
    )
    session.add(foreign_process_instance)
    session.flush()

    foreign_task = TaskModel(
        m8f_tenant_id=foreign_tenant.id,
        guid=f"validation-task-{uuid4().hex[:8]}",
        bpmn_process_id=foreign_bpmn_process.id,
        process_instance_id=foreign_process_instance.id,
        task_definition_id=foreign_task_definition.id,
        state="READY",
        properties_json={"task_spec": "Approve Invoice"},
        json_data_hash=f"validation-json-{uuid4().hex[:8]}",
        python_env_data_hash=f"validation-env-{uuid4().hex[:8]}",
    )
    session.add(foreign_task)
    session.flush()

    foreign_work_item = WorkItemModel(
        m8f_tenant_id=foreign_tenant.id,
        process_instance_id=foreign_process_instance.id,
        task_guid=foreign_task.guid,
        lane_assignment_id=None,
        completed_by_user_id=None,
        actual_owner_id=None,
        task_status="READY",
        completed=False,
    )
    session.add(foreign_work_item)
    session.flush()

    session.add(
        WorkItemUserModel(
            m8f_tenant_id=foreign_tenant.id,
            work_item_id=foreign_work_item.id,
            user_id=foreign_user.id,
            added_by=WorkItemUserAddedBy.process_initiator,
        )
    )
    session.flush()

    task = TaskModel(
        m8f_tenant_id=tenant.id,
        guid=f"validation-task-{uuid4().hex[:8]}",
        bpmn_process_id=bpmn_process.id,
        process_instance_id=process_instance.id,
        task_definition_id=task_definition.id,
        state="READY",
        properties_json={"task_spec": "Approve Invoice"},
        json_data_hash=f"validation-json-{uuid4().hex[:8]}",
        python_env_data_hash=f"validation-env-{uuid4().hex[:8]}",
    )
    session.add(task)
    session.flush()

    work_item = WorkItemModel(
        m8f_tenant_id=tenant.id,
        process_instance_id=process_instance.id,
        task_guid=task.guid,
        lane_assignment_id=None,
        completed_by_user_id=None,
        actual_owner_id=None,
        task_status="READY",
        completed=False,
    )
    session.add(work_item)
    session.flush()

    session.add(
        WorkItemUserModel(
            m8f_tenant_id=tenant.id,
            work_item_id=work_item.id,
            user_id=tenant_user.id,
            added_by=WorkItemUserAddedBy.process_initiator,
        )
    )
    session.flush()

    return TenantValidationContext(
        tenant=tenant,
        foreign_tenant=foreign_tenant,
        tenant_user=tenant_user,
        tenant_observer=tenant_observer,
        foreign_user=foreign_user,
        process_instance=process_instance,
        foreign_process_instance=foreign_process_instance,
        work_item=work_item,
        foreign_work_item=foreign_work_item,
    )
