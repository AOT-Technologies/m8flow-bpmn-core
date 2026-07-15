from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from flask.testing import FlaskClient
from sqlalchemy import select

from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.utils.keycloak import (
    ProvisionedKeycloakOrganization,
    ProvisionedKeycloakSharedRealmContext,
    ProvisionedKeycloakUser,
)
from m8flow_sample_app.app import create_app
from m8flow_sample_app.auth import (
    SESSION_PENDING_SHARED_LOGIN_KEY,
    SESSION_TENANT_ID_KEY,
    SESSION_USER_ID_KEY,
)
from m8flow_sample_app.db import session_scope
from m8flow_sample_app.keycloak_login import (
    AuthenticatedSharedRealmUser,
)
from m8flow_sample_app.settings import get_settings
from m8flow_sample_app.shared_m8flow import (
    SHARED_M8FLOW_AUDIT_CONTEXT_KEY,
    publish_process_model_to_m8flow_backend,
)


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
    monkeypatch.setenv(
        "M8FLOW_SAMPLE_APP_M8FLOW_BACKEND_PROCESS_MODELS_DIR",
        str(tmp_path / "process_models"),
    )
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
    assert 'onchange="this.form.requestSubmit()"' in response_text
    assert "Load users" not in response_text


def test_shared_process_definitions_page_hides_custom_import_forms(
    shared_app_client: FlaskClient,
) -> None:
    tenant, admin_user = _load_shared_seeded_admin()
    with shared_app_client.session_transaction() as session_payload:
        session_payload[SESSION_TENANT_ID_KEY] = tenant.id
        session_payload[SESSION_USER_ID_KEY] = admin_user.id

    response = shared_app_client.get("/process-definitions")
    response_text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Custom workflow from BPMN file" not in response_text
    assert "Import existing model from local m8flow catalog" not in response_text
    assert 'enctype="multipart/form-data"' not in response_text


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


def test_shared_process_definitions_page_imports_catalog_model(
    shared_app_client: FlaskClient,
) -> None:
    tenant, admin_user = _load_shared_seeded_admin()
    audit_context = shared_app_client.application.extensions[
        SHARED_M8FLOW_AUDIT_CONTEXT_KEY
    ]
    assert audit_context.uses_shared_m8flow is True

    publish_process_model_to_m8flow_backend(
        audit_context=audit_context,
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        process_model_identifier="custom/catalog-demo",
        bpmn_name="Custom Catalog Demo",
        source_bpmn_xml=_demo_bpmn_xml(),
        primary_file_name="custom_catalog_demo.bpmn",
    )

    with shared_app_client.session_transaction() as session_payload:
        session_payload[SESSION_TENANT_ID_KEY] = tenant.id
        session_payload[SESSION_USER_ID_KEY] = admin_user.id

    response = shared_app_client.post(
        "/process-definitions/import-catalog",
        data={
            "process_model_identifier": "custom/catalog-demo",
            "bpmn_name": "",
        },
        follow_redirects=True,
    )
    response_text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "imported from local m8flow catalog model" in response_text
    assert "custom/catalog-demo" in response_text

    with session_scope() as db_session:
        imported_definition = db_session.scalar(
            select(BpmnProcessDefinitionModel).where(
                BpmnProcessDefinitionModel.m8f_tenant_id == tenant.id
            )
        )
        assert imported_definition is not None
        assert imported_definition.process_model_identifier == "custom/catalog-demo"
        assert imported_definition.bpmn_name == "Custom Catalog Demo"


def test_shared_process_definitions_page_imports_uploaded_bpmn_file(
    shared_app_client: FlaskClient,
) -> None:
    tenant, admin_user = _load_shared_seeded_admin()
    with shared_app_client.session_transaction() as session_payload:
        session_payload[SESSION_TENANT_ID_KEY] = tenant.id
        session_payload[SESSION_USER_ID_KEY] = admin_user.id

    response = shared_app_client.post(
        "/process-definitions/import-upload",
        data={
            "process_model_identifier": "custom/uploaded-demo",
            "bpmn_name": "Uploaded Demo",
            "bpmn_file": (
                BytesIO(_demo_bpmn_xml().encode("utf-8")),
                "uploaded_demo.bpmn",
            ),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    response_text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "imported from uploaded BPMN file" in response_text
    assert "uploaded_demo.bpmn" in response_text

    with session_scope() as db_session:
        imported_definition = db_session.scalar(
            select(BpmnProcessDefinitionModel).where(
                BpmnProcessDefinitionModel.m8f_tenant_id == tenant.id
            )
        )
        assert imported_definition is not None
        assert imported_definition.process_model_identifier == "custom/uploaded-demo"
        assert imported_definition.bpmn_name == "Uploaded Demo"

    process_models_root = Path(
        shared_app_client.application.extensions[
            SHARED_M8FLOW_AUDIT_CONTEXT_KEY
        ].process_models_root
    )
    published_bpmn_path = (
        process_models_root
        / tenant.id
        / "custom"
        / "uploaded-demo"
        / "uploaded_demo.bpmn"
    )
    assert published_bpmn_path.exists()


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


def _demo_bpmn_xml() -> str:
    fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "sample_app_demo.bpmn"
    return fixture_path.read_text(encoding="utf-8")


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
