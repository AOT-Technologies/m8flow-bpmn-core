from __future__ import annotations

from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.services.tenant_users import (
    tenant_identifiers_for,
    user_belongs_to_tenant,
)


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
        tenant_specific_field_1=tenant.id,
        tenant_specific_field_2=tenant.slug,
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    foreign_user = UserModel(
        username="bob",
        email="bob@example.com",
        service=shared_realm_service,
        service_id="kc-bob",
        display_name="Bob",
        tenant_specific_field_1=foreign_tenant.id,
        tenant_specific_field_2=foreign_tenant.slug,
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    session.add_all([tenant, foreign_tenant, tenant_user, foreign_user])
    session.flush()

    tenant_identifiers = tenant_identifiers_for(session, tenant.id)

    assert user_belongs_to_tenant(tenant_user, tenant_identifiers) is True
    assert user_belongs_to_tenant(foreign_user, tenant_identifiers) is False
