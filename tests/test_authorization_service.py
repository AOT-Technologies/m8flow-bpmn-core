from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from m8flow_bpmn_core.application.commands import (
    ClaimTaskCommand,
    InitializeProcessInstanceFromDefinitionCommand,
)
from m8flow_bpmn_core.errors import AuthorizationError
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.services.authorization import (
    PROCESS_DEFINITION_IMPORT_COMMAND,
    PROCESS_START_COMMAND,
    PROCESS_SUSPEND_COMMAND,
    PROCESS_TERMINATE_COMMAND,
    ROLE_ADMIN,
    ROLE_MANAGER,
    TASK_CLAIM_COMMAND,
    TASK_COMPLETE_COMMAND,
    AuthorizationDecision,
    DatabaseAuthorizationPolicy,
    actor_user_id_from_command,
    authorization_policy_scope,
    authorization_spec_for_command,
    build_authorization_request,
    ensure_v1_role,
    grant_permission_to_user,
    require_command_authorization,
)


def test_authorization_specs_resolve_command_keys_and_actor_fields() -> None:
    claim_command = ClaimTaskCommand(
        tenant_id="tenant-a",
        human_task_id=10,
        user_id=123,
    )
    claim_spec = authorization_spec_for_command(claim_command)

    assert claim_spec.command_key == TASK_CLAIM_COMMAND
    assert claim_spec.actor_field_name == "user_id"
    assert actor_user_id_from_command(claim_command) == 123

    start_command = InitializeProcessInstanceFromDefinitionCommand(
        tenant_id="tenant-a",
        bpmn_process_definition_id=99,
        process_initiator_id=456,
        summary="Start",
        process_version=1,
        started_at_in_seconds=100,
        bpmn_process_id="Process_1",
    )
    start_spec = authorization_spec_for_command(start_command)

    assert start_spec.command_key == PROCESS_START_COMMAND
    assert actor_user_id_from_command(start_command) == 456


def test_database_authorization_policy_allows_tenant_scoped_role_grants(
    session: Session,
) -> None:
    tenant, user = _seed_tenant_and_user(session, tenant_id="tenant-a")
    ensure_v1_role(
        session,
        tenant_id=tenant.id,
        role_name=ROLE_MANAGER,
        user_ids=[user.id],
    )

    decision = DatabaseAuthorizationPolicy().authorize(
        session,
        build_authorization_request(
            tenant_id=tenant.id,
            actor_user_id=user.id,
            command_key=TASK_COMPLETE_COMMAND,
        ),
    )

    assert decision.allowed is True


def test_database_authorization_policy_rejects_other_tenant_group_permissions(
    session: Session,
) -> None:
    tenant, user = _seed_tenant_and_user(session, tenant_id="tenant-a")
    ensure_v1_role(
        session,
        tenant_id="tenant-b",
        role_name=ROLE_MANAGER,
        user_ids=[user.id],
    )

    decision = DatabaseAuthorizationPolicy().authorize(
        session,
        build_authorization_request(
            tenant_id=tenant.id,
            actor_user_id=user.id,
            command_key=TASK_COMPLETE_COMMAND,
        ),
    )

    assert decision.allowed is False
    assert decision.reason is not None
    assert "No matching permission grant" in decision.reason


def test_database_authorization_policy_honors_deny_over_permit(
    session: Session,
) -> None:
    tenant, user = _seed_tenant_and_user(session, tenant_id="tenant-a")
    ensure_v1_role(
        session,
        tenant_id=tenant.id,
        role_name=ROLE_MANAGER,
        user_ids=[user.id],
    )
    grant_permission_to_user(
        session,
        user_id=user.id,
        permission="execute",
        target_uri="/tasks/%",
        command=TASK_COMPLETE_COMMAND,
        grant_type="deny",
    )

    decision = DatabaseAuthorizationPolicy().authorize(
        session,
        build_authorization_request(
            tenant_id=tenant.id,
            actor_user_id=user.id,
            command_key=TASK_COMPLETE_COMMAND,
        ),
    )

    assert decision.allowed is False
    assert decision.reason is not None
    assert "deny permission matched" in decision.reason


