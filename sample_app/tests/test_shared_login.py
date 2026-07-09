from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from flask.testing import FlaskClient
from sqlalchemy import select

from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.utils.keycloak import (
    ProvisionedKeycloakOrganization,
    ProvisionedKeycloakSharedRealmContext,
    ProvisionedKeycloakUser,
)
from m8flow_sample_app.app import create_app
from m8flow_sample_app.auth import SESSION_PENDING_SHARED_LOGIN_KEY
from m8flow_sample_app.db import session_scope
from m8flow_sample_app.keycloak_login import (
    AuthenticatedSharedRealmUser,
)
from m8flow_sample_app.settings import get_settings


@pytest.fixture
def shared_app_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[FlaskClient]:
    database_path = tmp_path / "sample_app_shared_login.sqlite"
    monkeypatch.setenv(
        "M8FLOW_SAMPLE_APP_DATABASE_URL",
        f"sqlite+pysqlite:///{database_path}",
    )
    monkeypatch.setenv("M8FLOW_SAMPLE_APP_SECRET_KEY", "sample-app-test-secret")
    monkeypatch.setenv("M8FLOW_SAMPLE_APP_M8FLOW_AUDIT_MODE", "shared")
    monkeypatch.setenv("M8FLOW_SAMPLE_APP_SCHEDULER_ENABLED", "false")
    monkeypatch.setattr(
        "m8flow_sample_app.seed.ensure_shared_realm_organizations_and_users",
        lambda **_: _fake_keycloak_context(),
    )
    monkeypatch.setattr(
        "m8flow_sample_app.app.ensure_shared_realm_browser_client",
        lambda **_: None,
    )
    get_settings.cache_clear()

    app = create_app()
    app.config.update(TESTING=True)

    with app.test_client() as client:
        yield client

    get_settings.cache_clear()


def test_shared_session_page_shows_keycloak_prompt(
    shared_app_client: FlaskClient,
) -> None:
    response = shared_app_client.get("/session/select")
    response_text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Shared audit mode is active" in response_text
    assert 'name="password"' not in response_text
    assert "Continue to Keycloak" in response_text


def test_shared_login_start_redirects_to_keycloak_authorization_endpoint(
    shared_app_client: FlaskClient,
) -> None:
    tenant, admin_user = _load_shared_seeded_admin()

    response = shared_app_client.post(
        "/session/keycloak/start",
        data={
            "tenant_id": tenant.id,
            "user_id": str(admin_user.id),
        },
    )

    assert response.status_code == 302
    location = response.headers["Location"]
    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    assert parsed.path.endswith("/protocol/openid-connect/auth")
    assert query["client_id"] == ["m8flow-sample-app"]
    assert query["login_hint"] == ["alpha-admin"]
    assert query["prompt"] == ["login"]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["http://localhost/session/keycloak/callback"]

    with shared_app_client.session_transaction() as session_payload:
        pending_login = session_payload[SESSION_PENDING_SHARED_LOGIN_KEY]
    assert pending_login["tenant_id"] == tenant.id
    assert pending_login["expected_user_id"] == admin_user.id
    assert pending_login["redirect_uri"] == "http://localhost/session/keycloak/callback"
    assert pending_login["state"]
    assert pending_login["code_verifier"]


