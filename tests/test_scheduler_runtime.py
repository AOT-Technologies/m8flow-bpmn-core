from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.models.bpmn_process import BpmnProcessModel
from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_bpmn_core.models.future_task import FutureTaskModel
from m8flow_bpmn_core.models.human_task import HumanTaskModel
from m8flow_bpmn_core.models.human_task_user import HumanTaskUserModel
from m8flow_bpmn_core.models.json_data import JsonDataModel
from m8flow_bpmn_core.models.process_instance import (
    WORKFLOW_STATE_JSON_DATA_KEY,
    ProcessInstanceModel,
)
from m8flow_bpmn_core.models.scheduler_job import SchedulerJobModel
from m8flow_bpmn_core.models.task import TaskModel
from m8flow_bpmn_core.models.task_definition import TaskDefinitionModel
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.services import scheduler_runtime
from m8flow_bpmn_core.services.authorization import ROLE_ADMIN, ensure_v1_role
from m8flow_bpmn_core.services.scheduler_jobs import (
    delete_scheduler_job,
    upsert_scheduler_job,
)

INTERMEDIATE_TIMER_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  id="Definitions_Timer_Runtime"
  targetNamespace="http://m8flow.example/timers">
  <bpmn:process id="Process_timer_runtime" isExecutable="true">
    <bpmn:startEvent id="StartEvent_1" />
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="TimerCatch_1" />
    <bpmn:intermediateCatchEvent id="TimerCatch_1" name="TimerCatch_1">
      <bpmn:incoming>Flow_1</bpmn:incoming>
      <bpmn:outgoing>Flow_2</bpmn:outgoing>
      <bpmn:timerEventDefinition>
""" + (
    "        <bpmn:timeDate xsi:type=\"bpmn:tFormalExpression\">"
    "'2099-01-01T00:00:00+00:00'</bpmn:timeDate>\n"
) + """
      </bpmn:timerEventDefinition>
    </bpmn:intermediateCatchEvent>
    <bpmn:sequenceFlow id="Flow_2" sourceRef="TimerCatch_1" targetRef="Task_1" />
    <bpmn:userTask id="Task_1" name="Review Request" />
    <bpmn:sequenceFlow id="Flow_3" sourceRef="Task_1" targetRef="EndEvent_1" />
    <bpmn:endEvent id="EndEvent_1" />
  </bpmn:process>
</bpmn:definitions>
"""

TIMER_START_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  id="Definitions_Timer_Start_Runtime"
  targetNamespace="http://m8flow.example/timers">
  <bpmn:process id="Process_timer_start_runtime" isExecutable="true">
    <bpmn:startEvent id="StartEvent_timer_start" name="StartEvent_timer_start">
      <bpmn:timerEventDefinition>
""" + (
    "        <bpmn:timeDate xsi:type=\"bpmn:tFormalExpression\">"
    "'2099-01-01T00:00:00+00:00'</bpmn:timeDate>\n"
) + """
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

TIMER_START_LANE_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  id="Definitions_Timer_Start_Lane_Runtime"
  targetNamespace="http://m8flow.example/timers">
  <bpmn:process id="Process_timer_start_lane_runtime" isExecutable="true">
    <bpmn:laneSet id="LaneSet_1">
      <bpmn:lane id="Lane_Operations" name="Operations">
        <bpmn:flowNodeRef>Task_1</bpmn:flowNodeRef>
      </bpmn:lane>
    </bpmn:laneSet>
    <bpmn:startEvent id="StartEvent_timer_start" name="StartEvent_timer_start">
      <bpmn:timerEventDefinition>
""" + (
    "        <bpmn:timeDate xsi:type=\"bpmn:tFormalExpression\">"
    "'2099-01-01T00:00:00+00:00'</bpmn:timeDate>\n"
) + """
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

TIMER_START_CYCLE_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  id="Definitions_Timer_Start_Cycle_Runtime"
  targetNamespace="http://m8flow.example/timers">
  <bpmn:process id="Process_timer_start_cycle_runtime" isExecutable="true">
    <bpmn:startEvent id="StartEvent_timer_cycle" name="StartEvent_timer_cycle">
      <bpmn:timerEventDefinition>
""" + (
    "        <bpmn:timeCycle xsi:type=\"bpmn:tFormalExpression\">"
    "'R3/PT5M'</bpmn:timeCycle>\n"
) + """
      </bpmn:timerEventDefinition>
    </bpmn:startEvent>
    <bpmn:sequenceFlow
      id="Flow_1"
      sourceRef="StartEvent_timer_cycle"
      targetRef="Task_1"
    />
    <bpmn:userTask id="Task_1" name="Review Request" />
    <bpmn:sequenceFlow id="Flow_2" sourceRef="Task_1" targetRef="EndEvent_1" />
    <bpmn:endEvent id="EndEvent_1" />
  </bpmn:process>
</bpmn:definitions>
"""

