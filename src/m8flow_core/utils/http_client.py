#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

"""Outbound HTTP restricted to the http and https schemes.

The standard ``urlopen`` opener can also fetch ``ftp://``, ``file://`` and
``data:`` URLs, and its redirect handler follows a server's redirect to
``ftp://``. Checking only the initial URL therefore does not keep a request on
http(s). This opener has no handlers for other schemes and refuses redirects
that leave http(s).
"""

from __future__ import annotations

from http.client import HTTPMessage
from typing import IO, Any
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPDefaultErrorHandler,
    HTTPErrorProcessor,
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    UnknownHandler,
)

ALLOWED_URL_SCHEMES = frozenset({"http", "https"})


def is_http_url(url: str) -> bool:
    """Return whether ``url`` is an absolute http or https URL."""
    parts = urlsplit(url)
    return parts.scheme.lower() in ALLOWED_URL_SCHEMES and bool(parts.netloc)


class DisallowedRedirectError(URLError):
    """A server redirected an http(s) request to a non-http(s) URL."""


class _HttpOnlyRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        # ``newurl`` is already resolved against the request URL here.
        if not is_http_url(newurl):
            raise DisallowedRedirectError(
                f"redirect to a non-http(s) URL was refused: {newurl!r}"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _build_opener() -> OpenerDirector:
    opener = OpenerDirector()
    for handler in (
        ProxyHandler(),
        UnknownHandler(),
        HTTPHandler(),
        HTTPSHandler(),
        HTTPDefaultErrorHandler(),
        _HttpOnlyRedirectHandler(),
        HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


def open_http_url(request: Request, *, timeout: float) -> Any:
    """Open ``request`` over http(s) only, following http(s) redirects.

    Raises ``URLError`` (``DisallowedRedirectError`` for a refused redirect)
    or ``HTTPError`` like ``urlopen``. Callers must validate the initial URL
    with ``is_http_url`` first so the error they raise names their setting.
    """
    if not is_http_url(request.full_url):
        raise URLError(f"only http and https URLs are allowed: {request.full_url!r}")
    return _build_opener().open(request, timeout=timeout)
