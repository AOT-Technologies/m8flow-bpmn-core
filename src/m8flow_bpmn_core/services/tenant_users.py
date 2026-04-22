from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel


def ensure_user_belongs_to_tenant(
    session: Session,
    *,
    tenant_id: str,
    user_id: int,
) -> UserModel:
    user = session.get(UserModel, user_id)
    if user is None:
        raise LookupError(f"User {user_id} was not found")

    tenant_identifiers = tenant_identifiers_for(session, tenant_id)
    if not user_belongs_to_tenant(user, tenant_identifiers):
        raise PermissionError(
            f"User {user_id} does not belong to tenant {tenant_id}"
        )
    return user


def tenant_identifiers_for(session: Session, tenant_id: str) -> set[str]:
    normalized_tenant_id = tenant_id.strip()
    identifiers = {normalized_tenant_id} if normalized_tenant_id else set()
    tenant = session.scalars(
        select(M8flowTenantModel).where(
            or_(
                M8flowTenantModel.id == tenant_id,
                M8flowTenantModel.slug == tenant_id,
            )
        )
    ).first()
    if tenant is None:
        return identifiers

    for value in (tenant.id, tenant.slug):
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                identifiers.add(normalized)
    return identifiers


def user_belongs_to_tenant(
    user: UserModel,
    tenant_identifiers: set[str],
) -> bool:
    service_realm_value = service_realm(getattr(user, "service", None))
    return service_realm_value in tenant_identifiers


def service_realm(service: str | None) -> str | None:
    if not isinstance(service, str):
        return None

    normalized = service.rstrip("/")
    if "/realms/" in normalized:
        return normalized.split("/realms/")[-1].split("/")[0]
    if "/" in normalized:
        return normalized.rsplit("/", 1)[-1]
    return normalized or None