BOUNDARY_TIMER_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  id="Definitions_Boundary_Timer_Runtime"
  targetNamespace="http://m8flow.example/timers">
  <bpmn:process id="Process_boundary_timer_runtime" isExecutable="true">
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
""" + (
    "        <bpmn:timeDate xsi:type=\"bpmn:tFormalExpression\">"
    "'2099-01-01T00:00:00+00:00'</bpmn:timeDate>\n"
) + """
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


def test_run_due_scheduler_jobs_reschedules_stale_due_timer(
    session: Session,
) -> None:
    tenant, user = _seed_timer_actor(session)
    process_instance = _initialize_waiting_timer_process(
        session,
        tenant_id=tenant.id,
        user_id=user.id,
    )
    scheduler_job = _load_scheduler_job(session, tenant_id=tenant.id)
    expected_run_at = scheduler_job.run_at_in_seconds
    scheduler_job.run_at_in_seconds = 0
    session.flush()

    processed_count = api.run_due_scheduler_jobs(
        session,
        worker_id="inline-timer-worker",
        tenant_id=tenant.id,
    )

    assert processed_count == 1
    session.expire_all()

    process_instance = api.execute_query(
        session,
        api.GetProcessInstanceQuery(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
        ),
    )
    scheduler_job = _load_scheduler_job(session, tenant_id=tenant.id)
    assert process_instance.status == api.ProcessInstanceStatus.waiting.value
    assert scheduler_job.run_at_in_seconds == expected_run_at
    assert scheduler_job.locked_by is None
    assert scheduler_job.locked_at_in_seconds is None
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


def test_run_due_scheduler_jobs_advances_past_due_intermediate_timer(
    session: Session,
) -> None:
    tenant, user = _seed_timer_actor(session)
    process_instance = _initialize_waiting_timer_process(
        session,
        tenant_id=tenant.id,
        user_id=user.id,
    )
    timer_task = next(
        task
        for task in process_instance.tasks
        if task.task_definition.bpmn_identifier == "TimerCatch_1"
    )
    _set_waiting_timer_event_value(
        session,
        process_instance=process_instance,
        task_guid=timer_task.guid,
        event_value="1970-01-01T00:00:00+00:00",
    )
    scheduler_job = _load_scheduler_job(session, tenant_id=tenant.id)
    scheduler_job.run_at_in_seconds = 0
    session.flush()

    processed_count = api.run_due_scheduler_jobs(
        session,
        worker_id="inline-timer-worker",
        tenant_id=tenant.id,
    )

    assert processed_count == 1
    session.expire_all()

    process_instance = api.execute_query(
        session,
        api.GetProcessInstanceQuery(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
        ),
    )
    pending_tasks = api.execute_query(
        session,
        api.GetPendingTasksQuery(
            tenant_id=tenant.id,
            user_id=user.id,
        ),
    )
    scheduler_jobs = list(
        session.scalars(
            select(SchedulerJobModel).where(
                SchedulerJobModel.m8f_tenant_id == tenant.id
            )
        ).all()
    )

    assert process_instance.status == api.ProcessInstanceStatus.user_input_required
    assert len(pending_tasks) == 1
    assert pending_tasks[0].task_name == "Task_1"
    assert scheduler_jobs == []


