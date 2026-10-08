from __future__ import annotations

from datetime import UTC, datetime

from m8flow_bpmn_core.models.bpmn_process import BpmnProcessModel
from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_bpmn_core.models.future_task import FutureTaskModel
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel
from m8flow_bpmn_core.models.task import TaskModel
from m8flow_bpmn_core.models.task_definition import TaskDefinitionModel
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.models.work_item import WorkItemModel
from m8flow_bpmn_core.models.work_item_user import WorkItemUserModel
from m8flow_bpmn_core.services.authorization import ROLE_USER, ensure_v1_role
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
        process_xml_digest="test-definition-digest",
        bpmn_identifier="invoice-approval",
        bpmn_name="Invoice Approval",
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
        created_at=datetime.fromtimestamp(950, UTC),
        updated_at=datetime.fromtimestamp(950, UTC),
    )
    session.add(task_definition)
    session.flush()

    process_instance = ProcessInstanceModel(
        m8f_tenant_id=tenant.id,
        process_model_identifier="invoice-approval",
        process_model_display_name="invoice-approval",
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

    human_task = WorkItemModel(
        m8f_tenant_id=tenant.id,
        process_instance_id=process_instance.id,
        task_guid=task.guid,
        lane_assignment_id=None,
        completed_by_user_id=None,
        actual_owner_id=None,
        task_status="READY",
        completed=False,
    )
    session.add(human_task)
    session.flush()
    session.add(
        WorkItemUserModel(
            m8f_tenant_id=tenant.id,
            work_item_id=human_task.id,
            user_id=user.id,
            added_by="manual",
        )
    )
    session.flush()

    FutureTaskModel.insert_or_update(
        session,
        tenant_id=tenant.id,
        guid=task.guid,
        run_at=datetime.fromtimestamp(100, UTC),
        queued_to_run_at=datetime.fromtimestamp(90, UTC),
    )
    FutureTaskModel.insert_or_update(
        session,
        tenant_id=tenant.id,
        guid=task.guid,
        run_at=datetime.fromtimestamp(200, UTC),
        queued_to_run_at=datetime.fromtimestamp(150, UTC),
    )

    future_task = session.get(FutureTaskModel, task.guid)
    assert future_task is not None
    assert future_task.run_at.timestamp() == 200
    assert future_task.queued_to_run_at.timestamp() == 150
    assert future_task.completed is False

    claimed_task = claim_task(
        session,
        tenant_id=tenant.id,
        work_item_id=human_task.id,
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
        work_item_id=human_task.id,
        user_id=user.id,
        completed_at=datetime.fromtimestamp(1_234, UTC),
    )
    assert completed_task.completed is True
    assert completed_task.completed_by_user_id == user.id
    assert completed_task.task_status == "COMPLETED"
    assert completed_task.task_model.state == "COMPLETED"
    assert completed_task.task_model.ended_at.timestamp() == 1_234

    future_task = session.get(FutureTaskModel, task.guid)
    assert future_task is not None
    assert future_task.completed is True
    assert get_pending_tasks(session, tenant_id=tenant.id, user_id=user.id) == []
