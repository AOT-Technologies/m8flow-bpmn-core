from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
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
    human_task: HumanTaskModel


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
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
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
                started_at_in_seconds=100,
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
            started_at_in_seconds=110,
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
                human_task_id=context.human_task.id,
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
            human_task_id=context.human_task.id,
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
                human_task_id=context.human_task.id,
                user_id=context.actor.id,
                completed_at_in_seconds=120,
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
            human_task_id=context.human_task.id,
            user_id=context.actor.id,
        ),
    )
    completed_task = api.execute_command(
        session,
        api.CompleteTaskCommand(
            tenant_id=context.tenant.id,
            human_task_id=context.human_task.id,
            user_id=context.actor.id,
            completed_at_in_seconds=130,
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
                suspended_at_in_seconds=140,
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
            suspended_at_in_seconds=150,
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
                resumed_at_in_seconds=160,
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
            resumed_at_in_seconds=170,
        ),
    )

    assert process_instance.status == "running"


def test_process_retry_requires_command_permission(session: Session) -> None:
    context = _seed_task_context(session)
    context.process_instance.status = "error"
    context.process_instance.end_in_seconds = 180
    session.flush()

    with pytest.raises(api.AuthorizationError, match="process.retry"):
        api.execute_command(
            session,
            api.RetryProcessInstanceCommand(
                tenant_id=context.tenant.id,
                process_instance_id=context.process_instance.id,
                user_id=context.actor.id,
                retried_at_in_seconds=190,
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
            retried_at_in_seconds=200,
        ),
    )

    assert process_instance.status == "running"


def test_process_terminate_requires_command_permission(session: Session) -> None:
    context = _seed_task_context(session)

    with pytest.raises(api.AuthorizationError, match="process.terminate"):
        api.execute_command(
            session,
            api.TerminateProcessInstanceCommand(
                tenant_id=context.tenant.id,
                process_instance_id=context.process_instance.id,
                user_id=context.actor.id,
                terminated_at_in_seconds=210,
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
            terminated_at_in_seconds=220,
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
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    session.add_all([tenant, actor])
    session.flush()

    bpmn_xml = VALIDATION_BPMN_PATH.read_text(encoding="utf-8")
    definition = BpmnProcessDefinitionModel(
        m8f_tenant_id=tenant.id,
        single_process_hash=hashlib.sha256(f"single::{bpmn_xml}".encode()).hexdigest(),
        full_process_model_hash=hashlib.sha256(bpmn_xml.encode("utf-8")).hexdigest(),
        bpmn_identifier="invoice-approval-poc",
        bpmn_name="Invoice Approval POC",
        properties_json={"version": 1},
        bpmn_version_control_type="git",
        bpmn_version_control_identifier="main",
        created_at_in_seconds=90,
        updated_at_in_seconds=90,
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
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    session.add_all([tenant, actor])
    session.flush()

    definition = BpmnProcessDefinitionModel(
        m8f_tenant_id=tenant.id,
        single_process_hash="command-auth-single",
        full_process_model_hash="command-auth-full",
        bpmn_identifier="command-auth-process",
        bpmn_name="Command Auth Process",
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
        created_at_in_seconds=950,
        updated_at_in_seconds=950,
    )
    session.add(task_definition)
    session.flush()

    process_instance = ProcessInstanceModel(
        m8f_tenant_id=tenant.id,
        process_model_identifier="command-auth-process",
        process_model_display_name="Command Auth Process",
        process_initiator_id=actor.id,
        bpmn_process_definition_id=definition.id,
        bpmn_process_id=bpmn_process.id,
        status="running",
        created_at_in_seconds=1_000,
        updated_at_in_seconds=1_000,
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
