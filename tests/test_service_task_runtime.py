from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from SpiffWorkflow.util.task import TaskState
from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.db import session_scope
from m8flow_bpmn_core.models.scheduler_job import SchedulerJobModel
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.services.authorization import ROLE_ADMIN, ensure_v1_role
from m8flow_bpmn_core.services.workflow_runtime import (
    _prepare_process_instance_from_definition_in_session,
    _restore_workflow,
)

SERVICE_TASK_RUNTIME_BPMN_PATH = (
    Path(__file__).with_name("fixtures") / "service_task_runtime_poc.bpmn"
)


@dataclass
class RecordedServiceTaskRequest:
    operation_id: str
    parameters: dict[str, object]
    tenant_id: str
    process_instance_id: int | None
    process_definition_id: int | None
    task_guid: str | None
    task_name: str | None
    task_type: str | None


@dataclass
class DemoServiceTaskConnector:
    connector_key: str = "demo"
    recorded_requests: list[RecordedServiceTaskRequest] = field(default_factory=list)
    fail_operation_id: str | None = None

    def list_commands(self) -> tuple[api.ServiceTaskCommandDefinition, ...]:
        return (
            api.ServiceTaskCommandDefinition(
                connector_key=self.connector_key,
                command_name="PrepareReview",
            ),
            api.ServiceTaskCommandDefinition(
                connector_key=self.connector_key,
                command_name="FinalizeReview",
            ),
        )

    def execute(self, request: api.ServiceTaskRequest) -> api.ServiceTaskResult:
        if request.operation_id == self.fail_operation_id:
            raise RuntimeError(f"forced connector failure for {request.operation_id}")

        self.recorded_requests.append(
            RecordedServiceTaskRequest(
                operation_id=request.operation_id,
                parameters=dict(request.parameters or {}),
                tenant_id=request.context.tenant_id if request.context else "",
                process_instance_id=(
                    request.context.process_instance_id if request.context else None
                ),
                process_definition_id=(
                    request.context.process_definition_id if request.context else None
                ),
                task_guid=request.context.task_guid if request.context else None,
                task_name=request.context.task_name if request.context else None,
                task_type=request.context.task_type if request.context else None,
            )
        )
        return api.ServiceTaskResult(
            payload={
                "operation_id": request.operation_id,
                "parameters": dict(request.parameters or {}),
            }
        )


