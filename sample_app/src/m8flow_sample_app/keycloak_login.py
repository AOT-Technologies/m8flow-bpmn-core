from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from m8flow_bpmn_core.utils.keycloak import shared_realm_service_issuer
from m8flow_bpmn_core.utils.keycloak.config import keycloak_url, shared_realm_name
from m8flow_bpmn_core.utils.keycloak.service import (
    KeycloakProvisioningError,
    get_master_admin_token,
)
from m8flow_sample_app.settings import Settings, get_settings

SHARED_KEYCLOAK_CALLBACK_PATH = "/session/keycloak/callback"


class KeycloakLoginError(RuntimeError):
    """Raised when the sample-app shared Keycloak login flow fails."""


@dataclass(frozen=True, slots=True)
class AuthenticatedSharedRealmUser:
    issuer: str
    subject: str
    username: str
    email: str | None
    access_token: str


def create_pkce_code_verifier() -> str:
    return secrets.token_urlsafe(48)


def pkce_code_challenge_for(verifier: str) -> str:
    normalized_verifier = verifier.strip()
    if not normalized_verifier:
        raise KeycloakLoginError("A PKCE code verifier is required.")
    digest = hashlib.sha256(normalized_verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def build_shared_realm_authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    login_hint: str | None = None,
    prompt: str | None = None,
) -> str:
    normalized_client_id = client_id.strip()
    normalized_redirect_uri = redirect_uri.strip()
    normalized_state = state.strip()
    normalized_code_challenge = code_challenge.strip()
    if not normalized_client_id:
        raise KeycloakLoginError(
            "A Keycloak client id is required for shared Keycloak login."
        )
    if not normalized_redirect_uri:
        raise KeycloakLoginError("A redirect URI is required for shared login.")
    if not normalized_state:
        raise KeycloakLoginError("An OIDC state value is required for shared login.")
    if not normalized_code_challenge:
        raise KeycloakLoginError(
            "A PKCE code challenge is required for shared login."
        )

    query_params = {
        "response_type": "code",
        "client_id": normalized_client_id,
        "redirect_uri": normalized_redirect_uri,
        "scope": "openid profile email",
        "state": normalized_state,
        "code_challenge": normalized_code_challenge,
        "code_challenge_method": "S256",
    }
    normalized_login_hint = str(login_hint or "").strip()
    if normalized_login_hint:
        query_params["login_hint"] = normalized_login_hint
    normalized_prompt = str(prompt or "").strip()
    if normalized_prompt:
        query_params["prompt"] = normalized_prompt
    return _realm_authorization_url(shared_realm_name(), query_params=query_params)


def exchange_shared_realm_authorization_code(
    *,
    code: str,
    client_id: str,
    redirect_uri: str,
    code_verifier: str,
) -> AuthenticatedSharedRealmUser:
    normalized_code = code.strip()
    normalized_client_id = client_id.strip()
    normalized_redirect_uri = redirect_uri.strip()
    normalized_code_verifier = code_verifier.strip()
    if not normalized_code:
        raise KeycloakLoginError("Keycloak did not return an authorization code.")
    if not normalized_client_id:
        raise KeycloakLoginError(
            "A Keycloak client id is required for shared Keycloak login."
        )
    if not normalized_redirect_uri:
        raise KeycloakLoginError("A redirect URI is required for shared login.")
    if not normalized_code_verifier:
        raise KeycloakLoginError("A PKCE code verifier is required for shared login.")

    response_payload = _token_request(
        realm_name=shared_realm_name(),
        form_body={
            "grant_type": "authorization_code",
            "client_id": normalized_client_id,
            "code": normalized_code,
            "redirect_uri": normalized_redirect_uri,
            "code_verifier": normalized_code_verifier,
        },
    )
    return _authenticated_user_from_token_response(
        response_payload,
        client_id=normalized_client_id,
    )


