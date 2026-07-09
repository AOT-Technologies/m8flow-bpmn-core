from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from sqlalchemy import select

from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.utils.keycloak import (
    KeycloakUserSpec,
    ProvisionedKeycloakOrganization,
    ProvisionedKeycloakSharedRealmContext,
    ProvisionedKeycloakUser,
)
from m8flow_sample_app.db import run_migrations, session_scope
from m8flow_sample_app.seed import seed_static_reference_data
from m8flow_sample_app.settings import get_settings
from m8flow_sample_app.shared_m8flow import (
    SharedM8flowAuditContext,
    backend_container_names,
    discover_shared_m8flow_audit_context,
)


def test_auto_mode_detects_shared_m8flow_for_postgres_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "M8FLOW_SAMPLE_APP_DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:6843/postgres",
    )
    monkeypatch.setenv(
        "M8FLOW_SAMPLE_APP_M8FLOW_BACKEND_PROCESS_MODELS_DIR",
        str(tmp_path),
    )
    get_settings.cache_clear()

    context = discover_shared_m8flow_audit_context()

    assert context.uses_shared_m8flow is True
    assert context.mode == "shared"
    assert context.database_name == "postgres"
    assert context.process_models_root == tmp_path
    assert context.warnings == ()
    get_settings.cache_clear()


def test_off_mode_disables_shared_m8flow_detection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "M8FLOW_SAMPLE_APP_DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:6843/postgres",
    )
    monkeypatch.setenv("M8FLOW_SAMPLE_APP_M8FLOW_AUDIT_MODE", "off")
    monkeypatch.setenv(
        "M8FLOW_SAMPLE_APP_M8FLOW_BACKEND_PROCESS_MODELS_DIR",
        str(tmp_path),
    )
    get_settings.cache_clear()

    context = discover_shared_m8flow_audit_context()

    assert context.uses_shared_m8flow is False
    assert context.mode == "standalone"
    assert context.process_models_root is None
    get_settings.cache_clear()


def test_non_shared_database_stays_in_standalone_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "M8FLOW_SAMPLE_APP_DATABASE_URL",
        f"sqlite+pysqlite:///{tmp_path / 'sample_app.sqlite'}",
    )
    get_settings.cache_clear()

    context = discover_shared_m8flow_audit_context()

    assert context.uses_shared_m8flow is False
    assert context.mode == "standalone"
    assert context.database_name == str(tmp_path / "sample_app.sqlite")
    get_settings.cache_clear()


def test_backend_container_names_are_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "M8FLOW_SAMPLE_APP_M8FLOW_BACKEND_CONTAINER_NAMES",
        "m8flow-a,m8flow-b,m8flow-a, ,m8flow-c",
    )
    get_settings.cache_clear()

    names = backend_container_names()

    assert names == ["m8flow-a", "m8flow-b", "m8flow-c"]
    get_settings.cache_clear()


def test_shared_seed_uses_keycloak_organization_and_user_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_sqlite_database(monkeypatch, tmp_path / "sample_app_shared_seed.sqlite")
    run_migrations()
    _prime_m8flow_import_order()

    keycloak_context = _fake_keycloak_context()
    monkeypatch.setattr(
        "m8flow_sample_app.seed.ensure_shared_realm_organizations_and_users",
        lambda **_: keycloak_context,
    )

    with session_scope() as db_session:
        seed_static_reference_data(
            db_session,
            audit_context=_shared_audit_context(),
        )

    with session_scope() as db_session:
        alpha_tenant = db_session.scalar(
            select(M8flowTenantModel).where(
                M8flowTenantModel.slug == "sample-tenant-alpha"
            )
        )
        assert alpha_tenant is not None
        assert alpha_tenant.id == "org-alpha"

        alpha_admin = db_session.scalar(
            select(UserModel).where(UserModel.username == "alpha-admin")
        )
        assert alpha_admin is not None
        assert alpha_admin.service == keycloak_context.service_issuer
        assert alpha_admin.service_id == "kc-alpha-admin"
        assert alpha_admin.tenant_specific_field_1 == "org-alpha"
        assert alpha_admin.tenant_specific_field_2 == "sample-tenant-alpha"

    get_settings.cache_clear()


def test_shared_seed_realigns_legacy_tenant_and_user_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_sqlite_database(
        monkeypatch,
        tmp_path / "sample_app_shared_realign.sqlite",
    )
    run_migrations()
    _prime_m8flow_import_order()

    with session_scope() as db_session:
        seed_static_reference_data(db_session)

    keycloak_context = _fake_keycloak_context()
    monkeypatch.setattr(
        "m8flow_sample_app.seed.ensure_shared_realm_organizations_and_users",
        lambda **_: keycloak_context,
    )

    with session_scope() as db_session:
        seed_static_reference_data(
            db_session,
            audit_context=_shared_audit_context(),
        )

    with session_scope() as db_session:
        alpha_tenant = db_session.scalar(
            select(M8flowTenantModel).where(
                M8flowTenantModel.slug == "sample-tenant-alpha"
            )
        )
        assert alpha_tenant is not None
        assert alpha_tenant.id == "org-alpha"
        assert (
            db_session.scalar(
                select(M8flowTenantModel).where(
                    M8flowTenantModel.id == "sample-tenant-alpha"
                )
            )
            is None
        )

        alpha_users = list(
            db_session.scalars(
                select(UserModel).where(
                    UserModel.tenant_specific_field_2 == "sample-tenant-alpha"
                )
            )
        )
        assert sorted(user.username for user in alpha_users) == [
            "alpha-admin",
            "alpha-finance-reviewer",
            "alpha-operator",
            "alpha-reviewer",
            "alpha-supervisor",
        ]
        assert all(
            user.service == keycloak_context.service_issuer for user in alpha_users
        )
        assert all(user.tenant_specific_field_1 == "org-alpha" for user in alpha_users)

    get_settings.cache_clear()


def test_shared_seed_uses_username_as_keycloak_password(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_sqlite_database(
        monkeypatch,
        tmp_path / "sample_app_shared_passwords.sqlite",
    )
    run_migrations()
    _prime_m8flow_import_order()

    captured_users: list[KeycloakUserSpec] = []

    def _capture_keycloak_specs(
        *,
        organizations: list[object],
        users: list[KeycloakUserSpec],
    ) -> ProvisionedKeycloakSharedRealmContext:
        del organizations
        captured_users.extend(users)
        return _fake_keycloak_context()

    monkeypatch.setattr(
        "m8flow_sample_app.seed.ensure_shared_realm_organizations_and_users",
        _capture_keycloak_specs,
    )

    with session_scope() as db_session:
        seed_static_reference_data(
            db_session,
            audit_context=_shared_audit_context(),
        )

    assert captured_users
    assert all(user.password == user.username for user in captured_users)

    get_settings.cache_clear()


def _configure_sqlite_database(
    monkeypatch: pytest.MonkeyPatch,
    database_path: Path,
) -> None:
    monkeypatch.setenv(
        "M8FLOW_SAMPLE_APP_DATABASE_URL",
        f"sqlite+pysqlite:///{database_path}",
    )
    get_settings.cache_clear()


def _prime_m8flow_import_order() -> None:
    importlib.import_module("m8flow_bpmn_core.api")


def _shared_audit_context() -> SharedM8flowAuditContext:
    return SharedM8flowAuditContext(
        mode="shared",
        requested_mode="shared",
        database_name="postgres",
        process_models_root=None,
        backend_container_name=None,
        backend_tenant_root_override=None,
        warnings=(),
    )


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
