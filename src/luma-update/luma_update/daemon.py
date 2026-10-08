# SPDX-License-Identifier: Apache-2.0
"""luma-updated: the system D-Bus service org.projectluma.Update1.

Runs as root under a sandboxed systemd unit, is D-Bus- and timer-activated,
and exits after a few idle minutes. It never restarts the computer on its own;
Apply() restarts only when a person asks, after polkit agrees.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import system  # noqa: E402
from .config import Paths, load_settings  # noqa: E402
from .dbus_interface import BUS_NAME, ERROR_PREFIX, INTERFACE, METHODS, OBJECT_PATH, introspection_xml  # noqa: E402
from .engine import Busy, Engine, NothingToDo, UpdateError, classify_error, ensure_runtime_dir  # noqa: E402
from .http import Http  # noqa: E402
from .redact import redact  # noqa: E402
from .rpmostree import RpmOstree  # noqa: E402
from .status import DBUS_TYPES, STAGED, dbus_name, snake_name  # noqa: E402

log = logging.getLogger("luma-update")


class Probes:
    def __init__(self, connection):
        self.connection = connection

    def network(self):
        return system.network_state(self.connection)

    def power(self):
        return system.power_state(self.connection)


def _variant(name: str, value) -> GLib.Variant:
    signature = DBUS_TYPES[name]
    if signature in ("t", "x"):
        value = int(value)
    elif signature == "d":
        value = float(value)
    elif signature == "as":
        value = [str(item) for item in value]
    return GLib.Variant(signature, value)


class Daemon:
    def __init__(self) -> None:
        self.paths = Paths.from_environment()
        self.settings = load_settings(self.paths)
        self.loop = GLib.MainLoop()
        self.connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.engine = Engine(self.paths, self.settings, RpmOstree(self.connection), Probes(self.connection),
                             Http(allow_insecure=self.settings.allow_insecure_urls),
                             on_change=self._on_change_threaded)
        self.node = Gio.DBusNodeInfo.new_for_xml(introspection_xml())
        self.registration = 0
        self.owner = 0
        self.workers = 0
        self.workers_lock = threading.Lock()
        self.last_activity = time.monotonic()
        self._last_state = ""
        self._last_rolled_back_at = 0

    # ── Lifecycle ────────────────────────────────────────────────────────

    def run(self) -> int:
        ensure_runtime_dir(self.paths)
        try:
            self.engine.reconcile_boot(None)
        except Exception as error:
            log.warning("boot reconciliation skipped: %s", error)
        status = self.engine.refresh()
        self._last_state = status.state
        self._last_rolled_back_at = status.rolled_back_at
        interface = self.node.interfaces[0]
        register = getattr(self.connection, "register_object_with_closures2", None)
        if register is not None:
            self.registration = register(OBJECT_PATH, interface, self._method_call, self._get_property, None)
        else:
            self.registration = self.connection.register_object(OBJECT_PATH, interface, self._method_call,
                                                                self._get_property, None)
        self.owner = Gio.bus_own_name_on_connection(self.connection, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                                                    None, self._name_lost)
        # A metered connection or low power stops an automatic download at once.
        for sender, path, interface in (
                ("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager", "org.freedesktop.NetworkManager"),
                ("org.freedesktop.UPower", "/org/freedesktop/UPower", "org.freedesktop.UPower"),
                ("org.freedesktop.UPower", "/org/freedesktop/UPower/devices/DisplayDevice",
                 "org.freedesktop.UPower.Device")):
            self.connection.signal_subscribe(sender, "org.freedesktop.DBus.Properties", "PropertiesChanged", path,
                                             interface, Gio.DBusSignalFlags.NONE, self._power_or_network_changed)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._quit)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self._quit)
        if self.settings.idle_exit_seconds > 0:
            GLib.timeout_add_seconds(30, self._idle_tick)
        log.info("luma-updated ready: %s, channel %s, booted %s",
                 status.state, status.channel or "none", status.booted_version or "unknown")
        self.loop.run()
        try:
            self.engine.backend.unregister()
        except Exception:
            pass
        return 0

    def _quit(self) -> bool:
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def _name_lost(self, _connection, _name) -> None:
        log.error("lost or could not own %s; exiting", BUS_NAME)
        self.loop.quit()

    def _idle_tick(self) -> bool:
        with self.workers_lock:
            busy = self.workers > 0
        if not busy and time.monotonic() - self.last_activity >= self.settings.idle_exit_seconds:
            log.info("idle for %ss; exiting until needed", self.settings.idle_exit_seconds)
            self.loop.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _power_or_network_changed(self, *_signal) -> None:
        # Probing NetworkManager and UPower is synchronous: never on the main loop.
        if self.engine.download_running():
            threading.Thread(target=self.engine.connection_changed, daemon=True).start()

    def _touch(self) -> None:
        self.last_activity = time.monotonic()

    # ── Properties and signals ───────────────────────────────────────────

    def _get_property(self, _connection, _sender, _path, _interface, name):
        self._touch()
        key = snake_name(name)
        if key not in DBUS_TYPES:
            return None
        return _variant(key, getattr(self.engine.status(), key))

    def _on_change_threaded(self, changed, snapshot) -> None:
        GLib.idle_add(self._emit_changes, changed, snapshot)

    def _emit_changes(self, changed, snapshot) -> bool:
        values = {dbus_name(name): _variant(name, getattr(snapshot, name)) for name in changed if name in DBUS_TYPES}
        if values:
            self.connection.emit_signal(None, OBJECT_PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                        GLib.Variant("(sa{sv}as)", (INTERFACE, values, [])))
        if snapshot.state == STAGED and self._last_state != STAGED:
            self.connection.emit_signal(None, OBJECT_PATH, INTERFACE, "UpdateStaged",
                                        GLib.Variant("(ss)", (snapshot.staged_version, snapshot.importance)))
        if snapshot.rolled_back_at and snapshot.rolled_back_at != self._last_rolled_back_at:
            self.connection.emit_signal(None, OBJECT_PATH, INTERFACE, "RolledBack",
                                        GLib.Variant("(ss)", (snapshot.rolled_back_version, snapshot.booted_version)))
        self._last_state = snapshot.state
        self._last_rolled_back_at = snapshot.rolled_back_at
        return GLib.SOURCE_REMOVE

    # ── Methods ──────────────────────────────────────────────────────────

    def _work(self, method: str, args: tuple, sender: str):
        """The callable that performs ``method``; dispatch policy is in ``METHODS``."""
        return {
            "Check": self._do_check,
            "Download": self._do_download,
            "Cancel": self._do_cancel,
            "Apply": lambda: self._do_apply(sender),
            "SetChannel": lambda: self._do_channel(args[0], False),
            "SetChannelNow": lambda: self._do_channel(args[0], True),
            "Rollback": self._do_rollback,
            "EnrollPreview": lambda: self._do_enroll(args[0], args[1]),
            "LeavePreview": self._do_leave,
            "AcknowledgeRollback": self.engine.acknowledge_rollback_notice,
            "SetAutomaticDownload": lambda: self.engine.set_automatic_download(bool(args[0])),
            "IgnoreVersion": lambda: self.engine.ignore_version(args[0]),
            "ClearIgnoredVersion": self.engine.clear_ignored_version,
            "AdoptChannel": lambda: self._do_adopt(args[0]),
            "Automatic": self._do_automatic,
        }[method]

    def _method_call(self, connection, sender, _path, _interface, method, parameters, invocation) -> None:
        self._touch()
        args = parameters.unpack()
        if method not in METHODS:
            invocation.return_dbus_error(ERROR_PREFIX + "InvalidArgument", f"unknown method {method}")
            return
        action, wait = METHODS[method]
        work = self._work(method, args, sender)
        if action is None:
            try:
                uid = system.caller_uid(connection, sender)
            except GLib.Error:
                uid = -1
            if uid != 0:
                invocation.return_dbus_error(ERROR_PREFIX + "NotAuthorized", "only the system may call Automatic")
                return
            self._start(work, invocation, wait)
            return

        def authorized(allowed: bool) -> None:
            if not allowed:
                invocation.return_dbus_error(ERROR_PREFIX + "NotAuthorized",
                                             f"not authorized for {action}")
                return
            self._start(work, invocation, wait)

        self._authorize(sender, action, authorized)

    def _authorize(self, sender: str, action: str, callback) -> None:
        subject = ("system-bus-name", {"name": GLib.Variant("s", sender)})

        def done(connection, result):
            try:
                (is_authorized, _challenge, _details), = connection.call_finish(result).unpack()
            except GLib.Error as error:
                log.warning("polkit check for %s failed: %s", action, error.message)
                is_authorized = False
            callback(bool(is_authorized))

        self.connection.call("org.freedesktop.PolicyKit1", "/org/freedesktop/PolicyKit1/Authority",
                             "org.freedesktop.PolicyKit1.Authority", "CheckAuthorization",
                             GLib.Variant("((sa{sv})sa{ss}us)", (subject, action, {}, 1, "")),
                             GLib.VariantType.new("((bba{ss}))"), Gio.DBusCallFlags.NONE,
                             5 * 60 * 1000, None, done)

    def _start(self, work, invocation, wait: bool) -> None:
        with self.workers_lock:
            self.workers += 1

        def reply_error(error: BaseException) -> None:
            name = {"busy": "Busy", "unmanaged": "Unmanaged", "not-authorized": "NotAuthorized",
                    "not-entitled": "NotEntitled", "sign-in-required": "SignInRequired", "preview": "Preview", "not-ours": "NotOurs",
                    "inhibited": "Inhibited",
                    "invalid-argument": "InvalidArgument"}.get(classify_error(error), "Failed")
            if isinstance(error, NothingToDo):
                name = "NothingToDo"
            text = redact(error)
            GLib.idle_add(lambda: (invocation.return_dbus_error(ERROR_PREFIX + name, text), False)[1])

        def runner() -> None:
            try:
                if not wait:
                    GLib.idle_add(lambda: (invocation.return_value(None), False)[1])
                work()
                if wait:
                    GLib.idle_add(lambda: (invocation.return_value(None), False)[1])
            except Exception as error:  # reported to the caller and in the journal
                log.warning("%s", error)
                if wait:
                    reply_error(error)
            finally:
                with self.workers_lock:
                    self.workers -= 1
                self._touch()

        # Operations that cannot start (another one running) fail immediately.
        if not wait and not self.engine._operation.acquire(blocking=False):
            with self.workers_lock:
                self.workers -= 1
            invocation.return_dbus_error(ERROR_PREFIX + "Busy", "an update operation is already running")
            return
        if not wait:
            self.engine._operation.release()
        threading.Thread(target=runner, daemon=True).start()

    def _do_check(self) -> None:
        decision = self.engine.check(automatic=False)
        if decision is not None and decision.action != "none":
            from .engine import automatic_download_allowed
            allowed, _ = automatic_download_allowed(self.settings, self.engine.probes.network(),
                                                    self.engine.probes.power(),
                                                    self.engine.automatic_download_enabled())
            if allowed:
                self.engine.download(user_initiated=False)
        self.engine.flush_reports()

    def _do_download(self) -> None:
        self.engine.download(user_initiated=True)

    def _do_cancel(self) -> None:
        self.engine.cancel_download("asked over D-Bus")

    def _do_apply(self, sender: str) -> None:
        def restart():
            action = system.authorize_restart(self.connection, sender)
            log.info("the caller is allowed %s; asking logind to restart", action)
            system.reboot(self.connection)
        self.engine.apply(restart)

    def _do_rollback(self) -> None:
        self.engine.rollback()

    def _do_channel(self, channel: str, now: bool) -> None:
        self.engine.set_channel(channel, switch_now=now)
        self._background(self._do_check)

    def _do_enroll(self, channel: str, token: str) -> None:
        self.engine.enroll_preview(channel, token)
        self._background(self._do_check)

    def _do_adopt(self, channel: str) -> None:
        self.engine.adopt_channel(channel)

    def _do_leave(self) -> None:
        self.engine.leave_preview()
        self._background(self._do_check)

    def _do_automatic(self) -> None:
        self.engine.automatic()

    def _background(self, work) -> None:
        with self.workers_lock:
            self.workers += 1

        def later():
            try:
                work()
            except (Busy, UpdateError) as error:
                log.info("follow-up check not run: %s", error)
            except Exception as error:
                log.warning("follow-up check failed: %s", error)
            finally:
                with self.workers_lock:
                    self.workers -= 1
                self._touch()
        threading.Thread(target=later, daemon=True).start()


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    return Daemon().run()


if __name__ == "__main__":
    raise SystemExit(main())