def test_run_due_scheduler_jobs_executes_interrupting_boundary_timer(
    session: Session,
) -> None:
    tenant, user = _seed_timer_actor(session)
    definition = _import_process_definition(
        session,
        tenant_id=tenant.id,
        user_id=user.id,
        bpmn_identifier="boundary-timer-runtime-process",
        bpmn_name="Boundary Timer Runtime Process",
        source_bpmn_xml=BOUNDARY_TIMER_BPMN,
        properties_json={
            "lane_owners": {
                "Operations": [user.username],
            }
        },
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
    boundary_task = next(
        task
        for task in process_instance.tasks
        if task.task_definition.bpmn_identifier == "BoundaryEvent_timeout"
    )
    original_pending_tasks = api.execute_query(
        session,
        api.GetPendingTasksQuery(
            tenant_id=tenant.id,
            user_id=user.id,
        ),
    )
    original_review_human_task = next(
        task for task in original_pending_tasks if task.task_name == "Task_review"
    )
    _set_waiting_timer_event_value(
        session,
        process_instance=process_instance,
        task_guid=boundary_task.guid,
        event_value="1970-01-01T00:00:00+00:00",
    )
    scheduler_job = _load_scheduler_job(session, tenant_id=tenant.id)
    scheduler_job.run_at_in_seconds = 0
    session.flush()

    processed_count = api.run_due_scheduler_jobs(
        session,
        worker_id="inline-boundary-timer-worker",
        tenant_id=tenant.id,
    )

    assert processed_count == 1
    session.expire_all()

    process_instance = api.execute_query(
        session,
        api.GetProcessInstanceQuery(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
        ),
    )
    pending_tasks = api.execute_query(
        session,
        api.GetPendingTasksQuery(
            tenant_id=tenant.id,
            user_id=user.id,
        ),
    )
    scheduler_jobs = list(
        session.scalars(
            select(SchedulerJobModel).where(
                SchedulerJobModel.m8f_tenant_id == tenant.id
            )
        ).all()
    )
    events = api.execute_query(
        session,
        api.GetProcessInstanceEventsQuery(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
        ),
    )

    cancelled_review_human_task = session.get(
        HumanTaskModel,
        original_review_human_task.id,
    )
    assert cancelled_review_human_task is not None

    assert (
        process_instance.status
        == api.ProcessInstanceStatus.user_input_required.value
    )
    assert [task.task_name for task in pending_tasks] == ["Task_timeout"]
    assert pending_tasks[0].task_status == "READY"
    assert scheduler_jobs == []
    assert cancelled_review_human_task.completed is True
    assert cancelled_review_human_task.task_status == "CANCELLED"
    assert [event.event_type for event in events] == [
        api.ProcessInstanceEventType.process_instance_created.value,
        api.ProcessInstanceEventType.task_cancelled.value,
    ]


def test_run_due_scheduler_jobs_executes_scheduled_process_retry(
    session: Session,
) -> None:
    tenant, user, process_instance, human_task = _seed_errored_retry_process(session)
    api.execute_command(
        session,
        api.ScheduleProcessInstanceRetryCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            user_id=user.id,
            retry_at_in_seconds=200,
            scheduled_at_in_seconds=190,
        ),
    )

    processed_count = api.run_due_scheduler_jobs(
        session,
        now_in_seconds=200,
        worker_id="inline-retry-worker",
        tenant_id=tenant.id,
    )

    assert processed_count == 1
    session.expire_all()

    process_instance = api.execute_query(
        session,
        api.GetProcessInstanceQuery(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
        ),
    )
    pending_tasks = api.execute_query(
        session,
        api.GetPendingTasksQuery(
            tenant_id=tenant.id,
            user_id=user.id,
        ),
    )
    scheduler_jobs = list(
        session.scalars(
            select(SchedulerJobModel).where(
                SchedulerJobModel.m8f_tenant_id == tenant.id
            )
        ).all()
    )
    events = api.execute_query(
        session,
        api.GetProcessInstanceEventsQuery(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
        ),
    )

    assert process_instance.status == api.ProcessInstanceStatus.running.value
    assert process_instance.end_in_seconds is None
    assert len(pending_tasks) == 1
    assert pending_tasks[0].id == human_task.id
    assert pending_tasks[0].task_status == "READY"
    assert scheduler_jobs == []
    assert [event.event_type for event in events] == [
        api.ProcessInstanceEventType.process_instance_error.value,
        api.ProcessInstanceEventType.process_instance_retried.value,
    ]