def test_service_tasks_execute_before_and_after_manual_task(
    session: Session,
) -> None:
    tenant, user = _seed_tenant_and_admin(session)
    bpmn_xml = SERVICE_TASK_RUNTIME_BPMN_PATH.read_text(encoding="utf-8")
    connector = DemoServiceTaskConnector()
    registry = api.ServiceTaskRegistry(connectors=(connector,))

    with api.service_task_registry_scope(registry):
        definition = api.execute_command(
            session,
            api.ImportBpmnProcessDefinitionCommand(
                tenant_id=tenant.id,
                bpmn_identifier="service-task-runtime-poc",
                user_id=user.id,
                bpmn_name="Service Task Runtime POC",
                source_bpmn_xml=bpmn_xml,
                properties_json={
                    "lane_owners": {"Operations": [user.username]},
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
                submission_metadata={"submission_message": "hello-service-task"},
                started_at_in_seconds=20,
            ),
        )

        pending_tasks = api.execute_query(
            session,
            api.GetPendingTasksQuery(tenant_id=tenant.id, user_id=user.id),
        )
        assert process_instance.status == api.ProcessInstanceStatus.user_input_required
        assert len(pending_tasks) == 1
        assert connector.recorded_requests == [
            RecordedServiceTaskRequest(
                operation_id="demo/PrepareReview",
                parameters={"submission_message": "hello-service-task"},
                tenant_id=tenant.id,
                process_instance_id=process_instance.id,
                process_definition_id=definition.id,
                task_guid=connector.recorded_requests[0].task_guid,
                task_name="Task_prepare",
                task_type="ServiceTask",
            )
        ]
        assert process_instance.workflow_state_json is not None
        assert "service_stage_one" in process_instance.workflow_state_json

        claimed_task = api.execute_command(
            session,
            api.ClaimTaskCommand(
                tenant_id=tenant.id,
                human_task_id=pending_tasks[0].id,
                user_id=user.id,
            ),
        )
        assert claimed_task.task_status == "CLAIMED"

        api.execute_command(
            session,
            api.CompleteTaskCommand(
                tenant_id=tenant.id,
                human_task_id=pending_tasks[0].id,
                user_id=user.id,
                completed_at_in_seconds=30,
                task_payload={"decision": "approved"},
            ),
        )

    refreshed_process_instance = api.execute_query(
        session,
        api.GetProcessInstanceQuery(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
        ),
    )
    assert refreshed_process_instance.status == api.ProcessInstanceStatus.complete
    assert refreshed_process_instance.workflow_state_json is not None
    assert "service_stage_two" in refreshed_process_instance.workflow_state_json
    assert connector.recorded_requests[1].operation_id == "demo/FinalizeReview"
    assert connector.recorded_requests[1].parameters == {"decision": "approved"}
    assert connector.recorded_requests[1].tenant_id == tenant.id
    assert connector.recorded_requests[1].process_instance_id == process_instance.id
    assert connector.recorded_requests[1].process_definition_id == definition.id
    assert connector.recorded_requests[1].task_name == "Task_finalize"
    assert connector.recorded_requests[1].task_type == "ServiceTask"
    assert (
        api.execute_query(
            session,
            api.GetPendingTasksQuery(tenant_id=tenant.id, user_id=user.id),
        )
        == []
    )


def test_missing_service_task_connector_surfaces_service_task_execution_error(
    session: Session,
) -> None:
    tenant, user = _seed_tenant_and_admin(session)
    bpmn_xml = SERVICE_TASK_RUNTIME_BPMN_PATH.read_text(encoding="utf-8")

    with api.service_task_registry_scope(api.ServiceTaskRegistry()):
        definition = api.execute_command(
            session,
            api.ImportBpmnProcessDefinitionCommand(
                tenant_id=tenant.id,
                bpmn_identifier="service-task-runtime-poc-missing-connector",
                user_id=user.id,
                bpmn_name="Service Task Runtime POC",
                source_bpmn_xml=bpmn_xml,
                properties_json={
                    "lane_owners": {"Operations": [user.username]},
                },
                created_at_in_seconds=10,
                updated_at_in_seconds=10,
            ),
        )
        with pytest.raises(api.ServiceTaskExecutionError) as exc_info:
            api.execute_command(
                session,
                api.InitializeProcessInstanceFromDefinitionCommand(
                    tenant_id=tenant.id,
                    bpmn_process_definition_id=definition.id,
                    process_initiator_id=user.id,
                    submission_metadata={"submission_message": "hello-service-task"},
                    started_at_in_seconds=20,
                ),
            )

    assert "demo/PrepareReview" in str(exc_info.value)
    assert exc_info.value.__cause__ is not None

    process_instances = api.execute_query(
        session,
        api.ListProcessInstancesQuery(tenant_id=tenant.id),
    )
    assert len(process_instances) == 1
    assert process_instances[0].status == api.ProcessInstanceStatus.error
    assert process_instances[0].start_in_seconds == 20
    assert process_instances[0].end_in_seconds == 20
    assert (
        api.execute_query(
            session,
            api.GetPendingTasksQuery(tenant_id=tenant.id, user_id=user.id),
        )
        == []
    )

    events = api.execute_query(
        session,
        api.GetProcessInstanceEventsQuery(
            tenant_id=tenant.id,
            process_instance_id=process_instances[0].id,
        ),
    )
    assert [event.event_type for event in events] == [
        api.ProcessInstanceEventType.task_failed.value,
        api.ProcessInstanceEventType.process_instance_error.value,
    ]

    restored_workflow = _restore_workflow(process_instances[0].workflow_state_json)
    errored_tasks = restored_workflow.get_tasks(state=TaskState.ERROR)
    assert [task.task_spec.name for task in errored_tasks] == ["Task_prepare"]


def test_service_task_failure_persists_error_state_across_session_scope_rollback(
    engine,
) -> None:
    if engine.dialect.name == "sqlite":
        pytest.xfail(
            "SQLite cannot reliably validate autonomous failure-state "
            "persistence while the outer failing transaction is still open."
        )

    bpmn_xml = SERVICE_TASK_RUNTIME_BPMN_PATH.read_text(encoding="utf-8")

    with session_scope(engine) as seed_session:
        tenant, user = _seed_tenant_and_admin(seed_session)
        definition = api.execute_command(
            seed_session,
            api.ImportBpmnProcessDefinitionCommand(
                tenant_id=tenant.id,
                bpmn_identifier="service-task-runtime-poc-session-scope-regression",
                user_id=user.id,
                bpmn_name="Service Task Runtime Session Scope Regression POC",
                source_bpmn_xml=bpmn_xml,
                properties_json={
                    "lane_owners": {"Operations": [user.username]},
                },
                created_at_in_seconds=10,
                updated_at_in_seconds=10,
            ),
        )
        tenant_id = tenant.id
        user_id = user.id
        definition_id = definition.id

    with api.service_task_registry_scope(api.ServiceTaskRegistry()):
        with pytest.raises(api.ServiceTaskExecutionError):
            with session_scope(engine) as runtime_session:
                api.execute_command(
                    runtime_session,
                    api.InitializeProcessInstanceFromDefinitionCommand(
                        tenant_id=tenant_id,
                        bpmn_process_definition_id=definition_id,
                        process_initiator_id=user_id,
                        submission_metadata={
                            "submission_message": "hello-service-task"
                        },
                        started_at_in_seconds=20,
                    ),
                )

    with session_scope(engine) as verification_session:
        process_instances = api.execute_query(
            verification_session,
            api.ListProcessInstancesQuery(tenant_id=tenant_id),
        )

        assert len(process_instances) == 1
        assert process_instances[0].status == api.ProcessInstanceStatus.error
        assert process_instances[0].start_in_seconds == 20
        assert process_instances[0].end_in_seconds == 20

        events = api.execute_query(
            verification_session,
            api.GetProcessInstanceEventsQuery(
                tenant_id=tenant_id,
                process_instance_id=process_instances[0].id,
            ),
        )
        assert [event.event_type for event in events] == [
            api.ProcessInstanceEventType.task_failed.value,
            api.ProcessInstanceEventType.process_instance_error.value,
        ]


def test_initialize_workflow_service_task_failure_persists_error_state_across_rollback(
    engine,
) -> None:
    if engine.dialect.name == "sqlite":
        pytest.xfail(
            "SQLite cannot reliably validate autonomous failure-state "
            "persistence while the outer failing transaction is still open."
        )

    bpmn_xml = SERVICE_TASK_RUNTIME_BPMN_PATH.read_text(encoding="utf-8")

    with session_scope(engine) as seed_session:
        tenant, user = _seed_tenant_and_admin(seed_session)
        definition = api.execute_command(
            seed_session,
            api.ImportBpmnProcessDefinitionCommand(
                tenant_id=tenant.id,
                bpmn_identifier=(
                    "service-task-runtime-poc-initialize-workflow-session-scope"
                ),
                user_id=user.id,
                bpmn_name="Service Task Runtime Initialize Workflow POC",
                source_bpmn_xml=bpmn_xml,
                properties_json={
                    "lane_owners": {"Operations": [user.username]},
                },
                created_at_in_seconds=10,
                updated_at_in_seconds=10,
            ),
        )
        process_model_identifier = (
            definition.process_model_identifier or str(definition.id)
        )
        process_instance, selected_process_id = (
            _prepare_process_instance_from_definition_in_session(
                seed_session,
                tenant_id=tenant.id,
                process_definition=definition,
                process_model_identifier=process_model_identifier,
                process_initiator_id=user.id,
                submission_metadata={
                    "submission_message": "hello-service-task"
                },
                summary="initialize-workflow-session-scope",
                process_version=1,
                started_at_in_seconds=15,
                bpmn_process_id=None,
            )
        )
        tenant_id = tenant.id
        process_instance_id = process_instance.id

    with api.service_task_registry_scope(api.ServiceTaskRegistry()):
        with pytest.raises(api.ServiceTaskExecutionError):
            with session_scope(engine) as runtime_session:
                api.execute_command(
                    runtime_session,
                    api.InitializeProcessInstanceWorkflowCommand(
                        tenant_id=tenant_id,
                        process_instance_id=process_instance_id,
                        bpmn_xml=bpmn_xml,
                        bpmn_process_id=selected_process_id,
                        started_at_in_seconds=20,
                    ),
                )

    with session_scope(engine) as verification_session:
        process_instance = api.execute_query(
            verification_session,
            api.GetProcessInstanceQuery(
                tenant_id=tenant_id,
                process_instance_id=process_instance_id,
            ),
        )

        assert process_instance.status == api.ProcessInstanceStatus.error
        assert process_instance.start_in_seconds == 20
        assert process_instance.end_in_seconds == 20

        events = api.execute_query(
            verification_session,
            api.GetProcessInstanceEventsQuery(
                tenant_id=tenant_id,
                process_instance_id=process_instance.id,
            ),
        )
        assert [event.event_type for event in events] == [
            api.ProcessInstanceEventType.task_failed.value,
            api.ProcessInstanceEventType.process_instance_error.value,
        ]


def test_retry_service_task_failure_persists_error_state_across_session_scope_rollback(
    engine,
) -> None:
    if engine.dialect.name == "sqlite":
        pytest.xfail(
            "SQLite cannot reliably validate autonomous failure-state "
            "persistence while the outer failing transaction is still open."
        )

    bpmn_xml = SERVICE_TASK_RUNTIME_BPMN_PATH.read_text(encoding="utf-8")

    with session_scope(engine) as seed_session:
        tenant, user = _seed_tenant_and_admin(seed_session)
        definition = api.execute_command(
            seed_session,
            api.ImportBpmnProcessDefinitionCommand(
                tenant_id=tenant.id,
                bpmn_identifier="service-task-runtime-poc-retry-session-scope",
                user_id=user.id,
                bpmn_name="Service Task Runtime Retry Session Scope POC",
                source_bpmn_xml=bpmn_xml,
                properties_json={
                    "lane_owners": {"Operations": [user.username]},
                },
                created_at_in_seconds=10,
                updated_at_in_seconds=10,
            ),
        )
        tenant_id = tenant.id
        user_id = user.id
        definition_id = definition.id

    failing_registry = api.ServiceTaskRegistry(
        connectors=(DemoServiceTaskConnector(fail_operation_id="demo/PrepareReview"),)
    )
    with api.service_task_registry_scope(failing_registry):
        with session_scope(engine) as initial_failure_session:
            with pytest.raises(api.ServiceTaskExecutionError):
                api.execute_command(
                    initial_failure_session,
                    api.InitializeProcessInstanceFromDefinitionCommand(
                        tenant_id=tenant_id,
                        bpmn_process_definition_id=definition_id,
                        process_initiator_id=user_id,
                        submission_metadata={
                            "submission_message": "hello-service-task"
                        },
                        started_at_in_seconds=20,
                    ),
                )

    with session_scope(engine) as verification_session:
        process_instances = api.execute_query(
            verification_session,
            api.ListProcessInstancesQuery(tenant_id=tenant_id),
        )
        assert len(process_instances) == 1
        process_instance_id = process_instances[0].id
        assert process_instances[0].status == api.ProcessInstanceStatus.error
        assert process_instances[0].end_in_seconds == 20

    with api.service_task_registry_scope(failing_registry):
        with pytest.raises(api.ServiceTaskExecutionError):
            with session_scope(engine) as retry_session:
                api.execute_command(
                    retry_session,
                    api.RetryProcessInstanceCommand(
                        tenant_id=tenant_id,
                        process_instance_id=process_instance_id,
                        user_id=user_id,
                        retried_at_in_seconds=30,
                    ),
                )

    with session_scope(engine) as verification_session:
        process_instance = api.execute_query(
            verification_session,
            api.GetProcessInstanceQuery(
                tenant_id=tenant_id,
                process_instance_id=process_instance_id,
            ),
        )
        assert process_instance.status == api.ProcessInstanceStatus.error
        assert process_instance.start_in_seconds == 20
        assert process_instance.end_in_seconds == 30

        events = api.execute_query(
            verification_session,
            api.GetProcessInstanceEventsQuery(
                tenant_id=tenant_id,
                process_instance_id=process_instance_id,
            ),
        )
        assert [event.event_type for event in events] == [
            api.ProcessInstanceEventType.task_failed.value,
            api.ProcessInstanceEventType.process_instance_error.value,
            api.ProcessInstanceEventType.task_failed.value,
            api.ProcessInstanceEventType.process_instance_error.value,
        ]

        restored_workflow = _restore_workflow(process_instance.workflow_state_json)
        errored_tasks = restored_workflow.get_tasks(state=TaskState.ERROR)
        assert [task.task_spec.name for task in errored_tasks] == ["Task_prepare"]


def test_retry_process_instance_reruns_failed_service_task(
    session: Session,
) -> None:
    tenant, user = _seed_tenant_and_admin(session)
    bpmn_xml = SERVICE_TASK_RUNTIME_BPMN_PATH.read_text(encoding="utf-8")
    connector = DemoServiceTaskConnector(fail_operation_id="demo/PrepareReview")
    registry = api.ServiceTaskRegistry(connectors=(connector,))

    with api.service_task_registry_scope(registry):
        definition = api.execute_command(
            session,
            api.ImportBpmnProcessDefinitionCommand(
                tenant_id=tenant.id,
                bpmn_identifier="service-task-runtime-poc-retry",
                user_id=user.id,
                bpmn_name="Service Task Runtime Retry POC",
                source_bpmn_xml=bpmn_xml,
                properties_json={
                    "lane_owners": {"Operations": [user.username]},
                },
                created_at_in_seconds=10,
                updated_at_in_seconds=10,
            ),
        )
        with pytest.raises(api.ServiceTaskExecutionError):
            api.execute_command(
                session,
                api.InitializeProcessInstanceFromDefinitionCommand(
                    tenant_id=tenant.id,
                    bpmn_process_definition_id=definition.id,
                    process_initiator_id=user.id,
                    submission_metadata={"submission_message": "retry-me"},
                    started_at_in_seconds=20,
                ),
            )

    process_instance = api.execute_query(
        session,
        api.ListProcessInstancesQuery(tenant_id=tenant.id),
    )[0]
    connector.fail_operation_id = None

    with api.service_task_registry_scope(registry):
        retried_process_instance = api.execute_command(
            session,
            api.RetryProcessInstanceCommand(
                tenant_id=tenant.id,
                process_instance_id=process_instance.id,
                user_id=user.id,
                retried_at_in_seconds=30,
            ),
        )

    pending_tasks = api.execute_query(
        session,
        api.GetPendingTasksQuery(tenant_id=tenant.id, user_id=user.id),
    )
    events = api.execute_query(
        session,
        api.GetProcessInstanceEventsQuery(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
        ),
    )

    assert (
        retried_process_instance.status
        == api.ProcessInstanceStatus.user_input_required
    )
    assert retried_process_instance.end_in_seconds is None
    assert len(pending_tasks) == 1
    assert pending_tasks[0].task_name == "Task_review"
    assert [request.operation_id for request in connector.recorded_requests] == [
        "demo/PrepareReview"
    ]
    assert connector.recorded_requests[0].parameters == {
        "submission_message": "retry-me"
    }
    assert [event.event_type for event in events] == [
        api.ProcessInstanceEventType.task_failed.value,
        api.ProcessInstanceEventType.process_instance_error.value,
        api.ProcessInstanceEventType.process_instance_retried.value,
    ]

    restored_workflow = _restore_workflow(retried_process_instance.workflow_state_json)
    assert restored_workflow.get_tasks(state=TaskState.ERROR) == []


def test_scheduled_retry_reruns_failed_service_task(
    session: Session,
) -> None:
    tenant, user = _seed_tenant_and_admin(session)
    bpmn_xml = SERVICE_TASK_RUNTIME_BPMN_PATH.read_text(encoding="utf-8")
    connector = DemoServiceTaskConnector(fail_operation_id="demo/PrepareReview")
    registry = api.ServiceTaskRegistry(connectors=(connector,))

    with api.service_task_registry_scope(registry):
        definition = api.execute_command(
            session,
            api.ImportBpmnProcessDefinitionCommand(
                tenant_id=tenant.id,
                bpmn_identifier="service-task-runtime-poc-scheduled-retry",
                user_id=user.id,
                bpmn_name="Service Task Runtime Scheduled Retry POC",
                source_bpmn_xml=bpmn_xml,
                properties_json={
                    "lane_owners": {"Operations": [user.username]},
                },
                created_at_in_seconds=10,
                updated_at_in_seconds=10,
            ),
        )
        with pytest.raises(api.ServiceTaskExecutionError):
            api.execute_command(
                session,
                api.InitializeProcessInstanceFromDefinitionCommand(
                    tenant_id=tenant.id,
                    bpmn_process_definition_id=definition.id,
                    process_initiator_id=user.id,
                    submission_metadata={"submission_message": "scheduled-retry"},
                    started_at_in_seconds=20,
                ),
            )

    process_instance = api.execute_query(
        session,
        api.ListProcessInstancesQuery(tenant_id=tenant.id),
    )[0]
    api.execute_command(
        session,
        api.ScheduleProcessInstanceRetryCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            user_id=user.id,
            retry_at_in_seconds=40,
            scheduled_at_in_seconds=30,
        ),
    )
    connector.fail_operation_id = None

    with api.service_task_registry_scope(registry):
        processed_count = api.run_due_scheduler_jobs(
            session,
            now_in_seconds=40,
            worker_id="service-task-retry-worker",
            tenant_id=tenant.id,
        )

    refreshed_process_instance = api.execute_query(
        session,
        api.GetProcessInstanceQuery(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
        ),
    )
    pending_tasks = api.execute_query(
        session,
        api.GetPendingTasksQuery(tenant_id=tenant.id, user_id=user.id),
    )
    events = api.execute_query(
        session,
        api.GetProcessInstanceEventsQuery(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
        ),
    )

    assert processed_count == 1
    assert (
        refreshed_process_instance.status
        == api.ProcessInstanceStatus.user_input_required
    )
    assert refreshed_process_instance.end_in_seconds is None
    assert len(pending_tasks) == 1
    assert pending_tasks[0].task_name == "Task_review"
    assert [request.operation_id for request in connector.recorded_requests] == [
        "demo/PrepareReview"
    ]
    assert connector.recorded_requests[0].parameters == {
        "submission_message": "scheduled-retry"
    }
    assert [event.event_type for event in events] == [
        api.ProcessInstanceEventType.task_failed.value,
        api.ProcessInstanceEventType.process_instance_error.value,
        api.ProcessInstanceEventType.process_instance_retried.value,
    ]


