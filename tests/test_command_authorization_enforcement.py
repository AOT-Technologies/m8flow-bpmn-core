from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.models.bpmn_process import BpmnProcessModel
from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel
from m8flow_bpmn_core.models.scheduler_job import SchedulerJobModel
from m8flow_bpmn_core.models.task import TaskModel
from m8flow_bpmn_core.models.task_definition import TaskDefinitionModel
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.models.work_item import WorkItemModel
from m8flow_bpmn_core.models.work_item_user import WorkItemUserModel
from m8flow_bpmn_core.services.authorization import (
    ROLE_ADMIN,
    ROLE_USER,
    ensure_v1_role,
)

VALIDATION_BPMN_PATH = (
    Path(__file__).with_name("fixtures") / "invoice_approval_poc.bpmn"
)


@dataclass(frozen=True, slots=True)
class DefinitionContext:
    tenant: M8flowTenantModel
    actor: UserModel
    definition: BpmnProcessDefinitionModel


@dataclass(frozen=True, slots=True)
class TaskContext:
    tenant: M8flowTenantModel
    actor: UserModel
    process_instance: ProcessInstanceModel
    human_task: WorkItemModel


def test_process_definition_import_requires_command_permission(
    session: Session,
) -> None:
    tenant = M8flowTenantModel(
        id="tenant-command-auth-import",
        name="Command Auth Import",
        slug="tenant-command-auth-import",
    )
    actor = UserModel(
        username="importer",
        email="importer@example.com",
        service=f"http://localhost:7002/realms/{tenant.slug}",
        service_id="importer-keycloak",
        display_name="Importer",
        created_at=datetime.fromtimestamp(1, UTC),
        updated_at=datetime.fromtimestamp(1, UTC),
    )
    session.add_all([tenant, actor])
    session.flush()

    bpmn_xml = VALIDATION_BPMN_PATH.read_text(encoding="utf-8")

    with pytest.raises(api.AuthorizationError, match="process_definition.import"):
        api.execute_command(
            session,
            api.ImportBpmnProcessDefinitionCommand(
                tenant_id=tenant.id,
                bpmn_identifier="invoice-approval-poc",
                user_id=actor.id,
                source_bpmn_xml=bpmn_xml,
            ),
        )

    ensure_v1_role(
        session,
        tenant_id=tenant.id,
        role_name=ROLE_ADMIN,
        user_ids=[actor.id],
    )
    definition = api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_identifier="invoice-approval-poc",
            user_id=actor.id,
            source_bpmn_xml=bpmn_xml,
        ),
    )

    assert definition.m8f_tenant_id == tenant.id
    assert definition.process_model_identifier == "invoice-approval-poc"


def test_process_start_requires_command_permission(session: Session) -> None:
    context = _seed_definition_context(session)

    with pytest.raises(api.AuthorizationError, match="process.start"):
        api.execute_command(
            session,
            api.InitializeProcessInstanceFromDefinitionCommand(
                tenant_id=context.tenant.id,
                bpmn_process_definition_id=context.definition.id,
                process_initiator_id=context.actor.id,
                summary="Unauthorized start",
                process_version=1,
                started_at=datetime.fromtimestamp(100, UTC),
                bpmn_process_id="invoice_approval_poc",
            ),
        )

    ensure_v1_role(
        session,
        tenant_id=context.tenant.id,
        role_name=ROLE_USER,
        user_ids=[context.actor.id],
    )
    process_instance = api.execute_command(
        session,
        api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=context.tenant.id,
            bpmn_process_definition_id=context.definition.id,
            process_initiator_id=context.actor.id,
            summary="Authorized start",
            process_version=1,
            started_at=datetime.fromtimestamp(110, UTC),
            bpmn_process_id="invoice_approval_poc",
        ),
    )

    assert process_instance.process_initiator_id == context.actor.id
    assert process_instance.m8f_tenant_id == context.tenant.id


def test_task_claim_requires_command_permission(session: Session) -> None:
    context = _seed_task_context(session)

    with pytest.raises(api.AuthorizationError, match="task.claim"):
        api.execute_command(
            session,
            api.ClaimTaskCommand(
                tenant_id=context.tenant.id,
                work_item_id=context.human_task.id,
                user_id=context.actor.id,
            ),
        )

    ensure_v1_role(
        session,
        tenant_id=context.tenant.id,
        role_name=ROLE_USER,
        user_ids=[context.actor.id],
    )
    claimed_task = api.execute_command(
        session,
        api.ClaimTaskCommand(
            tenant_id=context.tenant.id,
            work_item_id=context.human_task.id,
            user_id=context.actor.id,
        ),
    )

    assert claimed_task.actual_owner_id == context.actor.id
    assert claimed_task.task_status == "CLAIMED"


