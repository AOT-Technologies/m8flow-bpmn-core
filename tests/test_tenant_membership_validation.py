from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.models.bpmn_process import BpmnProcessModel
from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_bpmn_core.models.human_task import HumanTaskModel
from m8flow_bpmn_core.models.human_task_user import HumanTaskUserModel
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel
from m8flow_bpmn_core.models.task import TaskModel
from m8flow_bpmn_core.models.task_definition import TaskDefinitionModel
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel

VALIDATION_BPMN_PATH = (
    Path(__file__).with_name("fixtures") / "invoice_approval_poc.bpmn"
)


@dataclass(frozen=True, slots=True)
class TenantValidationContext:
    tenant: M8flowTenantModel
    foreign_tenant: M8flowTenantModel
    tenant_user: UserModel
    foreign_user: UserModel
    definition: BpmnProcessDefinitionModel
    process_instance: ProcessInstanceModel
    human_task: HumanTaskModel


def test_initialize_process_instance_rejects_initiator_from_other_tenant(
    session: Session,
) -> None:
    context = _seed_validation_context(session)

    with pytest.raises(PermissionError, match="does not belong to tenant"):
        api.execute_command(
            session,
            api.InitializeProcessInstanceFromDefinitionCommand(
                tenant_id=context.tenant.id,
                bpmn_process_definition_id=context.definition.id,
                process_initiator_id=context.foreign_user.id,
                submission_metadata={
                    "expense_date": "2026-04-01",
                    "expense_type": "Travel",
                    "amount": "1500",
                    "description": "Trip to LA",
                },
                summary="Cross-tenant workflow start should fail",
                process_version=1,
                started_at_in_seconds=100,
                bpmn_process_id="invoice_approval_poc",
            ),
        )

    instances = api.execute_query(
        session,
        api.ListProcessInstancesQuery(tenant_id=context.tenant.id),
    )
    assert [item.id for item in instances] == [context.process_instance.id]


def test_get_pending_tasks_rejects_user_from_other_tenant(
    session: Session,
) -> None:
    context = _seed_validation_context(session)

    with pytest.raises(PermissionError, match="does not belong to tenant"):
        api.execute_command(
            session,
            api.GetPendingTasksCommand(
                tenant_id=context.tenant.id,
                user_id=context.foreign_user.id,
            ),
        )

    pending_tasks = api.execute_command(
        session,
        api.GetPendingTasksCommand(
            tenant_id=context.tenant.id,
            user_id=context.tenant_user.id,
        ),
    )
    assert [item.id for item in pending_tasks] == [context.human_task.id]


def test_claim_and_complete_task_reject_cross_tenant_users(
    session: Session,
) -> None:
    context = _seed_validation_context(session)

    with pytest.raises(PermissionError, match="does not belong to tenant"):
        api.execute_command(
            session,
            api.ClaimTaskCommand(
                tenant_id=context.tenant.id,
                human_task_id=context.human_task.id,
                user_id=context.foreign_user.id,
            ),
        )

    with pytest.raises(PermissionError, match="does not belong to tenant"):
        api.execute_command(
            session,
            api.CompleteTaskCommand(
                tenant_id=context.tenant.id,
                human_task_id=context.human_task.id,
                user_id=context.foreign_user.id,
                completed_at_in_seconds=120,
            ),
        )

    assert context.human_task.actual_owner_id is None
    assert context.human_task.completed is False
    assert context.human_task.task_status == "READY"


def _seed_validation_context(session: Session) -> TenantValidationContext:
    tenant = M8flowTenantModel(
        id="tenant-validation",
        name="Tenant Validation",
        slug="tenant-validation",
    )
    foreign_tenant = M8flowTenantModel(
        id="tenant-validation-foreign",
        name="Tenant Validation Foreign",
        slug="tenant-validation-foreign",
    )
    tenant_service = f"http://localhost:7002/realms/{tenant.slug}"
    foreign_service = f"http://localhost:7002/realms/{foreign_tenant.slug}"
    tenant_user = UserModel(
        username="tenant-user",
        email="tenant-user@example.com",
        service=tenant_service,
        service_id="tenant-user-keycloak",
        display_name="Tenant User",
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    foreign_user = UserModel(
        username="foreign-user",
        email="foreign-user@example.com",
        service=foreign_service,
        service_id="foreign-user-keycloak",
        display_name="Foreign User",
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    session.add_all([tenant, foreign_tenant, tenant_user, foreign_user])
    session.flush()

    definition = BpmnProcessDefinitionModel(
        m8f_tenant_id=tenant.id,
        single_process_hash="validation-single",
        full_process_model_hash="validation-full",
        bpmn_identifier="tenant-validation-process",
        bpmn_name="Tenant Validation Process",
        source_bpmn_xml=VALIDATION_BPMN_PATH.read_text(encoding="utf-8"),
        source_dmn_xml=None,
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
        guid="validation-process-a",
        bpmn_process_definition_id=definition.id,
        top_level_process_id=None,
        direct_parent_process_id=None,
        properties_json={"root": "task-root"},
        json_data_hash="validation-process-json-a",
    )
    session.add(bpmn_process)
    session.flush()

    task_definition = TaskDefinitionModel(
        m8f_tenant_id=tenant.id,
        bpmn_process_definition_id=definition.id,
        bpmn_identifier="approve_expense",
        bpmn_name="Approve Expense",
        typename="UserTask",
        properties_json={"allowGuest": False},
        created_at_in_seconds=950,
        updated_at_in_seconds=950,
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
        process_version=1,
        created_at_in_seconds=1_000,
        updated_at_in_seconds=1_000,
    )
    session.add(process_instance)
    session.flush()

    task = TaskModel(
        m8f_tenant_id=tenant.id,
        guid="validation-task-a",
        bpmn_process_id=bpmn_process.id,
        process_instance_id=process_instance.id,
        task_definition_id=task_definition.id,
        state="READY",
        properties_json={"task_spec": "Approve Expense"},
        json_data_hash="validation-json-a",
        python_env_data_hash="validation-env-a",
    )
    session.add(task)
    session.flush()

    human_task = HumanTaskModel(
        m8f_tenant_id=tenant.id,
        process_instance_id=process_instance.id,
        task_guid=task.guid,
        lane_assignment_id=None,
        completed_by_user_id=None,
        actual_owner_id=None,
        task_name="approve_expense",
        task_title="Approve Expense",
        task_type="UserTask",
        task_status="READY",
        process_model_display_name=process_instance.process_model_display_name,
        bpmn_process_identifier=process_instance.process_model_identifier,
        lane_name="finance",
        json_metadata={"priority": "high"},
        completed=False,
    )
    session.add(human_task)
    session.flush()

    session.add_all(
        [
            HumanTaskUserModel(
                m8f_tenant_id=tenant.id,
                human_task_id=human_task.id,
                user_id=tenant_user.id,
                added_by="manual",
            ),
            HumanTaskUserModel(
                m8f_tenant_id=tenant.id,
                human_task_id=human_task.id,
                user_id=foreign_user.id,
                added_by="manual",
            ),
        ]
    )
    session.flush()

    return TenantValidationContext(
        tenant=tenant,
        foreign_tenant=foreign_tenant,
        tenant_user=tenant_user,
        foreign_user=foreign_user,
        definition=definition,
        process_instance=process_instance,
        human_task=human_task,
    )
