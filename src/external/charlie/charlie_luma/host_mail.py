# SPDX-License-Identifier: Apache-2.0
"""Activated native protocol/keyring owner for the signed Charlie application.

Only the user's declared mailbox directory is shared with the UI. Passwords,
refresh tokens and protocol transports remain in this native process. Every
request and poll admits the actual signed application connection; job handles
are owner-bound, consumed at completion, and never disclose server exceptions.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib
from . import APP_ID
from .accounts import account_id_for_address, provider_by_key, preset_config
from .engine import ImapSmtpTransport, AuthenticationFailure
from .host_mail_wire import BUS, OBJECT, decode, encode, validate
from .mail_agent import default_store_path
from .model import Account
from .oauth import GmailOAuth, MicrosoftOAuth, failure_requires_sign_in, OAuthError
from .secrets import SecretStore
from .store import MailStore

XML = f"""<node><interface name="{BUS}">
<method name="Start"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
<method name="Poll"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
<method name="Cancel"><arg type="s" direction="in"/></method>
</interface></node>"""


from .host_mail_owner import MailOwner


class Broker:
    def __init__(self, connection, loop):
        self.connection, self.loop = connection, loop
        self.owner = MailOwner(); self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="charlie-host")
        self.jobs = {}; self.last = time.monotonic()
        self.registration = connection.register_object(OBJECT, Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], self.call, None, None)
        self.timer = GLib.timeout_add_seconds(5, self.expire)

    def admit(self, connection, sender):
        from luma_installer.app_data_broker import authenticate
        if authenticate(connection, sender) != APP_ID: raise PermissionError("Charlie service is unavailable to this application.")

    def call(self, connection, sender, _path, _interface, method, args, invocation):
        try:
            self.admit(connection, sender); self.last = time.monotonic()
            values = args.unpack()
            if method == "Start":
                operation, text = values; data = validate(operation, decode(text))
                if len(self.jobs) >= 16 or sum(not j["future"].done() for j in self.jobs.values()) >= 8: raise RuntimeError("Mail service is busy.")
                if operation in {"GoogleSignIn", "MicrosoftSignIn"} and any(j["operation"] in {"GoogleSignIn", "MicrosoftSignIn"} and not j["future"].done() for j in self.jobs.values()):
                    raise RuntimeError("A mail sign-in is already in progress.")
                token = secrets.token_hex(24); cancelled = threading.Event()
                watch = Gio.bus_watch_name_on_connection(connection, sender, Gio.BusNameWatcherFlags.NONE, None, lambda *_a: cancelled.set())
                future = self.pool.submit(self.owner.perform, operation, data, cancelled)
                self.jobs[token] = {"owner": sender, "operation": operation, "future": future, "cancel": cancelled, "watch": watch, "started": time.monotonic()}
                invocation.return_value(GLib.Variant("(s)", (token,))); return
            token, = values
            if not isinstance(token, str) or len(token) != 48: raise ValueError("Invalid mail operation handle.")
            job = self.jobs.get(token)
            if job is None or job["owner"] != sender: raise PermissionError("Mail operation unavailable.")
            if method == "Cancel":
                job["cancel"].set(); job["future"].cancel(); invocation.return_value(None); return
            if method != "Poll": raise ValueError("Unsupported mail method.")
            if not job["future"].done(): result = {"state": "running"}
            else:
                del self.jobs[token]; Gio.bus_unwatch_name(job["watch"])
                try: result = {"state": "done", "value": job["future"].result()}
                except AuthenticationFailure: result = {"state": "error", "kind": "authentication"}
                except Exception: result = {"state": "error", "kind": "unavailable"}
            invocation.return_value(GLib.Variant("(s)", (encode(result),)))
        except Exception:
            invocation.return_dbus_error(BUS + ".Unavailable", "The mail operation could not be accepted. Review the account or retry.")

    def expire(self):
        now = time.monotonic()
        for token, job in tuple(self.jobs.items()):
            if job["future"].done() and (job["cancel"].is_set() or now - job["started"] > 600):
                del self.jobs[token]; Gio.bus_unwatch_name(job["watch"])
        if not self.jobs and now - self.last > 120: self.loop.quit(); return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def close(self):
        for job in self.jobs.values(): job["cancel"].set(); job["future"].cancel(); Gio.bus_unwatch_name(job["watch"])
        self.pool.shutdown(wait=True, cancel_futures=True); self.owner.close()


def main():
    if os.getuid() == 0 or Path("/.flatpak-info").exists(): raise SystemExit("MailHost runs as the native session user.")
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None); loop = GLib.MainLoop(); brokers = []
    def acquired(bus, _name): brokers.append(Broker(bus, loop))
    name = Gio.bus_own_name_on_connection(connection, BUS, Gio.BusNameOwnerFlags.NONE, acquired, lambda *_a: loop.quit())
    try: loop.run()
    finally:
        for broker in brokers: broker.close()
        Gio.bus_unown_name(name)

if __name__ == "__main__": main()
