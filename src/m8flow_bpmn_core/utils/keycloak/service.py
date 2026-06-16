from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from m8flow_bpmn_core.utils.keycloak.config import (
    keycloak_admin_password,
    keycloak_admin_user,
    keycloak_url,
    master_realm_name,
    shared_realm_name,
)

DEFAULT_ORGANIZATIONAL_GROUP_NAMES = (
    "Approvers",
    "Designers",
    "Administrators",
    "Support",
    "Submitters",
    "Viewers",
)


class KeycloakProvisioningError(RuntimeError):
    """Raised when the local Keycloak provisioning flow cannot complete."""


@dataclass(frozen=True, slots=True)
class KeycloakOrganizationSpec:
    alias: str
    name: str


@dataclass(frozen=True, slots=True)
class KeycloakUserSpec:
    username: str
    email: str
    password: str
    organization_alias: str
    display_name: str | None = None
    organization_group_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProvisionedKeycloakOrganization:
    alias: str
    name: str
    organization_id: str
    created: bool


@dataclass(frozen=True, slots=True)
class ProvisionedKeycloakUser:
    username: str
    email: str
    user_id: str
    organization_alias: str
    organization_id: str
    organization_group_names: tuple[str, ...]
    created: bool


@dataclass(frozen=True, slots=True)
class ProvisionedKeycloakSharedRealmContext:
    shared_realm_name: str
    service_issuer: str
    organizations_by_alias: dict[str, ProvisionedKeycloakOrganization]
    users_by_username: dict[str, ProvisionedKeycloakUser]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _HttpResponse:
    status_code: int
    headers: dict[str, str]
    body_text: str


def shared_realm_service_issuer() -> str:
    return f"{keycloak_url()}/realms/{shared_realm_name()}"


def ensure_shared_realm_organizations_and_users(
    *,
    organizations: list[KeycloakOrganizationSpec],
    users: list[KeycloakUserSpec],
) -> ProvisionedKeycloakSharedRealmContext:
    token = get_master_admin_token()
    warnings: list[str] = []
    organizations_by_alias: dict[str, ProvisionedKeycloakOrganization] = {}

    for organization_spec in organizations:
        normalized_alias = organization_spec.alias.strip()
        normalized_name = organization_spec.name.strip()
        organization = get_organization_by_alias(normalized_alias, admin_token=token)
        if organization is None:
            organization = create_organization(
                normalized_alias,
                normalized_name,
                admin_token=token,
            )
            created = True
        else:
            created = False
            warnings.append(
                "Keycloak tenant "
                f"'{normalized_alias}' already exists in the shared realm; "
                "reusing it."
            )
            ensure_organization_role_groups(
                _required_string(
                    organization,
                    "id",
                    message=(
                        f"Keycloak organization '{normalized_alias}' does not "
                        "expose an id."
                    ),
                ),
                admin_token=token,
            )

        organization_id = _required_string(
            organization,
            "id",
            message=(
                f"Keycloak organization '{normalized_alias}' does not expose "
                "an id."
            ),
        )
        organization_name = (
            str(organization.get("name", "")).strip() or normalized_name
        )
        organizations_by_alias[normalized_alias] = ProvisionedKeycloakOrganization(
            alias=normalized_alias,
            name=organization_name,
            organization_id=organization_id,
            created=created,
        )

    users_by_username: dict[str, ProvisionedKeycloakUser] = {}
    shared_realm = shared_realm_name()
    for user_spec in users:
        normalized_username = user_spec.username.strip()
        normalized_email = user_spec.email.strip()
        organization = organizations_by_alias.get(user_spec.organization_alias.strip())
        if organization is None:
            raise KeycloakProvisioningError(
                "No provisioned Keycloak organization was available for alias "
                f"'{user_spec.organization_alias}'."
            )

        realm_user = get_realm_user_by_username(
            shared_realm,
            normalized_username,
            admin_token=token,
        )
        if realm_user is None:
            user_id = create_user_in_realm(
                shared_realm,
                normalized_username,
                user_spec.password,
                email=normalized_email,
                display_name=user_spec.display_name,
                admin_token=token,
            )
            created = True
        else:
            user_id = _required_string(
                realm_user,
                "id",
                message=(
                    f"Keycloak user '{normalized_username}' exists in shared "
                    "realm but does not expose an id."
                ),
            )
            created = False
            warnings.append(
                "Keycloak user "
                f"'{normalized_username}' already exists in shared realm "
                f"'{shared_realm}'; current credentials were left unchanged."
            )

        add_organization_member(
            organization.organization_id,
            user_id,
            admin_token=token,
        )

        organization_member = get_organization_member_by_username(
            organization.organization_id,
            normalized_username,
            admin_token=token,
        )
        member_id = user_id
        if organization_member is not None:
            member_id = _required_string(
                organization_member,
                "id",
                message=(
                    "Keycloak organization member "
                    f"'{normalized_username}' in '{organization.alias}' does "
                    "not expose an id."
                ),
            )

        for group_name in user_spec.organization_group_names:
            add_organization_group_member(
                organization.organization_id,
                group_name,
                member_id,
                admin_token=token,
            )

        users_by_username[normalized_username] = ProvisionedKeycloakUser(
            username=normalized_username,
            email=normalized_email,
            user_id=user_id,
            organization_alias=organization.alias,
            organization_id=organization.organization_id,
            organization_group_names=user_spec.organization_group_names,
            created=created,
        )

    return ProvisionedKeycloakSharedRealmContext(
        shared_realm_name=shared_realm,
        service_issuer=shared_realm_service_issuer(),
        organizations_by_alias=organizations_by_alias,
        users_by_username=users_by_username,
        warnings=tuple(warnings),
    )


