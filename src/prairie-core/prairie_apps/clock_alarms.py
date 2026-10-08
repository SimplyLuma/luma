# SPDX-License-Identifier: Apache-2.0

"""Clock's alarms and timers: the service that rings them.

Clock used to hand every alarm and timer to systemd as a user timer unit that
ran /usr/bin/prairie-clock-alarm. That cannot work for Clock as a Flatpak: the
sandbox cannot reach the user service manager, the unit directory it would
write is its private config, and the helper does not exist on the host.
Granting org.freedesktop.systemd1 would let the app run anything on the host.

This is the design GNOME Clocks and other alarm apps on Flathub use, and it is
the same code natively and in the sandbox:

* **The Clock process rings them.** Clock is one GApplication. While anything
  is scheduled — an enabled alarm, a snooze, a running timer — it holds itself
  alive, so closing the window does not stop it. With nothing scheduled it
  exits like any other app. Nothing is resident while there are no alarms.
* **It is started at login** by an autostart entry requested through the
  Background portal (`RequestBackground` with `autostart` and the command line
  `prairie-clock --gapplication-service`). ADR-033's `prairie-clock --agent` is the
  same service mode; the portal entry keeps the older spelling so an entry written
  now still starts an earlier Clock after a rollback). The portal writes the entry, asks
  the person if the desktop's policy says to, and removes the entry again when
  the last alarm goes. On the operating system, if the portal is not there,
  Clock writes the same entry itself.
* **It notifies through GNotification**, which is the Notification portal in
  the sandbox and the shell's notification service on the host. Snooze and
  Stop are notification buttons bound to application actions.
* **It waits on the wall clock, not a monotonic timeout.** A GLib timeout does
  not advance while the machine is suspended, so a 07:00 alarm armed at
  midnight would ring hours late. A CLOCK_REALTIME timerfd with an absolute
  deadline expires as soon as the machine resumes past that time, and
  TFD_TIMER_CANCEL_ON_SET wakes it if somebody changes the clock.

* **With luma-background, a windowless agent rings them (ADR-033).**
  `prairie-clock --agent` (clock_agent.py) runs this same service without GTK
  under the service's limits: it owns `org.projectluma.Clock.Agent`, publishes
  the next alarm or timer and whether one is ringing, notifies through
  org.freedesktop.Notifications with Snooze and Stop, follows edits to the
  store, and exits when nothing is scheduled. While that agent runs, a Clock
  window or service-mode Clock leaves ringing to it; when something is
  scheduled and luma-background is present, Clock asks the service for its
  agent. Without the service, or with background activity turned off, Clock
  rings alarms itself exactly as described above.

* **It wakes a sleeping computer for the next alarm.** Arming a wake-capable
  timer (CLOCK_REALTIME_ALARM, or a systemd timer with WakeSystem=true) needs
  CAP_WAKE_ALARM, which no session process has; the user service manager falls
  back to an ordinary clock without saying so. luma-background's system helper
  org.projectluma.BackgroundWake1 arms it for the active session (polkit
  decides; systemd holds the RTC alarm), so Clock asks it to wake the computer
  WAKE_LEAD_SECONDS before the next alarm, snooze or timer, and moves or clears
  that wake-up whenever the plan changes. Without the helper (the sandbox, an
  older system) an alarm that falls due while the computer sleeps rings when it
  resumes if that is within LATE_RING_LIMIT, and is reported as missed after.
* **It keeps the computer awake while an alarm needs it.** From
  AWAKE_BEFORE_SECONDS before an alarm or timer until it is answered, Clock holds
  systemd-logind's block inhibitor for sleep and idle and GNOME's session idle
  and suspend inhibitor (SessionInhibitor), so the computer neither suspends nor
  blanks while it rings. The ringing notification is critical, which is what
  makes the shell wake a screen that is already dark. In the sandbox the same
  hold is GtkApplication's, through the Inhibit portal.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from gi.repository import Gio, GLib

from .clock_backend import (
    Alarm, ClockStore, TimerRecord, format_clock, next_occurrence, previous_occurrence,
    snooze_until, uses_24_hour, valid_uid, volume_ramp,
)

APP_ID = "org.projectluma.Clock"
LOG = logging.getLogger("prairie.clock.alarms")

# How late an alarm may still ring. Past this it is reported as missed rather
# than going off long after the moment it was for (a laptop opened at noon
# should not blare the 07:00 alarm).
LATE_RING_LIMIT = 10 * 60
# The first time this service runs there is no record of when it last looked.
# A migrated systemd unit hands over the alarm it fired for explicitly; this
# small window only covers the seconds between that unit firing and the
# service starting.
FIRST_RUN_LOOKBACK = 2 * 60
TIMER_CHIME_SECONDS = 6
# How long a service started with nothing to do waits before it exits, so a
# D-Bus activation that wants to open a window has time to arrive.
IDLE_EXIT_MS = 10_000

KIND_ALARM, KIND_SNOOZE, KIND_TIMER = "alarm", "snooze", "timer"

TONES = {"Chime": "alarm-clock-elapsed", "Ripple": "complete", "Ascend": "bell"}

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
BACKGROUND_REASON = "Clock rings your alarms and timers when its window is closed."

BACKGROUND_UNKNOWN = "unknown"
BACKGROUND_ALLOWED = "allowed"
BACKGROUND_DENIED = "denied"
BACKGROUND_UNAVAILABLE = "unavailable"

# Keeping the computer awake, and waking it, for an alarm.
LOGIND_BUS = "org.freedesktop.login1"
LOGIND_PATH = "/org/freedesktop/login1"
LOGIND_MANAGER = "org.freedesktop.login1.Manager"
SESSION_MANAGER_BUS = "org.gnome.SessionManager"
SESSION_MANAGER_PATH = "/org/gnome/SessionManager"
GSM_INHIBIT_SUSPEND, GSM_INHIBIT_IDLE = 4, 8
WAKE_BUS = "org.projectluma.BackgroundWake1"
WAKE_PATH = "/org/projectluma/BackgroundWake1"
# The computer is woken this long before an alarm or timer is due, so it has
# resumed, found its audio device and drawn the lock screen by the time it rings.
WAKE_LEAD_SECONDS = 60
# An alarm or timer due this soon keeps the computer from going to sleep first.
AWAKE_BEFORE_SECONDS = 120


def is_sandboxed() -> bool:
    return Path("/.flatpak-info").exists()


# ---------------------------------------------------------------------------
# What is due, and when
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    kind: str
    uid: str
    at: float


def _local(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch).astimezone()


def upcoming_events(alarms, timers, snoozes: dict[str, float], after: float) -> list[Event]:
    """Every event strictly after `after`, soonest first."""
    moment = _local(after)
    events = [
        Event(KIND_ALARM, alarm.uid, next_occurrence(alarm, moment).timestamp())
        for alarm in alarms if alarm.enabled
    ]
    events += [Event(KIND_SNOOZE, uid, float(at)) for uid, at in snoozes.items() if at > after]
    events += [Event(KIND_TIMER, record.uid, float(record.fires_at)) for record in timers if record.fires_at > after]
    return sorted(events, key=lambda event: (event.at, event.kind, event.uid))


def missed_events(alarms, timers, snoozes: dict[str, float], since: float, now: float) -> list[Event]:
    """What fell due in (since, now] while nothing was running to ring it.

    A saved timer that has passed is always unfinished business, whenever it
    passed: finishing a timer removes its record.
    """
    moment = _local(now)
    events = []
    for alarm in alarms:
        if not alarm.enabled:
            continue
        previous = previous_occurrence(alarm, moment)
        if previous is not None and since < previous.timestamp() <= now:
            events.append(Event(KIND_ALARM, alarm.uid, previous.timestamp()))
    events += [Event(KIND_SNOOZE, uid, float(at)) for uid, at in snoozes.items() if at <= now]
    events += [Event(KIND_TIMER, record.uid, float(record.fires_at)) for record in timers if record.fires_at <= now]
    return sorted(events, key=lambda event: (event.at, event.kind, event.uid))


def rings_now(event: Event, now: float) -> bool:
    """Ring it, or report it missed."""
    return now - event.at <= LATE_RING_LIMIT


def timer_sentence(total_seconds: int) -> str:
    """`Your 5-minute timer finished.` · `Your 90-second timer finished.`"""
    if total_seconds <= 0:
        return "Your timer finished."
    if total_seconds % 60 and total_seconds < 600:
        return f"Your {total_seconds}-second timer finished."
    return f"Your {round(total_seconds / 60)}-minute timer finished."


# ---------------------------------------------------------------------------
# The scheduler's own state
# ---------------------------------------------------------------------------


class SchedulerState:
    """When the service last looked, open snoozes, and what autostart it holds.

    Kept beside Clock's records but in its own file, because Connect sync and
    the window watch state.json and this changes every time an alarm rings.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.checked_at: float | None = None
        self.snoozes: dict[str, float] = {}
        self.autostart: bool | None = None
        self.handoff: list[dict] = []
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        checked = data.get("checked_at")
        self.checked_at = float(checked) if isinstance(checked, (int, float)) else None
        self.snoozes = {}
        for uid, at in (data.get("snoozes") or {}).items():
            try:
                self.snoozes[valid_uid(uid)] = float(at)
            except (TypeError, ValueError):
                continue
        autostart = data.get("autostart")
        self.autostart = autostart if isinstance(autostart, bool) else None
        self.handoff = [item for item in data.get("handoff") or [] if isinstance(item, dict)]

    def save(self) -> None:
        data = {
            "checked_at": self.checked_at, "snoozes": self.snoozes,
            "autostart": self.autostart, "handoff": self.handoff,
        }
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".scheduler-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(data, output, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


