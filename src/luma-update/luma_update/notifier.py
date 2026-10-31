# SPDX-License-Identifier: Apache-2.0
"""luma-update-notifier: the one notification a person sees about system updates.

Not a resident process. A user path unit starts it whenever luma-updated
publishes a new status, and a half-hourly user timer starts it for reminders;
it posts or withdraws notifications and exits. Clicking a notification's
buttons D-Bus-activates it (``--gapplication-service``) to handle the action.
A freedesktop notification server (Phosh on phones) cannot start it that way:
it only signals the process that posted the notice, so that process stays
while its notice is up, follows the status file and reminds on its own.

Notifications use Gio.Notification under the application id
org.projectluma.Update so Luma Shell styles and groups them:

* "Luma (Prairie, Beta 1) is ready" / "Restart to finish updating", with Restart and Later.
  Later reminds once a day; an important security update reminds every 4 hours.
* "Luma couldn't finish updating to Luma (Prairie, Beta 1) and went back to the previous version."
* Clicking the notification opens Depot at Updates (``luma-depot --view updates``).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import sys
import time

__all__ = ("main", "decide", "NotifierState", "READY_ID", "ROLLBACK_ID")

APP_ID = "org.projectluma.Update"
READY_ID = "update-ready"
ROLLBACK_ID = "update-rolled-back"
REMIND_NORMAL = 24 * 3600
REMIND_SECURITY = 4 * 3600
STATUS_FILE = Path(os.environ.get("LUMA_UPDATE_STATUS_FILE", "/run/luma-update/status.json"))


def _state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "luma-update" / "notifier.json"


@dataclass
class NotifierState:
    ready_commit: str = ""
    ready_shown_at: float = 0.0
    snoozed_until: float = 0.0
    rollback_seen_at: int = 0

    @classmethod
    def load(cls, path: Path) -> "NotifierState":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        return cls(ready_commit=str(data.get("ready_commit", "")),
                   ready_shown_at=float(data.get("ready_shown_at", 0) or 0),
                   snoozed_until=float(data.get("snoozed_until", 0) or 0),
                   rollback_seen_at=int(data.get("rollback_seen_at", 0) or 0))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.__dict__), encoding="utf-8")
        os.replace(temporary, path)


@dataclass(frozen=True)
class Plan:
    show_ready: bool = False
    withdraw_ready: bool = False
    show_rollback: bool = False
    title: str = ""
    body: str = ""
    security: bool = False
    rollback_body: str = ""


def _reminder_due(state: NotifierState, now: float, interval: float) -> bool:
    """Remind after ``interval`` and after any snooze. Times recorded while the
    clock ran ahead (in the future now, or a snooze longer than one interval)
    do not hold a reminder back."""
    since_shown = now - state.ready_shown_at
    shown_due = since_shown < 0 or since_shown >= interval
    snooze_left = state.snoozed_until - now
    snooze_over = snooze_left <= 0 or snooze_left > REMIND_NORMAL  # Later snoozes one interval at most
    return shown_due and snooze_over


def _name(status: dict, key: str) -> str:
    """The published name of a version; derived the same way when an agent too old
    to publish names wrote the status (without the booted codename: pure)."""
    from . import names
    return names.clean(status.get(f"{key}_name")) or names.display_name(str(status.get(f"{key}_version") or ""))


def decide(status: dict, state: NotifierState, now: float) -> tuple[Plan, NotifierState]:
    """Pure decision: what to post, given the published status and what was shown."""
    show_ready = withdraw_ready = show_rollback = False
    title = body = ""
    security = status.get("importance") == "security"
    ignored = str(status.get("ignored_version") or "")
    if ignored and ignored == status.get("staged_version"):
        # The person said no to this one in Depot. Nothing more is said about it
        # until they ask for it there; a different version notifies normally.
        if state.ready_commit:
            withdraw_ready = True
            state.ready_commit = ""
            state.ready_shown_at = 0.0
            state.snoozed_until = 0.0
    elif status.get("state") == "staged" and status.get("staged_version"):
        commit = status.get("staged_commit") or status.get("staged_version")
        interval = REMIND_SECURITY if security else REMIND_NORMAL
        if commit != state.ready_commit:
            show_ready = True
            state.snoozed_until = 0.0
        elif _reminder_due(state, now, interval):
            show_ready = True
        if show_ready:
            state.ready_commit = commit
            state.ready_shown_at = now
            name = _name(status, "staged")
            title = "Important security update" if security else f"{name} is ready"
            body = (f"{name} is ready. Restart to finish updating."
                    if security else "Restart to finish updating.")
    elif state.ready_commit:
        withdraw_ready = True
        state.ready_commit = ""
        state.ready_shown_at = 0.0
        state.snoozed_until = 0.0
    rolled_back_at = int(status.get("rolled_back_at") or 0)
    if rolled_back_at and rolled_back_at != state.rollback_seen_at:
        show_rollback = True
        state.rollback_seen_at = rolled_back_at
    rolled_back = _name(status, "rolled_back") if show_rollback else ""
    rollback_body = (f"Luma couldn't finish updating to {rolled_back} and went back to the previous version."
                     if rolled_back else "Luma couldn't finish updating and went back to the previous version.")
    return Plan(show_ready, withdraw_ready, show_rollback, title, body, security, rollback_body), state


def _read_status() -> dict:
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def main(argv=None) -> int:
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib

    class Notifier(Gio.Application):
        def __init__(self) -> None:
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
            # Only an instance activated for a notification action lingers, briefly.
            # A sync run exits at once so the path unit can start it again on the
            # next status change (a path unit cannot re-trigger a running service).
            self.set_inactivity_timeout(10000 if "--gapplication-service" in (argv or sys.argv) else 0)
            for name, handler, parameter in (("restart", self._restart, None), ("later", self._later, None),
                                             ("open-updates", self._open_updates, GLib.VariantType.new("s")),
                                             ("dismiss-rollback", self._dismiss_rollback, None)):
                action = Gio.SimpleAction.new(name, parameter)
                action.connect("activate", handler)
                self.add_action(action)
            self._shown: set[str] = set()   # notices this process posted that are still up
            self._staying = False
            self._monitor = None
            self._reminder = 0
            self._resync_pending = False

        def do_command_line(self, command_line) -> int:
            self.hold()
            try:
                self.sync()
            finally:
                self.release()
            return 0

        def do_activate(self) -> None:
            self._open_updates(None, None)

        def sync(self) -> None:
            # Re-read if luma-updated published again while this run was deciding.
            for _ in range(5):
                try:
                    before = STATUS_FILE.stat().st_mtime_ns
                except OSError:
                    before = None
                self._sync_once()
                try:
                    after = STATUS_FILE.stat().st_mtime_ns
                except OSError:
                    after = None
                if before == after:
                    break
            self._settle()

        def _settle(self) -> None:
            """Stay while a notice is up on a server that cannot start this app for its buttons."""
            stay = bool(self._shown) and not self._server_starts_app()
            if stay and not self._staying:
                self._staying = True
                self.hold()
                self._monitor = Gio.File.new_for_path(str(STATUS_FILE)).monitor_file(Gio.FileMonitorFlags.NONE, None)
                self._monitor.connect("changed", self._status_changed)
                # The reminder timer cannot start a service that is still running.
                self._reminder = GLib.timeout_add_seconds(30 * 60, self._remind)
            elif not stay and self._staying:
                self._staying = False
                self._monitor.cancel()
                self._monitor = None
                GLib.source_remove(self._reminder)
                self._reminder = 0
                self.release()

        @staticmethod
        def _server_starts_app() -> bool:
            """GNOME Shell (org.gtk.Notifications) D-Bus-activates the app for a click;
            a freedesktop server such as Phosh signals only the process that posted."""
            try:
                session = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                reply = session.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                          "NameHasOwner", GLib.Variant("(s)", ("org.gtk.Notifications",)),
                                          GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, 5000, None)
                return bool(reply.unpack()[0])
            except GLib.Error:
                return True

        def _status_changed(self, *_args) -> None:
            # luma-updated replaces the file in one rename; one sync covers the burst.
            if not self._resync_pending:
                self._resync_pending = True
                GLib.timeout_add(500, self._resync)

        def _resync(self) -> bool:
            self._resync_pending = False
            self.sync()
            return GLib.SOURCE_REMOVE

        def _remind(self) -> bool:
            self.sync()
            return GLib.SOURCE_CONTINUE

        def _sync_once(self) -> None:
            status = _read_status()
            path = _state_path()
            plan, state = decide(status, NotifierState.load(path), time.time())
            if plan.withdraw_ready:
                self.withdraw_notification(READY_ID)
                self._shown.discard(READY_ID)
            if not status.get("rolled_back_at"):   # acknowledged, here or in Depot
                self._shown.discard(ROLLBACK_ID)
            if plan.show_ready:
                notification = Gio.Notification.new(plan.title)
                notification.set_body(plan.body)
                notification.set_icon(Gio.ThemedIcon.new("software-update-available-symbolic"))
                notification.set_priority(Gio.NotificationPriority.HIGH if plan.security
                                          else Gio.NotificationPriority.NORMAL)
                notification.set_default_action_and_target("app.open-updates", GLib.Variant.new_string(READY_ID))
                notification.add_button("Later", "app.later")
                notification.add_button("Restart", "app.restart")
                self.send_notification(READY_ID, notification)
                self._shown.add(READY_ID)
            if plan.show_rollback:
                notification = Gio.Notification.new("Luma went back to the previous version")
                notification.set_body(plan.rollback_body)
                notification.set_icon(Gio.ThemedIcon.new("dialog-warning-symbolic"))
                notification.set_default_action_and_target("app.open-updates", GLib.Variant.new_string(ROLLBACK_ID))
                notification.add_button("OK", "app.dismiss-rollback")
                self.send_notification(ROLLBACK_ID, notification)
                self._shown.add(ROLLBACK_ID)
            state.save(path)

        def _restart(self, _action, _parameter) -> None:
            self.withdraw_notification(READY_ID)
            self._shown.discard(READY_ID)
            # The session's own restart path lets applications save work, shows
            # the end-session dialog and respects inhibitors, as GNOME Software
            # does. Its Reboot call returns only when that dialog is done, and
            # the person may cancel it, so there is no timeout and no fallback
            # on error. luma-updated's Apply() (logind) is used only when there
            # is no GNOME-style session manager at all.
            no_session_manager = ("org.freedesktop.DBus.Error.ServiceUnknown",
                                  "org.freedesktop.DBus.Error.NameHasNoOwner",
                                  "org.freedesktop.DBus.Error.UnknownMethod")
            self.hold()

            def apply_done(connection, result):
                try:
                    connection.call_finish(result)
                except GLib.Error as error:
                    print(f"luma-update-notifier: restart failed: {error.message}", file=sys.stderr)
                finally:
                    self.release()

            def reboot_done(connection, result):
                try:
                    connection.call_finish(result)
                except GLib.Error as error:
                    if Gio.DBusError.get_remote_error(error) in no_session_manager:
                        system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
                        system.call("org.projectluma.Update1", "/org/projectluma/Update1", "org.projectluma.Update1",
                                    "Apply", None, None, Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION,
                                    GLib.MAXINT, None, apply_done)
                        return
                self.release()

            session = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            session.call("org.gnome.SessionManager", "/org/gnome/SessionManager", "org.gnome.SessionManager",
                         "Reboot", None, None, Gio.DBusCallFlags.NONE, GLib.MAXINT, None, reboot_done)
            self._settle()

        def _later(self, _action, _parameter) -> None:
            self.withdraw_notification(READY_ID)
            self._shown.discard(READY_ID)
            path = _state_path()
            state = NotifierState.load(path)
            status = _read_status()
            interval = REMIND_SECURITY if status.get("importance") == "security" else REMIND_NORMAL
            state.ready_shown_at = time.time()
            state.snoozed_until = time.time() + interval
            state.save(path)
            self._settle()

        def _open_updates(self, _action, parameter) -> None:
            if parameter is not None:   # the server closes a notice once it is clicked
                self._shown.discard(parameter.get_string())
            # Depot starts over D-Bus in its own unit: a child of this service
            # would be stopped with it when this process exits.
            self.hold()

            def done(connection, result):
                try:
                    connection.call_finish(result)
                except GLib.Error as error:
                    print(f"luma-update-notifier: could not open Depot over D-Bus: {error.message}", file=sys.stderr)
                    self._launch_depot()
                self._settle()
                self.release()

            session = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            session.call("org.projectluma.Depot", "/org/projectluma/Depot", "org.freedesktop.Application",
                         "ActivateAction", GLib.Variant("(sava{sv})", ("show-view", [GLib.Variant("s", "updates")], {})),
                         None, Gio.DBusCallFlags.NONE, 60000, None, done)

        def _launch_depot(self) -> None:
            executable = shutil.which("luma-depot")
            if executable:
                Gio.Subprocess.new([executable, "--view", "updates"], Gio.SubprocessFlags.NONE)
            else:
                info = Gio.DesktopAppInfo.new("org.projectluma.Depot.desktop")
                if info is not None:
                    info.launch([], None)

        def _dismiss_rollback(self, _action, _parameter) -> None:
            self.withdraw_notification(ROLLBACK_ID)
            self._shown.discard(ROLLBACK_ID)
            try:
                system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
                system.call_sync("org.projectluma.Update1", "/org/projectluma/Update1", "org.projectluma.Update1",
                                 "AcknowledgeRollback", None, None, Gio.DBusCallFlags.NONE, 30000, None)
            except GLib.Error:
                pass
            self._settle()

    return Notifier().run(sys.argv if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