def get_master_admin_token() -> str:
    response = _request(
        "POST",
        (
            f"{keycloak_url()}/realms/{quote(master_realm_name(), safe='')}"
            "/protocol/openid-connect/token"
        ),
        form_body={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": keycloak_admin_user(),
            "password": keycloak_admin_password(),
        },
    )
    payload = _json_body(response)
    if not isinstance(payload, dict):
        raise KeycloakProvisioningError(
            "Keycloak token endpoint did not return a JSON object."
        )
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise KeycloakProvisioningError(
            "Keycloak token endpoint did not return an access_token."
        )
    return access_token.strip()


def get_organization_by_id(
    organization_id: str,
    *,
    admin_token: str,
) -> dict[str, Any] | None:
    response = _request(
        "GET",
        _shared_realm_organizations_url(organization_id),
        headers=_bearer_headers(admin_token),
        allow_statuses={404},
    )
    if response.status_code == 404:
        return None
    payload = _json_body(response)
    return payload if isinstance(payload, dict) else None


def get_organization_by_alias(
    alias: str,
    *,
    admin_token: str,
) -> dict[str, Any] | None:
    normalized_alias = alias.strip()
    if not normalized_alias:
        raise KeycloakProvisioningError("Keycloak organization alias is required.")

    def _load_organizations(*, exact: bool | None) -> list[dict[str, Any]]:
        params: dict[str, str] = {
            "briefRepresentation": "false",
            "max": "100",
        }
        if exact is not None:
            params["search"] = normalized_alias
            params["exact"] = "true" if exact else "false"
        response = _request(
            "GET",
            _shared_realm_organizations_url(query_params=params),
            headers=_bearer_headers(admin_token),
        )
        payload = _json_body(response)
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    organizations = _load_organizations(exact=True)
    if not organizations:
        organizations = _load_organizations(exact=False)
    if not organizations:
        organizations = _load_organizations(exact=None)

    for organization in organizations:
        if str(organization.get("alias", "")).strip() == normalized_alias:
            return organization
    return None


