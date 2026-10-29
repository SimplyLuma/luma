# SPDX-License-Identifier: Apache-2.0
"""Ari-owned Sign in with ChatGPT registrations. Never reads CLI credentials.

Only public account summaries leave this module through the D-Bus interface.
Tokens live together in Secret Service; a process/file lock serializes rotating
refresh tokens. The local OSS protocol is documented at developers.openai.com/siwc.
"""
from __future__ import annotations

import base64
import copy
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import paths
from .persistence import _locked

ISSUER = "https://auth.openai.com"
API = "https://api.openai.com/v1"
USAGE_URL = "https://chatgpt.com/settings/usage"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = "openid profile email offline_access resource.invoke " + PLAN_SCOPE
APP_NAME = "Luma Ari"
MAX_JSON = 2 * 1024 * 1024
LOGIN_SECONDS = 300


class ChatGPTError(ValueError):
    """Safe, user-facing error; never includes remote response text or tokens."""


class NetworkError(ChatGPTError):
    def __init__(self, status=0, code=""):
        self.status, self.code = status, code
        super().__init__(error_message(status, code))


def error_message(status, code):
    if code in ("unsupported_tool", "unsupported_tool_type", "unsupported_value", "invalid_request_error"):
        return "ChatGPT rejected a requested tool or setting (" + code + "). This route may not allow hosted web search or the selected effort. No fallback was used."
    if code == "subscription_sharing_usage_limit_exceeded" or status == 429:
        return "A ChatGPT plan or Ari usage limit was reached. Manage usage in ChatGPT settings."
    if code == "subscription_sharing_user_not_eligible":
        return "ChatGPT plan usage is not available for this account or workspace. Choose another account."
    if status == 401:
        return "ChatGPT did not accept this session. Sign in again in Ari's ChatGPT settings."
    if status == 403:
        return "ChatGPT did not authorize this request. Check the account, workspace and plan permissions."
    if status == 400:
        return "ChatGPT could not accept this request. Try another available model."
    if status == 404:
        return "That ChatGPT model is no longer available. Refresh models in Ari's ChatGPT settings."
    return "ChatGPT could not be reached or is temporarily unavailable. Try again later."


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class Transport:
    def __init__(self):
        self.opener = urllib.request.build_opener(_NoRedirect())

    def open(self, url, *, token="", form=None, body=None, timeout=30):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in ("auth.openai.com", "api.openai.com")
                or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment):
            raise ChatGPTError("The ChatGPT service address could not be verified.")
        headers = {"Accept": "application/json", "User-Agent": "Luma-Ari/0.1"}
        if token:
            if not isinstance(token, str) or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise ChatGPTError("The ChatGPT session is invalid. Sign in again.")
            headers["Authorization"] = "Bearer " + token
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            headers.update({"Content-Type": "application/json", "Accept": "text/event-stream"})
        try:
            return self.opener.open(urllib.request.Request(url, data=data, headers=headers), timeout=timeout)
        except urllib.error.HTTPError as error:
            code = ""
            try:
                value = json.loads(error.read(MAX_JSON + 1))
                candidate = value.get("error", "")
                code = candidate.get("code", "") if isinstance(candidate, dict) else candidate
                if not isinstance(code, str) or len(code) > 100:
                    code = ""
            except (ValueError, OSError, AttributeError):
                pass
            finally:
                error.close()
            raise NetworkError(error.code, code) from None
        except (OSError, ValueError):
            raise NetworkError() from None

    def json(self, url, **kwargs):
        with self.open(url, **kwargs) as response:
            raw = response.read(MAX_JSON + 1)
        if len(raw) > MAX_JSON:
            raise ChatGPTError("ChatGPT returned too much account data.")
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, UnicodeError):
            raise ChatGPTError("ChatGPT returned invalid account data.") from None

    def discovery(self):
        value = self.json(ISSUER + "/.well-known/openid-configuration")
        if value.get("issuer") != ISSUER:
            raise ChatGPTError("The ChatGPT sign-in service could not be verified.")
        for name in ("authorization_endpoint", "token_endpoint", "jwks_uri", "revocation_endpoint"):
            url = urllib.parse.urlsplit(value.get(name, ""))
            if (url.scheme != "https" or url.netloc != "auth.openai.com"
                    or not url.path or url.query or url.fragment):
                raise ChatGPTError("The ChatGPT sign-in service could not be verified.")
        return value