def test_shared_login_callback_authenticates_selected_user(
    shared_app_client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, admin_user = _load_shared_seeded_admin()
    shared_app_client.post(
        "/session/keycloak/start",
        data={
            "tenant_id": tenant.id,
            "user_id": str(admin_user.id),
        },
    )
    with shared_app_client.session_transaction() as session_payload:
        pending_login = session_payload[SESSION_PENDING_SHARED_LOGIN_KEY]

    monkeypatch.setattr(
        "m8flow_sample_app.web.exchange_shared_realm_authorization_code",
        lambda **_: AuthenticatedSharedRealmUser(
            issuer="http://localhost:6842/realms/m8flow",
            subject="kc-alpha-admin",
            username="alpha-admin",
            email="alpha-admin@example.com",
            access_token="token",
        ),
    )

    response = shared_app_client.get(
        "/session/keycloak/callback",
        query_string={
            "state": pending_login["state"],
            "code": "sample-code",
        },
        follow_redirects=True,
    )
    response_text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Shared Keycloak login succeeded for alpha-admin." in response_text
    assert "m8flow-bpmn-core Sample App" in response_text


def test_shared_login_callback_rejects_mismatched_user(
    shared_app_client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, admin_user = _load_shared_seeded_admin()
    shared_app_client.post(
        "/session/keycloak/start",
        data={
            "tenant_id": tenant.id,
            "user_id": str(admin_user.id),
        },
    )
    with shared_app_client.session_transaction() as session_payload:
        pending_login = session_payload[SESSION_PENDING_SHARED_LOGIN_KEY]

    monkeypatch.setattr(
        "m8flow_sample_app.web.exchange_shared_realm_authorization_code",
        lambda **_: AuthenticatedSharedRealmUser(
            issuer="http://localhost:6842/realms/m8flow",
            subject="kc-alpha-operator",
            username="alpha-operator",
            email="alpha-operator@example.com",
            access_token="token",
        ),
    )

    response = shared_app_client.get(
        "/session/keycloak/callback",
        query_string={
            "state": pending_login["state"],
            "code": "sample-code",
        },
        follow_redirects=True,
    )
    response_text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Keycloak authenticated a different user" in response_text
    assert "Select tenant and user" in response_text


def test_shared_login_callback_surfaces_keycloak_error(
    shared_app_client: FlaskClient,
) -> None:
    tenant, admin_user = _load_shared_seeded_admin()
    shared_app_client.post(
        "/session/keycloak/start",
        data={
            "tenant_id": tenant.id,
            "user_id": str(admin_user.id),
        },
    )
    with shared_app_client.session_transaction() as session_payload:
        pending_login = session_payload[SESSION_PENDING_SHARED_LOGIN_KEY]

    response = shared_app_client.get(
        "/session/keycloak/callback",
        query_string={
            "state": pending_login["state"],
            "error": "access_denied",
            "error_description": "The user cancelled the login flow.",
        },
        follow_redirects=True,
    )
    response_text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "The user cancelled the login flow." in response_text
    assert "Select tenant and user" in response_text


def _load_shared_seeded_admin() -> tuple[M8flowTenantModel, UserModel]:
    with session_scope() as db_session:
        tenant = db_session.scalar(
            select(M8flowTenantModel).where(
                M8flowTenantModel.slug == "sample-tenant-alpha"
            )
        )
        assert tenant is not None
        admin_user = db_session.scalar(
            select(UserModel).where(
                UserModel.username == "alpha-admin",
                UserModel.tenant_specific_field_1 == tenant.id,
            )
        )
        assert admin_user is not None
        return tenant, admin_user


def _fake_keycloak_context() -> ProvisionedKeycloakSharedRealmContext:
    return ProvisionedKeycloakSharedRealmContext(
        shared_realm_name="m8flow",
        service_issuer="http://localhost:6842/realms/m8flow",
        organizations_by_alias={
            "sample-tenant-alpha": ProvisionedKeycloakOrganization(
                alias="sample-tenant-alpha",
                name="Sample Tenant Alpha",
                organization_id="org-alpha",
                created=True,
            ),
            "sample-tenant-beta": ProvisionedKeycloakOrganization(
                alias="sample-tenant-beta",
                name="Sample Tenant Beta",
                organization_id="org-beta",
                created=True,
            ),
        },
        users_by_username={
            "alpha-admin": _fake_keycloak_user(
                username="alpha-admin",
                organization_alias="sample-tenant-alpha",
                organization_id="org-alpha",
                user_id="kc-alpha-admin",
            ),
            "alpha-operator": _fake_keycloak_user(
                username="alpha-operator",
                organization_alias="sample-tenant-alpha",
                organization_id="org-alpha",
                user_id="kc-alpha-operator",
            ),
            "alpha-finance-reviewer": _fake_keycloak_user(
                username="alpha-finance-reviewer",
                organization_alias="sample-tenant-alpha",
                organization_id="org-alpha",
                user_id="kc-alpha-finance-reviewer",
            ),
            "alpha-reviewer": _fake_keycloak_user(
                username="alpha-reviewer",
                organization_alias="sample-tenant-alpha",
                organization_id="org-alpha",
                user_id="kc-alpha-reviewer",
            ),
            "alpha-supervisor": _fake_keycloak_user(
                username="alpha-supervisor",
                organization_alias="sample-tenant-alpha",
                organization_id="org-alpha",
                user_id="kc-alpha-supervisor",
            ),
            "beta-admin": _fake_keycloak_user(
                username="beta-admin",
                organization_alias="sample-tenant-beta",
                organization_id="org-beta",
                user_id="kc-beta-admin",
            ),
            "beta-operator": _fake_keycloak_user(
                username="beta-operator",
                organization_alias="sample-tenant-beta",
                organization_id="org-beta",
                user_id="kc-beta-operator",
            ),
            "beta-finance-reviewer": _fake_keycloak_user(
                username="beta-finance-reviewer",
                organization_alias="sample-tenant-beta",
                organization_id="org-beta",
                user_id="kc-beta-finance-reviewer",
            ),
            "beta-reviewer": _fake_keycloak_user(
                username="beta-reviewer",
                organization_alias="sample-tenant-beta",
                organization_id="org-beta",
                user_id="kc-beta-reviewer",
            ),
            "beta-supervisor": _fake_keycloak_user(
                username="beta-supervisor",
                organization_alias="sample-tenant-beta",
                organization_id="org-beta",
                user_id="kc-beta-supervisor",
            ),
        },
        warnings=(),
    )


def _fake_keycloak_user(
    *,
    username: str,
    organization_alias: str,
    organization_id: str,
    user_id: str,
) -> ProvisionedKeycloakUser:
    return ProvisionedKeycloakUser(
        username=username,
        email=f"{username}@example.com",
        user_id=user_id,
        organization_alias=organization_alias,
        organization_id=organization_id,
        organization_group_names=("Viewers",),
        created=True,
    )