def create_organization(
    alias: str,
    name: str,
    *,
    enabled: bool = True,
    admin_token: str,
) -> dict[str, Any]:
    normalized_alias = alias.strip()
    normalized_name = name.strip() or normalized_alias
    response = _request(
        "POST",
        _shared_realm_organizations_url(),
        headers=_bearer_headers(admin_token),
        json_body={
            "alias": normalized_alias,
            "name": normalized_name,
            "enabled": enabled,
        },
    )
    location = response.headers.get("Location", "").strip()
    organization: dict[str, Any] | None = None
    if location:
        organization_id = location.rstrip("/").split("/")[-1]
        organization = get_organization_by_id(
            organization_id,
            admin_token=admin_token,
        )
    if organization is None:
        organization = get_organization_by_alias(
            normalized_alias,
            admin_token=admin_token,
        )
    if organization is None:
        raise KeycloakProvisioningError(
            f"Keycloak created organization '{normalized_alias}' but it "
            "could not be loaded afterward."
        )

    ensure_organization_role_groups(
        _required_string(
            organization,
            "id",
            message=(
                f"Keycloak organization '{normalized_alias}' does not expose "
                "an id."
            ),
        ),
        admin_token=admin_token,
    )
    return organization


def get_realm_user_by_username(
    realm: str,
    username: str,
    *,
    admin_token: str,
) -> dict[str, Any] | None:
    response = _request(
        "GET",
        _realm_users_url(
            realm,
            query_params={
                "username": username.strip(),
                "exact": "true",
                "max": "100",
            },
        ),
        headers=_bearer_headers(admin_token),
    )
    payload = _json_body(response)
    if not isinstance(payload, list):
        return None
    exact_matches = [
        item
        for item in payload
        if isinstance(item, dict)
        and str(item.get("username", "")).strip() == username.strip()
    ]
    if len(exact_matches) != 1:
        return None
    return exact_matches[0]


def create_user_in_realm(
    realm: str,
    username: str,
    password: str,
    *,
    email: str | None = None,
    display_name: str | None = None,
    enabled: bool = True,
    admin_token: str,
) -> str:
    first_name, last_name = _display_name_parts(display_name, username)
    response = _request(
        "POST",
        _realm_users_url(realm),
        headers=_bearer_headers(admin_token),
        json_body={
            "username": username.strip(),
            "enabled": enabled,
            "email": (email or "").strip(),
            "emailVerified": True,
            "firstName": first_name,
            "lastName": last_name,
            "credentials": [
                {
                    "type": "password",
                    "value": password,
                    "temporary": False,
                }
            ],
        },
    )
    location = response.headers.get("Location", "").strip()
    if not location:
        raise KeycloakProvisioningError(
            "Keycloak did not return a Location header when creating user "
            f"'{username}'."
        )
    user_id = location.rstrip("/").split("/")[-1]

    user_response = _request(
        "GET",
        _realm_user_url(realm, user_id),
        headers=_bearer_headers(admin_token),
    )
    user_payload = _json_body(user_response)
    if not isinstance(user_payload, dict):
        raise KeycloakProvisioningError(
            f"Keycloak user '{username}' could not be loaded after creation."
        )
    user_payload["requiredActions"] = []
    user_payload["emailVerified"] = True
    user_payload.setdefault("firstName", first_name)
    user_payload.setdefault("lastName", last_name)
    _request(
        "PUT",
        _realm_user_url(realm, user_id),
        headers=_bearer_headers(admin_token),
        json_body=user_payload,
    )
    return user_id


def add_organization_member(
    organization_id: str,
    user_id: str,
    *,
    admin_token: str,
) -> None:
    _request(
        "POST",
        _shared_realm_organizations_url(organization_id, "members"),
        headers=_bearer_headers(admin_token),
        json_body=user_id.strip(),
        allow_statuses={409},
    )


