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

TIMER_START_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  id="Definitions_Timer_Start_Scheduling"
  targetNamespace="http://m8flow.example/timers">
  <bpmn:process id="Process_timer_start_scheduling" isExecutable="true">
    <bpmn:startEvent id="StartEvent_timer_start" name="StartEvent_timer_start">
      <bpmn:timerEventDefinition>
        <bpmn:timeDate
          xsi:type="bpmn:tFormalExpression">'2099-01-01T00:00:00+00:00'</bpmn:timeDate>
      </bpmn:timerEventDefinition>
    </bpmn:startEvent>
    <bpmn:sequenceFlow
      id="Flow_1"
      sourceRef="StartEvent_timer_start"
      targetRef="Task_1"
    />
    <bpmn:userTask id="Task_1" name="Review Request" />
    <bpmn:sequenceFlow id="Flow_2" sourceRef="Task_1" targetRef="EndEvent_1" />
    <bpmn:endEvent id="EndEvent_1" />
  </bpmn:process>
</bpmn:definitions>
"""


def test_import_definition_schedules_timer_start_job(session: Session) -> None:
    tenant = M8flowTenantModel(
        id="tenant-timer-start-scheduling",
        name="Tenant Timer Start Scheduling",
        slug="tenant-timer-start-scheduling",
    )
    user = UserModel(
        username="timer-start-admin",
        email="timer-start-admin@example.com",
        service="http://localhost:7002/realms/tenant-timer-start-scheduling",
        service_id="timer-start-admin-keycloak",
        display_name="Timer Start Admin",
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
            bpmn_identifier="timer-start-scheduling-process",
            user_id=user.id,
            bpmn_name="Timer Start Scheduling Process",
            source_bpmn_xml=TIMER_START_BPMN,
            created_at_in_seconds=10,
            updated_at_in_seconds=10,
        ),
    )

    scheduler_jobs = list(
        session.scalars(
            select(SchedulerJobModel).where(
                SchedulerJobModel.m8f_tenant_id == tenant.id
            )
        ).all()
    )
    process_instances = api.execute_query(
        session,
        api.ListProcessInstancesQuery(tenant_id=tenant.id),
    )

    assert process_instances == []
    assert len(scheduler_jobs) == 1

    scheduler_job = scheduler_jobs[0]
    expected_run_at = math.ceil(
        datetime.fromisoformat("2099-01-01T00:00:00+00:00").timestamp()
    )
    assert (
        scheduler_job.job_key
        == f"timer_start|pd:{definition.id}|q:StartEvent_timer_start"
    )
    assert scheduler_job.job_type == "timer_start"
    assert scheduler_job.process_instance_id is None
    assert scheduler_job.bpmn_process_definition_id == definition.id
    assert scheduler_job.run_at_in_seconds == expected_run_at
    assert scheduler_job.locked_by is None
    assert scheduler_job.locked_at_in_seconds is None
    assert scheduler_job.payload_json == {
        "scheduled_from": "process_definition_import",
        "timer_task": {
            "event_definition_type": "TimeDateEventDefinition",
            "event_value": "2099-01-01T00:00:00+00:00",
            "run_at_in_seconds": expected_run_at,
            "task_guid": scheduler_job.payload_json["timer_task"]["task_guid"],
            "task_spec_name": "StartEvent_timer_start",
            "task_spec_type": "StartEvent",
        },
    }
