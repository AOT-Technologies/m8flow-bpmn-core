from __future__ import annotations

import os

# Bandit: local demo fallback only.
DEFAULT_KEYCLOAK_ADMIN_PASSWORD = "admin"  # nosec B105
DEFAULT_KEYCLOAK_ADMIN_USER = "admin"
DEFAULT_KEYCLOAK_URL = "http://localhost:6842"
DEFAULT_MASTER_REALM_NAME = "master"
DEFAULT_SHARED_REALM_NAME = "m8flow"


def _get(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip()
    if not normalized:
        return default
    return normalized


def keycloak_url() -> str:
    return (
        _get("KEYCLOAK_URL")
        or _get("M8FLOW_KEYCLOAK_URL")
        or DEFAULT_KEYCLOAK_URL
    ).rstrip("/")


def keycloak_admin_user() -> str:
    return (
        _get("KEYCLOAK_ADMIN_USER")
        or _get("M8FLOW_KEYCLOAK_ADMIN_USER")
        or DEFAULT_KEYCLOAK_ADMIN_USER
    )


def keycloak_admin_password() -> str:
    return (
        _get("KEYCLOAK_ADMIN_PASSWORD")
        or _get("M8FLOW_KEYCLOAK_ADMIN_PASSWORD")
        or DEFAULT_KEYCLOAK_ADMIN_PASSWORD
    )


def shared_realm_name() -> str:
    return _get("M8FLOW_KEYCLOAK_SHARED_REALM") or DEFAULT_SHARED_REALM_NAME


def master_realm_name() -> str:
    return _get("M8FLOW_KEYCLOAK_MASTER_REALM") or DEFAULT_MASTER_REALM_NAME

