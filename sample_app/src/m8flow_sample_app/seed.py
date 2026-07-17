from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from m8flow_bpmn_core.models import Base
from m8flow_bpmn_core.models.tenant import M8flowTenantModel, TenantStatus
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.utils.keycloak import (
    KeycloakOrganizationSpec,
    KeycloakUserSpec,
    ProvisionedKeycloakSharedRealmContext,
    ensure_shared_realm_organizations_and_users,
)
from m8flow_sample_app.models import ALL_METADATA, SecretModel
from m8flow_sample_app.shared_m8flow import SharedM8flowAuditContext

ROLE_USER = "user"
ROLE_MANAGER = "manager"
ROLE_ADMIN = "admin"
KEYCLOAK_GROUPS_BY_ROLE = {
    ROLE_ADMIN: ("Administrators", "Approvers", "Viewers"),
    ROLE_USER: ("Submitters", "Viewers"),
    ROLE_MANAGER: ("Approvers", "Viewers"),
}


@dataclass(frozen=True, slots=True)
class SeedUserDefinition:
    username: str
    email: str
    display_name: str
    service_id: str
    role_name: str
    lane_name: str | None = None


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
                lane_name="Operations",
            ),
            SeedUserDefinition(
                username="alpha-finance-reviewer",
                email="alpha-finance-reviewer@example.com",
                display_name="Alpha Finance Reviewer",
                service_id="alpha-finance-reviewer",
                role_name=ROLE_MANAGER,
                lane_name="Finance",
            ),
            SeedUserDefinition(
                username="alpha-reviewer",
                email="alpha-reviewer@example.com",
                display_name="Alpha Reviewer",
                service_id="alpha-reviewer",
                role_name=ROLE_MANAGER,
                lane_name="Review",
            ),
            SeedUserDefinition(
                username="alpha-supervisor",
                email="alpha-supervisor@example.com",
                display_name="Alpha Supervisor",
                service_id="alpha-supervisor",
                role_name=ROLE_MANAGER,
                lane_name="Supervisor",
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
                lane_name="Operations",
            ),
            SeedUserDefinition(
                username="beta-finance-reviewer",
                email="beta-finance-reviewer@example.com",
                display_name="Beta Finance Reviewer",
                service_id="beta-finance-reviewer",
                role_name=ROLE_MANAGER,
                lane_name="Finance",
            ),
            SeedUserDefinition(
                username="beta-reviewer",
                email="beta-reviewer@example.com",
                display_name="Beta Reviewer",
                service_id="beta-reviewer",
                role_name=ROLE_MANAGER,
                lane_name="Review",
            ),
            SeedUserDefinition(
                username="beta-supervisor",
                email="beta-supervisor@example.com",
                display_name="Beta Supervisor",
                service_id="beta-supervisor",
                role_name=ROLE_MANAGER,
                lane_name="Supervisor",
            ),
        ),
    ),
)

DEFAULT_TENANT_SECRET_VALUES = (
    ("SMTP_HOST", "sandbox.smtp.mailtrap.io"),
    ("SMTP_PORT", "2525"),
    ("SMTP_USER", "fce006e9972d8b"),
    ("SMTP_PASSWORD", "CHANGE_ME_IN_SECRETS_UI"),
    ("SMTP_STARTTLS", "True"),
    ("SMTP_FROM_EMAIL", "sample-app-reimbursements@example.com"),
)


def seed_static_reference_data(
    session: Session,
    *,
    audit_context: SharedM8flowAuditContext | None = None,
) -> None:
    from m8flow_bpmn_core.services.authorization import (
        ensure_v1_role,
        find_or_create_principal_for_user,
    )

    keycloak_context: ProvisionedKeycloakSharedRealmContext | None = None
    if audit_context is not None and audit_context.uses_shared_m8flow:
        keycloak_context = _provision_shared_keycloak_context()

    for tenant_definition in SEED_TENANTS:
        tenant = _ensure_tenant(
            session,
            tenant_definition,
            keycloak_context=keycloak_context,
        )
        role_user_ids: dict[str, list[int]] = {
            ROLE_ADMIN: [],
            ROLE_USER: [],
            ROLE_MANAGER: [],
        }
        admin_user_id: int | None = None
        for user_definition in tenant_definition.users:
            user = _ensure_user(
                session,
                tenant,
                user_definition,
                keycloak_context=keycloak_context,
            )
            find_or_create_principal_for_user(session, user_id=user.id)
            role_user_ids[user_definition.role_name].append(user.id)
            if user_definition.role_name == ROLE_ADMIN and admin_user_id is None:
                admin_user_id = user.id

        for role_name, user_ids in role_user_ids.items():
            if user_ids:
                ensure_v1_role(
                    session,
                    tenant_id=tenant.id,
                    role_name=role_name,
                    user_ids=user_ids,
                )
        if admin_user_id is not None:
            _ensure_default_tenant_secrets(
                session,
                tenant_id=tenant.id,
                user_id=admin_user_id,
            )

    session.flush()


