from __future__ import annotations

from dataclasses import dataclass

from flask import session as flask_session
from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_bpmn_core.errors import AuthorizationError
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.services.tenant_users import (
    ensure_user_belongs_to_tenant,
    tenant_identifiers_for,
    user_belongs_to_tenant,
)

SESSION_TENANT_ID_KEY = "tenant_id"
SESSION_USER_ID_KEY = "user_id"
SESSION_PENDING_SHARED_LOGIN_KEY = "pending_shared_login"


@dataclass(frozen=True, slots=True)
class ActiveIdentity:
    tenant: M8flowTenantModel
    user: UserModel


@dataclass(frozen=True, slots=True)
class PendingSharedLogin:
    tenant_id: str
    expected_user_id: int
    state: str
    code_verifier: str
    redirect_uri: str


def set_active_identity(*, tenant_id: str, user_id: int) -> None:
    clear_pending_shared_login()
    flask_session[SESSION_TENANT_ID_KEY] = tenant_id
    flask_session[SESSION_USER_ID_KEY] = user_id


def clear_active_identity() -> None:
    flask_session.pop(SESSION_TENANT_ID_KEY, None)
    flask_session.pop(SESSION_USER_ID_KEY, None)
    clear_pending_shared_login()


def set_pending_shared_login(
    *,
    tenant_id: str,
    expected_user_id: int,
    state: str,
    code_verifier: str,
    redirect_uri: str,
) -> None:
    flask_session[SESSION_PENDING_SHARED_LOGIN_KEY] = {
        "tenant_id": tenant_id,
        "expected_user_id": expected_user_id,
        "state": state,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
    }


def clear_pending_shared_login() -> None:
    flask_session.pop(SESSION_PENDING_SHARED_LOGIN_KEY, None)


def get_pending_shared_login() -> PendingSharedLogin | None:
    payload = flask_session.get(SESSION_PENDING_SHARED_LOGIN_KEY)
    if not isinstance(payload, dict):
        return None

    tenant_id = payload.get("tenant_id")
    expected_user_id = payload.get("expected_user_id")
    state = payload.get("state")
    code_verifier = payload.get("code_verifier")
    redirect_uri = payload.get("redirect_uri")
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        return None
    if not isinstance(expected_user_id, int):
        return None
    if not isinstance(state, str) or not state.strip():
        return None
    if not isinstance(code_verifier, str) or not code_verifier.strip():
        return None
    if not isinstance(redirect_uri, str) or not redirect_uri.strip():
        return None

    return PendingSharedLogin(
        tenant_id=tenant_id.strip(),
        expected_user_id=expected_user_id,
        state=state.strip(),
        code_verifier=code_verifier.strip(),
        redirect_uri=redirect_uri.strip(),
    )


def get_active_identity(session: Session) -> ActiveIdentity | None:
    tenant_id = flask_session.get(SESSION_TENANT_ID_KEY)
    user_id = flask_session.get(SESSION_USER_ID_KEY)

    if not isinstance(tenant_id, str) or not isinstance(user_id, int):
        return None

    tenant = session.scalar(
        select(M8flowTenantModel).where(M8flowTenantModel.id == tenant_id)
    )
    if tenant is None:
        clear_active_identity()
        return None

    try:
        user = ensure_user_belongs_to_tenant(
            session,
            tenant_id=tenant.id,
            user_id=user_id,
        )
    except AuthorizationError:
        clear_active_identity()
        return None

    return ActiveIdentity(tenant=tenant, user=user)


def list_tenants(session: Session) -> list[M8flowTenantModel]:
    return list(
        session.scalars(
            select(M8flowTenantModel).order_by(M8flowTenantModel.name.asc())
        )
    )


def list_users_for_tenant(session: Session, *, tenant_id: str) -> list[UserModel]:
    tenant_identifiers = tenant_identifiers_for(session, tenant_id)
    users = list(session.scalars(select(UserModel).order_by(UserModel.username.asc())))
    return [
        user for user in users if user_belongs_to_tenant(user, tenant_identifiers)
    ]


def find_user_for_service_identity(
    session: Session,
    *,
    tenant_id: str,
    service: str,
    service_id: str,
) -> UserModel | None:
    tenant_identifiers = tenant_identifiers_for(session, tenant_id)
    matching_users = [
        user
        for user in session.scalars(
            select(UserModel).where(
                UserModel.service == service,
                UserModel.service_id == service_id,
            )
        ).all()
        if user_belongs_to_tenant(user, tenant_identifiers)
    ]
    if len(matching_users) != 1:
        return None
    return matching_users[0]
