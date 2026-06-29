from __future__ import annotations

import math
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.models.scheduler_job import SchedulerJobModel
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.services.authorization import ROLE_ADMIN, ensure_v1_role

INTERMEDIATE_TIMER_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  id="Definitions_Timer_Waiting"
  targetNamespace="http://m8flow.example/timers">
  <bpmn:process id="Process_timer_waiting" isExecutable="true">
    <bpmn:startEvent id="StartEvent_1" />
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="TimerCatch_1" />
    <bpmn:intermediateCatchEvent id="TimerCatch_1" name="TimerCatch_1">
      <bpmn:incoming>Flow_1</bpmn:incoming>
      <bpmn:outgoing>Flow_2</bpmn:outgoing>
      <bpmn:timerEventDefinition>
        <bpmn:timeDate
          xsi:type="bpmn:tFormalExpression">'2099-01-01T00:00:00+00:00'</bpmn:timeDate>
      </bpmn:timerEventDefinition>
    </bpmn:intermediateCatchEvent>
    <bpmn:sequenceFlow id="Flow_2" sourceRef="TimerCatch_1" targetRef="Task_1" />
    <bpmn:userTask id="Task_1" name="Review Request" />
    <bpmn:sequenceFlow id="Flow_3" sourceRef="Task_1" targetRef="EndEvent_1" />
    <bpmn:endEvent id="EndEvent_1" />
  </bpmn:process>
</bpmn:definitions>
"""

BOUNDARY_TIMER_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  id="Definitions_Boundary_Timer_Waiting"
  targetNamespace="http://m8flow.example/timers">
  <bpmn:process id="Process_boundary_timer_waiting" isExecutable="true">
    <bpmn:laneSet id="LaneSet_1">
      <bpmn:lane id="Lane_Operations" name="Operations">
        <bpmn:flowNodeRef>Task_review</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Task_timeout</bpmn:flowNodeRef>
      </bpmn:lane>
    </bpmn:laneSet>
    <bpmn:startEvent id="StartEvent_1" />
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="Task_review" />
    <bpmn:userTask id="Task_review" name="Review Request" />
    <bpmn:boundaryEvent
      id="BoundaryEvent_timeout"
      attachedToRef="Task_review"
      cancelActivity="true">
      <bpmn:timerEventDefinition>
        <bpmn:timeDate
          xsi:type="bpmn:tFormalExpression">'2099-01-01T00:00:00+00:00'</bpmn:timeDate>
      </bpmn:timerEventDefinition>
    </bpmn:boundaryEvent>
    <bpmn:sequenceFlow
      id="Flow_timeout"
      sourceRef="BoundaryEvent_timeout"
      targetRef="Task_timeout"
    />
    <bpmn:userTask id="Task_timeout" name="Handle Timeout" />
    <bpmn:sequenceFlow
      id="Flow_2"
      sourceRef="Task_review"
      targetRef="EndEvent_review"
    />
    <bpmn:endEvent id="EndEvent_review" />
    <bpmn:sequenceFlow
      id="Flow_3"
      sourceRef="Task_timeout"
      targetRef="EndEvent_timeout"
    />
    <bpmn:endEvent id="EndEvent_timeout" />
  </bpmn:process>
</bpmn:definitions>
"""