def lane_owner_usernames_for_tenant(
    tenant_id: str,
    *,
    tenant_slug: str | None = None,
) -> dict[str, list[str]]:
    tenant_definition = _seed_tenant_definition(
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
    )
    if tenant_definition is None:
        raise KeyError(
            "No seeded tenant definition exists for "
            f"tenant_id={tenant_id!r} tenant_slug={tenant_slug!r}"
        )

    operations_owners = [
        user.username
        for user in tenant_definition.users
        if user.lane_name == "Operations"
    ]
    finance_owners = [
        user.username
        for user in tenant_definition.users
        if user.lane_name == "Finance"
    ]
    review_owners = [
        user.username
        for user in tenant_definition.users
        if user.lane_name == "Review"
    ]
    supervisor_owners = [
        user.username
        for user in tenant_definition.users
        if user.lane_name == "Supervisor"
    ]
    return {
        "Operations": operations_owners,
        "Finance": finance_owners,
        "Review": review_owners,
        "Supervisor": supervisor_owners,
    }


def _ensure_default_tenant_secrets(
    session: Session,
    *,
    tenant_id: str,
    user_id: int,
) -> None:
    existing_secret_keys = set(
        session.scalars(
            select(SecretModel.key).where(SecretModel.m8f_tenant_id == tenant_id)
        )
    )
    for key, value in DEFAULT_TENANT_SECRET_VALUES:
        if key in existing_secret_keys:
            continue
        session.add(
            SecretModel(
                m8f_tenant_id=tenant_id,
                key=key,
                value=value,
                user_id=user_id,
                created_at_in_seconds=0,
                updated_at_in_seconds=0,
            )
        )
    session.flush()