def test_run_due_scheduler_jobs_executes_timer_start_job(
    session: Session,
) -> None:
    tenant, user = _seed_timer_actor(session)
    definition = _import_process_definition(
        session,
        tenant_id=tenant.id,
        user_id=user.id,
        bpmn_identifier="timer-start-runtime-process",
        bpmn_name="Timer Start Runtime Process",
        source_bpmn_xml=TIMER_START_BPMN,
    )
    scheduler_job = _load_scheduler_job(session, tenant_id=tenant.id)
    scheduler_job.run_at_in_seconds = 0
    session.flush()

    processed_count = api.run_due_scheduler_jobs(
        session,
        worker_id="inline-timer-start-worker",
        tenant_id=tenant.id,
    )

    assert processed_count == 1
    session.expire_all()

    process_instances = api.execute_query(
        session,
        api.ListProcessInstancesQuery(tenant_id=tenant.id),
    )
    scheduler_jobs = list(
        session.scalars(
            select(SchedulerJobModel).where(
                SchedulerJobModel.m8f_tenant_id == tenant.id
            )
        ).all()
    )

    assert len(process_instances) == 1
    assert process_instances[0].bpmn_process_definition_id == definition.id
    assert process_instances[0].process_initiator_id != user.id
    assert process_instances[0].status == api.ProcessInstanceStatus.user_input_required
    assert len(process_instances[0].human_tasks) == 1
    assert process_instances[0].human_tasks[0].task_status == "READY"
    assert scheduler_jobs == []


def test_run_due_scheduler_jobs_uses_definition_lane_owners_for_timer_start(
    session: Session,
) -> None:
    tenant, user = _seed_timer_actor(session)
    definition = _import_process_definition(
        session,
        tenant_id=tenant.id,
        user_id=user.id,
        bpmn_identifier="timer-start-lane-runtime-process",
        bpmn_name="Timer Start Lane Runtime Process",
        source_bpmn_xml=TIMER_START_LANE_BPMN,
        properties_json={
            "lane_owners": {
                "Operations": [user.username],
            }
        },
    )
    scheduler_job = _load_scheduler_job(session, tenant_id=tenant.id)
    scheduler_job.run_at_in_seconds = 0
    session.flush()

    processed_count = api.run_due_scheduler_jobs(
        session,
        worker_id="inline-timer-start-worker",
        tenant_id=tenant.id,
    )

    assert processed_count == 1
    session.expire_all()

    process_instances = api.execute_query(
        session,
        api.ListProcessInstancesQuery(tenant_id=tenant.id),
    )
    pending_tasks = api.execute_query(
        session,
        api.GetPendingTasksQuery(
            tenant_id=tenant.id,
            user_id=user.id,
        ),
    )

    assert len(process_instances) == 1
    assert process_instances[0].bpmn_process_definition_id == definition.id
    assert process_instances[0].human_tasks[0].lane_name == "Operations"
    assert process_instances[0].human_tasks[0].json_metadata["lane_owners"] == {
        "Operations": [user.username]
    }
    assert len(pending_tasks) == 1
    assert pending_tasks[0].process_instance_id == process_instances[0].id


