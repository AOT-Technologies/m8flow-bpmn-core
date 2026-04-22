from __future__ import annotations

from m8flow_bpmn_core.models.bpmn_process import BpmnProcessModel
from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_bpmn_core.models.future_task import FutureTaskModel
from m8flow_bpmn_core.models.human_task import HumanTaskModel
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel
from m8flow_bpmn_core.models.task import TaskModel
from m8flow_bpmn_core.models.task_definition import TaskDefinitionModel
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.services.tasks import claim_task, complete_task, get_pending_tasks


def test_task_claim_complete_and_future_task_upsert(session) -> None:
    tenant = M8flowTenantModel(id="tenant-a", name="Tenant A", slug="tenant-a")
    service_url = f"http://localhost:7002/realms/{tenant.slug}"
    user = UserModel(
        username="alice",
        email="alice@example.com",
        service=service_url,
        service_id="alice-keycloak",
        display_name="Alice",
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )

    session.add_all([tenant, user])
    session.flush()

    definition = BpmnProcessDefinitionModel(
        m8f_tenant_id=tenant.id,
        single_process_hash="def-single",
        full_process_model_hash="def-full",
        bpmn_identifier="invoice-approval",
        bpmn_name="Invoice Approval",
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
        guid="process-a",
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
        process_model_identifier="invoice-approval",
        process_model_display_name="Invoice Approval",
        process_initiator_id=user.id,
        bpmn_process_definition_id=definition.id,
        bpmn_process_id=bpmn_process.id,
        status="running",
        process_version=3,
        created_at_in_seconds=1_000,
        updated_at_in_seconds=1_000,
    )
    session.add(process_instance)
    session.flush()

    task = TaskModel(
        m8f_tenant_id=tenant.id,
        guid="task-a",
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
        lane_name="finance",
        json_metadata={"priority": "high"},
        completed=False,
    )
    session.add(human_task)
    session.flush()

    FutureTaskModel.insert_or_update(
        session,
        tenant_id=tenant.id,
        guid=task.guid,
        run_at_in_seconds=100,
        queued_to_run_at_in_seconds=90,
    )
    FutureTaskModel.insert_or_update(
        session,
        tenant_id=tenant.id,
        guid=task.guid,
        run_at_in_seconds=200,
        queued_to_run_at_in_seconds=150,
    )

    future_task = session.get(FutureTaskModel, task.guid)
    assert future_task is not None
    assert future_task.run_at_in_seconds == 200
    assert future_task.queued_to_run_at_in_seconds == 150
    assert future_task.completed is False

    claimed_task = claim_task(
        session,
        tenant_id=tenant.id,
        human_task_id=human_task.id,
        user_id=user.id,
    )
    assert claimed_task.actual_owner_id == user.id
    assert claimed_task.task_status == "CLAIMED"
    assert [
        task.id
        for task in get_pending_tasks(session, tenant_id=tenant.id, user_id=user.id)
    ] == [human_task.id]

    completed_task = complete_task(
        session,
        tenant_id=tenant.id,
        human_task_id=human_task.id,
        user_id=user.id,
        completed_at_in_seconds=1_234,
    )
    assert completed_task.completed is True
    assert completed_task.completed_by_user_id == user.id
    assert completed_task.task_status == "COMPLETED"
    assert completed_task.task_model.state == "COMPLETED"
    assert completed_task.task_model.end_in_seconds == 1_234

    future_task = session.get(FutureTaskModel, task.guid)
    assert future_task is not None
    assert future_task.completed is True
    assert get_pending_tasks(session, tenant_id=tenant.id, user_id=user.id) == []
