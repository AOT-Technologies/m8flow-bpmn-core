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

VALIDATION_BPMN_PATH = (
    Path(__file__).with_name("fixtures") / "invoice_approval_poc.bpmn"
)


@dataclass(frozen=True, slots=True)
class AuthorizationHookContext:
    tenant: M8flowTenantModel
    user: UserModel
    definition: BpmnProcessDefinitionModel
    human_task: HumanTaskModel


def test_public_policy_scope_receives_task_request_metadata(
    session: Session,
) -> None:
    context = _seed_authorization_hook_context(session)
    captured: dict[str, api.AuthorizationRequest] = {}

    class CaptureAllowPolicy:
        def authorize(
            self,
            session: Session,
            request: api.AuthorizationRequest,
        ) -> api.AuthorizationDecision:
            captured["request"] = request
            return api.AuthorizationDecision(True)

    with api.authorization_policy_scope(CaptureAllowPolicy()):
        claimed_task = api.execute_command(
            session,
            api.ClaimTaskCommand(
                tenant_id=context.tenant.id,
                human_task_id=context.human_task.id,
                user_id=context.user.id,
            ),
        )

    request = captured["request"]
    assert request.command_key == api.TASK_CLAIM_COMMAND
    assert request.target_id == context.human_task.id
    assert request.metadata is not None
    assert request.metadata["human_task_id"] == context.human_task.id
    assert (
        request.metadata["process_instance_id"]
        == context.human_task.process_instance_id
    )
    assert request.metadata["lane_name"] == "finance"
    assert claimed_task.actual_owner_id == context.user.id


def test_public_default_policy_factory_overrides_process_start_authorization(
    session: Session,
) -> None:
    context = _seed_authorization_hook_context(session)
    captured: dict[str, api.AuthorizationRequest] = {}

    class DenyStartPolicy:
        def authorize(
            self,
            session: Session,
            request: api.AuthorizationRequest,
        ) -> api.AuthorizationDecision:
            captured["request"] = request
            return api.AuthorizationDecision(
                False,
                reason="blocked by custom policy",
            )

    api.set_default_authorization_policy_factory(DenyStartPolicy)
    try:
        with pytest.raises(
            api.AuthorizationError,
            match="blocked by custom policy",
        ):
            api.execute_command(
                session,
                api.InitializeProcessInstanceFromDefinitionCommand(
                    tenant_id=context.tenant.id,
                    bpmn_process_definition_id=context.definition.id,
                    process_initiator_id=context.user.id,
                    summary="Blocked by policy hook",
                    process_version=1,
                    started_at_in_seconds=100,
                    bpmn_process_id="invoice_approval_poc",
                ),
            )
    finally:
        api.set_default_authorization_policy_factory(
            api.DatabaseAuthorizationPolicy
        )

    request = captured["request"]
    assert request.command_key == api.PROCESS_START_COMMAND
    assert request.target_id == context.definition.id
    assert request.metadata is not None
    assert (
        request.metadata["bpmn_process_definition_id"]
        == context.definition.id
    )
    assert (
        request.metadata["process_model_identifier"]
        == context.definition.process_model_identifier
    )
    assert request.metadata["requested_bpmn_process_id"] == "invoice_approval_poc"


def _seed_authorization_hook_context(
    session: Session,
) -> AuthorizationHookContext:
    tenant = M8flowTenantModel(
        id="tenant-authorization-hooks",
        name="Authorization Hooks",
        slug="tenant-authorization-hooks",
    )
    user = UserModel(
        username="policy-user",
        email="policy-user@example.com",
        service=f"http://localhost:7002/realms/{tenant.slug}",
        service_id="policy-user-keycloak",
        display_name="Policy User",
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    session.add_all([tenant, user])
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

    bpmn_process = BpmnProcessModel(
        m8f_tenant_id=tenant.id,
        guid="authorization-hooks-process-guid",
        bpmn_process_definition_id=definition.id,
        top_level_process_id=None,
        direct_parent_process_id=None,
        properties_json={"root": "approve_invoice"},
        json_data_hash="authorization-hooks-process-json",
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
        created_at_in_seconds=95,
        updated_at_in_seconds=95,
    )
    session.add(task_definition)
    session.flush()

    process_instance = ProcessInstanceModel(
        m8f_tenant_id=tenant.id,
        process_model_identifier=definition.process_model_identifier,
        process_model_display_name="Invoice Approval POC",
        process_initiator_id=user.id,
        bpmn_process_definition_id=definition.id,
        bpmn_process_id=bpmn_process.id,
        status="running",
        created_at_in_seconds=100,
        updated_at_in_seconds=100,
    )
    session.add(process_instance)
    session.flush()

    task = TaskModel(
        m8f_tenant_id=tenant.id,
        guid="authorization-hooks-task-guid",
        bpmn_process_id=bpmn_process.id,
        process_instance_id=process_instance.id,
        task_definition_id=task_definition.id,
        state="READY",
        properties_json={"task_spec": "Approve Invoice"},
        json_data_hash="authorization-hooks-task-json",
        python_env_data_hash="authorization-hooks-task-env",
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
            user_id=user.id,
            added_by="manual",
        )
    )
    session.flush()

    return AuthorizationHookContext(
        tenant=tenant,
        user=user,
        definition=definition,
        human_task=human_task,
    )
