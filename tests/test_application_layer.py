from __future__ import annotations

import hashlib

import pytest
from sqlalchemy.orm import Session

from m8flow_bpmn_core.application import (
    ClaimTaskCommand,
    CompleteTaskCommand,
    ErrorProcessInstanceCommand,
    GetPendingTasksCommand,
    GetPendingTasksQuery,
    GetProcessInstanceEventsQuery,
    GetProcessInstanceMetadataQuery,
    GetProcessInstanceQuery,
    ImportBpmnProcessDefinitionCommand,
    ListErrorProcessInstancesQuery,
    ListSuspendedProcessInstancesQuery,
    ListTerminatedProcessInstancesQuery,
    RecordProcessInstanceEventCommand,
    ResumeProcessInstanceCommand,
    RetryProcessInstanceCommand,
    SuspendProcessInstanceCommand,
    TerminateProcessInstanceCommand,
    UpsertProcessInstanceMetadataCommand,
    execute_command,
    execute_query,
)
from m8flow_bpmn_core.models.bpmn_process import BpmnProcessModel
from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_bpmn_core.models.future_task import FutureTaskModel
from m8flow_bpmn_core.models.human_task import HumanTaskModel
from m8flow_bpmn_core.models.human_task_user import HumanTaskUserModel
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel
from m8flow_bpmn_core.models.process_instance_event import ProcessInstanceEventType
from m8flow_bpmn_core.models.task import TaskModel
from m8flow_bpmn_core.models.task_definition import TaskDefinitionModel
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel


def test_application_layer_handles_tasks_events_and_metadata(
    session: Session,
) -> None:
    tenant, user, process_instance, task, human_task = _seed_process_instance(session)

    pending_tasks = execute_query(
        session, GetPendingTasksQuery(tenant_id=tenant.id, user_id=user.id)
    )
    assert [task.id for task in pending_tasks] == [human_task.id]

    claimed_task = execute_command(
        session,
        ClaimTaskCommand(
            tenant_id=tenant.id,
            human_task_id=human_task.id,
            user_id=user.id,
        ),
    )
    assert claimed_task.actual_owner_id == user.id
    assert claimed_task.task_status == "CLAIMED"

    event = execute_command(
        session,
        RecordProcessInstanceEventCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            event_type=ProcessInstanceEventType.process_instance_created,
            task_guid=task.guid,
            user_id=user.id,
            timestamp=100.25,
        ),
    )
    assert event.event_type == ProcessInstanceEventType.process_instance_created.value
    assert float(event.timestamp) == 100.25

    metadata = execute_command(
        session,
        UpsertProcessInstanceMetadataCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            key="approval_state",
            value="pending",
            updated_at_in_seconds=101,
            created_at_in_seconds=100,
        ),
    )
    assert metadata.value == "pending"
    assert metadata.created_at_in_seconds == 100

    metadata = execute_command(
        session,
        UpsertProcessInstanceMetadataCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            key="approval_state",
            value="approved",
            updated_at_in_seconds=120,
        ),
    )
    assert metadata.value == "approved"
    assert metadata.updated_at_in_seconds == 120

    events = execute_query(
        session,
        GetProcessInstanceEventsQuery(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
        ),
    )
    assert [item.id for item in events] == [event.id]

    metadata_rows = execute_query(
        session,
        GetProcessInstanceMetadataQuery(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
        ),
    )
    assert [item.key for item in metadata_rows] == ["approval_state"]
    assert metadata_rows[0].value == "approved"

    completed_task = execute_command(
        session,
        CompleteTaskCommand(
            tenant_id=tenant.id,
            human_task_id=human_task.id,
            user_id=user.id,
            completed_at_in_seconds=130,
        ),
    )
    assert completed_task.completed is True
    assert completed_task.task_status == "COMPLETED"

    assert execute_query(
        session, GetPendingTasksQuery(tenant_id=tenant.id, user_id=user.id)
    ) == []