class Keyring:
    """One atomic secret holds the vault, including rotating refresh tokens."""
    @staticmethod
    def _service():
        import gi
        gi.require_version("Secret", "1")
        from gi.repository import Secret
        schema = Secret.Schema.new("org.projectluma.Ari.ChatGPT", Secret.SchemaFlags.NONE,
                                   {"service": Secret.SchemaAttributeType.STRING})
        service = Secret.Service.get_sync(Secret.ServiceFlags.OPEN_SESSION | Secret.ServiceFlags.LOAD_COLLECTIONS, None)
        collection = Secret.Collection.for_alias_sync(service, Secret.COLLECTION_DEFAULT, Secret.CollectionFlags.NONE, None)
        if collection is None or collection.get_locked():
            raise ChatGPTError("Unlock Luma's default keyring to connect ChatGPT.")
        return Secret, schema

    def load(self):
        try:
            Secret, schema = self._service()
            raw = Secret.password_lookup_sync(schema, {"service": "chatgpt"}, None)
            return json.loads(raw) if raw else None
        except Exception:
            raise ChatGPTError("Unlock Luma's keyring to connect your ChatGPT account.") from None

    def save(self, value):
        try:
            Secret, schema = self._service()
            ok = Secret.password_store_sync(schema, {"service": "chatgpt"}, Secret.COLLECTION_DEFAULT,
                                            "ChatGPT accounts for Luma Ari", json.dumps(value), None)
            if not ok:
                raise ValueError()
        except Exception:
            raise ChatGPTError("Luma's keyring could not save the ChatGPT session. Unlock it and try again.") from None


def validate_identity(encoded, client_id, nonce, discovery, transport):
    """Verify signature before trusting account claims; pin asymmetric algorithms."""
    try:
        import jwt
        header = jwt.get_unverified_header(encoded)
        if header.get("alg") not in ("RS256", "ES256") or not isinstance(header.get("kid"), str):
            raise ValueError()
        keys = transport.json(discovery["jwks_uri"])["keys"]
        matching = [key for key in keys if key.get("kid") == header["kid"]
                    and key.get("use", "sig") == "sig" and key.get("alg", header["alg"]) == header["alg"]]
        if len(matching) != 1:
            raise ValueError()
        key = jwt.PyJWK.from_dict(matching[0], algorithm=header["alg"])
        claims = jwt.decode(encoded, key.key, algorithms=[header["alg"]], audience=client_id,
                            issuer=ISSUER, options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]})
        if (not isinstance(claims["sub"], str) or not claims["sub"]
                or not isinstance(claims["nonce"], str) or not secrets.compare_digest(claims["nonce"], nonce)
                or (isinstance(claims["aud"], list) and len(claims["aud"]) > 1 and claims.get("azp") != client_id)
                or ("azp" in claims and claims["azp"] != client_id)):
            raise ValueError()
        return claims
    except ImportError:
        raise ChatGPTError("Ari's ChatGPT sign-in dependency is missing. Update or reinstall Ari.") from None
    except Exception:
        raise ChatGPTError("The ChatGPT account identity could not be verified. Start sign-in again.") from None


def _token_set(value, *, previous=None):
    try:
        result = dict(previous or {})
        scope = value.get("scope")
        if scope is None and previous is not None:
            result["scopes"] = previous["scopes"]
        elif isinstance(scope, str):
            result["scopes"] = scope.split()
        else:
            raise ValueError()
        required = ("access_token", "refresh_token") if previous is not None or "offline_access" in result["scopes"] else ("access_token",)
        for key in required:
            token = value[key]
            if not isinstance(token, str) or not token or len(token) > 64000 or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise ValueError()
            result[key] = token
        if value.get("token_type", "").lower() != "bearer":
            raise ValueError()
        expires = float(value["expires_in"])
        if not math.isfinite(expires) or expires <= 0 or expires > 86400:
            raise ValueError()
        if "refresh_token" in value and "refresh_token" not in required:
            token = value["refresh_token"]
            if not isinstance(token, str) or not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise ValueError()
            result["refresh_token"] = token
        result["expires_at"] = time.time() + expires
        if "id_token" in value:
            result["id_token"] = value["id_token"]
        return result
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ChatGPTError("ChatGPT returned an incomplete session. Start sign-in again.") from None


