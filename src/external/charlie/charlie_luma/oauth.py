# SPDX-License-Identifier: Apache-2.0
"""Browser-owned OAuth 2.0 PKCE flows for Gmail and Microsoft mail.

The browser owns interactive authentication.  Charlie receives only the
loopback callback, stores the resulting token in Secret Service, and presents
an access token to the protocol engine on demand.
"""
from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import secrets
import time
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


GOOGLE_CLIENT_ID = "1002038957685-o7bd0mhvjdrqprrvim2ovrmbdd9rh28o.apps.googleusercontent.com"
# Desktop OAuth clients are public clients; this value is client metadata, not
# a user credential.  User access and refresh tokens live only in libsecret.
GOOGLE_CLIENT_SECRET = "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"
GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_SCOPE = "https://mail.google.com/ openid email profile"
MICROSOFT_AUTH_ENDPOINT = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
MICROSOFT_TOKEN_ENDPOINT = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
MICROSOFT_SCOPE = (
    "openid profile email offline_access "
    "https://outlook.office.com/IMAP.AccessAsUser.All "
    "https://outlook.office.com/SMTP.Send"
)


class OAuthError(Exception):
    """A deliberately non-sensitive OAuth failure suitable for the UI."""


GOOGLE_UNAVAILABLE = (
    "Google sign-in is unavailable in this release. "
    "Existing mail and sign-in data are kept."
)


class GoogleOAuthUnavailable(OAuthError):
    """Release policy pauses Google OAuth without invalidating saved grants."""


def require_google_oauth() -> None:
    raise GoogleOAuthUnavailable(GOOGLE_UNAVAILABLE)


def failure_requires_sign_in(error: OAuthError) -> bool:
    """Separate rejected/damaged grants from a temporarily unreachable Google.

    OAuth's token endpoint uses 400-series responses for a revoked or invalid
    refresh grant. Network loss, rate limits and server failures must remain
    retryable and must never tell the person that their saved sign-in is bad.
    """

    if isinstance(error, GoogleOAuthUnavailable):
        return False
    cause = error.__cause__
    if isinstance(cause, HTTPError):
        return cause.code in {400, 401, 403}
    if isinstance(cause, (URLError, TimeoutError, OSError)):
        return False
    return True


@dataclass(frozen=True, slots=True)
class OAuthIdentity:
    email: str
    name: str
    picture: str
    token_json: str