def test_database_authorization_policy_accepts_uri_only_grants_as_fallback(
    session: Session,
) -> None:
    tenant, user = _seed_tenant_and_user(session, tenant_id="tenant-a")
    grant_permission_to_user(
        session,
        user_id=user.id,
        permission="execute",
        target_uri="/tasks/%",
        command=None,
    )

    decision = DatabaseAuthorizationPolicy().authorize(
        session,
        build_authorization_request(
            tenant_id=tenant.id,
            actor_user_id=user.id,
            command_key=TASK_CLAIM_COMMAND,
        ),
    )

    assert decision.allowed is True


def test_authorization_policy_scope_overrides_db_policy(
    session: Session,
) -> None:
    tenant, user = _seed_tenant_and_user(session, tenant_id="tenant-a")

    class DenyAllPolicy:
        def authorize(self, session: Session, request) -> AuthorizationDecision:
            return AuthorizationDecision(False, reason="forced by test hook")

    with authorization_policy_scope(DenyAllPolicy()):
        with pytest.raises(AuthorizationError, match="forced by test hook"):
            require_command_authorization(
                session,
                tenant_id=tenant.id,
                actor_user_id=user.id,
                command_key=TASK_CLAIM_COMMAND,
            )


def test_ensure_v1_role_supports_basic_roles(session: Session) -> None:
    tenant, user = _seed_tenant_and_user(session, tenant_id="tenant-a")
    ensure_v1_role(
        session,
        tenant_id=tenant.id,
        role_name=ROLE_ADMIN,
        user_ids=[user.id],
    )

    start_decision = DatabaseAuthorizationPolicy().authorize(
        session,
        build_authorization_request(
            tenant_id=tenant.id,
            actor_user_id=user.id,
            command_key=PROCESS_START_COMMAND,
        ),
    )
    claim_decision = DatabaseAuthorizationPolicy().authorize(
        session,
        build_authorization_request(
            tenant_id=tenant.id,
            actor_user_id=user.id,
            command_key=TASK_CLAIM_COMMAND,
        ),
    )
    import_decision = DatabaseAuthorizationPolicy().authorize(
        session,
        build_authorization_request(
            tenant_id=tenant.id,
            actor_user_id=user.id,
            command_key=PROCESS_DEFINITION_IMPORT_COMMAND,
        ),
    )
    suspend_decision = DatabaseAuthorizationPolicy().authorize(
        session,
        build_authorization_request(
            tenant_id=tenant.id,
            actor_user_id=user.id,
            command_key=PROCESS_SUSPEND_COMMAND,
        ),
    )
    terminate_decision = DatabaseAuthorizationPolicy().authorize(
        session,
        build_authorization_request(
            tenant_id=tenant.id,
            actor_user_id=user.id,
            command_key=PROCESS_TERMINATE_COMMAND,
        ),
    )

    assert start_decision.allowed is True
    assert claim_decision.allowed is True
    assert import_decision.allowed is True
    assert suspend_decision.allowed is True
    assert terminate_decision.allowed is True


def _seed_tenant_and_user(
    session: Session,
    *,
    tenant_id: str,
) -> tuple[M8flowTenantModel, UserModel]:
    tenant = M8flowTenantModel(
        id=tenant_id,
        name=f"Tenant {tenant_id}",
        slug=tenant_id,
    )
    user = UserModel(
        username=f"user-{tenant_id}",
        email=f"user-{tenant_id}@example.com",
        service=f"http://localhost:7002/realms/{tenant_id}",
        service_id=f"user-{tenant_id}-keycloak",
        display_name=f"User {tenant_id}",
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    session.add_all([tenant, user])
    session.flush()
    return tenant, user