def ensure_shared_realm_browser_client(
    *,
    client_id: str,
    redirect_uris: tuple[str, ...] | None = None,
    web_origins: tuple[str, ...] | None = None,
) -> None:
    normalized_client_id = client_id.strip()
    if not normalized_client_id:
        raise KeycloakProvisioningError(
            "A shared Keycloak browser client id is required."
        )

    normalized_redirect_uris = _normalize_url_values(
        redirect_uris or shared_login_client_redirect_uris()
    )
    normalized_web_origins = _normalize_url_values(
        web_origins or shared_login_client_web_origins()
    )
    if not normalized_redirect_uris:
        raise KeycloakProvisioningError(
            "At least one shared Keycloak redirect URI is required."
        )

    admin_token = get_master_admin_token()
    realm_name = shared_realm_name()
    existing_client = _get_realm_client_by_client_id(
        realm_name,
        normalized_client_id,
        admin_token=admin_token,
    )

    desired_root_url = _shared_client_root_url(
        normalized_web_origins,
        normalized_redirect_uris,
    )
    desired_payload = {
        "clientId": normalized_client_id,
        "name": "m8flow-bpmn-core Sample App",
        "description": (
            "Browser login client provisioned for the m8flow-bpmn-core "
            "sample app."
        ),
        "enabled": True,
        "protocol": "openid-connect",
        "publicClient": True,
        "standardFlowEnabled": True,
        "directAccessGrantsEnabled": False,
        "implicitFlowEnabled": False,
        "serviceAccountsEnabled": False,
        "redirectUris": list(normalized_redirect_uris),
        "webOrigins": list(normalized_web_origins),
        "rootUrl": desired_root_url,
        "baseUrl": desired_root_url or "",
    }

    if existing_client is None:
        _create_realm_client(
            realm_name,
            desired_payload,
            admin_token=admin_token,
        )
        return

    existing_client_id = _required_provisioning_string(
        existing_client,
        "id",
        message=(
            "Keycloak client "
            f"'{normalized_client_id}' exists but does not expose an id."
        ),
    )
    if not _shared_client_matches(
        existing_client,
        redirect_uris=normalized_redirect_uris,
        web_origins=normalized_web_origins,
        root_url=desired_root_url,
    ):
        updated_payload = dict(existing_client)
        updated_payload.update(desired_payload)
        _update_realm_client(
            realm_name,
            existing_client_id,
            updated_payload,
            admin_token=admin_token,
        )


def shared_login_client_redirect_uris(
    *,
    settings: Settings | None = None,
) -> tuple[str, ...]:
    return tuple(
        f"{base_url}{SHARED_KEYCLOAK_CALLBACK_PATH}"
        for base_url in _shared_login_base_urls(settings=settings)
    )


def shared_login_client_web_origins(
    *,
    settings: Settings | None = None,
) -> tuple[str, ...]:
    return _shared_login_base_urls(settings=settings)


def _authenticated_user_from_token_response(
    response_payload: dict[str, Any],
    *,
    client_id: str,
) -> AuthenticatedSharedRealmUser:
    access_token = _required_string(
        response_payload,
        "access_token",
        message="Keycloak did not return an access token.",
    )
    identity_token = response_payload.get("id_token")
    token_for_claims = (
        identity_token.strip()
        if isinstance(identity_token, str) and identity_token.strip()
        else access_token
    )
    claims = _decode_jwt_claims(token_for_claims)

    issuer = str(claims.get("iss", "")).strip() or shared_realm_service_issuer()
    if issuer != shared_realm_service_issuer():
        raise KeycloakLoginError(
            "Keycloak returned a token from an unexpected issuer."
        )

    audience = claims.get("aud")
    if audience is not None and not _audience_contains_client_id(audience, client_id):
        raise KeycloakLoginError(
            "Keycloak returned a token for an unexpected client id."
        )

    subject = str(claims.get("sub", "")).strip()
    if not subject:
        raise KeycloakLoginError("Keycloak token did not include a subject claim.")

    token_username = str(claims.get("preferred_username", "")).strip()
    if not token_username:
        raise KeycloakLoginError(
            "Keycloak token did not include a preferred_username claim."
        )

    email_value = str(claims.get("email", "")).strip() or None
    return AuthenticatedSharedRealmUser(
        issuer=issuer,
        subject=subject,
        username=token_username,
        email=email_value,
        access_token=access_token,
    )