def test_run_due_scheduler_jobs_executes_past_due_timer_start_definition(
    session: Session,
) -> None:
    tenant, user = _seed_timer_actor(session)
    definition = _import_process_definition(
        session,
        tenant_id=tenant.id,
        user_id=user.id,
        bpmn_identifier="timer-start-past-due-runtime-process",
        bpmn_name="Timer Start Past Due Runtime Process",
        source_bpmn_xml=TIMER_START_LANE_BPMN,
        properties_json={
            "lane_owners": {
                "Operations": [user.username],
            }
        },
    )
    scheduler_job = _load_scheduler_job(session, tenant_id=tenant.id)
    scheduler_job.run_at_in_seconds = 0
    definition.source_bpmn_xml = TIMER_START_LANE_BPMN.replace(
        "2099-01-01T00:00:00+00:00",
        "1970-01-01T00:00:00+00:00",
    )
    session.flush()

    processed_count = api.run_due_scheduler_jobs(
        session,
        worker_id="inline-timer-start-worker",
        tenant_id=tenant.id,
    )

    assert processed_count == 1
    session.expire_all()

    process_instances = api.execute_query(
        session,
        api.ListProcessInstancesQuery(tenant_id=tenant.id),
    )
    pending_tasks = api.execute_query(
        session,
        api.GetPendingTasksQuery(
            tenant_id=tenant.id,
            user_id=user.id,
        ),
    )

    assert len(process_instances) == 1
    assert process_instances[0].bpmn_process_definition_id == definition.id
    assert process_instances[0].status == api.ProcessInstanceStatus.user_input_required
    assert len(process_instances[0].human_tasks) == 1
    assert len(pending_tasks) == 1
    assert pending_tasks[0].process_instance_id == process_instances[0].id


def test_run_due_scheduler_jobs_reschedules_recurring_timer_start_job(
    session: Session,
) -> None:
    tenant, user = _seed_timer_actor(session)
    _import_process_definition(
        session,
        tenant_id=tenant.id,
        user_id=user.id,
        bpmn_identifier="timer-start-cycle-runtime-process",
        bpmn_name="Timer Start Cycle Runtime Process",
        source_bpmn_xml=TIMER_START_CYCLE_BPMN,
    )
    scheduler_job = _load_scheduler_job(session, tenant_id=tenant.id)
    scheduler_job.run_at_in_seconds = 0
    scheduler_job.payload_json = {
        "scheduled_from": "test",
        "timer_task": {
            "event_definition_type": "TimeCycleEventDefinition",
            "event_value": {
                "cycles": 3,
                "next": "1970-01-01T00:00:00+00:00",
                "duration": 300.0,
            },
            "run_at_in_seconds": 0,
            "task_guid": "timer-start-cycle-guid",
            "task_spec_name": "StartEvent_timer_cycle",
            "task_spec_type": "StartEvent",
        },
    }
    session.flush()

    processed_count = api.run_due_scheduler_jobs(
        session,
        worker_id="inline-timer-start-worker",
        tenant_id=tenant.id,
    )

    assert processed_count == 1
    session.expire_all()

    process_instances = api.execute_query(
        session,
        api.ListProcessInstancesQuery(tenant_id=tenant.id),
    )
    scheduler_job = _load_scheduler_job(session, tenant_id=tenant.id)
    human_tasks = list(
        session.scalars(
            select(HumanTaskModel).order_by(HumanTaskModel.id)
        ).all()
    )

    assert len(process_instances) == 1
    assert len(human_tasks) == 1
    assert human_tasks[0].process_instance_id == process_instances[0].id
    assert human_tasks[0].task_name == "Task_1"
    assert scheduler_job.job_type == "timer_start"
    assert scheduler_job.run_at_in_seconds == 300
    assert scheduler_job.payload_json == {
        "scheduled_from": "timer_start_runtime",
        "timer_task": {
            "event_definition_type": "TimeCycleEventDefinition",
            "event_value": {
                "cycles": 2,
                "duration": 300.0,
                "next": "1970-01-01T00:05:00+00:00",
            },
            "run_at_in_seconds": 300,
            "task_guid": "timer-start-cycle-guid",
            "task_spec_name": "StartEvent_timer_cycle",
            "task_spec_type": "StartEvent",
        },
    }