def scheduler_path(store: ClockStore) -> Path:
    return store.path.parent / "scheduler.json"


def hand_off(kind: str, uid: str, *, store: ClockStore | None = None, now: float | None = None) -> None:
    """Record that a legacy systemd unit fired for this alarm or timer."""
    if kind not in {KIND_ALARM, KIND_TIMER}:
        raise ValueError("Unknown kind.")
    store = store or ClockStore()
    state = SchedulerState(scheduler_path(store))
    state.handoff.append({"kind": kind, "uid": valid_uid(uid), "at": time.time() if now is None else now})
    state.save()


# ---------------------------------------------------------------------------
# Waiting on the wall clock
# ---------------------------------------------------------------------------


def _fd_add(fd: int, callback) -> int:
    try:
        import gi

        gi.require_version("GLibUnix", "2.0")
        from gi.repository import GLibUnix

        return GLibUnix.fd_add_full(GLib.PRIORITY_DEFAULT, fd, GLib.IOCondition.IN, callback)
    except (ImportError, ValueError, AttributeError):
        return GLib.unix_fd_add_full(GLib.PRIORITY_DEFAULT, fd, GLib.IOCondition.IN, callback)


class WallClockTimer:
    """Call back at a wall-clock instant, across suspend and clock changes."""

    FALLBACK_STEP = 30

    def __init__(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        self._fd: int | None = None
        self._source = 0
        self.deadline: float | None = None

    def arm(self, deadline: float) -> None:
        self.cancel()
        self.deadline = deadline
        if hasattr(os, "timerfd_create"):
            try:
                fd = os.timerfd_create(time.CLOCK_REALTIME, flags=os.TFD_NONBLOCK | os.TFD_CLOEXEC)
                os.timerfd_settime(
                    fd, flags=os.TFD_TIMER_ABSTIME | os.TFD_TIMER_CANCEL_ON_SET,
                    initial=max(deadline, 1e-3),
                )
            except OSError as error:
                LOG.warning("A wall-clock timer could not be armed (%s); checking periodically instead", error)
            else:
                self._fd = fd
                self._source = _fd_add(fd, self._ready)
                return
        # Without timerfd: short monotonic steps that re-read the wall clock,
        # so a suspend costs at most one step of lateness.
        self._source = GLib.timeout_add_seconds(self._step(), self._poll)

    def _step(self) -> int:
        remaining = (self.deadline or 0) - time.time()
        return max(1, min(self.FALLBACK_STEP, int(remaining) + 1))

    def _poll(self) -> bool:
        if self.deadline is not None and time.time() >= self.deadline:
            self._source = 0
            self.deadline = None
            self._callback()
            return GLib.SOURCE_REMOVE
        self._source = GLib.timeout_add_seconds(self._step(), self._poll)
        return GLib.SOURCE_REMOVE

    def _ready(self, fd, _condition) -> bool:
        try:
            os.read(fd, 8)
        except OSError as error:
            # ECANCELED: the clock was set. Either way the schedule is worked
            # out again from the wall clock as it is now.
            if error.errno not in (errno.ECANCELED, errno.EAGAIN):
                LOG.warning("Wall-clock timer read failed: %s", error)
        self._source = 0
        self._close()
        self.deadline = None
        self._callback()
        return GLib.SOURCE_REMOVE

    def _close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def cancel(self) -> None:
        if self._source:
            GLib.source_remove(self._source)
            self._source = 0
        self._close()
        self.deadline = None


# ---------------------------------------------------------------------------
# Migration from the systemd units
# ---------------------------------------------------------------------------

_LEGACY_UNIT = re.compile(r"prairie-(alarm|timer)-([0-9a-f]{32})")


def user_unit_dir() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config_home / "systemd/user"


def legacy_units(unit_dir: Path | None = None) -> list[str]:
    directory = user_unit_dir() if unit_dir is None else Path(unit_dir)
    try:
        names = {path.stem for path in directory.glob("prairie-*-*.timer")}
    except OSError:
        return []
    return sorted(name for name in names if _LEGACY_UNIT.fullmatch(name))


def migrate_systemd_units(*, unit_dir: Path | None = None, runner=None) -> list[str]:
    """Withdraw the units earlier Clock releases wrote, keeping the records.

    The alarm and timer records stay in Clock's store, which is all the service
    needs to take over. A snooze that was running as a transient systemd timer
    is stopped too; its alarm rings again at its next occurrence.
    """
    if is_sandboxed():
        return []
    directory = user_unit_dir() if unit_dir is None else Path(unit_dir)
    names = legacy_units(directory)
    if not names:
        return []
    runner = runner or subprocess.run
    options = dict(check=False, capture_output=True, text=True, timeout=15)
    try:
        for name in names:
            runner(["systemctl", "--user", "disable", "--now", f"{name}.timer"], **options)
        # Timers only: a snooze service that is running is the hand-over itself.
        runner(["systemctl", "--user", "stop", "prairie-snooze-*.timer"], **options)
    except (OSError, subprocess.SubprocessError) as error:
        LOG.warning("systemctl was not available to stop the old alarm units: %s", error)
    for name in names:
        for suffix in (".timer", ".service"):
            try:
                (directory / f"{name}{suffix}").unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                LOG.warning("Could not remove %s%s: %s", name, suffix, error)
    try:
        runner(["systemctl", "--user", "daemon-reload"], **options)
    except (OSError, subprocess.SubprocessError):
        pass
    LOG.info("Moved %d alarm and timer units from systemd to the Clock service", len(names))
    return names


# ---------------------------------------------------------------------------
# Portals and autostart
# ---------------------------------------------------------------------------


def register_host_application(app_id: str = APP_ID, *, connection: Gio.DBusConnection | None = None) -> bool:
    """Tell xdg-desktop-portal which app this unsandboxed process is.

    A host app is otherwise identified from its systemd scope, which a D-Bus
    activated or terminal-started Clock does not have, and the Background portal
    refuses autostart to an app it cannot name. It has to happen before any
    other portal call on the connection, so Clock does it before GTK starts.
    """
    if is_sandboxed():
        return False
    try:
        connection = connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        connection.call_sync(
            PORTAL_BUS, PORTAL_PATH, "org.freedesktop.host.portal.Registry", "Register",
            GLib.Variant("(sa{sv})", (app_id, {})), None, Gio.DBusCallFlags.NONE, 2000, None,
        )
    except GLib.Error as error:
        LOG.debug("Host app registration not available: %s", error.message)
        return False
    return True


def autostart_command(sandboxed: bool | None = None) -> list[str]:
    if is_sandboxed() if sandboxed is None else sandboxed:
        # The portal rewrites this into `flatpak run --command=… <app id>`.
        return ["prairie-clock", "--gapplication-service"]
    return [shutil.which("prairie-clock") or "/usr/bin/prairie-clock", "--gapplication-service"]


def autostart_entry_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config_home / "autostart" / f"{APP_ID}.desktop"


def write_autostart_entry(enable: bool, *, path: Path | None = None, command: list[str] | None = None) -> bool:
    """The entry the portal would write, for a host without the portal."""
    path = autostart_entry_path() if path is None else Path(path)
    if not enable:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return False
    command = command or autostart_command()
    keyfile = GLib.KeyFile()
    group = GLib.KEY_FILE_DESKTOP_GROUP
    keyfile.set_string(group, "Type", "Application")
    keyfile.set_string(group, "Name", "Clock")
    keyfile.set_string(group, "Exec", " ".join(_exec_argument(part) for part in command))
    keyfile.set_boolean(group, "NoDisplay", True)
    keyfile.set_string(group, "X-XDP-Autostart", APP_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    keyfile.save_to_file(str(path))
    return True


def _exec_argument(argument: str) -> str:
    """Quote one Exec argument the way the Desktop Entry specification says."""
    if argument and not any(c in argument for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return argument
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in argument)
    return f'"{escaped}"'


class BackgroundPortal:
    """org.freedesktop.portal.Background, asynchronously.

    The Response signal is subscribed before the call, on the request path the
    handle_token makes predictable: a portal that answers at once would
    otherwise emit it before anybody is listening.
    """

    def __init__(self, connection: Gio.DBusConnection) -> None:
        self.connection = connection

    def request(self, *, autostart: bool, commandline: list[str], reason: str, callback) -> None:
        token = f"luma_clock_{uuid.uuid4().hex[:16]}"
        sender = (self.connection.get_unique_name() or "").lstrip(":").replace(".", "_")
        handle = f"{PORTAL_PATH}/request/{sender}/{token}"
        subscription = 0

        def response(connection, _sender, _path, _interface, _signal, parameters, *_user):
            connection.signal_unsubscribe(subscription)
            code, results = parameters.unpack()
            callback(int(code), dict(results), None)

        subscription = self.connection.signal_subscribe(
            None, "org.freedesktop.portal.Request", "Response", handle, None,
            Gio.DBusSignalFlags.NONE, response,
        )
        options = {
            "handle_token": GLib.Variant("s", token),
            "reason": GLib.Variant("s", reason),
            "autostart": GLib.Variant("b", autostart),
            "commandline": GLib.Variant("as", commandline),
            "dbus-activatable": GLib.Variant("b", False),
        }

        def done(connection, result, *_user):
            try:
                connection.call_finish(result)
            except GLib.Error as error:
                connection.signal_unsubscribe(subscription)
                callback(None, {}, error)

        self.connection.call(
            PORTAL_BUS, PORTAL_PATH, "org.freedesktop.portal.Background", "RequestBackground",
            GLib.Variant("(sa{sv})", ("", options)), GLib.VariantType("(o)"),
            Gio.DBusCallFlags.NONE, -1, None, done,
        )

    def set_status(self, message: str) -> None:
        """What the desktop's list of background apps says Clock is doing."""
        self.connection.call(
            PORTAL_BUS, PORTAL_PATH, "org.freedesktop.portal.Background", "SetStatus",
            GLib.Variant("(a{sv})", ({"message": GLib.Variant("s", message[:96])},)), None,
            Gio.DBusCallFlags.NONE, -1, None, lambda connection, result, *_: _ignore(connection, result),
        )


def _ignore(connection, result) -> None:
    try:
        connection.call_finish(result)
    except GLib.Error as error:
        LOG.debug("Portal call failed: %s", error.message)


@dataclass(frozen=True)
class AlarmNotification:
    """What Clock shows: buttons and the default action name app actions and a string target."""

    title: str
    body: str
    priority: str  # "urgent" | "high" | "normal"
    category: str
    buttons: tuple[tuple[str, str, str], ...]  # (label, action, target)
    default: tuple[str, str] | None


class ApplicationNotifications:
    """GNotification through the application: the Notification portal in a sandbox, the shell on the host."""

    PRIORITIES = {"urgent": Gio.NotificationPriority.URGENT, "high": Gio.NotificationPriority.HIGH,
                  "normal": Gio.NotificationPriority.NORMAL}

    def __init__(self, application: Gio.Application) -> None:
        self.application = application

    def post(self, identifier: str, spec: AlarmNotification) -> None:
        notification = Gio.Notification.new(spec.title)
        notification.set_body(spec.body)
        notification.set_priority(self.PRIORITIES.get(spec.priority, Gio.NotificationPriority.NORMAL))
        if spec.category:
            notification.set_category(spec.category)
        notification.set_icon(Gio.ThemedIcon.new(APP_ID))
        for label, action, target in spec.buttons:
            notification.add_button_with_target(label, f"app.{action}", GLib.Variant("s", target))
        if spec.default is not None:
            notification.set_default_action_and_target(f"app.{spec.default[0]}", GLib.Variant("s", spec.default[1]))
        self.application.send_notification(identifier, notification)

    def withdraw(self, identifier: str) -> None:
        self.application.withdraw_notification(identifier)


# ---------------------------------------------------------------------------
# Sound
# ---------------------------------------------------------------------------


def tone_file(sound: str) -> Path | None:
    name = TONES.get(sound, TONES["Chime"])
    directories = [os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")]
    directories += (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    for directory in directories:
        for suffix in (".oga", ".ogg", ".wav"):
            candidate = Path(directory) / "sounds/freedesktop/stereo" / f"{name}{suffix}"
            if candidate.is_file():
                return candidate
    return None


_GST = None


def _gst():
    global _GST
    if _GST is None:
        try:
            import gi

            gi.require_version("Gst", "1.0")
            from gi.repository import Gst

            Gst.init(None)
            _GST = Gst
        except (ImportError, ValueError) as error:
            LOG.warning("GStreamer is not available, alarms will be silent: %s", error)
            _GST = False
    return _GST or None


class Ringer:
    """One looping tone at a volume that climbs rather than detonates.

    The ramp is this playback's own volume, never the system's: turning
    somebody's volume up and leaving it there would be a worse bug than a quiet
    alarm.
    """

    def __init__(self) -> None:
        self._playbin = None
        self._expiry = 0
        self._ramp_source = 0
        self._started = 0.0
        self._ramp = True
        self.on_finished: Callable[[], None] | None = None

    @property
    def playing(self) -> bool:
        return self._playbin is not None

    def start(self, sound: str, seconds: float, *, ramp: bool = True) -> bool:
        self.stop()
        Gst = _gst()
        path = tone_file(sound)
        if Gst is None or path is None:
            if path is None:
                LOG.warning("No alarm tone is installed (sound-theme-freedesktop)")
            return False
        playbin = Gst.ElementFactory.make("playbin", None)
        if playbin is None:
            LOG.warning("GStreamer has no playbin, alarms will be silent")
            return False
        playbin.set_property("uri", Gio.File.new_for_path(str(path)).get_uri())
        playbin.set_property("volume", volume_ramp(0.0) if ramp else 1.0)
        bus = playbin.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._message)
        self._playbin, self._ramp, self._started = playbin, ramp, time.monotonic()
        if playbin.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            LOG.warning("The alarm tone could not be played")
            self.stop()
            return False
        self._expiry = GLib.timeout_add(int(seconds * 1000), self._expire)
        if ramp:
            self._ramp_source = GLib.timeout_add(500, self._raise)
        return True

    def _message(self, _bus, message) -> None:
        Gst = _gst()
        if Gst is None or self._playbin is None:
            return
        if message.type == Gst.MessageType.EOS:
            self._playbin.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, 0)
        elif message.type == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            LOG.warning("The alarm tone stopped: %s", error.message)
            self.stop()

    def _raise(self) -> bool:
        if self._playbin is None:
            self._ramp_source = 0
            return GLib.SOURCE_REMOVE
        self._playbin.set_property("volume", volume_ramp(time.monotonic() - self._started))
        return GLib.SOURCE_CONTINUE

    def _expire(self) -> bool:
        self._expiry = 0
        self.stop(expired=True)
        return GLib.SOURCE_REMOVE

    def stop(self, *, expired: bool = False) -> None:
        for name in ("_expiry", "_ramp_source"):
            source = getattr(self, name)
            if source:
                GLib.source_remove(source)
                setattr(self, name, 0)
        playbin, self._playbin = self._playbin, None
        if playbin is not None:
            Gst = _gst()
            playbin.get_bus().remove_signal_watch()
            if Gst is not None:
                playbin.set_state(Gst.State.NULL)
        if expired and self.on_finished is not None:
            self.on_finished()


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


class SessionInhibitor:
    """Keeps the computer awake, and its screen on, while an alarm needs it.

    Two holds, because two components decide. systemd-logind's block inhibitor
    for sleep and idle stops a suspend that any session tool starts, and is what
    desktops other than GNOME read. GNOME's session-manager inhibitor is what
    gnome-settings-daemon consults before it blanks the screen or suspends an
    idle computer. (A lid closed on purpose still suspends: logind ignores
    inhibitors for the lid switch by default, and should.)

    If logind is not on the bus when the hold is wanted -- it restarted, or the
    computer is still resuming -- the hold is taken the moment it returns, and
    taken again if logind restarts while it is held.
    """

    def __init__(self, *, system: Gio.DBusConnection | None = None,
                 session: Gio.DBusConnection | None = None, app_id: str = APP_ID) -> None:
        self._buses = {Gio.BusType.SYSTEM: system, Gio.BusType.SESSION: session}
        self.app_id = app_id
        self.reason = ""
        self._fd = -1
        self._cookie = 0
        self._watch = 0

    @property
    def held(self) -> bool:
        return bool(self.reason)

    def _bus(self, kind: Gio.BusType) -> Gio.DBusConnection | None:
        if self._buses.get(kind) is None:
            try:
                self._buses[kind] = Gio.bus_get_sync(kind, None)
            except GLib.Error as error:
                LOG.info("No %s bus for the sleep hold: %s",
                         "system" if kind == Gio.BusType.SYSTEM else "session", error.message)
                return None
        return self._buses[kind]

    def hold(self, reason: str) -> None:
        if self.held:
            return
        self.reason = reason
        self._take_logind()
        self._take_session_manager()
        system = self._bus(Gio.BusType.SYSTEM)
        if system is not None and not self._watch:
            self._watch = Gio.bus_watch_name_on_connection(
                system, LOGIND_BUS, Gio.BusNameWatcherFlags.NONE, self._logind_appeared, self._logind_vanished)

    def release(self) -> None:
        if not self.held:
            return
        self.reason = ""
        if self._watch:
            Gio.bus_unwatch_name(self._watch)
            self._watch = 0
        self._close_logind()
        cookie, self._cookie = self._cookie, 0
        session = self._bus(Gio.BusType.SESSION) if cookie else None
        if session is not None:
            try:
                session.call_sync(SESSION_MANAGER_BUS, SESSION_MANAGER_PATH, SESSION_MANAGER_BUS, "Uninhibit",
                                  GLib.Variant("(u)", (cookie,)), None, Gio.DBusCallFlags.NO_AUTO_START, 3000, None)
            except GLib.Error as error:
                LOG.info("GNOME's session manager kept an idle hold: %s", error.message)
        LOG.info("Sleep and idle are no longer held off")

    def _take_logind(self) -> None:
        system = self._bus(Gio.BusType.SYSTEM)
        if system is None or self._fd >= 0:
            return
        try:
            result, fds = system.call_with_unix_fd_list_sync(
                LOGIND_BUS, LOGIND_PATH, LOGIND_MANAGER, "Inhibit",
                GLib.Variant("(ssss)", ("sleep:idle", "Clock", self.reason, "block")),
                GLib.VariantType("(h)"), Gio.DBusCallFlags.NO_AUTO_START, 3000, None, None)
        except GLib.Error as error:
            LOG.info("systemd-logind did not take the sleep hold (yet): %s", error.message)
            return
        index = result.unpack()[0]
        if fds is None or index < 0 or index >= fds.get_length():
            LOG.warning("systemd-logind answered Inhibit without a file descriptor")
            return
        # A duplicate: the list closes its own copy, this one is the hold.
        self._fd = fds.get(index)
        LOG.info("Holding off sleep and idle: %s", self.reason)

    def _close_logind(self) -> None:
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = -1

    def _logind_appeared(self, _connection, _name, _owner) -> None:
        if self.held and self._fd < 0:
            self._take_logind()

    def _logind_vanished(self, _connection, _name) -> None:
        # A logind that went away took its inhibitors with it.
        self._close_logind()

    def _take_session_manager(self) -> None:
        session = self._bus(Gio.BusType.SESSION)
        if session is None:
            return
        try:
            result = session.call_sync(
                SESSION_MANAGER_BUS, SESSION_MANAGER_PATH, SESSION_MANAGER_BUS, "Inhibit",
                GLib.Variant("(susu)", (self.app_id, 0, self.reason, GSM_INHIBIT_SUSPEND | GSM_INHIBIT_IDLE)),
                GLib.VariantType("(u)"), Gio.DBusCallFlags.NO_AUTO_START, 3000, None)
        except GLib.Error as error:
            LOG.debug("No GNOME session manager to hold idle: %s", error.message)
            return
        self._cookie = int(result.unpack()[0])


class SystemWake:
    """Wakes a sleeping computer shortly before Clock's next alarm or timer.

    Only the system can arm the RTC; luma-background's helper
    org.projectluma.BackgroundWake1 does it for the active session, with polkit
    deciding. There is one wake-up, named by Clock's application ID: setting a
    new time replaces it and None clears it. A call is made only when the time
    changes. Without the helper, or when it refuses, alarms ring on resume as
    before, and the journal says why once.
    """

    def __init__(self, *, connection: Gio.DBusConnection | None = None, name: str = APP_ID) -> None:
        self._connection = connection
        self.name = name
        # Nothing is known to be armed when Clock starts; a wake-up a crashed
        # Clock left behind elapses once, and systemd then forgets it.
        self.at: int | None = None
        self._reported: set[str] = set()

    def _bus(self) -> Gio.DBusConnection | None:
        if self._connection is None:
            try:
                self._connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            except GLib.Error as error:
                self._report("no-system-bus", error.message)
                return None
        return self._connection

    def _report(self, key: str, message: str) -> None:
        if key not in self._reported:
            self._reported.add(key)
            LOG.info("The computer will not be woken for alarms (%s): %s", key, message)

    def set(self, at: float | None, *, wait: bool = False) -> None:
        at = None if at is None else int(at)
        if at == self.at:
            return
        previous, self.at = self.at, at
        connection = self._bus()
        if connection is None:
            return
        if at is None:
            method, parameters = "ClearWakeup", GLib.Variant("(s)", (self.name,))
        else:
            method, parameters = "SetWakeup", GLib.Variant("(sx)", (self.name, at))

        def finished(error: GLib.Error | None) -> None:
            if error is None:
                if at is not None:
                    LOG.info("The computer will wake at %s for Clock",
                             datetime.fromtimestamp(at).astimezone().isoformat(timespec="seconds"))
                return
            self._report(Gio.DBusError.get_remote_error(error) or "error", error.message)
            if self.at == at:
                # Not armed after all: the next change of plan tries again.
                self.at = previous if at is None else None

        if wait:
            try:
                connection.call_sync(WAKE_BUS, WAKE_PATH, WAKE_BUS, method, parameters, None,
                                     Gio.DBusCallFlags.NONE, 3000, None)
                finished(None)
            except GLib.Error as error:
                finished(error)
            return

        def done(source, result) -> None:
            try:
                source.call_finish(result)
            except GLib.Error as error:
                finished(error)
                return
            finished(None)

        connection.call(WAKE_BUS, WAKE_PATH, WAKE_BUS, method, parameters, None,
                        Gio.DBusCallFlags.NONE, 20_000, None, done)


class AlarmService:
    """Everything that makes an alarm ring with Clock's window closed."""

    def __init__(self, application: Gio.Application, *, store: ClockStore | None = None,
                 clock: Callable[[], float] = time.time, portal: BackgroundPortal | None = None,
                 ringer: Ringer | None = None, sandboxed: bool | None = None,
                 notifications=None, agent=None, inhibitor=None, wake=None) -> None:
        self.application = application
        # How notifications are shown: GNotification through the application,
        # or org.freedesktop.Notifications from the windowless agent.
        self.notifications = notifications or ApplicationNotifications(application)
        # The agent's AgentPublisher when this service is the agent itself.
        self.agent = agent
        self.store = store or ClockStore()
        self.state = SchedulerState(scheduler_path(self.store))
        self.clock = clock
        self.sandboxed = is_sandboxed() if sandboxed is None else sandboxed
        # On the host, logind and the session manager hold sleep and idle off and
        # luma-background's helper wakes the computer; in the sandbox,
        # GtkApplication's inhibit (the Inhibit portal) is all there is.
        self.inhibitor = inhibitor if inhibitor is not None else (None if self.sandboxed else SessionInhibitor())
        self.system_wake = wake if wake is not None else (None if self.sandboxed else SystemWake())
        self._managed: bool | None = None
        self._portal = portal
        self.ringer = ringer or Ringer()
        self.ringer.on_finished = self._ring_expired
        self.timer = WallClockTimer(self._wake)
        self.upcoming: list[Event] = []
        self.ringing: str | None = None  # notification id
        self._ringing_event: Event | None = None
        self._held = False
        self._inhibit = 0
        self._background_pending = False
        self._background_wanted: bool | None = None
        self._asked_this_run = False
        self.background = BACKGROUND_UNKNOWN
        self._listeners: list[Callable[[str], None]] = []
        self._agent_requested = False
        self._install_actions()

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        migrate_systemd_units()
        now = self.clock()
        since = self.state.checked_at
        if since is None:
            since = now - FIRST_RUN_LOOKBACK
        handed = self._take_handoff(now)
        missed = missed_events(self.store.alarms(), self.store.timers(), self.state.snoozes, since, now)
        seen = {(event.kind, event.uid) for event in missed}
        missed += [event for event in handed if (event.kind, event.uid) not in seen]
        self.state.checked_at = now
        self._process(sorted(missed, key=lambda event: event.at), now)
        self.reschedule()

    def shutdown(self) -> None:
        self.timer.cancel()
        self.ringer.stop()
        self._uninhibit()
        if getattr(self.store, "host_scheduled", False): self.store.close()
        if self.system_wake is not None and not self._deferring:
            # Nobody is left to ring it (the switch was turned off, or the session
            # ends): do not wake the computer for nothing.
            self.system_wake.set(None, wait=True)

    def _take_handoff(self, now: float) -> list[Event]:
        events = []
        for item in self.state.handoff:
            try:
                kind, uid, at = str(item["kind"]), valid_uid(item["uid"]), float(item["at"])
            except (KeyError, TypeError, ValueError):
                continue
            if kind == KIND_ALARM:
                alarm = self._alarm(uid)
                if alarm is not None and alarm.enabled:
                    # The unit fired at `at`, which is when it was due, snooze
                    # or not.
                    events.append(Event(KIND_ALARM, uid, at))
            elif kind == KIND_TIMER:
                record = self._timer(uid)
                if record is not None:
                    events.append(Event(KIND_TIMER, uid, float(record.fires_at)))
        if self.state.handoff:
            self.state.handoff = []
            self._save_state()
        return events

    # -- scheduling --------------------------------------------------------
    def reschedule(self) -> None:
        """Work the next event out again from the store. Call after any change."""
        now = self.clock()
        if getattr(self.store, "host_scheduled", False):
            self.store._read()
            self.state.snoozes = dict(self.store.host_status["snoozes"])
        if self._agent_elsewhere():
            # Clock's agent rings them and follows the store; this process only
            # keeps its own view current and holds nothing.
            self.upcoming = upcoming_events(self.store.alarms(), self.store.timers(), self.state.snoozes, now)
            self.timer.cancel()
            self._deferring = True
            self._update_hold()
            return
        self._deferring = False
        # Anything the previous plan had falling due by now goes first, so a
        # change made in the same second an alarm was due cannot swallow it.
        self._process([event for event in self.upcoming if event.at <= now], now)
        self.state.checked_at = now
        self._drop_stale_snoozes()
        self.upcoming = upcoming_events(self.store.alarms(), self.store.timers(), self.state.snoozes, now)
        self._save_state()
        if self.upcoming:
            due = self.upcoming[0].at
            # Look again shortly before it is due, to hold sleep off in time.
            self.timer.arm(due - AWAKE_BEFORE_SECONDS if due - AWAKE_BEFORE_SECONDS > now else due)
        else:
            self.timer.cancel()
        self._update_hold()
        self._keep_awake(now)
        self._arm_system_wake(now)
        if self.upcoming:
            self._request_agent()
        self._update_background(bool(self.upcoming))

    def _wake(self) -> None:
        self.reschedule()

    def _drop_stale_snoozes(self) -> None:
        alarms = {alarm.uid for alarm in self.store.alarms()}
        self.state.snoozes = {uid: at for uid, at in self.state.snoozes.items() if uid in alarms}

    def _process(self, events: list[Event], now: float) -> None:
        if events and self._agent_elsewhere():
            return  # the agent rings them
        for event in events:
            if event.kind == KIND_TIMER:
                record = self._timer(event.uid)
                if record is None or float(record.fires_at) != event.at:
                    continue
                self._finish_timer(record, late=not rings_now(event, now))
                continue
            alarm = self._alarm(event.uid)
            if event.kind == KIND_SNOOZE:
                if self.state.snoozes.pop(event.uid, None) is None or alarm is None:
                    continue
            elif alarm is None or not alarm.enabled:
                continue
            if rings_now(event, now):
                self._ring(alarm, event)
            else:
                self._missed(alarm, event)
            if event.kind == KIND_ALARM and not alarm.days:
                # A one-time alarm is spent the moment it goes off. It is turned
                # off rather than deleted, so it can be turned on again.
                self._save_alarm(replace(alarm, enabled=False))

    # -- ringing -----------------------------------------------------------
    def _ring(self, alarm: Alarm, event: Event) -> None:
        if self.ringing:
            self._stop_ringing(withdraw=False)
        identifier = f"alarm-{alarm.uid}"
        self.notifications.post(identifier, AlarmNotification(
            alarm.label or "Alarm", self._alarm_body(alarm, event), "urgent", "alarm.ringing",
            # Snooze off in the editor saves snooze_minutes = 0: then the alarm offers Stop alone.
            (((f"Snooze {alarm.snooze_minutes} min", "clock-snooze", alarm.uid),) if alarm.snooze_minutes > 0 else ())
            + (("Stop", "clock-stop", alarm.uid),),
            ("clock-open", "alarm")))
        self.ringing, self._ringing_event = identifier, event
        self._inhibit_sleep("An alarm is ringing")
        self.ringer.start(alarm.sound, max(5, alarm.ring_seconds))
        self._update_hold()
        LOG.info("Alarm %s ringing", alarm.uid)

    def _alarm_body(self, alarm: Alarm, event: Event) -> str:
        clock, period = format_clock(_local(event.at), hour24=uses_24_hour())
        when = f"{clock} {period}".strip()
        if event.kind == KIND_SNOOZE:
            return f"Snoozed alarm · {when}"
        late = self.clock() - event.at
        if late >= 90:
            return f"{when} · rang late, the device was asleep"
        return when

    def _missed(self, alarm: Alarm, event: Event) -> None:
        clock, period = format_clock(_local(event.at), hour24=uses_24_hour())
        self.notifications.post(f"alarm-{alarm.uid}", AlarmNotification(
            "Missed alarm", f"{alarm.label or 'Alarm'} · {clock} {period}".strip(), "high", "", (),
            ("clock-open", "alarm")))
        LOG.info("Alarm %s missed", alarm.uid)

    def _finish_timer(self, record: TimerRecord, *, late: bool) -> None:
        if late:
            clock, period = format_clock(_local(record.fires_at), hour24=uses_24_hour())
            body = f"Your timer finished at {clock} {period}".strip() + "."
        else:
            body = timer_sentence(record.total_seconds)
        self.notifications.post(f"timer-{record.uid}", AlarmNotification(
            "Timer done", body, "high", "", (), ("clock-open", "timer")))
        try:
            self.store.clear_timer(record.uid)
        except OSError as error:
            LOG.warning("Finished timer could not be cleared: %s", error)
        if not late and not self.ringing:
            if self.ringer.start("Chime", TIMER_CHIME_SECONDS, ramp=False):
                self._inhibit_sleep("A timer finished")
        LOG.info("Timer %s finished", record.uid)

    def _ring_expired(self) -> None:
        """Nobody answered. The sound stops; the notification says it was missed."""
        event, identifier = self._ringing_event, self.ringing
        self.ringing, self._ringing_event = None, None
        self._uninhibit()
        if identifier and event is not None:
            alarm = self._alarm(event.uid)
            if alarm is not None:
                self._missed(alarm, event)
        self._update_hold()

    def _stop_ringing(self, *, withdraw: bool = True) -> None:
        identifier = self.ringing
        self.ringing, self._ringing_event = None, None
        self.ringer.stop()
        self._uninhibit()
        if withdraw and identifier:
            self.notifications.withdraw(identifier)

    def stop(self, uid: str) -> None:
        if getattr(self.store, "host_scheduled", False):
            self.store.action("Stop", uid); self.reschedule(); return
        if self.ringing == f"alarm-{uid}":
            self._stop_ringing()
        else:
            self.notifications.withdraw(f"alarm-{uid}")
        if self.state.snoozes.pop(uid, None) is not None:
            self._save_state()
        self.reschedule()

    def snooze(self, uid: str) -> None:
        if getattr(self.store, "host_scheduled", False):
            self.store.action("Snooze", uid); self.reschedule(); return
        alarm = self._alarm(uid)
        if self.ringing == f"alarm-{uid}":
            self._stop_ringing()
        if alarm is None:
            return
        self.state.snoozes[uid] = snooze_until(alarm, _local(self.clock())).timestamp()
        self._save_state()
        self.reschedule()

    # -- actions -----------------------------------------------------------
    def _install_actions(self) -> None:
        for name, handler in (("clock-stop", self.stop), ("clock-snooze", self.snooze)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", lambda _action, parameter, handler=handler: self._act(handler, parameter))
            self.application.add_action(action)

    def _act(self, handler, parameter) -> None:
        try:
            uid = valid_uid(parameter.get_string())
        except (AttributeError, ValueError):
            return
        handler(uid)

    # -- keeping the process alive ----------------------------------------
    _deferring = False

    def _update_hold(self) -> None:
        wanted = (bool(self.upcoming) and not self._deferring) or self.ringing is not None or self.ringer.playing
        if wanted and not self._held:
            self.application.hold()
            self._held = True
        elif not wanted and self._held:
            self.application.release()
            self._held = False
        self._publish_agent()

    # -- the background agent (ADR-033) -------------------------------------
    def _agent_elsewhere(self) -> bool:
        """Whether Clock's agent runs in another process, and rings alarms there."""
        if self.agent is not None:
            return False
        if getattr(self.store, "host_scheduled", False):
            return True
        connection = self.application.get_dbus_connection()
        if connection is None:
            return False
        try:
            from .background_agent import name_has_owner
        except ImportError:
            return False
        return name_has_owner(connection, f"{APP_ID}.Agent")

    def _request_agent(self) -> None:
        """Something is scheduled: ask luma-background for Clock's agent, once per run."""
        if self.agent is not None or self._agent_requested or self.sandboxed:
            return
        connection = self.application.get_dbus_connection()
        if connection is None:
            return
        self._agent_requested = True
        try:
            from .background_agent import ensure_agent, service_present
        except ImportError:
            return
        if not service_present(connection):
            return

        def ask() -> None:
            if ensure_agent(APP_ID, f"{APP_ID}.Agent", reason=BACKGROUND_REASON):
                GLib.idle_add(lambda: (self.reschedule(), False)[1])
        import threading
        threading.Thread(target=ask, daemon=True, name="clock-agent-request").start()

    def _publish_agent(self) -> None:
        if self.agent is None:
            return
        upcoming = self.upcoming[0] if self.upcoming else None
        self.agent.publish("next-at", int(upcoming.at) if upcoming else 0)
        self.agent.publish("next-kind", "timer" if upcoming and upcoming.kind == KIND_TIMER else "alarm" if upcoming else "")
        self.agent.publish("scheduled", len(self.upcoming))
        self.agent.publish("ringing", self.ringing is not None)

    def _agent_wake(self, event: str) -> None:
        if event in {"resume", "unlock", "schedule", "network", "login", "request"}:
            self.reschedule()

    # -- awake for the alarm -------------------------------------------------
    def _keep_awake(self, now: float) -> None:
        """Hold sleep off from shortly before an alarm or timer until it is answered."""
        soon = bool(self.upcoming) and self.upcoming[0].at - now <= AWAKE_BEFORE_SECONDS
        if soon:
            self._inhibit_sleep("An alarm is about to go off")
        elif self.ringing is None and not self.ringer.playing:
            self._uninhibit()

    def _arm_system_wake(self, now: float) -> None:
        """Wake the computer WAKE_LEAD_SECONDS before the next alarm, snooze or timer."""
        if self.system_wake is None:
            return
        if not self.upcoming:
            self.system_wake.set(None)
            return
        at = self.upcoming[0].at - WAKE_LEAD_SECONDS
        if at > now + 10:
            self.system_wake.set(at)
        # Closer than that the sleep hold keeps the computer awake instead, and a
        # wake-up already armed for it is left to elapse.

    def _inhibit_sleep(self, reason: str) -> None:
        if self.inhibitor is not None:
            self.inhibitor.hold(reason)
            return
        if self._inhibit or not hasattr(self.application, "inhibit"):
            return
        try:
            from gi.repository import Gtk

            flags = Gtk.ApplicationInhibitFlags.SUSPEND | Gtk.ApplicationInhibitFlags.IDLE
            self._inhibit = self.application.inhibit(None, flags, reason)
        except (ImportError, TypeError, ValueError, GLib.Error):
            self._inhibit = 0

    def _uninhibit(self) -> None:
        if self.inhibitor is not None:
            self.inhibitor.release()
            return
        if self._inhibit:
            self.application.uninhibit(self._inhibit)
            self._inhibit = 0

    # -- background and autostart -----------------------------------------
    def connect_background(self, callback: Callable[[str], None]) -> None:
        self._listeners.append(callback)
        if self.background != BACKGROUND_UNKNOWN:
            callback(self.background)

    def disconnect_background(self, callback: Callable[[str], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _set_background(self, value: str) -> None:
        changed = value != self.background
        self.background = value
        if changed:
            for listener in list(self._listeners):
                listener(value)

    def _portal_connection(self) -> BackgroundPortal | None:
        if self._portal is None:
            connection = self.application.get_dbus_connection()
            if connection is None:
                return None
            self._portal = BackgroundPortal(connection)
        return self._portal

    def _background_managed(self) -> bool:
        """Whether luma-background runs Clock's agent, so its switch decides, not an autostart entry.

        Asked once per run. The answer also becomes what Clock shows about
        working in the background.
        """
        if self._managed is None:
            allowed = None
            connection = self.application.get_dbus_connection()
            if connection is not None:
                try:
                    from .background_agent import agent_allowed
                except ImportError:
                    agent_allowed = None
                if agent_allowed is not None:
                    allowed = agent_allowed(connection, APP_ID)
            self._managed = allowed is not None
            if allowed is not None:
                self._set_background(BACKGROUND_ALLOWED if allowed else BACKGROUND_DENIED)
        return self._managed

    def _update_background(self, wanted: bool) -> None:
        if self.agent is not None:
            return  # the agent is started by luma-background, not by an autostart entry
        if wanted and not self.sandboxed and self._background_managed():
            # luma-background starts Clock's agent at login. An autostart entry of
            # Clock's own would be a second start, which the service masks anyway;
            # an entry left from before it was installed is still removed below
            # once nothing is scheduled.
            return
        if self._background_pending:
            self._background_wanted = wanted
            return
        if not wanted and not self.state.autostart:
            return
        if wanted == self.state.autostart and self._asked_this_run:
            self._set_status()
            return
        portal = self._portal_connection()
        if portal is None:
            self._fallback_autostart(wanted, None)
            return
        self._background_pending = True
        self._asked_this_run = True
        portal.request(
            autostart=wanted, commandline=autostart_command(self.sandboxed), reason=BACKGROUND_REASON,
            callback=lambda code, results, error: self._background_answer(wanted, code, results, error),
        )

    def _background_answer(self, wanted: bool, code, results: dict, error) -> None:
        self._background_pending = False
        if code == 0:
            allowed = bool(results.get("background", False))
            self.state.autostart = bool(results.get("autostart", False))
            self._set_background(BACKGROUND_ALLOWED if allowed else BACKGROUND_DENIED)
            if wanted and allowed and not self.state.autostart:
                LOG.warning("Background allowed but autostart was not set up; alarms need Clock opened after login")
        else:
            message = error.message if error is not None else f"response {code}"
            LOG.warning("Background portal request failed: %s", message)
            self._fallback_autostart(wanted, code)
        self._save_state()
        self._set_status()
        pending, self._background_wanted = self._background_wanted, None
        if pending is not None and pending != wanted:
            self._update_background(pending)

    def _fallback_autostart(self, wanted: bool, code) -> None:
        if not self.sandboxed:
            try:
                self.state.autostart = write_autostart_entry(wanted, command=autostart_command(False))
                self._set_background(BACKGROUND_ALLOWED)
            except (OSError, GLib.Error) as error:
                LOG.warning("Could not write Clock's autostart entry: %s", error)
                self._set_background(BACKGROUND_UNAVAILABLE)
        else:
            self._set_background(BACKGROUND_DENIED if code == 1 else BACKGROUND_UNAVAILABLE)
        self._save_state()

    def _set_status(self) -> None:
        if not self.sandboxed or self._portal is None or not self.upcoming:
            return
        event = self.upcoming[0]
        clock, period = format_clock(_local(event.at), hour24=uses_24_hour())
        when = f"{clock} {period}".strip()
        day = _local(event.at).strftime("%a")
        noun = "Timer ends" if event.kind == KIND_TIMER else "Next alarm"
        self._portal.set_status(f"{noun} {day} {when}")

    # -- store access ------------------------------------------------------
    def _alarm(self, uid: str) -> Alarm | None:
        return next((alarm for alarm in self.store.alarms() if alarm.uid == uid), None)

    def _timer(self, uid: str) -> TimerRecord | None:
        return next((record for record in self.store.timers() if record.uid == uid), None)

    def _save_alarm(self, alarm: Alarm) -> None:
        try:
            self.store.save_alarm(alarm)
        except (OSError, ValueError) as error:
            LOG.warning("Alarm %s could not be turned off after ringing: %s", alarm.uid, error)

    def _save_state(self) -> None:
        try:
            self.state.save()
        except OSError as error:
            LOG.warning("Clock's scheduler state could not be saved: %s", error)


__all__ = [
    "APP_ID", "AWAKE_BEFORE_SECONDS", "AlarmNotification", "AlarmService", "ApplicationNotifications",
    "BackgroundPortal", "Event", "LATE_RING_LIMIT", "Ringer", "SessionInhibitor", "SystemWake", "WAKE_LEAD_SECONDS",
    "SchedulerState", "WallClockTimer", "autostart_command", "hand_off", "is_sandboxed",
    "legacy_units", "migrate_systemd_units", "missed_events", "register_host_application",
    "rings_now", "scheduler_path", "tone_file", "upcoming_events", "write_autostart_entry",
]
