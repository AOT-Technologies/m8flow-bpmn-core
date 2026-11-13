#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.error import URLError
from urllib.request import Request

import pytest

from m8flow_core import api
from m8flow_core.utils.http_client import DisallowedRedirectError, open_http_url
from m8flow_core.utils.keycloak import service as keycloak_service


class _RedirectHandler(BaseHTTPRequestHandler):
    """``/ok`` returns JSON; any other path redirects to ``?to=<location>``."""

    def do_GET(self) -> None:
        if self.path == "/ok":
            body = json.dumps([]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(302)
        self.send_header("Location", self.server.redirect_location)  # type: ignore[attr-defined]
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _redirecting_server(location: str) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
    server.redirect_location = location  # type: ignore[attr-defined]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_open_http_url_refuses_redirect_to_ftp() -> None:
    # The stdlib redirect handler already refuses file: and data: targets,
    # but follows ftp: ones.
    with _redirecting_server("ftp://127.0.0.1/catalog") as base_url:
        with pytest.raises(DisallowedRedirectError, match="non-http"):
            open_http_url(Request(f"{base_url}/start"), timeout=5)


def test_open_http_url_follows_http_redirect() -> None:
    with _redirecting_server("/ok") as base_url:
        with open_http_url(Request(f"{base_url}/start"), timeout=5) as response:
            assert response.status == 200
            assert json.loads(response.read()) == []


@pytest.mark.parametrize("url", ["ftp://127.0.0.1/x", "file:///etc/passwd"])
def test_open_http_url_refuses_non_http_initial_url(url: str) -> None:
    with pytest.raises(URLError, match="only http and https"):
        open_http_url(Request(url), timeout=5)


def test_connector_proxy_refuses_redirect_to_ftp() -> None:
    with _redirecting_server("ftp://127.0.0.1/commands") as base_url:
        with pytest.raises(api.ServiceTaskExecutionError, match="non-http"):
            api.build_connector_proxy_service_task_registry(base_url)


def test_keycloak_request_refuses_redirect_to_ftp() -> None:
    with _redirecting_server("ftp://127.0.0.1/realms") as base_url:
        with pytest.raises(
            keycloak_service.KeycloakProvisioningError, match="non-http"
        ):
            keycloak_service._request("GET", f"{base_url}/realms")
