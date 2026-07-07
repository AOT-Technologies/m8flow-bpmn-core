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


@dataclass(frozen=True, slots=True)
class ActiveIdentity:
    tenant: M8flowTenantModel
    user: UserModel


def set_active_identity(*, tenant_id: str, user_id: int) -> None:
    flask_session[SESSION_TENANT_ID_KEY] = tenant_id
    flask_session[SESSION_USER_ID_KEY] = user_id


def clear_active_identity() -> None:
    flask_session.pop(SESSION_TENANT_ID_KEY, None)
    flask_session.pop(SESSION_USER_ID_KEY, None)


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