def _ensure_tenant(
    session: Session,
    tenant_definition: SeedTenantDefinition,
    *,
    keycloak_context: ProvisionedKeycloakSharedRealmContext | None = None,
) -> M8flowTenantModel:
    canonical_tenant_id = _canonical_tenant_id(
        tenant_definition,
        keycloak_context=keycloak_context,
    )
    tenant = session.scalar(
        select(M8flowTenantModel).where(
            M8flowTenantModel.id == canonical_tenant_id
        )
    )
    if tenant is None:
        tenant = session.scalar(
            select(M8flowTenantModel).where(
                M8flowTenantModel.slug == tenant_definition.slug
            )
        )
        if tenant is not None and tenant.id != canonical_tenant_id:
            tenant = _realign_tenant_to_canonical_id(
                session,
                tenant=tenant,
                canonical_tenant_id=canonical_tenant_id,
                canonical_name=tenant_definition.name,
            )

    if tenant is None:
        tenant = M8flowTenantModel(
            id=canonical_tenant_id,
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
    *,
    keycloak_context: ProvisionedKeycloakSharedRealmContext | None = None,
) -> UserModel:
    service, service_id = _resolved_user_identity(
        tenant=tenant,
        user_definition=user_definition,
        keycloak_context=keycloak_context,
    )
    user = session.scalar(
        select(UserModel).where(
            UserModel.service == service,
            UserModel.service_id == service_id,
        )
    )
    if user is None and keycloak_context is not None:
        user = session.scalar(
            select(UserModel).where(
                UserModel.username == user_definition.username,
                or_(
                    UserModel.tenant_specific_field_1 == tenant.id,
                    UserModel.tenant_specific_field_2 == tenant.slug,
                ),
            )
        )
    if user is None:
        user = UserModel(
            username=user_definition.username,
            email=user_definition.email,
            service=service,
            service_id=service_id,
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
    user.service = service
    user.service_id = service_id
    user.display_name = user_definition.display_name
    user.tenant_specific_field_1 = tenant.id
    user.tenant_specific_field_2 = tenant.slug
    user.updated_at_in_seconds = 0
    return user


def _seed_tenant_definition(
    *,
    tenant_id: str | None = None,
    tenant_slug: str | None = None,
) -> SeedTenantDefinition | None:
    for tenant_definition in SEED_TENANTS:
        if tenant_id and tenant_definition.tenant_id == tenant_id:
            return tenant_definition
        if tenant_slug and tenant_definition.slug == tenant_slug:
            return tenant_definition
    return None


def _resolved_user_identity(
    *,
    tenant: M8flowTenantModel,
    user_definition: SeedUserDefinition,
    keycloak_context: ProvisionedKeycloakSharedRealmContext | None,
) -> tuple[str, str]:
    if keycloak_context is None:
        return _local_realm_service_issuer(tenant.slug), user_definition.service_id

    provisioned_user = keycloak_context.users_by_username.get(user_definition.username)
    if provisioned_user is None:
        raise KeyError(
            "No provisioned Keycloak user was available for "
            f"{user_definition.username!r}"
        )
    return keycloak_context.service_issuer, provisioned_user.user_id


def _canonical_tenant_id(
    tenant_definition: SeedTenantDefinition,
    *,
    keycloak_context: ProvisionedKeycloakSharedRealmContext | None,
) -> str:
    if keycloak_context is None:
        return tenant_definition.tenant_id

    organization = keycloak_context.organizations_by_alias.get(tenant_definition.slug)
    if organization is None:
        raise KeyError(
            "No provisioned Keycloak organization was available for "
            f"{tenant_definition.slug!r}"
        )
    return organization.organization_id


def _provision_shared_keycloak_context() -> ProvisionedKeycloakSharedRealmContext:
    return ensure_shared_realm_organizations_and_users(
        organizations=[
            KeycloakOrganizationSpec(
                alias=tenant_definition.slug,
                name=tenant_definition.name,
            )
            for tenant_definition in SEED_TENANTS
        ],
        users=[
            KeycloakUserSpec(
                username=user_definition.username,
                email=user_definition.email,
                password=user_definition.username,
                organization_alias=tenant_definition.slug,
                display_name=user_definition.display_name,
                organization_group_names=_organization_group_names_for_seed_user(
                    user_definition
                ),
            )
            for tenant_definition in SEED_TENANTS
            for user_definition in tenant_definition.users
        ],
    )


def _organization_group_names_for_seed_user(
    user_definition: SeedUserDefinition,
) -> tuple[str, ...]:
    group_names = list(KEYCLOAK_GROUPS_BY_ROLE[user_definition.role_name])
    if user_definition.lane_name is not None:
        group_names.append(user_definition.lane_name)
    return tuple(dict.fromkeys(group_names))


def _realign_tenant_to_canonical_id(
    session: Session,
    *,
    tenant: M8flowTenantModel,
    canonical_tenant_id: str,
    canonical_name: str,
) -> M8flowTenantModel:
    if tenant.id == canonical_tenant_id:
        if tenant.name != canonical_name:
            tenant.name = canonical_name
            session.flush()
        return tenant

    original_tenant_id = tenant.id
    original_slug = tenant.slug
    tenant.slug = _legacy_tenant_slug(original_slug, canonical_tenant_id)
    tenant.name = f"{canonical_name} (legacy)"
    session.flush()

    canonical_tenant = M8flowTenantModel(
        id=canonical_tenant_id,
        slug=original_slug,
        name=canonical_name,
        status=tenant.status,
        created_by=tenant.created_by,
        modified_by=tenant.modified_by,
        created_at_in_seconds=tenant.created_at_in_seconds,
        updated_at_in_seconds=tenant.updated_at_in_seconds,
    )
    session.add(canonical_tenant)
    session.flush()

    for table in _tenant_scoped_tables():
        if table.name == M8flowTenantModel.__tablename__:
            continue
        if "m8f_tenant_id" not in table.c:
            continue
        session.execute(
            table.update()
            .where(table.c.m8f_tenant_id == original_tenant_id)
            .values(m8f_tenant_id=canonical_tenant_id)
        )

    session.execute(
        UserModel.__table__.update()
        .where(UserModel.__table__.c.tenant_specific_field_1 == original_tenant_id)
        .values(tenant_specific_field_1=canonical_tenant_id)
    )
    session.flush()
    session.delete(tenant)
    session.flush()
    return canonical_tenant


def _tenant_scoped_tables() -> list[object]:
    tables_by_name: dict[str, object] = {}
    metadata_objects = [Base.metadata, *ALL_METADATA]
    for metadata in metadata_objects:
        for table in metadata.sorted_tables:
            tables_by_name[table.name] = table
    return list(tables_by_name.values())


def _legacy_tenant_slug(slug: str, desired_tenant_id: str) -> str:
    suffix = desired_tenant_id.replace("-", "")[:8] or "legacy"
    return f"{slug}-legacy-{suffix}"


def _local_realm_service_issuer(tenant_slug: str) -> str:
    return f"http://localhost:7002/realms/{tenant_slug}"