def test_task_completion_requires_command_permission(session: Session) -> None:
    context = _seed_task_context(session)

    with pytest.raises(api.AuthorizationError, match="task.complete"):
        api.execute_command(
            session,
            api.CompleteTaskCommand(
                tenant_id=context.tenant.id,
                work_item_id=context.human_task.id,
                user_id=context.actor.id,
                completed_at=datetime.fromtimestamp(120, UTC),
            ),
        )

    ensure_v1_role(
        session,
        tenant_id=context.tenant.id,
        role_name=ROLE_USER,
        user_ids=[context.actor.id],
    )
    api.execute_command(
        session,
        api.ClaimTaskCommand(
            tenant_id=context.tenant.id,
            work_item_id=context.human_task.id,
            user_id=context.actor.id,
        ),
    )
    completed_task = api.execute_command(
        session,
        api.CompleteTaskCommand(
            tenant_id=context.tenant.id,
            work_item_id=context.human_task.id,
            user_id=context.actor.id,
            completed_at=datetime.fromtimestamp(130, UTC),
        ),
    )

    assert completed_task.completed is True
    assert completed_task.completed_by_user_id == context.actor.id


def test_process_suspend_requires_command_permission(session: Session) -> None:
    context = _seed_task_context(session)

    with pytest.raises(api.AuthorizationError, match="process.suspend"):
        api.execute_command(
            session,
            api.SuspendProcessInstanceCommand(
                tenant_id=context.tenant.id,
                process_instance_id=context.process_instance.id,
                user_id=context.actor.id,
                suspended_at=datetime.fromtimestamp(140, UTC),
            ),
        )

    ensure_v1_role(
        session,
        tenant_id=context.tenant.id,
        role_name=ROLE_ADMIN,
        user_ids=[context.actor.id],
    )
    process_instance = api.execute_command(
        session,
        api.SuspendProcessInstanceCommand(
            tenant_id=context.tenant.id,
            process_instance_id=context.process_instance.id,
            user_id=context.actor.id,
            suspended_at=datetime.fromtimestamp(150, UTC),
        ),
    )

    assert process_instance.status == "suspended"


def test_process_resume_requires_command_permission(session: Session) -> None:
    context = _seed_task_context(session)
    context.process_instance.status = "suspended"
    session.flush()

    with pytest.raises(api.AuthorizationError, match="process.resume"):
        api.execute_command(
            session,
            api.ResumeProcessInstanceCommand(
                tenant_id=context.tenant.id,
                process_instance_id=context.process_instance.id,
                user_id=context.actor.id,
                resumed_at=datetime.fromtimestamp(160, UTC),
            ),
        )

    ensure_v1_role(
        session,
        tenant_id=context.tenant.id,
        role_name=ROLE_ADMIN,
        user_ids=[context.actor.id],
    )
    process_instance = api.execute_command(
        session,
        api.ResumeProcessInstanceCommand(
            tenant_id=context.tenant.id,
            process_instance_id=context.process_instance.id,
            user_id=context.actor.id,
            resumed_at=datetime.fromtimestamp(170, UTC),
        ),
    )

    assert process_instance.status == "running"


def test_process_retry_requires_command_permission(session: Session) -> None:
    context = _seed_task_context(session)
    context.process_instance.status = "error"
    context.process_instance.ended_at = datetime.fromtimestamp(180, UTC)
    session.flush()

    with pytest.raises(api.AuthorizationError, match="process.retry"):
        api.execute_command(
            session,
            api.RetryProcessInstanceCommand(
                tenant_id=context.tenant.id,
                process_instance_id=context.process_instance.id,
                user_id=context.actor.id,
                retried_at=datetime.fromtimestamp(190, UTC),
            ),
        )

    ensure_v1_role(
        session,
        tenant_id=context.tenant.id,
        role_name=ROLE_ADMIN,
        user_ids=[context.actor.id],
    )
    process_instance = api.execute_command(
        session,
        api.RetryProcessInstanceCommand(
            tenant_id=context.tenant.id,
            process_instance_id=context.process_instance.id,
            user_id=context.actor.id,
            retried_at=datetime.fromtimestamp(200, UTC),
        ),
    )

    assert process_instance.status == "running"


