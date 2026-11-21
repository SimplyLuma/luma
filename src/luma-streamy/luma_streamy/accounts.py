# SPDX-License-Identifier: Apache-2.0
"""Signing in to Twitch and YouTube, and what a signed-in row needs from them.

Pure Python (urllib); every call blocks, so the panel runs them on a thread. Twitch uses the OAuth
device flow (a code the person enters at twitch.tv/activate), YouTube the installed-app loopback flow
with PKCE (Google's page in the browser, the answer back on 127.0.0.1). After signing in Streamy reads
the account's name and its stream key and ingest, so a signed-in row never asks for a key.

Client ids come from config, first found wins:
  $STREAMY_TWITCH_CLIENT_ID, $STREAMY_YOUTUBE_CLIENT_ID, $STREAMY_YOUTUBE_CLIENT_SECRET;
  $XDG_CONFIG_HOME/streamy/oauth.json, then /etc/luma-streamy/oauth.json:
    {"twitch": {"client_id": "…"}, "youtube": {"client_id": "…", "client_secret": "…"}}
Without them the flows run against a placeholder id and the platform refuses it; the panel says so.
Twitch needs a public client ("Public" type, no redirect needed for the device flow). YouTube needs a
Google Cloud OAuth client of type "Desktop app" with the YouTube Data API v3 enabled (Google requires
its client secret even for installed apps; it is not a secret there).
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PLACEHOLDER = "streamy-placeholder-client-id"

TWITCH_SCOPES = "channel:read:stream_key user:read:chat user:write:chat"
YOUTUBE_SCOPES = "https://www.googleapis.com/auth/youtube"

ENDPOINTS = {
    "twitch_device": "https://id.twitch.tv/oauth2/device",
    "twitch_token": "https://id.twitch.tv/oauth2/token",
    "twitch_api": "https://api.twitch.tv/helix",
    "google_auth": "https://accounts.google.com/o/oauth2/v2/auth",
    "google_token": "https://oauth2.googleapis.com/token",
    "youtube_api": "https://www.googleapis.com/youtube/v3",
}

#: Twitch's ingest for a signed-in row (the nearest server is found by Twitch's own DNS).
TWITCH_INGEST = "rtmps://live.twitch.tv/app"


class SignInError(RuntimeError):
    """Why signing in stopped, in words a person reads."""


def config_file() -> dict:
    """The first oauth.json found ({} when none)."""
    home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    for path in (home / "streamy" / "oauth.json", Path("/etc/luma-streamy/oauth.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def config(platform: str) -> dict:
    env = {"twitch": {"client_id": os.environ.get("STREAMY_TWITCH_CLIENT_ID")},
           "youtube": {"client_id": os.environ.get("STREAMY_YOUTUBE_CLIENT_ID"),
                       "client_secret": os.environ.get("STREAMY_YOUTUBE_CLIENT_SECRET")}}[platform]
    if env.get("client_id"):
        return {k: v for k, v in env.items() if v}
    found = config_file().get(platform)
    if isinstance(found, dict) and found.get("client_id"):
        return found
    return {"client_id": PLACEHOLDER}


def configured(platform: str) -> bool:
    return config(platform)["client_id"] != PLACEHOLDER


# ── HTTP ──

Http = Callable[..., tuple[int, dict]]


def request(method: str, url: str, *, form: dict | None = None, body: dict | None = None,
            headers: dict | None = None, timeout: float = 15) -> tuple[int, dict]:
    """One HTTP call: (status, JSON body or {})."""
    data, head = None, dict(headers or {})
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        head["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        head["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=head, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as reply:
            status, raw = reply.status, reply.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    except (urllib.error.URLError, socket.timeout, OSError) as error:
        raise SignInError("Streamy couldn't reach the sign-in service. Check the connection.") from error
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {}


@dataclass
class Tokens:
    access: str
    refresh: str = ""
    expires_at: float = 0.0

    def to_json(self) -> str:
        return json.dumps({"access": self.access, "refresh": self.refresh, "expires_at": self.expires_at})

    @classmethod
    def from_json(cls, text: str) -> "Tokens":
        data = json.loads(text)
        return cls(data["access"], data.get("refresh", ""), data.get("expires_at", 0.0))


@dataclass
class Account:
    """What a signed-in row keeps: the name it shows, and where and with what key it streams."""

    name: str
    server: str
    key: str
    user_id: str = ""


def _tokens(reply: dict) -> Tokens:
    return Tokens(reply["access_token"], reply.get("refresh_token", ""),
                  time.time() + float(reply.get("expires_in", 3600)))


def _refused(platform: str, reply: dict) -> SignInError:
    message = str(reply.get("message") or reply.get("error_description") or reply.get("error") or "")
    if "client" in message.lower() or reply.get("error") in ("invalid_client", "unauthorized_client"):
        return SignInError(f"{platform} didn't accept Streamy's app id. Signing in needs a {platform} client id "
                           "in Streamy's settings (oauth.json).")
    return SignInError(f"{platform} said: {message}" if message else f"{platform} refused the sign-in")


# ── Twitch: device flow ──

@dataclass
class DeviceCode:
    user_code: str
    verification_uri: str
    device_code: str
    interval: float
    expires_at: float


class TwitchSignIn:
    def __init__(self, *, http: Http = request, sleep: Callable[[float], None] = time.sleep,
                 endpoints: dict | None = None) -> None:
        self.http, self.sleep = http, sleep
        self.endpoints = {**ENDPOINTS, **(endpoints or {})}
        self.client_id = config("twitch")["client_id"]

    def start(self) -> DeviceCode:
        status, reply = self.http("POST", self.endpoints["twitch_device"],
                                  form={"client_id": self.client_id, "scopes": TWITCH_SCOPES})
        if status != 200 or "device_code" not in reply:
            raise _refused("Twitch", reply)
        return DeviceCode(reply["user_code"], reply.get("verification_uri", "https://www.twitch.tv/activate"),
                          reply["device_code"], float(reply.get("interval", 5)),
                          time.time() + float(reply.get("expires_in", 1800)))

    def wait(self, code: DeviceCode, cancelled: Callable[[], bool] = lambda: False) -> Tokens:
        """Poll until the person approves (or declines, or the code expires)."""
        interval = code.interval
        while time.time() < code.expires_at:
            if cancelled():
                raise SignInError("Signing in was cancelled")
            status, reply = self.http("POST", self.endpoints["twitch_token"], form={
                "client_id": self.client_id, "scopes": TWITCH_SCOPES, "device_code": code.device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
            if status == 200 and "access_token" in reply:
                return _tokens(reply)
            message = str(reply.get("message", ""))
            if message == "authorization_pending":
                self.sleep(interval)
                continue
            if message == "slow_down":
                interval += 5
                self.sleep(interval)
                continue
            if message in ("access_denied", "authorization_declined"):
                raise SignInError("Signing in to Twitch was declined")
            if message == "expired_token":
                break
            raise _refused("Twitch", reply)
        raise SignInError("The Twitch code expired. Sign in again.")

    def refresh(self, tokens: Tokens) -> Tokens:
        status, reply = self.http("POST", self.endpoints["twitch_token"], form={
            "client_id": self.client_id, "grant_type": "refresh_token", "refresh_token": tokens.refresh})
        if status != 200:
            raise SignInError("Twitch signed Streamy out. Sign in again.")
        return _tokens(reply)

    def account(self, tokens: Tokens) -> Account:
        head = {"Authorization": f"Bearer {tokens.access}", "Client-Id": self.client_id}
        status, users = self.http("GET", f"{self.endpoints['twitch_api']}/users", headers=head)
        if status != 200 or not users.get("data"):
            raise _refused("Twitch", users)
        user = users["data"][0]
        query = urllib.parse.urlencode({"broadcaster_id": user["id"]})
        status, keys = self.http("GET", f"{self.endpoints['twitch_api']}/streams/key?{query}", headers=head)
        if status != 200 or not keys.get("data"):
            raise _refused("Twitch", keys)
        return Account(user.get("display_name") or user.get("login", ""), TWITCH_INGEST,
                       keys["data"][0]["stream_key"], user["id"])


# ── YouTube: loopback + PKCE ──

class _Catch(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server's name)
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
        if "code" in query or "error" in query:
            self.server.answer = query
            words = "You can close this tab and go back to Streamy." if "code" in query else \
                "Signing in was cancelled. You can close this tab."
        else:
            words = "Waiting for Google…"
        page = f"<!doctype html><meta charset=utf-8><title>Streamy</title><p>{words}</p>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, *_args) -> None:
        pass


class YouTubeSignIn:
    def __init__(self, *, http: Http = request, endpoints: dict | None = None) -> None:
        self.http = http
        self.endpoints = {**ENDPOINTS, **(endpoints or {})}
        conf = config("youtube")
        self.client_id, self.client_secret = conf["client_id"], conf.get("client_secret", "")
        self.verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
        self.state = secrets.token_urlsafe(16)
        self.server: http.server.HTTPServer | None = None
        self.redirect = ""

    def begin(self) -> str:
        """Listen on 127.0.0.1 and return the page to open in the browser."""
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _Catch)
        self.server.answer = None
        self.server.timeout = 0.5
        self.redirect = f"http://127.0.0.1:{self.server.server_port}"
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).rstrip(b"=").decode()
        return self.endpoints["google_auth"] + "?" + urllib.parse.urlencode({
            "client_id": self.client_id, "redirect_uri": self.redirect, "response_type": "code",
            "scope": YOUTUBE_SCOPES, "code_challenge": challenge, "code_challenge_method": "S256",
            "state": self.state, "access_type": "offline", "prompt": "consent"})

    def wait(self, cancelled: Callable[[], bool] = lambda: False, timeout: float = 600) -> Tokens:
        if self.server is None:
            raise SignInError("Sign-in didn't start")
        until = time.time() + timeout
        try:
            while self.server.answer is None:
                if cancelled():
                    raise SignInError("Signing in was cancelled")
                if time.time() > until:
                    raise SignInError("Google didn't answer in time. Sign in again.")
                self.server.handle_request()
        finally:
            self.server.server_close()
        answer = self.server.answer
        if answer.get("state") != self.state:
            raise SignInError("The sign-in answer didn't match. Sign in again.")
        if "error" in answer:
            raise SignInError("Signing in to YouTube was declined" if answer["error"] == "access_denied"
                              else _refused("YouTube", answer).args[0])
        return self.exchange(answer["code"])

    def exchange(self, code: str) -> Tokens:
        form = {"code": code, "client_id": self.client_id, "code_verifier": self.verifier,
                "grant_type": "authorization_code", "redirect_uri": self.redirect}
        if self.client_secret:
            form["client_secret"] = self.client_secret
        status, reply = self.http("POST", self.endpoints["google_token"], form=form)
        if status != 200 or "access_token" not in reply:
            raise _refused("YouTube", reply)
        return _tokens(reply)

    def account(self, tokens: Tokens) -> Account:
        head = {"Authorization": f"Bearer {tokens.access}"}
        api = self.endpoints["youtube_api"]
        status, channels = self.http("GET", f"{api}/channels?part=snippet&mine=true", headers=head)
        if status != 200 or not channels.get("items"):
            raise SignInError("This Google account has no YouTube channel") if status == 200 else \
                _refused("YouTube", channels.get("error", channels) if isinstance(channels.get("error"), dict) else channels)
        name = channels["items"][0]["snippet"]["title"]
        status, streams = self.http("GET", f"{api}/liveStreams?part=cdn,snippet&mine=true", headers=head)
        items = streams.get("items", []) if status == 200 else []
        stream = next((s for s in items if s.get("snippet", {}).get("title") == "Streamy"), items[0] if items else None)
        if stream is None:
            status, stream = self.http("POST", f"{api}/liveStreams?part=snippet,cdn", headers=head, body={
                "snippet": {"title": "Streamy"},
                "cdn": {"frameRate": "variable", "ingestionType": "rtmp", "resolution": "variable"}})
            if status not in (200, 201):
                raise SignInError("YouTube didn't make a stream for this channel. Is live streaming turned on?")
        ingest = stream["cdn"]["ingestionInfo"]
        server = ingest.get("rtmpsIngestionAddress") or ingest["ingestionAddress"]
        return Account(name, server, ingest["streamName"], channels["items"][0].get("id", ""))


def run(work: Callable[[], object], done: Callable[[object | None, str | None], None]) -> threading.Thread:
    """Run ``work`` on a thread; ``done(result, error words)`` is called on that thread (the panel moves
    it to the main loop)."""
    def body() -> None:
        try:
            result = work()
        except SignInError as error:
            done(None, str(error))
        except Exception as error:  # a malformed answer: say so, never crash the window
            done(None, f"Signing in failed: {error}")
        else:
            done(result, None)

    thread = threading.Thread(target=body, daemon=True, name="streamy-sign-in")
    thread.start()
    return thread