def test_run_due_scheduler_jobs_continues_batch_after_job_error(
    session: Session,
    monkeypatch,
) -> None:
    tenant, _user = _seed_timer_actor(session)
    first_job = upsert_scheduler_job(
        session,
        tenant_id=tenant.id,
        job_key="process_retry|pi:1|q:first",
        job_type="process_retry",
        process_instance_id=1,
        run_at_in_seconds=10,
        payload_json={"requested_by_user_id": 1},
        updated_at_in_seconds=10,
        created_at_in_seconds=10,
    )
    second_job = upsert_scheduler_job(
        session,
        tenant_id=tenant.id,
        job_key="process_retry|pi:2|q:second",
        job_type="process_retry",
        process_instance_id=2,
        run_at_in_seconds=10,
        payload_json={"requested_by_user_id": 1},
        updated_at_in_seconds=10,
        created_at_in_seconds=10,
    )

    executed_job_keys: list[str] = []

    def _execute_with_first_failure(session, *, job, occurred_at) -> None:
        executed_job_keys.append(job.job_key)
        if job.id == first_job.id:
            raise api.ValidationError("boom")
        delete_scheduler_job(
            session,
            tenant_id=job.m8f_tenant_id,
            job_key=job.job_key,
        )

    monkeypatch.setattr(
        scheduler_runtime,
        "_execute_claimed_scheduler_job",
        _execute_with_first_failure,
    )

    with pytest.raises(api.ValidationError, match="boom"):
        api.run_due_scheduler_jobs(
            session,
            now_in_seconds=10,
            worker_id="inline-failure-worker",
            tenant_id=tenant.id,
        )

    session.expire_all()
    first_job = session.get(SchedulerJobModel, first_job.id)
    second_job = session.get(SchedulerJobModel, second_job.id)
    assert first_job is not None
    assert second_job is None
    assert executed_job_keys == [
        "process_retry|pi:1|q:first",
        "process_retry|pi:2|q:second",
    ]
    assert first_job.locked_by is None
    assert first_job.locked_at_in_seconds is None


def test_run_due_scheduler_jobs_raises_summary_for_multiple_job_errors(
    session: Session,
    monkeypatch,
) -> None:
    tenant, _user = _seed_timer_actor(session)
    first_job = upsert_scheduler_job(
        session,
        tenant_id=tenant.id,
        job_key="process_retry|pi:11|q:first",
        job_type="process_retry",
        process_instance_id=11,
        run_at_in_seconds=10,
        payload_json={"requested_by_user_id": 1},
        updated_at_in_seconds=10,
        created_at_in_seconds=10,
    )
    second_job = upsert_scheduler_job(
        session,
        tenant_id=tenant.id,
        job_key="process_retry|pi:12|q:second",
        job_type="process_retry",
        process_instance_id=12,
        run_at_in_seconds=10,
        payload_json={"requested_by_user_id": 1},
        updated_at_in_seconds=10,
        created_at_in_seconds=10,
    )

    executed_job_keys: list[str] = []

    def _fail_both_jobs(session, *, job, occurred_at) -> None:
        executed_job_keys.append(job.job_key)
        if job.id == first_job.id:
            raise api.ValidationError("first failure")
        if job.id == second_job.id:
            raise api.NotFoundError("second failure")

    monkeypatch.setattr(
        scheduler_runtime,
        "_execute_claimed_scheduler_job",
        _fail_both_jobs,
    )

    with pytest.raises(api.BpmnCoreError) as exc_info:
        api.run_due_scheduler_jobs(
            session,
            now_in_seconds=10,
            worker_id="inline-failure-worker",
            tenant_id=tenant.id,
        )

    session.expire_all()
    first_job = session.get(SchedulerJobModel, first_job.id)
    second_job = session.get(SchedulerJobModel, second_job.id)
    assert first_job is not None
    assert second_job is not None
    assert executed_job_keys == [
        "process_retry|pi:11|q:first",
        "process_retry|pi:12|q:second",
    ]
    assert first_job.locked_by is None
    assert first_job.locked_at_in_seconds is None
    assert second_job.locked_by is None
    assert second_job.locked_at_in_seconds is None
    assert (
        str(exc_info.value)
        == "2 scheduler jobs failed in one batch: "
        "process_retry|pi:11|q:first: ValidationError: first failure, "
        "process_retry|pi:12|q:second: NotFoundError: second failure"
    )
    assert isinstance(exc_info.value.__cause__, api.ValidationError)


