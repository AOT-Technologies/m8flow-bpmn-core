from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.models.bpmn_process import BpmnProcessModel
from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_bpmn_core.models.future_task import FutureTaskModel
from m8flow_bpmn_core.models.human_task import HumanTaskModel
from m8flow_bpmn_core.models.human_task_user import HumanTaskUserModel
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel
from m8flow_bpmn_core.models.task import TaskModel
from m8flow_bpmn_core.models.task_definition import TaskDefinitionModel
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel

EXAMPLE_BPMN_PATH = Path(__file__).with_name("fixtures") / "invoice_approval_poc.bpmn"

BPMN_NAMESPACES = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}


@dataclass(frozen=True, slots=True)
class WorkflowScenario:
    name: str
    approval_state: str
    approval_amount: int
    decision_note: str
    decision_path: str


SCENARIOS = [
    WorkflowScenario(
        name="approved",
        approval_state="approved",
        approval_amount=1_250,
        decision_note="auto-approved",
        decision_path="approved_end",
    ),
    WorkflowScenario(
        name="rejected",
        approval_state="rejected",
        approval_amount=8_750,
        decision_note="needs-manual-review",
        decision_path="rejected_end",
    ),
]


def test_invoice_approval_workflow_poc_end_to_end(session: Session) -> None:
    tenant, user, process_instance, task, human_task = _seed_example_workflow(
        session
    )

    running_instances = api.execute_query(
        session,
        api.ListProcessInstancesQuery(
            tenant_id=tenant.id,
            status=api.ProcessInstanceStatus.running,
        ),
    )
    assert [item.id for item in running_instances] == [process_instance.id]
    assert (
        api.execute_query(
            session,
            api.GetProcessInstanceQuery(
                tenant_id=tenant.id,
                process_instance_id=process_instance.id,
            ),
        ).status
        == "running"
    )
    assert [
        item.id
        for item in api.execute_query(
            session,
            api.GetPendingTasksQuery(
                tenant_id=tenant.id,
                user_id=user.id,
            ),
        )
    ] == [human_task.id]

    api.execute_command(
        session,
        api.RecordProcessInstanceEventCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            event_type=api.ProcessInstanceEventType.process_instance_created,
            task_guid=task.guid,
            user_id=user.id,
            timestamp=100.0,
        ),
    )
    metadata = api.execute_command(
        session,
        api.UpsertProcessInstanceMetadataCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            key="approval_state",
            value="submitted",
            updated_at_in_seconds=101,
            created_at_in_seconds=100,
        ),
    )
    assert metadata.value == "submitted"
    assert metadata.created_at_in_seconds == 100

    claimed_task = api.execute_command(
        session,
        api.ClaimTaskCommand(
            tenant_id=tenant.id,
            human_task_id=human_task.id,
            user_id=user.id,
        ),
    )
    assert claimed_task.actual_owner_id == user.id
    assert claimed_task.task_status == "CLAIMED"

    suspended_process_instance = api.execute_command(
        session,
        api.SuspendProcessInstanceCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            user_id=user.id,
            suspended_at_in_seconds=110,
        ),
    )
    assert suspended_process_instance.status == "suspended"
    assert [
        item.id
        for item in api.execute_query(
            session,
            api.ListSuspendedProcessInstancesQuery(tenant_id=tenant.id),
        )
    ] == [process_instance.id]

    resumed_process_instance = api.execute_command(
        session,
        api.ResumeProcessInstanceCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            user_id=user.id,
            resumed_at_in_seconds=120,
        ),
    )
    assert resumed_process_instance.status == "running"
    assert [
        item.id
        for item in api.execute_query(
            session,
            api.ListProcessInstancesQuery(
                tenant_id=tenant.id,
                status=api.ProcessInstanceStatus.running,
            ),
        )
    ] == [process_instance.id]

    errored_process_instance = api.execute_command(
        session,
        api.ErrorProcessInstanceCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            user_id=user.id,
            errored_at_in_seconds=130,
        ),
    )
    assert errored_process_instance.status == "error"
    assert errored_process_instance.end_in_seconds == 130
    assert errored_process_instance.tasks[0].state == "TERMINATED"
    assert errored_process_instance.tasks[0].future_task is not None
    assert errored_process_instance.tasks[0].future_task.completed is True
    assert errored_process_instance.human_tasks[0].completed is True
    assert errored_process_instance.human_tasks[0].task_status == "TERMINATED"
    assert [
        item.id
        for item in api.execute_query(
            session,
            api.ListErrorProcessInstancesQuery(tenant_id=tenant.id),
        )
    ] == [process_instance.id]
    assert api.execute_query(
        session,
        api.GetPendingTasksQuery(
            tenant_id=tenant.id,
            user_id=user.id,
        ),
    ) == []

    api.execute_command(
        session,
        api.RecordProcessInstanceEventCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            event_type=api.ProcessInstanceEventType.task_failed,
            task_guid=task.guid,
            user_id=user.id,
            timestamp=131.0,
        ),
    )

    retried_process_instance = api.execute_command(
        session,
        api.RetryProcessInstanceCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            user_id=user.id,
            retried_at_in_seconds=140,
        ),
    )
    assert retried_process_instance.status == "running"
    assert retried_process_instance.end_in_seconds is None
    assert retried_process_instance.tasks[0].state == "READY"
    assert retried_process_instance.tasks[0].start_in_seconds is None
    assert retried_process_instance.tasks[0].end_in_seconds is None
    assert retried_process_instance.tasks[0].future_task is not None
    assert retried_process_instance.tasks[0].future_task.completed is False
    assert (
        retried_process_instance.tasks[0]
        .future_task.archived_for_process_instance_status
        is False
    )
    assert retried_process_instance.human_tasks[0].completed is False
    assert retried_process_instance.human_tasks[0].task_status == "READY"
    assert retried_process_instance.human_tasks[0].actual_owner_id is None
    assert retried_process_instance.human_tasks[0].completed_by_user_id is None
    assert [
        item.id
        for item in api.execute_query(
            session,
            api.GetPendingTasksQuery(
                tenant_id=tenant.id,
                user_id=user.id,
            ),
        )
    ] == [human_task.id]
    assert api.execute_query(
        session,
        api.ListErrorProcessInstancesQuery(tenant_id=tenant.id),
    ) == []

    reclaimed_task = api.execute_command(
        session,
        api.ClaimTaskCommand(
            tenant_id=tenant.id,
            human_task_id=human_task.id,
            user_id=user.id,
        ),
    )
    assert reclaimed_task.actual_owner_id == user.id
    assert reclaimed_task.task_status == "CLAIMED"

    completed_task = api.execute_command(
        session,
        api.CompleteTaskCommand(
            tenant_id=tenant.id,
            human_task_id=human_task.id,
            user_id=user.id,
            completed_at_in_seconds=150,
        ),
    )
    assert completed_task.completed is True
    assert completed_task.task_status == "COMPLETED"
    assert completed_task.task_model.state == "COMPLETED"
    assert completed_task.task_model.end_in_seconds == 150
    assert completed_task.task_model.future_task is not None
    assert completed_task.task_model.future_task.completed is True

    updated_metadata = api.execute_command(
        session,
        api.UpsertProcessInstanceMetadataCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            key="approval_state",
            value="approved",
            updated_at_in_seconds=151,
        ),
    )
    assert updated_metadata.value == "approved"
    assert updated_metadata.created_at_in_seconds == 100

    api.execute_command(
        session,
        api.RecordProcessInstanceEventCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            event_type=api.ProcessInstanceEventType.task_completed,
            task_guid=task.guid,
            user_id=user.id,
            timestamp=152.0,
        ),
    )

    terminated_process_instance = api.execute_command(
        session,
        api.TerminateProcessInstanceCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            user_id=user.id,
            terminated_at_in_seconds=160,
        ),
    )
    assert terminated_process_instance.status == "terminated"
    assert terminated_process_instance.end_in_seconds == 160
    assert terminated_process_instance.tasks[0].state == "COMPLETED"
    assert terminated_process_instance.tasks[0].end_in_seconds == 160
    assert terminated_process_instance.tasks[0].future_task is not None
    assert terminated_process_instance.tasks[0].future_task.completed is True
    assert (
        terminated_process_instance.tasks[0]
        .future_task.archived_for_process_instance_status
        is True
    )
    assert terminated_process_instance.human_tasks[0].completed is True
    assert terminated_process_instance.human_tasks[0].task_status == "COMPLETED"
    assert terminated_process_instance.human_tasks[0].actual_owner_id == user.id
    assert terminated_process_instance.human_tasks[0].completed_by_user_id == user.id

    assert [
        item.id
        for item in api.execute_query(
            session,
            api.ListTerminatedProcessInstancesQuery(tenant_id=tenant.id),
        )
    ] == [process_instance.id]
    assert [
        item.id
        for item in api.execute_query(
            session,
            api.ListProcessInstancesQuery(
                tenant_id=tenant.id,
                status=api.ProcessInstanceStatus.terminated,
            ),
        )
    ] == [process_instance.id]
    assert api.execute_query(
        session,
        api.GetPendingTasksQuery(
            tenant_id=tenant.id,
            user_id=user.id,
        ),
    ) == []
    assert api.execute_query(
        session,
        api.ListErrorProcessInstancesQuery(tenant_id=tenant.id),
    ) == []

    metadata_rows = api.execute_query(
        session,
        api.GetProcessInstanceMetadataQuery(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
        ),
    )
    assert [(row.key, row.value) for row in metadata_rows] == [
        ("approval_state", "approved")
    ]
    assert metadata_rows[0].created_at_in_seconds == 100
    assert metadata_rows[0].updated_at_in_seconds == 151

    events = api.execute_query(
        session,
        api.GetProcessInstanceEventsQuery(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
        ),
    )
    assert [event.event_type for event in events] == [
        "process_instance_created",
        "process_instance_suspended",
        "process_instance_resumed",
        "process_instance_error",
        "task_failed",
        "process_instance_retried",
        "task_completed",
        "process_instance_terminated",
    ]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