def test_application_layer_supports_connection_transaction_control(
    engine,
) -> None:
    tenant_id = ""
    process_instance_id = 0

    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(
            bind=connection,
            autoflush=False,
            expire_on_commit=False,
        )
        try:
            tenant, user, process_instance, _task, human_task = _seed_process_instance(
                session
            )
            tenant_id = tenant.id
            process_instance_id = process_instance.id

            pending_tasks = execute_command(
                connection,
                GetPendingTasksCommand(
                    tenant_id=tenant.id,
                    user_id=user.id,
                ),
            )
            assert [task.id for task in pending_tasks] == [human_task.id]

            current_process_instance = execute_query(
                connection,
                GetProcessInstanceQuery(
                    tenant_id=tenant.id,
                    process_instance_id=process_instance.id,
                ),
            )
            assert current_process_instance.status == "running"

            claimed_task = execute_command(
                connection,
                ClaimTaskCommand(
                    tenant_id=tenant.id,
                    human_task_id=human_task.id,
                    user_id=user.id,
                ),
            )
            assert claimed_task.actual_owner_id == user.id
            assert claimed_task.task_status == "CLAIMED"

            completed_task = execute_command(
                connection,
                CompleteTaskCommand(
                    tenant_id=tenant.id,
                    human_task_id=human_task.id,
                    user_id=user.id,
                ),
            )
            assert completed_task.completed is True
            assert completed_task.task_status == "COMPLETED"
        finally:
            try:
                transaction.rollback()
            finally:
                session.close()

    with Session(bind=engine) as verify_session:
        with pytest.raises(LookupError):
            execute_query(
                verify_session,
                GetProcessInstanceQuery(
                    tenant_id=tenant_id,
                    process_instance_id=process_instance_id,
                ),
            )


def test_process_lifecycle_commands_and_queries(session: Session) -> None:
    tenant, user, process_instance, _task, human_task = _seed_process_instance(session)

    suspended_process_instance = execute_command(
        session,
        SuspendProcessInstanceCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            user_id=user.id,
            suspended_at_in_seconds=200,
        ),
    )
    assert suspended_process_instance.status == "suspended"
    assert suspended_process_instance.updated_at_in_seconds == 200
    assert (
        execute_query(
            session,
            GetProcessInstanceQuery(
                tenant_id=tenant.id, process_instance_id=process_instance.id
            ),
        ).status
        == "suspended"
    )
    assert [
        item.id
        for item in execute_query(
            session,
            ListSuspendedProcessInstancesQuery(tenant_id=tenant.id),
        )
    ] == [process_instance.id]
    assert [
        event.event_type
        for event in execute_query(
            session,
            GetProcessInstanceEventsQuery(
                tenant_id=tenant.id,
                process_instance_id=process_instance.id,
            ),
        )
    ] == ["process_instance_suspended"]

    resumed_process_instance = execute_command(
        session,
        ResumeProcessInstanceCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            user_id=user.id,
            resumed_at_in_seconds=250,
        ),
    )
    assert resumed_process_instance.status == "running"
    assert resumed_process_instance.updated_at_in_seconds == 250

    terminated_process_instance = execute_command(
        session,
        TerminateProcessInstanceCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            user_id=user.id,
            terminated_at_in_seconds=300,
        ),
    )
    assert terminated_process_instance.status == "terminated"
    assert terminated_process_instance.end_in_seconds == 300
    assert terminated_process_instance.updated_at_in_seconds == 300
    assert terminated_process_instance.tasks[0].state == "TERMINATED"
    assert terminated_process_instance.tasks[0].future_task is not None
    assert terminated_process_instance.tasks[0].future_task.completed is True
    assert (
        terminated_process_instance.tasks[0]
        .future_task.archived_for_process_instance_status
        is True
    )
    assert terminated_process_instance.human_tasks[0].completed is True
    assert terminated_process_instance.human_tasks[0].task_status == "TERMINATED"
    assert execute_query(
        session, GetPendingTasksQuery(tenant_id=tenant.id, user_id=user.id)
    ) == []
    assert [
        item.id
        for item in execute_query(
            session,
            ListTerminatedProcessInstancesQuery(tenant_id=tenant.id),
        )
    ] == [process_instance.id]

    assert [
        event.event_type
        for event in execute_query(
            session,
            GetProcessInstanceEventsQuery(
                tenant_id=tenant.id,
                process_instance_id=process_instance.id,
            ),
        )
    ] == [
        "process_instance_suspended",
        "process_instance_resumed",
        "process_instance_terminated",
    ]