def test_failed_scheduled_retry_keeps_scheduler_job_available_for_retry(
    session: Session,
) -> None:
    tenant, user = _seed_tenant_and_admin(session)
    bpmn_xml = SERVICE_TASK_RUNTIME_BPMN_PATH.read_text(encoding="utf-8")
    connector = DemoServiceTaskConnector(fail_operation_id="demo/PrepareReview")
    registry = api.ServiceTaskRegistry(connectors=(connector,))

    with api.service_task_registry_scope(registry):
        definition = api.execute_command(
            session,
            api.ImportBpmnProcessDefinitionCommand(
                tenant_id=tenant.id,
                bpmn_identifier="service-task-runtime-poc-scheduled-retry-failure",
                user_id=user.id,
                bpmn_name="Service Task Runtime Scheduled Retry Failure POC",
                source_bpmn_xml=bpmn_xml,
                properties_json={
                    "lane_owners": {"Operations": [user.username]},
                },
                created_at_in_seconds=10,
                updated_at_in_seconds=10,
            ),
        )
        with pytest.raises(api.ServiceTaskExecutionError):
            api.execute_command(
                session,
                api.InitializeProcessInstanceFromDefinitionCommand(
                    tenant_id=tenant.id,
                    bpmn_process_definition_id=definition.id,
                    process_initiator_id=user.id,
                    submission_metadata={"submission_message": "scheduled-retry"},
                    started_at_in_seconds=20,
                ),
            )

    process_instance = api.execute_query(
        session,
        api.ListProcessInstancesQuery(tenant_id=tenant.id),
    )[0]
    scheduler_job = api.execute_command(
        session,
        api.ScheduleProcessInstanceRetryCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            user_id=user.id,
            retry_at_in_seconds=40,
            scheduled_at_in_seconds=30,
        ),
    )

    with api.service_task_registry_scope(registry):
        with pytest.raises(api.ServiceTaskExecutionError):
            api.run_due_scheduler_jobs(
                session,
                now_in_seconds=40,
                worker_id="service-task-retry-worker",
                tenant_id=tenant.id,
            )

    session.expire_all()
    refreshed_process_instance = api.execute_query(
        session,
        api.GetProcessInstanceQuery(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
        ),
    )
    persisted_scheduler_job = session.scalar(
        select(SchedulerJobModel).where(
            SchedulerJobModel.id == scheduler_job.id,
        )
    )
    events = api.execute_query(
        session,
        api.GetProcessInstanceEventsQuery(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
        ),
    )

    assert refreshed_process_instance.status == api.ProcessInstanceStatus.error
    assert persisted_scheduler_job is not None
    assert persisted_scheduler_job.job_key == scheduler_job.job_key
    assert persisted_scheduler_job.locked_by is None
    assert persisted_scheduler_job.locked_at_in_seconds is None
    assert [event.event_type for event in events] == [
        api.ProcessInstanceEventType.task_failed.value,
        api.ProcessInstanceEventType.process_instance_error.value,
        api.ProcessInstanceEventType.task_failed.value,
        api.ProcessInstanceEventType.process_instance_error.value,
    ]


def _seed_tenant_and_admin(
    session: Session,
) -> tuple[M8flowTenantModel, UserModel]:
    tenant = M8flowTenantModel(
        id="tenant-service-task-runtime",
        name="Tenant Service Task Runtime",
        slug="tenant-service-task-runtime",
    )
    user = UserModel(
        username="service-admin",
        email="service-admin@example.com",
        service="http://localhost:7002/realms/tenant-service-task-runtime",
        service_id="service-admin-keycloak",
        display_name="Service Admin",
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
