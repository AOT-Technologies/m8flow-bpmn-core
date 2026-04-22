from __future__ import annotations

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
from m8flow_bpmn_core.services.tasks import get_pending_tasks


def test_get_pending_tasks_returns_only_uncompleted_tasks_for_the_requested_tenant(
    session,
) -> None:
    tenant_a = M8flowTenantModel(id="tenant-a", name="Tenant A", slug="tenant-a")
    tenant_b = M8flowTenantModel(id="tenant-b", name="Tenant B", slug="tenant-b")
    service_url = f"http://localhost:7002/realms/{tenant_a.slug}"
    user = UserModel(
        username="alice",
        email="alice@example.com",
        service=service_url,
        service_id="alice-keycloak",
        display_name="Alice",
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )

    session.add_all([tenant_a, tenant_b, user])
    session.flush()

    definition_a = BpmnProcessDefinitionModel(
        m8f_tenant_id=tenant_a.id,
        single_process_hash="def-a-single",
        full_process_model_hash="def-a-full",
        bpmn_identifier="invoice-approval",
        bpmn_name="Invoice Approval",
        properties_json={"version": 1},
        bpmn_version_control_type="git",
        bpmn_version_control_identifier="main",
        created_at_in_seconds=900,
        updated_at_in_seconds=900,
    )
    definition_b = BpmnProcessDefinitionModel(
        m8f_tenant_id=tenant_b.id,
        single_process_hash="def-b-single",
        full_process_model_hash="def-b-full",
        bpmn_identifier="invoice-approval",
        bpmn_name="Invoice Approval",
        properties_json={"version": 1},
        bpmn_version_control_type="git",
        bpmn_version_control_identifier="main",
        created_at_in_seconds=1_900,
        updated_at_in_seconds=1_900,
    )
    session.add_all([definition_a, definition_b])
    session.flush()

    bpmn_process_a = BpmnProcessModel(
        m8f_tenant_id=tenant_a.id,
        guid="process-a",
        bpmn_process_definition_id=definition_a.id,
        top_level_process_id=None,
        direct_parent_process_id=None,
        properties_json={"root": "task-a-root"},
        json_data_hash="process-json-a",
    )
    bpmn_process_b = BpmnProcessModel(
        m8f_tenant_id=tenant_b.id,
        guid="process-b",
        bpmn_process_definition_id=definition_b.id,
        top_level_process_id=None,
        direct_parent_process_id=None,
        properties_json={"root": "task-b-root"},
        json_data_hash="process-json-b",
    )
    session.add_all([bpmn_process_a, bpmn_process_b])
    session.flush()

    task_definition_a = TaskDefinitionModel(
        m8f_tenant_id=tenant_a.id,
        bpmn_process_definition_id=definition_a.id,
        bpmn_identifier="approve_invoice",
        bpmn_name="Approve Invoice",
        typename="UserTask",
        properties_json={"allowGuest": False},
        created_at_in_seconds=950,
        updated_at_in_seconds=950,
    )
    task_definition_b = TaskDefinitionModel(
        m8f_tenant_id=tenant_b.id,
        bpmn_process_definition_id=definition_b.id,
        bpmn_identifier="approve_invoice",
        bpmn_name="Approve Invoice",
        typename="UserTask",
        properties_json={"allowGuest": False},
        created_at_in_seconds=1_950,
        updated_at_in_seconds=1_950,
    )
    session.add_all([task_definition_a, task_definition_b])
    session.flush()

    process_a = ProcessInstanceModel(
        m8f_tenant_id=tenant_a.id,
        process_model_identifier="invoice-approval",
        process_model_display_name="Invoice Approval",
        process_initiator_id=user.id,
        bpmn_process_definition_id=definition_a.id,
        bpmn_process_id=bpmn_process_a.id,
        status="running",
        process_version=3,
        created_at_in_seconds=1_000,
        updated_at_in_seconds=1_000,
    )
    process_b = ProcessInstanceModel(
        m8f_tenant_id=tenant_b.id,
        process_model_identifier="invoice-approval",
        process_model_display_name="Invoice Approval",
        process_initiator_id=user.id,
        bpmn_process_definition_id=definition_b.id,
        bpmn_process_id=bpmn_process_b.id,
        status="running",
        process_version=3,
        created_at_in_seconds=2_000,
        updated_at_in_seconds=2_000,
    )
    session.add_all([process_a, process_b])
    session.flush()

    task_pending = TaskModel(
        m8f_tenant_id=tenant_a.id,
        guid="task-pending",
        bpmn_process_id=bpmn_process_a.id,
        process_instance_id=process_a.id,
        task_definition_id=task_definition_a.id,
        state="READY",
        properties_json={"task_spec": "Approve Invoice"},
        json_data_hash="json-hash-a",
        python_env_data_hash="env-hash-a",
    )
    task_completed = TaskModel(
        m8f_tenant_id=tenant_a.id,
        guid="task-completed",
        bpmn_process_id=bpmn_process_a.id,
        process_instance_id=process_a.id,
        task_definition_id=task_definition_a.id,
        state="COMPLETED",
        properties_json={"task_spec": "Send Receipt"},
        json_data_hash="json-hash-b",
        python_env_data_hash="env-hash-b",
    )
    task_other_tenant = TaskModel(
        m8f_tenant_id=tenant_b.id,
        guid="task-other-tenant",
        bpmn_process_id=bpmn_process_b.id,
        process_instance_id=process_b.id,
        task_definition_id=task_definition_b.id,
        state="READY",
        properties_json={"task_spec": "Approve Invoice"},
        json_data_hash="json-hash-c",
        python_env_data_hash="env-hash-c",
    )
    session.add_all([task_pending, task_completed, task_other_tenant])
    session.flush()

    pending_human_task = HumanTaskModel(
        m8f_tenant_id=tenant_a.id,
        process_instance_id=process_a.id,
        task_guid=task_pending.guid,
        lane_assignment_id=None,
        completed_by_user_id=None,
        actual_owner_id=user.id,
        task_name="approve_invoice",
        task_title="Approve Invoice",
        task_type="User Task",
        task_status="READY",
        process_model_display_name=process_a.process_model_display_name,
        bpmn_process_identifier=process_a.process_model_identifier,
        lane_name="finance",
        json_metadata={"priority": "high"},
        completed=False,
    )
    completed_human_task = HumanTaskModel(
        m8f_tenant_id=tenant_a.id,
        process_instance_id=process_a.id,
        task_guid=task_completed.guid,
        lane_assignment_id=None,
        completed_by_user_id=user.id,
        actual_owner_id=user.id,
        task_name="send_receipt",
        task_title="Send Receipt",
        task_type="User Task",
        task_status="COMPLETED",
        process_model_display_name=process_a.process_model_display_name,
        bpmn_process_identifier=process_a.process_model_identifier,
        lane_name="finance",
        json_metadata={"priority": "low"},
        completed=True,
    )
    other_tenant_human_task = HumanTaskModel(
        m8f_tenant_id=tenant_b.id,
        process_instance_id=process_b.id,
        task_guid=task_other_tenant.guid,
        lane_assignment_id=None,
        completed_by_user_id=None,
        actual_owner_id=user.id,
        task_name="approve_invoice",
        task_title="Approve Invoice",
        task_type="User Task",
        task_status="READY",
        process_model_display_name=process_b.process_model_display_name,
        bpmn_process_identifier=process_b.process_model_identifier,
        lane_name="finance",
        json_metadata={"priority": "high"},
        completed=False,
    )
    session.add_all([pending_human_task, completed_human_task, other_tenant_human_task])
    session.flush()

    session.add_all(
        [
            HumanTaskUserModel(
                m8f_tenant_id=tenant_a.id,
                human_task_id=pending_human_task.id,
                user_id=user.id,
                added_by="manual",
            ),
            HumanTaskUserModel(
                m8f_tenant_id=tenant_a.id,
                human_task_id=completed_human_task.id,
                user_id=user.id,
                added_by="manual",
            ),
            HumanTaskUserModel(
                m8f_tenant_id=tenant_b.id,
                human_task_id=other_tenant_human_task.id,
                user_id=user.id,
                added_by="manual",
            ),
        ]
    )
    session.flush()

    tasks = get_pending_tasks(session, tenant_id=tenant_a.id, user_id=user.id)

    assert [task.id for task in tasks] == [pending_human_task.id]
    assert tasks[0].task_name == "approve_invoice"
    assert tasks[0].completed is False
