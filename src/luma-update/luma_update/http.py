# SPDX-License-Identifier: Apache-2.0
"""Bounded HTTPS requests with the standard library.

TLS is verified against the system trust store, bodies are size-limited, and
no cookie, identifier or detailed user agent is sent. ``get`` (the public,
signed update graph) may follow redirects that stay on HTTPS. ``request_json``
(Hub's API, which carries bearer tokens) follows no redirect at all: urllib
copies request headers, Authorization included, to wherever a redirect points.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

__all__ = ("Http", "HttpError", "USER_AGENT")

USER_AGENT = "luma-update/1"


class HttpError(Exception):
    error_class = "network"

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_insecure: bool) -> None:
        super().__init__()
        self.allow_insecure = allow_insecure

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.allow_insecure and not newurl.startswith("https://"):
            raise HttpError(f"refused a redirect away from HTTPS to {newurl[:80]}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HttpError(f"refused a redirect (HTTP {code}) from {req.full_url[:80]} to {newurl[:80]}", code)


class Http:
    def __init__(self, allow_insecure: bool = False, timeout: float = 30.0) -> None:
        self.allow_insecure = allow_insecure
        self.timeout = timeout
        # An empty ProxyHandler: no proxy discovery from a service's environment.
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _HttpsOnlyRedirect(allow_insecure),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        self._api_opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def _check_url(self, url: str) -> None:
        if url.startswith("https://"):
            return
        if self.allow_insecure and (url.startswith("http://") or url.startswith("file://")):
            return
        raise HttpError(f"refused a non-HTTPS URL: {url[:80]}")

    def get(self, url: str, max_bytes: int, headers: dict | None = None) -> bytes:
        self._check_url(url)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                data = response.read(max_bytes + 1)
        except urllib.error.HTTPError as error:
            raise HttpError(f"{url} answered HTTP {error.code}", error.code) from None
        except (urllib.error.URLError, OSError, ValueError) as error:
            reason = getattr(error, "reason", error)
            raise HttpError(f"could not reach {url}: {reason}") from None
        if len(data) > max_bytes:
            raise HttpError(f"{url} returned more than {max_bytes} bytes")
        return data

    def request_json(self, method: str, url: str, payload: dict | None, headers: dict | None = None,
                     max_bytes: int = 65536) -> tuple[int, dict]:
        self._check_url(url)
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(url, data=body, method=method, headers={
            "User-Agent": USER_AGENT, "Accept": "application/json",
            **({"Content-Type": "application/json"} if body is not None else {}), **(headers or {})})
        try:
            with self._api_opener.open(request, timeout=self.timeout) as response:
                status = response.status
                data = response.read(max_bytes + 1)
        except urllib.error.HTTPError as error:
            status = error.code
            try:
                data = error.read(max_bytes + 1)
            except OSError:
                data = b""
        except (urllib.error.URLError, OSError, ValueError) as error:
            reason = getattr(error, "reason", error)
            raise HttpError(f"could not reach {url}: {reason}") from None
        if len(data) > max_bytes:
            raise HttpError(f"{url} returned more than {max_bytes} bytes", status)
        try:
            decoded = json.loads(data.decode("utf-8")) if data.strip() else {}
        except (UnicodeDecodeError, ValueError):
            decoded = {}
        return status, decoded if isinstance(decoded, dict) else {}