def get_organization_member_by_username(
    organization_id: str,
    username: str,
    *,
    admin_token: str,
) -> dict[str, Any] | None:
    normalized_username = username.strip()

    def _search(*, exact: bool) -> list[dict[str, Any]]:
        response = _request(
            "GET",
            _shared_realm_organizations_url(
                organization_id,
                "members",
                query_params={
                    "search": normalized_username,
                    "exact": "true" if exact else "false",
                    "max": "100",
                },
            ),
            headers=_bearer_headers(admin_token),
        )
        payload = _json_body(response)
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    members = _search(exact=True) or _search(exact=False)
    matches = [
        member
        for member in members
        if str(member.get("username", "")).strip() == normalized_username
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def get_organization_group_by_name(
    organization_id: str,
    group_name: str,
    *,
    admin_token: str,
) -> dict[str, Any] | None:
    response = _request(
        "GET",
        _shared_realm_organization_groups_url(
            organization_id,
            query_params={
                "search": group_name.strip(),
                "exact": "true",
                "briefRepresentation": "true",
                "populateHierarchy": "false",
                "subGroupsCount": "false",
                "max": "100",
            },
        ),
        headers=_bearer_headers(admin_token),
    )
    payload = _json_body(response)
    if not isinstance(payload, list):
        return None

    normalized_group_name = group_name.strip()
    for item in payload:
        if not isinstance(item, dict):
            continue
        if str(item.get("name", "")).strip() != normalized_group_name:
            continue
        group_path = str(item.get("path", "")).strip()
        if group_path in {"", normalized_group_name, f"/{normalized_group_name}"}:
            return item
    return None


def create_organization_group(
    organization_id: str,
    group_name: str,
    *,
    admin_token: str,
) -> dict[str, Any]:
    response = _request(
        "POST",
        _shared_realm_organization_groups_url(organization_id),
        headers=_bearer_headers(admin_token),
        json_body={"name": group_name.strip()},
    )
    location = response.headers.get("Location", "").strip()
    if location:
        group_id = location.rstrip("/").split("/")[-1]
        group = get_organization_group_by_id(
            organization_id,
            group_id,
            admin_token=admin_token,
        )
        if group is not None:
            return group
    group = get_organization_group_by_name(
        organization_id,
        group_name,
        admin_token=admin_token,
    )
    if group is None:
        raise KeycloakProvisioningError(
            f"Keycloak created organization group '{group_name}' in "
            f"organization '{organization_id}' but it could not be loaded "
            "afterward."
        )
    return group


def get_organization_group_by_id(
    organization_id: str,
    group_id: str,
    *,
    admin_token: str,
) -> dict[str, Any] | None:
    response = _request(
        "GET",
        _shared_realm_organization_groups_url(organization_id, group_id),
        headers=_bearer_headers(admin_token),
        allow_statuses={404},
    )
    if response.status_code == 404:
        return None
    payload = _json_body(response)
    return payload if isinstance(payload, dict) else None


def ensure_organization_role_groups(
    organization_id: str,
    *,
    admin_token: str,
    group_names: tuple[str, ...] | None = None,
) -> None:
    for group_name in group_names or DEFAULT_ORGANIZATIONAL_GROUP_NAMES:
        if not group_name.strip():
            continue
        group = get_organization_group_by_name(
            organization_id,
            group_name,
            admin_token=admin_token,
        )
        if group is None:
            create_organization_group(
                organization_id,
                group_name,
                admin_token=admin_token,
            )


def add_organization_group_member(
    organization_id: str,
    group_name: str,
    member_id: str,
    *,
    admin_token: str,
) -> None:
    group = get_organization_group_by_name(
        organization_id,
        group_name,
        admin_token=admin_token,
    )
    if group is None:
        group = create_organization_group(
            organization_id,
            group_name,
            admin_token=admin_token,
        )

    group_id = _required_string(
        group,
        "id",
        message=(
            f"Keycloak organization group '{group_name}' in organization "
            f"'{organization_id}' does not expose an id."
        ),
    )
    _request(
        "PUT",
        _shared_realm_organization_groups_url(
            organization_id,
            group_id,
            "members",
            member_id.strip(),
        ),
        headers=_bearer_headers(admin_token),
        allow_statuses={409},
    )


def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
    form_body: dict[str, str] | None = None,
    allow_statuses: set[int] | None = None,
    timeout: int = 30,
) -> _HttpResponse:
    request_headers = dict(headers or {})
    payload: bytes | None = None
    if json_body is not None:
        payload = json.dumps(json_body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    elif form_body is not None:
        payload = urlencode(form_body).encode("utf-8")
        request_headers.setdefault(
            "Content-Type", "application/x-www-form-urlencoded"
        )

    request = Request(url, data=payload, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return _HttpResponse(
                status_code=response.status,
                headers=dict(response.headers.items()),
                body_text=body,
            )
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if allow_statuses and exc.code in allow_statuses:
            return _HttpResponse(
                status_code=exc.code,
                headers=dict(exc.headers.items()),
                body_text=body,
            )
        raise KeycloakProvisioningError(
            f"Keycloak request failed with status {exc.code}: {method} {url} "
            f"{body[:500]}"
        ) from exc
    except URLError as exc:
        raise KeycloakProvisioningError(
            f"Unable to reach Keycloak at {url}: {exc.reason}"
        ) from exc


def _json_body(response: _HttpResponse) -> Any | None:
    normalized_body = response.body_text.strip()
    if not normalized_body:
        return None
    try:
        return json.loads(normalized_body)
    except json.JSONDecodeError as exc:
        raise KeycloakProvisioningError(
            "Keycloak returned invalid JSON: "
            f"{normalized_body[:500]}"
        ) from exc


def _required_string(
    payload: dict[str, Any],
    key: str,
    *,
    message: str,
) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise KeycloakProvisioningError(message)


def _realm_users_url(
    realm: str,
    *,
    query_params: dict[str, str] | None = None,
) -> str:
    base_url = (
        f"{keycloak_url()}/admin/realms/{quote(realm.strip(), safe='')}/users"
    )
    return _url_with_query(base_url, query_params)


def _realm_user_url(realm: str, user_id: str) -> str:
    return (
        f"{keycloak_url()}/admin/realms/{quote(realm.strip(), safe='')}/users/"
        f"{quote(user_id.strip(), safe='')}"
    )


def _shared_realm_organizations_url(
    *segments: str,
    query_params: dict[str, str] | None = None,
) -> str:
    base_url = (
        f"{keycloak_url()}/admin/realms/"
        f"{quote(shared_realm_name().strip(), safe='')}/organizations"
    )
    for segment in segments:
        normalized_segment = segment.strip()
        if normalized_segment:
            base_url = f"{base_url}/{quote(normalized_segment, safe='')}"
    return _url_with_query(base_url, query_params)


def _shared_realm_organization_groups_url(
    organization_id: str,
    *segments: str,
    query_params: dict[str, str] | None = None,
) -> str:
    base_url = _shared_realm_organizations_url(
        organization_id,
        "groups",
    )
    for segment in segments:
        normalized_segment = segment.strip()
        if normalized_segment:
            base_url = f"{base_url}/{quote(normalized_segment, safe='')}"
    return _url_with_query(base_url, query_params)


def _url_with_query(
    base_url: str,
    query_params: dict[str, str] | None,
) -> str:
    if not query_params:
        return base_url
    return f"{base_url}?{urlencode(query_params)}"


def _bearer_headers(admin_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json",
    }


def _display_name_parts(
    display_name: str | None,
    username: str,
) -> tuple[str, str]:
    normalized_display_name = str(display_name or "").strip()
    if not normalized_display_name:
        return username.strip(), "User"
    name_parts = normalized_display_name.split(None, 1)
    if len(name_parts) == 1:
        return name_parts[0], "User"
    return name_parts[0], name_parts[1]
