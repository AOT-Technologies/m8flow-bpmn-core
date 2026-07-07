from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_bpmn_core.models.tenant import M8flowTenantModel, TenantStatus
from m8flow_bpmn_core.models.user import UserModel

ROLE_USER = "user"
ROLE_MANAGER = "manager"
ROLE_ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class SeedUserDefinition:
    username: str
    email: str
    display_name: str
    service_id: str
    role_name: str


@dataclass(frozen=True, slots=True)
class SeedTenantDefinition:
    tenant_id: str
    slug: str
    name: str
    users: tuple[SeedUserDefinition, ...]


SEED_TENANTS = (
    SeedTenantDefinition(
        tenant_id="sample-tenant-alpha",
        slug="sample-tenant-alpha",
        name="Sample Tenant Alpha",
        users=(
            SeedUserDefinition(
                username="alpha-admin",
                email="alpha-admin@example.com",
                display_name="Alpha Admin",
                service_id="alpha-admin",
                role_name=ROLE_ADMIN,
            ),
            SeedUserDefinition(
                username="alpha-operator",
                email="alpha-operator@example.com",
                display_name="Alpha Operator",
                service_id="alpha-operator",
                role_name=ROLE_USER,
            ),
            SeedUserDefinition(
                username="alpha-reviewer",
                email="alpha-reviewer@example.com",
                display_name="Alpha Reviewer",
                service_id="alpha-reviewer",
                role_name=ROLE_MANAGER,
            ),
        ),
    ),
    SeedTenantDefinition(
        tenant_id="sample-tenant-beta",
        slug="sample-tenant-beta",
        name="Sample Tenant Beta",
        users=(
            SeedUserDefinition(
                username="beta-admin",
                email="beta-admin@example.com",
                display_name="Beta Admin",
                service_id="beta-admin",
                role_name=ROLE_ADMIN,
            ),
            SeedUserDefinition(
                username="beta-operator",
                email="beta-operator@example.com",
                display_name="Beta Operator",
                service_id="beta-operator",
                role_name=ROLE_USER,
            ),
            SeedUserDefinition(
                username="beta-reviewer",
                email="beta-reviewer@example.com",
                display_name="Beta Reviewer",
                service_id="beta-reviewer",
                role_name=ROLE_MANAGER,
            ),
        ),
    ),
)


def seed_static_reference_data(session: Session) -> None:
    from m8flow_bpmn_core.services.authorization import ensure_v1_role

    for tenant_definition in SEED_TENANTS:
        tenant = _ensure_tenant(session, tenant_definition)
        role_user_ids: dict[str, list[int]] = {
            ROLE_ADMIN: [],
            ROLE_USER: [],
            ROLE_MANAGER: [],
        }
        for user_definition in tenant_definition.users:
            user = _ensure_user(session, tenant, user_definition)
            role_user_ids[user_definition.role_name].append(user.id)

        for role_name, user_ids in role_user_ids.items():
            if user_ids:
                ensure_v1_role(
                    session,
                    tenant_id=tenant.id,
                    role_name=role_name,
                    user_ids=user_ids,
                )

    session.flush()


def lane_owner_usernames_for_tenant(tenant_id: str) -> dict[str, list[str]]:
    tenant_definition = _seed_tenant_definition(tenant_id)
    if tenant_definition is None:
        raise KeyError(f"No seeded tenant definition exists for {tenant_id!r}")

    operations_owners = [
        user.username for user in tenant_definition.users if user.role_name == ROLE_USER
    ]
    review_owners = [
        user.username
        for user in tenant_definition.users
        if user.role_name == ROLE_MANAGER
    ]
    return {
        "Operations": operations_owners,
        "Review": review_owners,
    }


def _ensure_tenant(
    session: Session,
    tenant_definition: SeedTenantDefinition,
) -> M8flowTenantModel:
    tenant = session.scalar(
        select(M8flowTenantModel).where(
            M8flowTenantModel.id == tenant_definition.tenant_id
        )
    )
    if tenant is None:
        tenant = M8flowTenantModel(
            id=tenant_definition.tenant_id,
            slug=tenant_definition.slug,
            name=tenant_definition.name,
            status=TenantStatus.ACTIVE,
            created_by="sample-app",
            modified_by="sample-app",
            created_at_in_seconds=0,
            updated_at_in_seconds=0,
        )
        session.add(tenant)
        session.flush()
        return tenant

    tenant.slug = tenant_definition.slug
    tenant.name = tenant_definition.name
    tenant.status = TenantStatus.ACTIVE
    tenant.modified_by = "sample-app"
    return tenant


def _ensure_user(
    session: Session,
    tenant: M8flowTenantModel,
    user_definition: SeedUserDefinition,
) -> UserModel:
    service = f"http://localhost:7002/realms/{tenant.slug}"
    user = session.scalar(
        select(UserModel).where(
            UserModel.service == service,
            UserModel.service_id == user_definition.service_id,
        )
    )
    if user is None:
        user = UserModel(
            username=user_definition.username,
            email=user_definition.email,
            service=service,
            service_id=user_definition.service_id,
            display_name=user_definition.display_name,
            tenant_specific_field_1=tenant.id,
            tenant_specific_field_2=tenant.slug,
            updated_at_in_seconds=0,
            created_at_in_seconds=0,
        )
        session.add(user)
        session.flush()
        return user

    user.username = user_definition.username
    user.email = user_definition.email
    user.display_name = user_definition.display_name
    user.tenant_specific_field_1 = tenant.id
    user.tenant_specific_field_2 = tenant.slug
    user.updated_at_in_seconds = 0
    return user


def _seed_tenant_definition(tenant_id: str) -> SeedTenantDefinition | None:
    for tenant_definition in SEED_TENANTS:
        if tenant_definition.tenant_id == tenant_id:
            return tenant_definition
    return None