def _seed_timer_actor(
    session: Session,
) -> tuple[M8flowTenantModel, UserModel]:
    tenant = M8flowTenantModel(
        id="tenant-scheduler-runtime",
        name="Tenant Scheduler Runtime",
        slug="tenant-scheduler-runtime",
    )
    user = UserModel(
        username="timer-runtime-admin",
        email="timer-runtime-admin@example.com",
        service="http://localhost:7002/realms/tenant-scheduler-runtime",
        service_id="timer-runtime-admin-keycloak",
        display_name="Timer Runtime Admin",
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
    return tenant, user


def _initialize_waiting_timer_process(
    session: Session,
    *,
    tenant_id: str,
    user_id: int,
):
    definition = api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant_id,
            bpmn_identifier="timer-runtime-process",
            user_id=user_id,
            bpmn_name="Timer Runtime Process",
            source_bpmn_xml=INTERMEDIATE_TIMER_BPMN,
            created_at_in_seconds=10,
            updated_at_in_seconds=10,
        ),
    )
    return api.execute_command(
        session,
        api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=tenant_id,
            bpmn_process_definition_id=definition.id,
            process_initiator_id=user_id,
            started_at_in_seconds=20,
        ),
    )


def _import_process_definition(
    session: Session,
    *,
    tenant_id: str,
    user_id: int,
    bpmn_identifier: str,
    bpmn_name: str,
    source_bpmn_xml: str,
    properties_json: dict[str, object] | None = None,
) -> BpmnProcessDefinitionModel:
    return api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant_id,
            bpmn_identifier=bpmn_identifier,
            user_id=user_id,
            bpmn_name=bpmn_name,
            source_bpmn_xml=source_bpmn_xml,
            properties_json=properties_json,
            created_at_in_seconds=10,
            updated_at_in_seconds=10,
        ),
    )


def _load_scheduler_job(
    session: Session,
    *,
    tenant_id: str,
) -> SchedulerJobModel:
    scheduler_job = session.scalar(
        select(SchedulerJobModel).where(
            SchedulerJobModel.m8f_tenant_id == tenant_id
        )
    )
    assert scheduler_job is not None
    return scheduler_job


def _set_waiting_timer_event_value(
    session: Session,
    *,
    process_instance,
    task_guid: str,
    event_value: str,
) -> None:
    if process_instance.bpmn_process is None:
        raise AssertionError("Process instance is missing its BPMN process")

    json_data = session.get(JsonDataModel, process_instance.bpmn_process.json_data_hash)
    assert json_data is not None
    payload = dict(json_data.data)
    serialized_workflow = json.loads(payload[WORKFLOW_STATE_JSON_DATA_KEY])
    serialized_workflow["tasks"][task_guid]["internal_data"]["event_value"] = (
        event_value
    )
    payload[WORKFLOW_STATE_JSON_DATA_KEY] = json.dumps(serialized_workflow)
    process_instance.bpmn_process.json_data_hash = (
        JsonDataModel.create_or_update_from_payload(session, payload)
    )
    session.flush()