def _token_request(
    *,
    realm_name: str,
    form_body: dict[str, str],
) -> dict[str, Any]:
    request = Request(
        _realm_token_url(realm_name),
        data=urlencode(form_body).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            body_text = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        payload = _json_loads(body_text, error_type=KeycloakLoginError)
        error_description = ""
        if isinstance(payload, dict):
            error_description = str(payload.get("error_description", "")).strip()
        if exc.code == 400 and error_description:
            raise KeycloakLoginError(error_description) from exc
        raise KeycloakLoginError(
            "Keycloak login request failed with status "
            f"{exc.code}: {body_text[:500]}"
        ) from exc
    except URLError as exc:
        raise KeycloakLoginError(
            "Unable to reach Keycloak for shared login: "
            f"{exc.reason}"
        ) from exc

    payload = _json_loads(body_text, error_type=KeycloakLoginError)
    if not isinstance(payload, dict):
        raise KeycloakLoginError("Keycloak did not return a JSON object.")
    return payload


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise KeycloakLoginError("Keycloak returned an invalid JWT token.")
    payload_segment = parts[1]
    padding = "=" * (-len(payload_segment) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{payload_segment}{padding}")
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise KeycloakLoginError(
            "Keycloak returned a JWT token with an unreadable payload."
        ) from exc
    if not isinstance(payload, dict):
        raise KeycloakLoginError(
            "Keycloak returned a JWT token without an object payload."
        )
    return payload


def _audience_contains_client_id(audience: Any, client_id: str) -> bool:
    if isinstance(audience, str):
        return audience.strip() == client_id
    if isinstance(audience, list):
        return any(str(item).strip() == client_id for item in audience)
    return False


def _get_realm_client_by_client_id(
    realm_name: str,
    client_id: str,
    *,
    admin_token: str,
) -> dict[str, Any] | None:
    response = _admin_request(
        "GET",
        _realm_clients_url(
            realm_name,
            query_params={"clientId": client_id, "max": "100"},
        ),
        admin_token=admin_token,
    )
    payload = _json_loads(response.body_text, error_type=KeycloakProvisioningError)
    if not isinstance(payload, list):
        return None
    matches = [
        item
        for item in payload
        if isinstance(item, dict)
        and str(item.get("clientId", "")).strip() == client_id
    ]
    if len(matches) != 1:
        return None

    client_uuid = _required_provisioning_string(
        matches[0],
        "id",
        message=f"Keycloak client '{client_id}' did not expose an id.",
    )
    full_response = _admin_request(
        "GET",
        _realm_client_url(realm_name, client_uuid),
        admin_token=admin_token,
    )
    full_payload = _json_loads(
        full_response.body_text,
        error_type=KeycloakProvisioningError,
    )
    return full_payload if isinstance(full_payload, dict) else None


def _create_realm_client(
    realm_name: str,
    payload: dict[str, Any],
    *,
    admin_token: str,
) -> None:
    _admin_request(
        "POST",
        _realm_clients_url(realm_name),
        admin_token=admin_token,
        json_body=payload,
    )


def _update_realm_client(
    realm_name: str,
    client_uuid: str,
    payload: dict[str, Any],
    *,
    admin_token: str,
) -> None:
    _admin_request(
        "PUT",
        _realm_client_url(realm_name, client_uuid),
        admin_token=admin_token,
        json_body=payload,
    )


def _admin_request(
    method: str,
    url: str,
    *,
    admin_token: str,
    json_body: dict[str, Any] | None = None,
) -> _HttpResponse:
    request_headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json",
    }
    request_payload = (
        json.dumps(json_body).encode("utf-8") if json_body is not None else None
    )
    request = Request(
        url,
        data=request_payload,
        headers=request_headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=30) as response:
            body_text = response.read().decode("utf-8", errors="replace")
            return _HttpResponse(
                status_code=response.status,
                headers=dict(response.headers.items()),
                body_text=body_text,
            )
    except HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise KeycloakProvisioningError(
            "Keycloak request failed with status "
            f"{exc.code}: {method} {url} {body_text[:500]}"
        ) from exc
    except URLError as exc:
        raise KeycloakProvisioningError(
            f"Unable to reach Keycloak at {url}: {exc.reason}"
        ) from exc


@dataclass(frozen=True, slots=True)
class _HttpResponse:
    status_code: int
    headers: dict[str, str]
    body_text: str


def _shared_client_matches(
    client_payload: dict[str, Any],
    *,
    redirect_uris: tuple[str, ...],
    web_origins: tuple[str, ...],
    root_url: str,
) -> bool:
    return (
        str(client_payload.get("protocol", "")).strip() == "openid-connect"
        and client_payload.get("enabled") is True
        and client_payload.get("publicClient") is True
        and client_payload.get("standardFlowEnabled") is True
        and client_payload.get("directAccessGrantsEnabled") is False
        and client_payload.get("implicitFlowEnabled") is False
        and client_payload.get("serviceAccountsEnabled") is False
        and tuple(_normalize_url_values(client_payload.get("redirectUris", [])))
        == redirect_uris
        and tuple(_normalize_url_values(client_payload.get("webOrigins", [])))
        == web_origins
        and str(client_payload.get("rootUrl", "")).strip() == root_url
        and str(client_payload.get("baseUrl", "")).strip() == root_url
    )


def _shared_client_root_url(
    web_origins: tuple[str, ...],
    redirect_uris: tuple[str, ...],
) -> str:
    if web_origins:
        return web_origins[0]
    if redirect_uris:
        return redirect_uris[0].rsplit("/", 1)[0]
    return ""


def _shared_login_base_urls(
    *,
    settings: Settings | None = None,
) -> tuple[str, ...]:
    settings = settings or get_settings()
    configured_values = [
        _normalize_optional_url(value)
        for value in (settings.keycloak_login_public_base_urls or "").split(",")
    ]
    configured_base_urls = tuple(
        value for value in configured_values if value is not None
    )
    if configured_base_urls:
        return configured_base_urls

    normalized_host = settings.host.strip().lower()
    candidates: list[str] = []
    if normalized_host and normalized_host != "0.0.0.0":
        candidates.append(f"http://{normalized_host}:{settings.port}")
    if normalized_host in {"127.0.0.1", "0.0.0.0"}:
        candidates.append(f"http://localhost:{settings.port}")
    if normalized_host in {"localhost", "0.0.0.0"}:
        candidates.append(f"http://127.0.0.1:{settings.port}")
    return tuple(_normalize_url_values(candidates))


def _normalize_url_values(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        values = [values]

    normalized_values: list[str] = []
    seen: set[str] = set()
    if not isinstance(values, (list, tuple, set)):
        return ()

    for value in values:
        normalized_value = _normalize_optional_url(value)
        if normalized_value is None or normalized_value in seen:
            continue
        seen.add(normalized_value)
        normalized_values.append(normalized_value)
    return tuple(normalized_values)


def _normalize_optional_url(value: Any) -> str | None:
    normalized_value = str(value or "").strip().rstrip("/")
    return normalized_value or None


def _realm_authorization_url(
    realm_name: str,
    *,
    query_params: dict[str, str],
) -> str:
    return (
        f"{keycloak_url()}/realms/{quote(realm_name.strip(), safe='')}"
        f"/protocol/openid-connect/auth?{urlencode(query_params)}"
    )


def _realm_token_url(realm_name: str) -> str:
    return (
        f"{keycloak_url()}/realms/{quote(realm_name.strip(), safe='')}"
        "/protocol/openid-connect/token"
    )


def _realm_clients_url(
    realm_name: str,
    *,
    query_params: dict[str, str] | None = None,
) -> str:
    base_url = (
        f"{keycloak_url()}/admin/realms/{quote(realm_name.strip(), safe='')}/clients"
    )
    if not query_params:
        return base_url
    return f"{base_url}?{urlencode(query_params)}"


def _realm_client_url(realm_name: str, client_uuid: str) -> str:
    return (
        f"{keycloak_url()}/admin/realms/{quote(realm_name.strip(), safe='')}"
        f"/clients/{quote(client_uuid.strip(), safe='')}"
    )


def _json_loads(body_text: str, *, error_type: type[Exception]) -> Any | None:
    normalized_body = body_text.strip()
    if not normalized_body:
        return None
    try:
        return json.loads(normalized_body)
    except json.JSONDecodeError as exc:
        raise error_type(
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
    raise KeycloakLoginError(message)


def _required_provisioning_string(
    payload: dict[str, Any],
    key: str,
    *,
    message: str,
) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise KeycloakProvisioningError(message)
