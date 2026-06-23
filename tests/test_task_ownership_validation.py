from __future__ import annotations

from dataclasses import dataclass

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
from m8flow_bpmn_core.services.authorization import ROLE_USER, ensure_v1_role
from m8flow_bpmn_core.services.tasks import claim_task, complete_task


@dataclass(frozen=True, slots=True)
class OwnershipContext:
    tenant: M8flowTenantModel
    primary_user: UserModel
    secondary_user: UserModel
    observer_user: UserModel
    human_task: HumanTaskModel


def test_claim_task_rejects_unassigned_user_with_role(session: Session) -> None:
    context = _seed_ownership_context(session)

    with pytest.raises(api.AuthorizationError, match="not assigned to this task"):
        claim_task(
            session,
            tenant_id=context.tenant.id,
            human_task_id=context.human_task.id,
            user_id=context.observer_user.id,
        )


def test_claim_task_rejects_other_assignee_after_owner_claims(session: Session) -> None:
    context = _seed_ownership_context(session)

    claim_task(
        session,
        tenant_id=context.tenant.id,
        human_task_id=context.human_task.id,
        user_id=context.primary_user.id,
    )

    with pytest.raises(api.AuthorizationError, match="already claimed by another user"):
        claim_task(
            session,
            tenant_id=context.tenant.id,
            human_task_id=context.human_task.id,
            user_id=context.secondary_user.id,
        )


def test_complete_task_requires_claim_before_completion(session: Session) -> None:
    context = _seed_ownership_context(session)

    with pytest.raises(
        api.InvalidStateError,
        match="must be claimed before completion",
    ):
        complete_task(
            session,
            tenant_id=context.tenant.id,
            human_task_id=context.human_task.id,
            user_id=context.primary_user.id,
        )


def test_complete_task_rejects_non_owner_assignee(session: Session) -> None:
    context = _seed_ownership_context(session)

    claim_task(
        session,
        tenant_id=context.tenant.id,
        human_task_id=context.human_task.id,
        user_id=context.primary_user.id,
    )

    with pytest.raises(api.AuthorizationError, match="does not own this task"):
        complete_task(
            session,
            tenant_id=context.tenant.id,
            human_task_id=context.human_task.id,
            user_id=context.secondary_user.id,
        )


def _seed_ownership_context(session: Session) -> OwnershipContext:
    tenant = M8flowTenantModel(
        id="tenant-task-ownership",
        name="Task Ownership",
        slug="tenant-task-ownership",
    )
    service_url = f"http://localhost:7002/realms/{tenant.slug}"
    primary_user = UserModel(
        username="primary-user",
        email="primary-user@example.com",
        service=service_url,
        service_id="primary-user-keycloak",
        display_name="Primary User",
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    secondary_user = UserModel(
        username="secondary-user",
        email="secondary-user@example.com",
        service=service_url,
        service_id="secondary-user-keycloak",
        display_name="Secondary User",
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    observer_user = UserModel(
        username="observer-user",
        email="observer-user@example.com",
        service=service_url,
        service_id="observer-user-keycloak",
        display_name="Observer User",
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    session.add_all([tenant, primary_user, secondary_user, observer_user])
    session.flush()
    ensure_v1_role(
        session,
        tenant_id=tenant.id,
        role_name=ROLE_USER,
        user_ids=[
            primary_user.id,
            secondary_user.id,
            observer_user.id,
        ],
    )

    definition = BpmnProcessDefinitionModel(
        m8f_tenant_id=tenant.id,
        single_process_hash="ownership-single",
        full_process_model_hash="ownership-full",
        bpmn_identifier="ownership-process",
        bpmn_name="Ownership Process",
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
        guid="ownership-process-guid",
        bpmn_process_definition_id=definition.id,
        top_level_process_id=None,
        direct_parent_process_id=None,
        properties_json={"root": "approve_invoice"},
        json_data_hash="ownership-process-json",
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
        process_model_identifier="ownership-process",
        process_model_display_name="Ownership Process",
        process_initiator_id=primary_user.id,
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
        guid="ownership-task-guid",
        bpmn_process_id=bpmn_process.id,
        process_instance_id=process_instance.id,
        task_definition_id=task_definition.id,
        state="READY",
        properties_json={"task_spec": "Approve Invoice"},
        json_data_hash="ownership-task-json",
        python_env_data_hash="ownership-task-env",
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

    session.add_all(
        [
            HumanTaskUserModel(
                m8f_tenant_id=tenant.id,
                human_task_id=human_task.id,
                user_id=primary_user.id,
                added_by="manual",
            ),
            HumanTaskUserModel(
                m8f_tenant_id=tenant.id,
                human_task_id=human_task.id,
                user_id=secondary_user.id,
                added_by="manual",
            ),
        ]
    )
    session.flush()

    return OwnershipContext(
        tenant=tenant,
        primary_user=primary_user,
        secondary_user=secondary_user,
        observer_user=observer_user,
        human_task=human_task,
    )