def _seed_errored_retry_process(
    session: Session,
) -> tuple[M8flowTenantModel, UserModel, ProcessInstanceModel, HumanTaskModel]:
    tenant = M8flowTenantModel(
        id="tenant-scheduler-retry-runtime",
        name="Tenant Scheduler Retry Runtime",
        slug="tenant-scheduler-retry-runtime",
    )
    user = UserModel(
        username="retry-runtime-admin",
        email="retry-runtime-admin@example.com",
        service="http://localhost:7002/realms/tenant-scheduler-retry-runtime",
        service_id="retry-runtime-admin-keycloak",
        display_name="Retry Runtime Admin",
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

    definition = BpmnProcessDefinitionModel(
        m8f_tenant_id=tenant.id,
        single_process_hash="retry-runtime-single",
        full_process_model_hash="retry-runtime-full",
        bpmn_identifier="retry-runtime-process",
        bpmn_name="Retry Runtime Process",
        properties_json={},
        created_at_in_seconds=10,
        updated_at_in_seconds=10,
    )
    session.add(definition)
    session.flush()

    bpmn_process = BpmnProcessModel(
        m8f_tenant_id=tenant.id,
        guid="retry-runtime-process-guid",
        bpmn_process_definition_id=definition.id,
        top_level_process_id=None,
        direct_parent_process_id=None,
        properties_json={"root": "retry-runtime-root"},
        json_data_hash="retry-runtime-json",
        start_in_seconds=20.0,
        end_in_seconds=130.0,
    )
    session.add(bpmn_process)
    session.flush()

    task_definition = TaskDefinitionModel(
        m8f_tenant_id=tenant.id,
        bpmn_process_definition_id=definition.id,
        bpmn_identifier="Task_retry_runtime",
        bpmn_name="Retry Runtime Task",
        typename="UserTask",
        properties_json={"allowGuest": False},
        created_at_in_seconds=30,
        updated_at_in_seconds=30,
    )
    session.add(task_definition)
    session.flush()

    process_instance = ProcessInstanceModel(
        m8f_tenant_id=tenant.id,
        process_model_identifier="retry-runtime-process",
        process_model_display_name="Retry Runtime Process",
        process_initiator_id=user.id,
        bpmn_process_definition_id=definition.id,
        bpmn_process_id=bpmn_process.id,
        status="error",
        start_in_seconds=20,
        end_in_seconds=130,
        task_updated_at_in_seconds=130,
        created_at_in_seconds=20,
        updated_at_in_seconds=130,
    )
    session.add(process_instance)
    session.flush()

    task = TaskModel(
        m8f_tenant_id=tenant.id,
        guid="retry-runtime-task-guid",
        bpmn_process_id=bpmn_process.id,
        process_instance_id=process_instance.id,
        task_definition_id=task_definition.id,
        state="TERMINATED",
        properties_json={"task_spec": "Retry Runtime Task"},
        json_data_hash="retry-runtime-task-json",
        python_env_data_hash="retry-runtime-task-env",
        start_in_seconds=20.0,
        end_in_seconds=130.0,
    )
    session.add(task)
    session.flush()

    future_task = FutureTaskModel(
        m8f_tenant_id=tenant.id,
        guid=task.guid,
        run_at_in_seconds=130,
        queued_to_run_at_in_seconds=130,
        completed=True,
        archived_for_process_instance_status=True,
        updated_at_in_seconds=130,
    )
    session.add(future_task)
    session.flush()

    human_task = HumanTaskModel(
        m8f_tenant_id=tenant.id,
        process_instance_id=process_instance.id,
        task_guid=task.guid,
        lane_assignment_id=None,
        completed_by_user_id=user.id,
        actual_owner_id=user.id,
        task_name="retry_runtime_task",
        task_title="Retry Runtime Task",
        task_type="User Task",
        task_status="TERMINATED",
        process_model_display_name=process_instance.process_model_display_name,
        bpmn_process_identifier=process_instance.process_model_identifier,
        lane_name="finance",
        json_metadata={"priority": "high"},
        completed=True,
        updated_at_in_seconds=130,
        created_at_in_seconds=20,
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

    api.record_process_instance_event(
        session,
        tenant_id=tenant.id,
        process_instance_id=process_instance.id,
        event_type=api.ProcessInstanceEventType.process_instance_error,
        timestamp=130.0,
        user_id=user.id,
    )
    session.flush()

    return tenant, user, process_instance, human_task