@dataclass(repr=False)
class Login:
    id: str
    profile: str
    expected: dict
    discovery: dict
    verifier: str
    nonce: str
    state: str
    redirect: str = ""
    cancelled: threading.Event = field(default_factory=threading.Event)
    status: str = "waiting"
    message: str = "Finish sign-in in your browser."
    account: str = ""


class Manager:
    def __init__(self, *, keyring=None, transport=None, lock_path=None):
        self.keyring = keyring or Keyring()
        self.transport = transport or Transport()
        self.lock_path = lock_path
        self.lock = threading.RLock()
        self.login = None
        self.cached = {"accounts": [], "active": "", "models": [], "model": "", "usage_url": USAGE_URL,
                       "error": "", "pending": None}

    @contextmanager
    def vault(self):
        with self.lock, _locked(self.lock_path or paths.data_dir() / "chatgpt-session"):
            value = self.keyring.load()
            if value is None:
                value = {"version": 1, "host_id": "urn:uuid:" + str(uuid.uuid4()), "active": "", "accounts": {}}
            if (not isinstance(value, dict) or value.get("version") != 1
                    or not isinstance(value.get("accounts"), dict) or not isinstance(value.get("host_id"), str)
                    or value.get("active", "") not in ("", *value["accounts"])):
                raise ChatGPTError("Ari's saved ChatGPT accounts could not be read. Unlock or repair the keyring.")
            if len(value["accounts"]) > 32 or any(
                    not isinstance(key, str) or not isinstance(item, dict)
                    or any(not isinstance(item.get(name), str) for name in ("label", "client_id", "subject"))
                    or not isinstance(item.get("models", []), list)
                    or not isinstance(item.get("scopes", []), list)
                    for key, item in value["accounts"].items()):
                raise ChatGPTError("Ari's saved ChatGPT accounts could not be read. Unlock or repair the keyring.")
            original = copy.deepcopy(value)
            try:
                yield value
            except Exception:
                # A failed keyring write must not make the UI report an
                # uncommitted account switch or successful sign-in.
                try:
                    self._publish(self.keyring.load() or original)
                except Exception:
                    self._publish(original)
                raise
            else:
                self._publish(value)

    def _publish(self, vault):
        active = vault["accounts"].get(vault["active"], {})
        self.cached = {"accounts": [{"id": key, "label": item["label"], "signed_in": bool(item.get("access_token")) and not item.get("needs_signin", False),
                                      "plan_enabled": PLAN_SCOPE in item.get("scopes", [])}
                                     for key, item in vault["accounts"].items()],
                       "active": vault["active"], "models": active.get("models", []), "model": active.get("model", ""),
                       "efforts": active.get("efforts", {}),
                       "usage_url": USAGE_URL, "error": "", "pending": self.pending()}

    @property
    def signing_in(self):
        return self.login is not None and self.login.status in ("waiting", "checking")

    def pending(self):
        p = self.login
        return {"id": p.id, "status": p.status, "message": p.message, "account": p.account} if p else None

    def status(self, refresh=False):
        try:
            with self.vault() as vault:
                if refresh and vault["active"]:
                    account = vault["accounts"][vault["active"]]
                    if account.get("access_token") and PLAN_SCOPE in account.get("scopes", []):
                        self._models(vault, account)
        except ChatGPTError as error:
            self.cached = {**self.cached, "error": str(error)}
        return {**self.cached, "pending": self.pending()}

    def _access(self, vault, account):
        if not account.get("access_token") or account.get("needs_signin"):
            raise ChatGPTError("Sign in to this ChatGPT account again.")
        if PLAN_SCOPE not in account.get("scopes", []):
            raise ChatGPTError("Allow Ari to use your ChatGPT plan when you sign in.")
        if account.get("expires_at", 0) > time.time() + 60:
            return account["access_token"]
        if not account.get("refresh_token"):
            raise ChatGPTError("This ChatGPT sign-in expired. Sign in again.")
        try:
            value = self.transport.json(ISSUER + "/api/accounts/oauth/token", form={
                "grant_type": "refresh_token", "client_id": account["client_id"],
                "refresh_token": account["refresh_token"], "resource": API})
        except NetworkError as error:
            if error.code in ("invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
                              "refresh_token_invalidated", "refresh_token_reused"):
                self._clear_tokens(account)
                self.keyring.save(vault)
                raise ChatGPTError("This ChatGPT session ended. Sign in again.") from None
            raise
        replacement = _token_set(value, previous=account)
        account.update(replacement)
        # Save the rotated tokens even if the renewed grant lacks plan scope.
        self.keyring.save(vault)
        if PLAN_SCOPE not in account["scopes"]:
            raise ChatGPTError("Allow Ari to use your ChatGPT plan when you sign in.")
        return account["access_token"]

    def access(self, profile):
        with self.vault() as vault:
            account = vault["accounts"].get(profile)
            if not account or profile != vault["active"]:
                raise ChatGPTError("The active ChatGPT account changed. Start a new request.")
            return self._access(vault, account)

    def require_signin(self, profile, rejected_token):
        """Stop retrying a rejected session, retaining tokens for revocation."""
        with self.vault() as vault:
            account = vault["accounts"].get(profile)
            if account and account.get("access_token") == rejected_token:
                account["needs_signin"] = True
                self.keyring.save(vault)

    def _models(self, vault, account):
        token = self._access(vault, account)
        try:
            value = self.transport.json(API + "/models", token=token)
        except NetworkError as error:
            if error.status == 401:
                account["needs_signin"] = True
                self.keyring.save(vault)
            raise
        rows = value.get("models")
        if not isinstance(rows, list):
            raise ChatGPTError("ChatGPT returned an invalid model list.")
        account["models"] = [_model_row(row) for row in rows
                             if isinstance(row, dict) and row.get("visibility") == "list"
                             and isinstance(row.get("slug"), str) and 0 < len(row["slug"]) <= 200
                             and isinstance(row.get("display_name"), str) and 0 < len(row["display_name"]) <= 200][:200]
        if account.get("model") not in [row["id"] for row in account["models"]]:
            account["model"] = account["models"][0]["id"] if account["models"] else ""
        self.keyring.save(vault)

    def select(self, profile):
        with self.vault() as vault:
            if profile not in vault["accounts"]:
                raise ChatGPTError("Choose a saved ChatGPT account.")
            vault["active"] = profile
            self.keyring.save(vault)

    def set_effort(self, model, level):
        with self.vault() as vault:
            account = vault["accounts"].get(vault["active"], {})
            row = next((row for row in account.get("models", []) if row["id"] == model), {})
            if level not in [entry["effort"] for entry in row.get("supported_reasoning_levels", [])]:
                raise ChatGPTError("Choose an effort supported by this model. Refresh its model list if needed.")
            account.setdefault("efforts", {})[model] = level
            self.keyring.save(vault)

    def effort(self, model):
        state = self.cached
        row = next((row for row in state["models"] if row["id"] == model), {})
        levels = [entry["effort"] for entry in row.get("supported_reasoning_levels", [])]
        chosen = state.get("efforts", {}).get(model)
        if chosen not in levels:
            chosen = row.get("default_reasoning_level")
        return chosen if chosen in levels else None

    def select_model(self, model):
        with self.vault() as vault:
            account = vault["accounts"].get(vault["active"], {})
            if not account.get("access_token") or account.get("needs_signin") or PLAN_SCOPE not in account.get("scopes", []):
                raise ChatGPTError("Connect a ChatGPT account and allow plan usage first.")
            if model not in [row["id"] for row in account.get("models", [])]:
                raise ChatGPTError("Refresh the ChatGPT model list and choose an available model.")
            account["model"] = model
            self.keyring.save(vault)

    @staticmethod
    def _clear_tokens(account):
        for name in ("access_token", "refresh_token", "id_token", "expires_at", "scopes"):
            account.pop(name, None)
        account.update(models=[], model="")

    def sign_out(self, profile):
        confirmed = True
        with self.vault() as vault:
            account = vault["accounts"].get(profile)
            if not account:
                raise ChatGPTError("Choose a saved ChatGPT account.")
            if account.get("refresh_token"):
                confirmed = False
                try:
                    discovery = self.transport.discovery()
                    for attempt in range(2):
                        try:
                            with self.transport.open(discovery["revocation_endpoint"], form={
                                "token": account["refresh_token"], "token_type_hint": "refresh_token",
                                "client_id": account["client_id"]}) as response:
                                confirmed = response.status == 200
                            break
                        except NetworkError as error:
                            if error.status and error.status < 500:
                                break
                            if attempt == 0:
                                time.sleep(.25)
                except ChatGPTError:
                    pass
            self._clear_tokens(account)
            self.keyring.save(vault)
        return ("Signed out of ChatGPT." if confirmed else
                "Signed out locally. Remote disconnection was not confirmed; disconnect Ari in ChatGPT settings.")

    def start(self, profile="", cancelled=None):
        if self.signing_in:
            raise ChatGPTError("A ChatGPT sign-in is already open.")
        discovery = self.transport.discovery()
        with self.vault() as vault:
            if profile and profile not in vault["accounts"]:
                raise ChatGPTError("Choose a saved ChatGPT account.")
            expected = dict(vault["accounts"].get(profile, {}))
            if not profile and len(vault["accounts"]) >= 32:
                raise ChatGPTError("Ari has reached its saved ChatGPT account limit. Use an existing registration.")
            host = vault["host_id"]
            # Prove secure storage is writable before starting authorization.
            self.keyring.save(vault)
        p = Login(uuid.uuid4().hex, profile, expected, discovery, secrets.token_urlsafe(48),
                  secrets.token_urlsafe(32), secrets.token_urlsafe(32),
                  cancelled=cancelled if cancelled is not None else threading.Event())
        manager = self

        class Handler(BaseHTTPRequestHandler):
            # Browser callbacks and authorization codes must never reach logs.
            def log_message(self, *_args):
                pass

            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                valid_path = parsed.path == "/auth/callback" and len(self.path) <= 16384
                try:
                    query = urllib.parse.parse_qs(parsed.query, max_num_fields=12, keep_blank_values=True)
                except ValueError:
                    query = {}
                state = query.get("state", [])
                if (not valid_path or self.headers.get("Host") != urllib.parse.urlsplit(p.redirect).netloc
                        or len(state) != 1 or not secrets.compare_digest(state[0].encode(), p.state.encode())
                        or p.status != "waiting" or p.cancelled.is_set()):
                    self.send_error(400, "Invalid sign-in callback")
                    return
                p.status = "checking"
                try:
                    manager._complete(p, query)
                    p.status, p.message = "complete", "Connected to ChatGPT. Choose a model in Ari."
                except ChatGPTError as error:
                    p.status, p.message = "failed", str(error)
                except Exception:
                    p.status, p.message = "failed", "ChatGPT sign-in could not be completed. Try again."
                body = b"<!doctype html><meta charset=utf-8><title>Luma Ari</title><p>Return to Ari to continue.</p>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

        class Listener(HTTPServer):
            def get_request(self):
                sock, address = super().get_request()
                sock.settimeout(2)
                return sock, address

        server = Listener(("127.0.0.1", 0), Handler)
        server.timeout = .25
        p.redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
        params = {"client_id": expected.get("client_id", "dynamic_agent_client"), "ext_agent_host_id": host,
                  "response_type": "code", "redirect_uri": p.redirect, "scope": SCOPES, "resource": API,
                  "state": p.state, "nonce": p.nonce, "code_challenge_method": "S256",
                  "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(p.verifier.encode()).digest()).rstrip(b"=").decode()}
        if not profile:
            params["agent_name_hint"] = APP_NAME
        elif expected.get("email"):
            params["login_hint"] = expected["email"]
        if profile and PLAN_SCOPE not in expected.get("scopes", []):
            params["prompt"] = "consent"
        self.login = p

        def listen():
            try:
                deadline = time.monotonic() + LOGIN_SECONDS
                while p.status == "waiting" and not p.cancelled.is_set() and time.monotonic() < deadline:
                    server.handle_request()
                if p.cancelled.is_set():
                    p.status, p.message = "cancelled", "Sign-in cancelled."
                elif p.status == "waiting":
                    p.status, p.message = "failed", "ChatGPT sign-in timed out. Try again."
            finally:
                server.server_close()
        threading.Thread(target=listen, daemon=True, name="ari-chatgpt-login").start()
        return {"id": p.id, "url": discovery["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)}

    def cancel(self, attempt):
        p = self.login
        if p and p.id == attempt:
            p.cancelled.set()

    def _complete(self, p, query):
        if "error" in query:
            raise ChatGPTError("ChatGPT sign-in was declined. You can connect again whenever you're ready.")
        if len(query.get("code", [])) != 1 or not query["code"][0]:
            raise ChatGPTError("ChatGPT did not return a sign-in code. Try again.")
        clients = query.get("client_id", [])
        if p.profile:
            client = p.expected["client_id"]
            if clients and clients != [client]:
                raise ChatGPTError("ChatGPT returned a different account registration. Start sign-in again.")
        elif (len(clients) == 1 and 0 < len(clients[0]) <= 512 and clients[0] != "dynamic_agent_client"
              and all(33 <= ord(c) <= 126 for c in clients[0])):
            client = clients[0]
        else:
            raise ChatGPTError("ChatGPT did not complete Ari's registration. Start sign-in again.")
        value = self.transport.json(p.discovery["token_endpoint"], form={
            "grant_type": "authorization_code", "client_id": client, "code": query["code"][0],
            "code_verifier": p.verifier, "redirect_uri": p.redirect, "resource": API})
        tokens = _token_set(value)
        claims = validate_identity(value.get("id_token", ""), client, p.nonce, p.discovery, self.transport)
        if p.profile and claims["sub"] != p.expected["subject"]:
            raise ChatGPTError("You signed in to a different ChatGPT account. Use Add account instead.")
        with self.vault() as vault:
            if p.cancelled.is_set():
                raise ChatGPTError("Sign-in cancelled.")
            for key, existing in vault["accounts"].items():
                if existing["client_id"] == client:
                    if existing["subject"] != claims["sub"]:
                        raise ChatGPTError("The ChatGPT account registration did not match.")
                    profile = key
                    break
            else:
                profile = p.profile or uuid.uuid4().hex
            email = claims.get("email") if isinstance(claims.get("email"), str) else ""
            label = (email or "ChatGPT account")[:200] + " · " + profile[:6]
            saved_efforts = vault["accounts"].get(profile, {}).get("efforts", {})
            vault["accounts"][profile] = {**tokens, "client_id": client, "subject": claims["sub"], "issuer": ISSUER,
                                          "efforts": saved_efforts,
                                          "email": email, "label": label, "models": [], "model": ""}
            vault["active"] = profile
            self.keyring.save(vault)
            p.account = profile


def _model_row(row):
    result = {"id": row["slug"], "name": row["display_name"]}
    levels = []
    values = row.get("supported_reasoning_levels")
    for value in (values if isinstance(values, list) else [])[:20]:
        effort = value.get("effort") if isinstance(value, dict) else value
        if isinstance(effort, str) and 0 < len(effort) <= 32 and all(c.isascii() and (c.isalnum() or c == '_') for c in effort):
            if effort not in [entry["effort"] for entry in levels]:
                levels.append({"effort": effort})
    if levels:
        result["supported_reasoning_levels"] = levels
        default = row.get("default_reasoning_level")
        if default in [entry["effort"] for entry in levels]:
            result["default_reasoning_level"] = default
    description = row.get("description")
    if isinstance(description, str) and len(description) <= 500:
        result["description"] = description
    return result
