from __future__ import annotations

from m8flow_bpmn_core.utils.keycloak import service as keycloak_service


def test_ensure_shared_realm_organizations_and_users_reuses_existing_rows(
    monkeypatch,
) -> None:
    created_organizations: list[tuple[str, str]] = []
    created_users: list[str] = []
    added_members: list[tuple[str, str]] = []
    added_group_memberships: list[tuple[str, str, str]] = []
    ensured_groups: list[str] = []

    monkeypatch.setattr(
        keycloak_service,
        "get_master_admin_token",
        lambda: "token",
    )
    monkeypatch.setattr(
        keycloak_service,
        "shared_realm_name",
        lambda: "m8flow",
    )
    monkeypatch.setattr(
        keycloak_service,
        "shared_realm_service_issuer",
        lambda: "http://localhost:6842/realms/m8flow",
    )

    def fake_get_organization_by_alias(
        alias: str,
        *,
        admin_token: str,
    ) -> dict[str, str] | None:
        assert admin_token == "token"
        if alias == "tenant-a":
            return {
                "id": "org-tenant-a",
                "alias": "tenant-a",
                "name": "Tenant A",
            }
        return None

    def fake_create_organization(
        alias: str,
        name: str,
        *,
        enabled: bool = True,
        admin_token: str,
    ) -> dict[str, str]:
        assert enabled is True
        assert admin_token == "token"
        created_organizations.append((alias, name))
        return {
            "id": f"org-{alias}",
            "alias": alias,
            "name": name,
        }

    def fake_get_realm_user_by_username(
        realm: str,
        username: str,
        *,
        admin_token: str,
    ) -> dict[str, str] | None:
        assert realm == "m8flow"
        assert admin_token == "token"
        if username == "alice":
            return {
                "id": "kc-alice",
                "username": "alice",
            }
        return None

    def fake_create_user_in_realm(
        realm: str,
        username: str,
        password: str,
        *,
        email: str | None = None,
        display_name: str | None = None,
        enabled: bool = True,
        admin_token: str,
    ) -> str:
        assert realm == "m8flow"
        assert enabled is True
        assert admin_token == "token"
        assert password == "demo-password"
        assert email == "bob@example.com"
        assert display_name == "Bob"
        created_users.append(username)
        return f"kc-{username}"

    monkeypatch.setattr(
        keycloak_service,
        "get_organization_by_alias",
        fake_get_organization_by_alias,
    )
    monkeypatch.setattr(
        keycloak_service,
        "create_organization",
        fake_create_organization,
    )
    monkeypatch.setattr(
        keycloak_service,
        "ensure_organization_role_groups",
        lambda organization_id, *, admin_token: ensured_groups.append(
            f"{organization_id}:{admin_token}"
        ),
    )
    monkeypatch.setattr(
        keycloak_service,
        "get_realm_user_by_username",
        fake_get_realm_user_by_username,
    )
    monkeypatch.setattr(
        keycloak_service,
        "create_user_in_realm",
        fake_create_user_in_realm,
    )
    monkeypatch.setattr(
        keycloak_service,
        "add_organization_member",
        lambda organization_id, user_id, *, admin_token: added_members.append(
            (organization_id, user_id)
        ),
    )
    monkeypatch.setattr(
        keycloak_service,
        "get_organization_member_by_username",
        lambda organization_id, username, *, admin_token: {
            "id": f"member-{username}",
            "username": username,
        },
    )
    monkeypatch.setattr(
        keycloak_service,
        "add_organization_group_member",
        lambda organization_id, group_name, member_id, *, admin_token: (
            added_group_memberships.append(
                (organization_id, group_name, member_id)
            )
        ),
    )

    result = keycloak_service.ensure_shared_realm_organizations_and_users(
        organizations=[
            keycloak_service.KeycloakOrganizationSpec(
                alias="tenant-a",
                name="Tenant A",
            ),
            keycloak_service.KeycloakOrganizationSpec(
                alias="tenant-b",
                name="Tenant B",
            ),
        ],
        users=[
            keycloak_service.KeycloakUserSpec(
                username="alice",
                email="alice@example.com",
                password="demo-password",
                organization_alias="tenant-a",
                organization_group_names=("Approvers", "Viewers"),
            ),
            keycloak_service.KeycloakUserSpec(
                username="bob",
                email="bob@example.com",
                password="demo-password",
                organization_alias="tenant-b",
                display_name="Bob",
                organization_group_names=("Submitters",),
            ),
        ],
    )

    assert created_organizations == [("tenant-b", "Tenant B")]
    assert created_users == ["bob"]
    assert added_members == [
        ("org-tenant-a", "kc-alice"),
        ("org-tenant-b", "kc-bob"),
    ]
    assert added_group_memberships == [
        ("org-tenant-a", "Approvers", "member-alice"),
        ("org-tenant-a", "Viewers", "member-alice"),
        ("org-tenant-b", "Submitters", "member-bob"),
    ]
    assert ensured_groups == ["org-tenant-a:token"]
    assert result.shared_realm_name == "m8flow"
    assert result.service_issuer == "http://localhost:6842/realms/m8flow"
    assert result.organizations_by_alias["tenant-a"].created is False
    assert result.organizations_by_alias["tenant-b"].created is True
    assert result.users_by_username["alice"].created is False
    assert result.users_by_username["bob"].created is True
    assert any("tenant-a" in warning for warning in result.warnings)
    assert any("alice" in warning for warning in result.warnings)