def test_initialize_workflow_schedules_waiting_intermediate_timer(
    session: Session,
) -> None:
    tenant = M8flowTenantModel(
        id="tenant-timer-scheduling",
        name="Tenant Timer Scheduling",
        slug="tenant-timer-scheduling",
    )
    user = UserModel(
        username="timer-admin",
        email="timer-admin@example.com",
        service="http://localhost:7002/realms/tenant-timer-scheduling",
        service_id="timer-admin-keycloak",
        display_name="Timer Admin",
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    session.add_all([tenant, user])
    session.flush()
    ensure_v1_role(
        session,
        tenant_id=tenant.id,
        role_name=ROLE_ADMIN,
        user_ids=[user.id],
    )

    definition = api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_identifier="timer-waiting-process",
            user_id=user.id,
            bpmn_name="Timer Waiting Process",
            source_bpmn_xml=INTERMEDIATE_TIMER_BPMN,
            created_at_in_seconds=10,
            updated_at_in_seconds=10,
        ),
    )
    process_instance = api.execute_command(
        session,
        api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_process_definition_id=definition.id,
            process_initiator_id=user.id,
            started_at_in_seconds=20,
        ),
    )

    assert process_instance.status == api.ProcessInstanceStatus.waiting.value
    assert process_instance.workflow_state_json is not None
    assert process_instance.human_tasks == []
    assert (
        api.execute_query(
            session,
            api.GetPendingTasksQuery(
                tenant_id=tenant.id,
                user_id=user.id,
            ),
        )
        == []
    )

    scheduler_jobs = list(
        session.scalars(
            select(SchedulerJobModel).where(
                SchedulerJobModel.m8f_tenant_id == tenant.id
            )
        ).all()
    )
    assert len(scheduler_jobs) == 1

    scheduler_job = scheduler_jobs[0]
    expected_run_at = math.ceil(
        datetime.fromisoformat("2099-01-01T00:00:00+00:00").timestamp()
    )
    timer_task = next(
        task
        for task in process_instance.tasks
        if task.task_definition.bpmn_identifier == "TimerCatch_1"
    )
    assert scheduler_job.job_key == f"intermediate_timer|pi:{process_instance.id}"
    assert scheduler_job.job_type == "intermediate_timer"
    assert scheduler_job.process_instance_id == process_instance.id
    assert scheduler_job.bpmn_process_definition_id == definition.id
    assert scheduler_job.run_at_in_seconds == expected_run_at
    assert scheduler_job.locked_by is None
    assert scheduler_job.locked_at_in_seconds is None
    assert scheduler_job.payload_json["scheduled_from"] == "workflow_runtime"
    assert scheduler_job.payload_json["timer_tasks"] == [
        {
            "event_definition_type": "TimeDateEventDefinition",
            "event_value": "2099-01-01T00:00:00+00:00",
            "run_at_in_seconds": expected_run_at,
            "task_guid": timer_task.guid,
            "task_spec_name": "TimerCatch_1",
            "task_spec_type": "IntermediateCatchEvent",
        }
    ]


def test_initialize_workflow_schedules_waiting_boundary_timer(
    session: Session,
) -> None:
    tenant = M8flowTenantModel(
        id="tenant-boundary-timer-scheduling",
        name="Tenant Boundary Timer Scheduling",
        slug="tenant-boundary-timer-scheduling",
    )
    user = UserModel(
        username="boundary-timer-admin",
        email="boundary-timer-admin@example.com",
        service="http://localhost:7002/realms/tenant-boundary-timer-scheduling",
        service_id="boundary-timer-admin-keycloak",
        display_name="Boundary Timer Admin",
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    session.add_all([tenant, user])
    session.flush()
    ensure_v1_role(
        session,
        tenant_id=tenant.id,
        role_name=ROLE_ADMIN,
        user_ids=[user.id],
    )

    definition = api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_identifier="boundary-timer-waiting-process",
            user_id=user.id,
            bpmn_name="Boundary Timer Waiting Process",
            source_bpmn_xml=BOUNDARY_TIMER_BPMN,
            properties_json={
                "lane_owners": {
                    "Operations": [user.username],
                }
            },
            created_at_in_seconds=10,
            updated_at_in_seconds=10,
        ),
    )
    process_instance = api.execute_command(
        session,
        api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_process_definition_id=definition.id,
            process_initiator_id=user.id,
            started_at_in_seconds=20,
        ),
    )

    assert (
        process_instance.status
        == api.ProcessInstanceStatus.user_input_required.value
    )
    assert process_instance.workflow_state_json is not None
    pending_tasks = api.execute_query(
        session,
        api.GetPendingTasksQuery(
            tenant_id=tenant.id,
            user_id=user.id,
        ),
    )
    assert len(pending_tasks) == 1
    assert pending_tasks[0].task_name == "Task_review"

    scheduler_jobs = list(
        session.scalars(
            select(SchedulerJobModel).where(
                SchedulerJobModel.m8f_tenant_id == tenant.id
            )
        ).all()
    )
    assert len(scheduler_jobs) == 1

    scheduler_job = scheduler_jobs[0]
    expected_run_at = math.ceil(
        datetime.fromisoformat("2099-01-01T00:00:00+00:00").timestamp()
    )
    timer_task = next(
        task
        for task in process_instance.tasks
        if task.task_definition.bpmn_identifier == "BoundaryEvent_timeout"
    )
    assert scheduler_job.job_key == f"intermediate_timer|pi:{process_instance.id}"
    assert scheduler_job.job_type == "intermediate_timer"
    assert scheduler_job.process_instance_id == process_instance.id
    assert scheduler_job.bpmn_process_definition_id == definition.id
    assert scheduler_job.run_at_in_seconds == expected_run_at
    assert scheduler_job.payload_json["scheduled_from"] == "workflow_runtime"
    assert scheduler_job.payload_json["timer_tasks"] == [
        {
            "event_definition_type": "TimeDateEventDefinition",
            "event_value": "2099-01-01T00:00:00+00:00",
            "run_at_in_seconds": expected_run_at,
            "task_guid": timer_task.guid,
            "task_spec_name": "BoundaryEvent_timeout",
            "task_spec_type": "BoundaryEvent",
        }
    ]