def test_process_retry_schedule_requires_command_permission(session: Session) -> None:
    context = _seed_task_context(session)
    context.process_instance.status = "error"
    context.process_instance.ended_at = datetime.fromtimestamp(180, UTC)
    session.flush()

    with pytest.raises(api.AuthorizationError, match="process.retry"):
        api.execute_command(
            session,
            api.ScheduleProcessInstanceRetryCommand(
                tenant_id=context.tenant.id,
                process_instance_id=context.process_instance.id,
                user_id=context.actor.id,
                retry_at=datetime.fromtimestamp(220, UTC),
                scheduled_at=datetime.fromtimestamp(190, UTC),
            ),
        )

    ensure_v1_role(
        session,
        tenant_id=context.tenant.id,
        role_name=ROLE_ADMIN,
        user_ids=[context.actor.id],
    )
    scheduler_job = api.execute_command(
        session,
        api.ScheduleProcessInstanceRetryCommand(
            tenant_id=context.tenant.id,
            process_instance_id=context.process_instance.id,
            user_id=context.actor.id,
            retry_at=datetime.fromtimestamp(220, UTC),
            scheduled_at=datetime.fromtimestamp(200, UTC),
        ),
    )

    assert isinstance(scheduler_job, SchedulerJobModel)
    assert scheduler_job.process_instance_id == context.process_instance.id
    assert scheduler_job.job_type == "process_retry"


def test_process_terminate_requires_command_permission(session: Session) -> None:
    context = _seed_task_context(session)

    with pytest.raises(api.AuthorizationError, match="process.terminate"):
        api.execute_command(
            session,
            api.TerminateProcessInstanceCommand(
                tenant_id=context.tenant.id,
                process_instance_id=context.process_instance.id,
                user_id=context.actor.id,
                terminated_at=datetime.fromtimestamp(210, UTC),
            ),
        )

    ensure_v1_role(
        session,
        tenant_id=context.tenant.id,
        role_name=ROLE_ADMIN,
        user_ids=[context.actor.id],
    )
    process_instance = api.execute_command(
        session,
        api.TerminateProcessInstanceCommand(
            tenant_id=context.tenant.id,
            process_instance_id=context.process_instance.id,
            user_id=context.actor.id,
            terminated_at=datetime.fromtimestamp(220, UTC),
        ),
    )

    assert process_instance.status == "terminated"


def _seed_definition_context(session: Session) -> DefinitionContext:
    tenant = M8flowTenantModel(
        id="tenant-command-auth-start",
        name="Command Auth Start",
        slug="tenant-command-auth-start",
    )
    actor = UserModel(
        username="starter",
        email="starter@example.com",
        service=f"http://localhost:7002/realms/{tenant.slug}",
        service_id="starter-keycloak",
        display_name="Starter",
        created_at=datetime.fromtimestamp(1, UTC),
        updated_at=datetime.fromtimestamp(1, UTC),
    )
    session.add_all([tenant, actor])
    session.flush()

    bpmn_xml = VALIDATION_BPMN_PATH.read_text(encoding="utf-8")
    definition = BpmnProcessDefinitionModel(
        m8f_tenant_id=tenant.id,
        process_xml_digest="test-definition-digest",
        bpmn_identifier="invoice-approval-poc",
        bpmn_name="Invoice Approval POC",
        properties_json={"version": 1},
        bpmn_version_control_type="git",
        bpmn_version_control_identifier="main",
        created_at=datetime.fromtimestamp(90, UTC),
        updated_at=datetime.fromtimestamp(90, UTC),
    )
    definition.source_bpmn_xml = bpmn_xml
    session.add(definition)
    session.flush()

    return DefinitionContext(
        tenant=tenant,
        actor=actor,
        definition=definition,
    )


def _seed_task_context(session: Session) -> TaskContext:
    tenant = M8flowTenantModel(
        id="tenant-command-auth-task",
        name="Command Auth Task",
        slug="tenant-command-auth-task",
    )
    actor = UserModel(
        username="task-user",
        email="task-user@example.com",
        service=f"http://localhost:7002/realms/{tenant.slug}",
        service_id="task-user-keycloak",
        display_name="Task User",
        created_at=datetime.fromtimestamp(1, UTC),
        updated_at=datetime.fromtimestamp(1, UTC),
    )
    session.add_all([tenant, actor])
    session.flush()

    definition = BpmnProcessDefinitionModel(
        m8f_tenant_id=tenant.id,
        process_xml_digest="test-definition-digest",
        bpmn_identifier="command-auth-process",
        bpmn_name="Command Auth Process",
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
        guid="command-auth-process-guid",
        bpmn_process_definition_id=definition.id,
        top_level_process_id=None,
        direct_parent_process_id=None,
        properties_json={"root": "approve_invoice"},
        json_data_hash="command-auth-process-json",
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
        process_model_identifier="command-auth-process",
        process_model_display_name="command-auth-process",
        process_initiator_id=actor.id,
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
        guid="command-auth-task-guid",
        bpmn_process_id=bpmn_process.id,
        process_instance_id=process_instance.id,
        task_definition_id=task_definition.id,
        state="READY",
        properties_json={"task_spec": "Approve Invoice"},
        json_data_hash="command-auth-task-json",
        python_env_data_hash="command-auth-task-env",
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
            user_id=actor.id,
            added_by="manual",
        )
    )
    session.flush()

    return TaskContext(
        tenant=tenant,
        actor=actor,
        process_instance=process_instance,
        human_task=human_task,
    )