def test_invoice_approval_workflow_scenarios(
    session: Session,
    scenario: WorkflowScenario,
) -> None:
    _assert_example_bpmn_has_branching()

    tenant, user, process_instance, task, human_task = _seed_example_workflow(
        session, scenario
    )

    assert process_instance.summary == f"Scenario: {scenario.name}"
    assert process_instance.process_model_display_name == "Invoice Approval POC"

    api.execute_command(
        session,
        api.RecordProcessInstanceEventCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            event_type=api.ProcessInstanceEventType.process_instance_created,
            task_guid=task.guid,
            user_id=user.id,
            timestamp=100.0,
        ),
    )

    variable_values = {
        "approval_state": scenario.approval_state,
        "approval_amount": str(scenario.approval_amount),
        "decision_note": scenario.decision_note,
        "decision_path": scenario.decision_path,
    }
    updated_at = 101
    for key, value in variable_values.items():
        api.execute_command(
            session,
            api.UpsertProcessInstanceMetadataCommand(
                tenant_id=tenant.id,
                process_instance_id=process_instance.id,
                key=key,
                value=value,
                updated_at_in_seconds=updated_at,
            ),
        )
        updated_at += 1

    pending_tasks = api.execute_query(
        session,
        api.GetPendingTasksQuery(
            tenant_id=tenant.id,
            user_id=user.id,
        ),
    )
    assert [item.id for item in pending_tasks] == [human_task.id]

    claimed_task = api.execute_command(
        session,
        api.ClaimTaskCommand(
            tenant_id=tenant.id,
            human_task_id=human_task.id,
            user_id=user.id,
        ),
    )
    assert claimed_task.actual_owner_id == user.id
    assert claimed_task.task_status == "CLAIMED"

    completed_at = 150 if scenario.approval_state == "approved" else 175
    completed_task = api.execute_command(
        session,
        api.CompleteTaskCommand(
            tenant_id=tenant.id,
            human_task_id=human_task.id,
            user_id=user.id,
            completed_at_in_seconds=completed_at,
        ),
    )
    assert completed_task.completed is True
    assert completed_task.task_status == "COMPLETED"
    assert completed_task.task_model.state == "COMPLETED"
    assert completed_task.task_model.end_in_seconds == completed_at

    api.execute_command(
        session,
        api.RecordProcessInstanceEventCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            event_type=api.ProcessInstanceEventType.task_completed,
            task_guid=task.guid,
            user_id=user.id,
            timestamp=float(completed_at),
        ),
    )

    terminated_at = completed_at + 10
    terminated_process_instance = api.execute_command(
        session,
        api.TerminateProcessInstanceCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            user_id=user.id,
            terminated_at_in_seconds=terminated_at,
        ),
    )
    assert terminated_process_instance.status == "terminated"
    assert terminated_process_instance.end_in_seconds == terminated_at

    metadata_rows = api.execute_query(
        session,
        api.GetProcessInstanceMetadataQuery(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
        ),
    )
    metadata_map = {item.key: item.value for item in metadata_rows}
    assert metadata_map == {
        "approval_amount": str(scenario.approval_amount),
        "approval_state": scenario.approval_state,
        "decision_note": scenario.decision_note,
        "decision_path": scenario.decision_path,
    }

    assert [
        item.id
        for item in api.execute_query(
            session,
            api.ListProcessInstancesQuery(
                tenant_id=tenant.id,
                status=api.ProcessInstanceStatus.terminated,
            ),
        )
    ] == [process_instance.id]

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
        "process_instance_terminated",
    ]


