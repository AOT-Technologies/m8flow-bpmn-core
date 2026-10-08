from __future__ import annotations

from datetime import UTC, datetime

from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.services.tenant_users import (
    tenant_identifiers_for,
    user_belongs_to_tenant,
    user_tenant_identifiers,
)


def test_user_identity_fields_are_canonical() -> None:
    user = UserModel(
        username="alice",
        service="keycloak/realms/m8flow",
        service_id="kc-alice",
        realm_identifier="realm-a",
        external_org_id="org-a",
        external_user_id="external-alice",
    )

    assert user.realm_identifier == "realm-a"
    assert user.external_org_id == "org-a"
    assert user.external_user_id == "external-alice"
    assert user_tenant_identifiers(user) >= {
        "realm-a",
        "org-a",
        "external-alice",
    }


def test_user_belongs_to_tenant_accepts_shared_realm_membership_fields(
    session,
) -> None:
    tenant = M8flowTenantModel(
        id="tenant-a",
        name="Tenant A",
        slug="tenant-a",
    )
    foreign_tenant = M8flowTenantModel(
        id="tenant-b",
        name="Tenant B",
        slug="tenant-b",
    )
    shared_realm_service = "http://localhost:6842/realms/m8flow"
    tenant_user = UserModel(
        username="alice",
        email="alice@example.com",
        service=shared_realm_service,
        service_id="kc-alice",
        display_name="Alice",
        external_org_id=tenant.id,
        realm_identifier=tenant.slug,
        created_at=datetime.fromtimestamp(1, UTC),
        updated_at=datetime.fromtimestamp(1, UTC),
    )
    foreign_user = UserModel(
        username="bob",
        email="bob@example.com",
        service=shared_realm_service,
        service_id="kc-bob",
        display_name="Bob",
        external_org_id=foreign_tenant.id,
        realm_identifier=foreign_tenant.slug,
        created_at=datetime.fromtimestamp(1, UTC),
        updated_at=datetime.fromtimestamp(1, UTC),
    )
    session.add_all([tenant, foreign_tenant, tenant_user, foreign_user])
    session.flush()

    tenant_identifiers = tenant_identifiers_for(session, tenant.id)

    assert user_belongs_to_tenant(tenant_user, tenant_identifiers) is True
    assert user_belongs_to_tenant(foreign_user, tenant_identifiers) is False