def pkce_challenge(verifier: str) -> str:
    return urlsafe_b64encode(sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")


def _random_urlsafe(length: int) -> str:
    return secrets.token_urlsafe(length)[:length]


def _jwt_claim(token: str, claim: str) -> str:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        value = json.loads(urlsafe_b64decode(payload).decode("utf-8")).get(claim, "")
        return value if isinstance(value, str) else ""
    except (IndexError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return ""


def _post_form(url: str, values: dict[str, str], provider: str = "The provider") -> dict:
    request = Request(
        url,
        data=urlencode(values).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except Exception as error:
        raise OAuthError(f"{provider} could not complete authentication. Please try again.") from error


def _userinfo(access_token: str) -> dict:
    request = Request(
        GOOGLE_USERINFO_ENDPOINT,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except Exception:
        return {}


class _LoopbackHandler(BaseHTTPRequestHandler):
    query: dict[str, list[str]] = {}

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        type(self).query = parse_qs(urlparse(self.path).query)
        ok = "code" in type(self).query
        body = (
            "<!doctype html><meta charset=utf-8><title>Charlie</title>"
            "<style>body{font:16px system-ui;background:#15151a;color:#f5f5f7;"
            "display:grid;place-items:center;min-height:100vh;margin:0}main{text-align:center}</style>"
            f"<main><h1>{'Signed in' if ok else 'Sign-in stopped'}</h1>"
            "<p>You can close this tab and return to Charlie.</p></main>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args) -> None:
        # Callback query parameters can contain an authorization code.
        pass


class GmailOAuth:
    def __init__(self, client_id: str = GOOGLE_CLIENT_ID, client_secret: str = GOOGLE_CLIENT_SECRET) -> None:
        self.client_id = client_id
        self.client_secret = client_secret

    def authorization_url(self, redirect_uri: str, verifier: str, state: str) -> str:
        require_google_oauth()

    @staticmethod
    def profile_picture(token_json: str) -> str:
        try:
            token = json.loads(token_json)
        except (TypeError, json.JSONDecodeError):
            return ""
        return _jwt_claim(str(token.get("id_token", "")), "picture")

    @staticmethod
    def profile_picture_for_access_token(access_token: str) -> str:
        if not access_token:
            return ""
        require_google_oauth()

    def sign_in(self, open_uri: Callable[[str], bool], *, cancel=None) -> OAuthIdentity:
        # Refuse before opening a browser, loopback server, or provider request.
        require_google_oauth()

    def access_token(
        self, token_json: str, *, force_refresh: bool = False
    ) -> tuple[str, str | None]:
        # An unexpired saved grant is paused too; leave its bytes untouched.
        require_google_oauth()


class MicrosoftOAuth:
    """Microsoft identity-platform public-client OAuth for IMAP and SMTP."""

    def __init__(self, client_id: str) -> None:
        self.client_id = client_id.strip()

    @classmethod
    def from_token_json(cls, token_json: str) -> "MicrosoftOAuth":
        try:
            token = json.loads(token_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise OAuthError("The saved Microsoft sign-in is damaged. Reconnect the account.") from error
        client_id = str(token.get("client_id", "")).strip()
        if not client_id:
            raise OAuthError("The saved Microsoft application ID is missing. Reconnect the account.")
        return cls(client_id)

    def authorization_url(self, redirect_uri: str, verifier: str, state: str) -> str:
        if not self.client_id:
            raise OAuthError("Enter the Microsoft Application (client) ID.")
        return MICROSOFT_AUTH_ENDPOINT + "?" + urlencode({
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "response_mode": "query",
            "scope": MICROSOFT_SCOPE,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "state": state,
            "prompt": "select_account",
        })

    def sign_in(self, open_uri: Callable[[str], bool], *, cancel=None) -> OAuthIdentity:
        verifier = _random_urlsafe(64)
        state = _random_urlsafe(32)
        _LoopbackHandler.query = {}
        server = HTTPServer(("127.0.0.1", 0), _LoopbackHandler)
        server.timeout = 0.2
        redirect_uri = f"http://localhost:{server.server_port}"
        try:
            if not open_uri(self.authorization_url(redirect_uri, verifier, state)):
                raise OAuthError("Charlie could not open the browser for Microsoft sign-in.")
            deadline = time.monotonic() + 300
            while not _LoopbackHandler.query and time.monotonic() < deadline:
                if cancel is not None and cancel.is_set():
                    raise OAuthError("Sign-in was cancelled.")
                server.handle_request()
            if cancel is not None and cancel.is_set():
                raise OAuthError("Sign-in was cancelled.")
            query = _LoopbackHandler.query
            if not query:
                raise OAuthError("Microsoft sign-in timed out.")
            if query.get("state", [""])[0] != state:
                raise OAuthError("Microsoft sign-in could not be verified. Please try again.")
            code = query.get("code", [""])[0]
            if not code:
                raise OAuthError("Microsoft sign-in was cancelled.")
            token = _post_form(MICROSOFT_TOKEN_ENDPOINT, {
                "client_id": self.client_id,
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "scope": MICROSOFT_SCOPE,
            }, "Microsoft")
            token["client_id"] = self.client_id
            token["provider"] = "microsoft"
            token["expires_at"] = int(time.time()) + int(token.get("expires_in", 3600))
            id_token = str(token.get("id_token", ""))
            email = _jwt_claim(id_token, "preferred_username") or _jwt_claim(id_token, "email")
            if not email:
                raise OAuthError("Microsoft did not return an email address.")
            name = _jwt_claim(id_token, "name")
            return OAuthIdentity(email, name, "", json.dumps(token, separators=(",", ":")))
        finally:
            server.server_close()

    def access_token(
        self, token_json: str, *, force_refresh: bool = False
    ) -> tuple[str, str | None]:
        try:
            token = json.loads(token_json)
        except json.JSONDecodeError as error:
            raise OAuthError("The saved Microsoft sign-in is damaged. Reconnect the account.") from error
        access_token = str(token.get("access_token", ""))
        if (
            not force_refresh
            and access_token
            and int(token.get("expires_at", 0)) > int(time.time()) + 60
        ):
            return access_token, None
        refresh_token = str(token.get("refresh_token", ""))
        if not refresh_token:
            raise OAuthError("Microsoft sign-in has expired. Reconnect the account.")
        refreshed = _post_form(MICROSOFT_TOKEN_ENDPOINT, {
            "client_id": self.client_id,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": MICROSOFT_SCOPE,
        }, "Microsoft")
        refreshed["refresh_token"] = refresh_token
        refreshed["client_id"] = self.client_id
        refreshed["provider"] = "microsoft"
        refreshed["expires_at"] = int(time.time()) + int(refreshed.get("expires_in", 3600))
        if "id_token" not in refreshed and "id_token" in token:
            refreshed["id_token"] = token["id_token"]
        fresh_json = json.dumps(refreshed, separators=(",", ":"))
        return str(refreshed.get("access_token", "")), fresh_json
