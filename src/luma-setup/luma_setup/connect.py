# SPDX-License-Identifier: Apache-2.0
"""Luma Connect from the setup sheet: the OAuth 2.0 device authorization grant (RFC 8628).

The setup session has no browser, so the person approves the sign-in on their
phone or another device: the sheet shows the verification address and the
code the identity provider issued. Only when the device's Connect client
configuration exists (/etc/luma-connect/client.json) and the provider
advertises a device_authorization_endpoint is it offered; otherwise the page
records "sign me in later". The token is held in memory and handed to the
root helper at Finish, which seals it for the new account (luma-keyring's
systemd-creds path); it is never written in plain text.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from gi.repository import GLib

__all__ = ("ConnectFlow",)

log = logging.getLogger("luma-setup")
CLIENT = Path("/etc/luma-connect/client.json")


class ConnectFlow:
    def __init__(self, changed: Callable[[], None]) -> None:
        self._changed = changed
        self.code = ""
        self.url = ""
        self.url_short = ""
        self.qr = None
        self.token: str | None = None
        self.account = ""
        self.done = False
        self.later = False
        self.started = False
        self.why = ""
        self._stop = threading.Event()
        self.config = self._config()
        self.available = self.config is not None

    @staticmethod
    def _config() -> dict | None:
        try:
            data = json.loads(CLIENT.read_text(encoding="utf-8"))
            return data if data.get("issuer") and data.get("client_id") else None
        except (OSError, ValueError):
            return None

    def _tell(self) -> None:
        GLib.idle_add(lambda: (self._changed(), False)[1])

    def start(self) -> None:
        if self.started or not self.available:
            return
        self.started = True
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _post(self, url: str, fields: dict) -> dict:
        body = urllib.parse.urlencode(fields).encode()
        request = urllib.request.Request(url, data=body, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read(65536))
        except urllib.error.HTTPError as error:
            return json.loads(error.read(65536) or b"{}")

    def _run(self) -> None:
        cfg = self.config
        try:
            with urllib.request.urlopen(cfg["issuer"] + "/.well-known/openid-configuration", timeout=15) as r:
                meta = json.loads(r.read(262144))
            endpoint = meta.get("device_authorization_endpoint")
            if not endpoint:
                self.why = "Sign in to Luma Connect from your desktop once Luma starts."
                self.available = False
                self._tell()
                return
            grant = self._post(endpoint, {"client_id": cfg["client_id"],
                                          "scope": cfg.get("scope", "openid profile email offline_access")})
            self.code = grant["user_code"]
            self.url = grant.get("verification_uri_complete") or grant["verification_uri"]
            self.url_short = urllib.parse.urlsplit(grant["verification_uri"]).netloc + \
                urllib.parse.urlsplit(grant["verification_uri"]).path
            self._tell()
            interval = max(5, int(grant.get("interval", 5)))
            deadline = time.monotonic() + int(grant.get("expires_in", 600))
            while not self._stop.wait(interval) and time.monotonic() < deadline:
                answer = self._post(meta["token_endpoint"], {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": grant["device_code"], "client_id": cfg["client_id"]})
                if "access_token" in answer:
                    self.token = json.dumps(answer)
                    self.done = True
                    self._tell()
                    return
                if answer.get("error") == "slow_down":
                    interval += 5
                elif answer.get("error") not in ("authorization_pending", None):
                    break
            self.code = ""
            self.why = "That code has run out. Sign in from your desktop once Luma starts."
            self._tell()
        except (OSError, ValueError, KeyError) as error:
            log.info("Connect sign-in unavailable: %s", error)
            self.why = "Sign in to Luma Connect from your desktop once Luma starts."
            self._tell()