def test_error_and_retry_lifecycle_commands(session: Session) -> None:
    tenant, user, process_instance, _task, human_task = _seed_process_instance(session)

    errored_process_instance = execute_command(
        session,
        ErrorProcessInstanceCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            user_id=user.id,
            errored_at_in_seconds=275,
        ),
    )
    assert errored_process_instance.status == "error"
    assert errored_process_instance.end_in_seconds == 275
    assert errored_process_instance.updated_at_in_seconds == 275
    assert errored_process_instance.tasks[0].state == "TERMINATED"
    assert errored_process_instance.tasks[0].future_task is not None
    assert errored_process_instance.tasks[0].future_task.completed is True
    assert (
        errored_process_instance.tasks[0]
        .future_task.archived_for_process_instance_status
        is True
    )
    assert errored_process_instance.human_tasks[0].completed is True
    assert errored_process_instance.human_tasks[0].task_status == "TERMINATED"
    assert [
        item.id
        for item in execute_query(
            session, ListErrorProcessInstancesQuery(tenant_id=tenant.id)
        )
    ] == [process_instance.id]
    assert [
        event.event_type
        for event in execute_query(
            session,
            GetProcessInstanceEventsQuery(
                tenant_id=tenant.id,
                process_instance_id=process_instance.id,
            ),
        )
    ] == ["process_instance_error"]

    retried_process_instance = execute_command(
        session,
        RetryProcessInstanceCommand(
            tenant_id=tenant.id,
            process_instance_id=process_instance.id,
            user_id=user.id,
            retried_at_in_seconds=290,
        ),
    )
    assert retried_process_instance.status == "running"
    assert retried_process_instance.end_in_seconds is None
    assert retried_process_instance.updated_at_in_seconds == 290
    assert retried_process_instance.tasks[0].state == "READY"
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
        for item in execute_query(
            session, GetPendingTasksQuery(tenant_id=tenant.id, user_id=user.id)
        )
    ] == [human_task.id]
    assert execute_query(
        session, ListErrorProcessInstancesQuery(tenant_id=tenant.id)
    ) == []
    assert [
        event.event_type
        for event in execute_query(
            session,
            GetProcessInstanceEventsQuery(
                tenant_id=tenant.id,
                process_instance_id=process_instance.id,
            ),
        )
    ] == ["process_instance_error", "process_instance_retried"]


def test_application_layer_imports_bpmn_process_definition(session: Session) -> None:
    tenant = M8flowTenantModel(
        id="tenant-definition",
        name="Tenant Definition",
        slug="tenant-definition",
    )
    session.add(tenant)
    session.flush()

    bpmn_xml = "<definitions><process id='Process_import_1'/></definitions>"
    dmn_xml = "<definitions><decision id='Decision_import_1'/></definitions>"
    definition = execute_command(
        session,
        ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_identifier="imported-process",
            bpmn_name="Imported Process",
            source_bpmn_xml=bpmn_xml,
            source_dmn_xml=dmn_xml,
            properties_json={"source": "application-layer-test", "version": 1},
            bpmn_version_control_type="git",
            bpmn_version_control_identifier="main",
            created_at_in_seconds=10,
            updated_at_in_seconds=20,
        ),
    )
    assert definition.id is not None
    assert definition.bpmn_identifier == "imported-process"
    assert definition.bpmn_name == "Imported Process"
    assert definition.source_bpmn_xml == bpmn_xml
    assert definition.source_dmn_xml == dmn_xml
    assert definition.full_process_model_hash == hashlib.sha256(
        bpmn_xml.encode("utf-8")
    ).hexdigest()
    assert definition.single_process_hash == hashlib.sha256(
        f"single::{bpmn_xml}".encode()
    ).hexdigest()
    assert definition.properties_json == {
        "source": "application-layer-test",
        "version": 1,
    }

    imported_definition = execute_command(
        session,
        ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_identifier="imported-process",
            bpmn_name="Imported Process",
            source_bpmn_xml=bpmn_xml,
            source_dmn_xml=dmn_xml,
            properties_json={"source": "application-layer-test", "version": 1},
            bpmn_version_control_type="git",
            bpmn_version_control_identifier="main",
            created_at_in_seconds=10,
            updated_at_in_seconds=20,
        ),
    )
    assert imported_definition.id == definition.id


def _seed_process_instance(
    session: Session,
) -> tuple[
    M8flowTenantModel,
    UserModel,
    ProcessInstanceModel,
    TaskModel,
    HumanTaskModel,
]:
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

    return tenant, user, process_instance, task, human_task