def _seed_example_workflow(
    session: Session,
    scenario: WorkflowScenario | None = None,
) -> tuple[
    M8flowTenantModel,
    UserModel,
    ProcessInstanceModel,
    TaskModel,
    HumanTaskModel,
]:
    bpmn_xml = EXAMPLE_BPMN_PATH.read_text(encoding="utf-8")
    full_process_model_hash = hashlib.sha256(bpmn_xml.encode("utf-8")).hexdigest()
    single_process_hash = hashlib.sha256(
        f"single::{bpmn_xml}".encode()
    ).hexdigest()

    tenant = M8flowTenantModel(
        id="tenant-poc",
        name="Tenant POC",
        slug="tenant-poc",
    )
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
        single_process_hash=single_process_hash,
        full_process_model_hash=full_process_model_hash,
        bpmn_identifier="invoice-approval-poc",
        bpmn_name="Invoice Approval POC",
        properties_json={
            "version": 1,
            "flow": "invoice_approval",
            "source_bpmn_fixture": EXAMPLE_BPMN_PATH.name,
            "source_bpmn_sha256": full_process_model_hash,
            "decision_variables": [
                "approval_amount",
                "approval_state",
                "decision_note",
                "decision_path",
            ],
            "scenario_name": scenario.name if scenario is not None else "default",
        },
        bpmn_version_control_type="git",
        bpmn_version_control_identifier="main",
        created_at_in_seconds=900,
        updated_at_in_seconds=900,
    )
    session.add(definition)
    session.flush()

    bpmn_process = BpmnProcessModel(
        m8f_tenant_id=tenant.id,
        guid="poc-process-a",
        bpmn_process_definition_id=definition.id,
        top_level_process_id=None,
        direct_parent_process_id=None,
        properties_json={"root": "approve_invoice"},
        json_data_hash="poc-process-json-a",
    )
    session.add(bpmn_process)
    session.flush()

    task_definition = TaskDefinitionModel(
        m8f_tenant_id=tenant.id,
        bpmn_process_definition_id=definition.id,
        bpmn_identifier="approve_invoice",
        bpmn_name="Approve Invoice",
        typename="UserTask",
        properties_json={"allowGuest": False, "slaHours": 24},
        created_at_in_seconds=950,
        updated_at_in_seconds=950,
    )
    session.add(task_definition)
    session.flush()

    process_instance = ProcessInstanceModel(
        m8f_tenant_id=tenant.id,
        process_model_identifier="invoice-approval-poc",
        process_model_display_name="Invoice Approval POC",
        process_initiator_id=user.id,
        bpmn_process_definition_id=definition.id,
        bpmn_process_id=bpmn_process.id,
        status="running",
        process_version=1,
        summary=(
            f"Scenario: {scenario.name}"
            if scenario is not None
            else "Invoice approval POC"
        ),
        created_at_in_seconds=1_000,
        updated_at_in_seconds=1_000,
    )
    session.add(process_instance)
    session.flush()

    task = TaskModel(
        m8f_tenant_id=tenant.id,
        guid="poc-task-a",
        bpmn_process_id=bpmn_process.id,
        process_instance_id=process_instance.id,
        task_definition_id=task_definition.id,
        state="READY",
        properties_json={"task_spec": "Approve Invoice"},
        json_data_hash="poc-json-hash-a",
        python_env_data_hash="poc-env-hash-a",
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
        lane_name="finance",
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
            added_by="manual",
        )
    )
    session.flush()

    assert "invoice_approval_poc" in bpmn_xml

    return tenant, user, process_instance, task, human_task


def _assert_example_bpmn_has_branching() -> None:
    tree = ET.parse(EXAMPLE_BPMN_PATH)
    root = tree.getroot()

    gateway = root.find(
        ".//bpmn:exclusiveGateway[@id='approval_decision_gateway']",
        BPMN_NAMESPACES,
    )
    assert gateway is not None
    assert gateway.get("default") == "flow_rejected_to_end"

    approved_flow = root.find(
        ".//bpmn:sequenceFlow[@id='flow_approved_to_end']",
        BPMN_NAMESPACES,
    )
    assert approved_flow is not None
    approved_condition = approved_flow.find(
        "bpmn:conditionExpression",
        BPMN_NAMESPACES,
    )
    assert approved_condition is not None
    assert "".join(approved_condition.itertext()).strip() == (
        "${approval_state == 'approved'}"
    )

    rejected_flow = root.find(
        ".//bpmn:sequenceFlow[@id='flow_rejected_to_end']",
        BPMN_NAMESPACES,
    )
    assert rejected_flow is not None
    assert rejected_flow.find("bpmn:conditionExpression", BPMN_NAMESPACES) is None
