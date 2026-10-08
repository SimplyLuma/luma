#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""An alarm and a timer ring with Clock's window closed, and after a restart.

Runs inside an existing D-Bus session with an X display and a window manager
(xdotool closes the window the way a person does, with Ctrl+W). It starts the
real xdg-desktop-portal against portal_test_backend.py, then:

 1. seeds a timer due in about 20 seconds and a one-time alarm for a minute
    boundary at least 75 seconds away;
 2. opens Clock, checks the window is up, closes it, and checks the process
    stays alive holding its bus name;
 3. waits for the timer's notification (xdg-desktop-portal validated it on the
    way to the backend) and checks the autostart entry the Background portal
    wrote;
 4. stops Clock, as a logout would, and starts it from that autostart entry's
    command line: no window appears and the name is owned again;
 5. waits for the alarm's notification (urgent, category alarm.ringing, with
    Snooze and Stop), presses Stop through the backend, and checks the
    notification is withdrawn, the alarm turned off, the process exits with
    nothing left to ring, and the autostart entry is gone.

Two ways to launch, the same steps:

  --launch flatpak --app-id org.projectluma.Clock   (sandboxed: Notification
      and Background portals; data in ~/.var/app/<id>/data)
  --launch native   (prairie-clock on PATH: GNotification goes to
      org.gtk.Notifications, which the backend also provides the way GNOME
      Shell does, and actions come back through org.freedesktop.Application)

  --launch native --handoff   (the move off the systemd units: an alarm
      scheduled by an older Clock has its prairie-alarm-<uid> unit files on
      disk and no Clock running; at its minute the unit's command,
      `prairie-clock-alarm alarm <uid>`, runs; Clock must start by D-Bus
      activation, withdraw the unit files, ring and answer Stop. Needs
      org.projectluma.Clock.service on the session bus's service path.)

Evidence (portal calls, app output, autostart entry, summary) goes to --evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_ID = "org.projectluma.Clock"


class Failure(AssertionError):
    pass


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def wait_for(what: str, predicate, timeout: float, step: float = 0.5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    raise Failure(f"timed out after {timeout:.0f}s waiting for {what}")


def gdbus(*arguments: str, check: bool = True) -> str:
    result = subprocess.run(["gdbus", "call", "--session", *arguments], capture_output=True, text=True, timeout=30)
    if check and result.returncode != 0:
        raise Failure(f"gdbus {' '.join(arguments)}: {result.stderr.strip()}")
    return result.stdout.strip()


def name_owned(name: str) -> bool:
    out = gdbus("--dest", "org.freedesktop.DBus", "--object-path", "/org/freedesktop/DBus",
                "--method", "org.freedesktop.DBus.NameHasOwner", name, check=False)
    return out.startswith("(true")


def clock_windows() -> list[str]:
    result = subprocess.run(["xdotool", "search", "--onlyvisible", "--name", "^Clock$"],
                            capture_output=True, text=True, timeout=10)
    return result.stdout.split()


def calls(log_path: Path) -> list[dict]:
    try:
        return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    except OSError:
        return []


def find_call(log_path: Path, call: str, identifier: str) -> dict | None:
    return next((entry for entry in calls(log_path) if entry.get("call") == call and entry.get("id") == identifier), None)


class Session:
    def __init__(self, args) -> None:
        self.args = args
        self.evidence = Path(args.evidence)
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.portal_log = self.evidence / "portal-calls.jsonl"
        self.portal_log.unlink(missing_ok=True)
        self.processes: list[subprocess.Popen] = []
        home = Path.home()
        self.config_home = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
        if args.launch == "flatpak":
            self.data_dir = home / ".var/app" / args.app_id / "data/prairie/clock"
        else:
            self.data_dir = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share") / "prairie/clock"
        self.autostart = self.config_home / "autostart" / f"{args.app_id}.desktop"
        self.summary: list[str] = []

    # -- infrastructure ----------------------------------------------------
    def spawn(self, name: str, command: list[str], env: dict | None = None) -> subprocess.Popen:
        output = open(self.evidence / f"{name}.log", "ab")
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                   env={**os.environ, **(env or {})}, start_new_session=True)
        self.processes.append(process)
        return process

    def start_portals(self) -> None:
        portal_dir = self.evidence / "portals"
        portal_dir.mkdir(exist_ok=True)
        (portal_dir / "lumatest.portal").write_text(
            "[portal]\nDBusName=org.freedesktop.impl.portal.desktop.lumatest\n"
            "Interfaces=org.freedesktop.impl.portal.Access;org.freedesktop.impl.portal.Background;"
            "org.freedesktop.impl.portal.Notification;\n"
        )
        # With XDG_DESKTOP_PORTAL_DIR set, xdg-desktop-portal reads its
        # configuration from that directory and nowhere else.
        (portal_dir / "portals.conf").write_text("[preferred]\ndefault=lumatest\n")
        env = {"PORTAL_TEST_LOG": str(self.portal_log)}
        if self.args.launch == "native":
            env["PORTAL_TEST_GTK_NOTIFICATIONS"] = "1"
        self.spawn("portal-backend", [sys.executable, str(HERE / "portal_test_backend.py")], env)
        wait_for("the test portal backend", lambda: name_owned("org.freedesktop.impl.portal.desktop.lumatest"), 20)
        self.spawn("xdg-desktop-portal", [self.args.portal, "--replace", "--verbose"],
                   {"XDG_DESKTOP_PORTAL_DIR": str(portal_dir)})
        wait_for("xdg-desktop-portal", lambda: name_owned("org.freedesktop.portal.Desktop"), 30)
        version = gdbus("--dest", "org.freedesktop.portal.Desktop", "--object-path", "/org/freedesktop/portal/desktop",
                        "--method", "org.freedesktop.DBus.Properties.Get", "org.freedesktop.portal.Background", "version")
        self.note(f"xdg-desktop-portal is up; Background portal version {version}")

    def note(self, line: str) -> None:
        log(line)
        self.summary.append(line)

    # -- the app -----------------------------------------------------------
    def launch_command(self, extra: list[str] | None = None) -> list[str]:
        if self.args.launch == "flatpak":
            return ["flatpak", "run", "--user", self.args.app_id, *(extra or [])]
        return ["prairie-clock", *(extra or [])]

    def seed(self) -> tuple[str, str, datetime, int]:
        now = datetime.now().astimezone()
        ring = (now + timedelta(seconds=75)).replace(second=0, microsecond=0) + timedelta(minutes=1)
        timer_at = int(time.time() + 20)
        alarm_uid, timer_uid = uuid.uuid4().hex, uuid.uuid4().hex
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        state = {
            "world": [{"uid": uuid.uuid4().hex, "label": "Tokyo", "zone": "Asia/Tokyo"}],
            "alarms": [{"uid": alarm_uid, "label": "Session test", "hour": ring.hour, "minute": ring.minute,
                        "days": [], "enabled": True, "sound": "Chime", "snooze_minutes": 9, "ring_seconds": 120}],
            "timers": [{"uid": timer_uid, "label": "Timer", "fires_at": timer_at, "total_seconds": 20}],
        }
        (self.data_dir / "state.json").write_text(json.dumps(state))
        (self.data_dir / "scheduler.json").unlink(missing_ok=True)
        self.note(f"seeded a timer for {datetime.fromtimestamp(timer_at):%H:%M:%S} and a one-time alarm for {ring:%H:%M}")
        return alarm_uid, timer_uid, ring, timer_at

    def close_window(self) -> None:
        window = wait_for("the Clock window", clock_windows, 60)[0]
        self.note(f"Clock window {window} mapped")
        time.sleep(2)
        activated = subprocess.run(["xdotool", "windowactivate", "--sync", window], capture_output=True, timeout=15)
        if activated.returncode != 0:
            subprocess.run(["xdotool", "windowfocus", "--sync", window], capture_output=True, timeout=15)
        subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+w"], check=True, timeout=15)
        wait_for("the Clock window to close", lambda: not clock_windows(), 20)
        self.note("window closed with Ctrl+W")

    def stop_app(self, process: subprocess.Popen | None) -> None:
        if self.args.launch == "flatpak":
            subprocess.run(["flatpak", "kill", self.args.app_id], capture_output=True, timeout=20)
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
        wait_for("Clock to stop", lambda: not name_owned(self.args.app_id), 20)

    # -- the test ----------------------------------------------------------
    def run(self) -> None:
        self.start_portals()
        alarm_uid, timer_uid, ring, timer_at = self.seed()

        first = self.spawn("clock-window", self.launch_command())
        wait_for("Clock's bus name", lambda: name_owned(self.args.app_id), 60)
        self.close_window()
        time.sleep(3)
        if not name_owned(self.args.app_id):
            raise Failure("Clock exited when its window closed, with a timer and an alarm scheduled")
        self.note("Clock still owns its bus name with no window: the alarm service is holding it")

        timer = wait_for("the timer notification", lambda: find_call(self.portal_log, "AddNotification", f"timer-{timer_uid}"),
                         max(30, timer_at - time.time() + 25))
        late = timer["time"] - timer_at
        self.note(f"timer notification arrived {late:+.1f}s from its deadline with the window closed: "
                  f"{timer['notification'].get('title')!r} / {timer['notification'].get('body')!r}")
        if late < -1 or late > 5:
            raise Failure(f"timer notification was {late:+.1f}s off its deadline")
        state = json.loads((self.data_dir / "state.json").read_text())
        if state["timers"]:
            raise Failure("the finished timer is still on record")

        entry = wait_for("the autostart entry", lambda: self.autostart.is_file() and self.autostart.read_text(), 20)
        (self.evidence / "autostart.desktop").write_text(entry)
        exec_line = next(line.split("=", 1)[1] for line in entry.splitlines() if line.startswith("Exec="))
        if "--gapplication-service" not in exec_line:
            raise Failure(f"autostart entry does not start the service: {exec_line}")
        self.note(f"Background portal wrote {self.autostart.name}: Exec={exec_line}")

        self.stop_app(first)
        self.note("Clock stopped, as at logout")
        login = self.spawn("clock-autostart", shlex.split(exec_line))
        wait_for("Clock started from its autostart entry", lambda: name_owned(self.args.app_id), 60)
        time.sleep(5)
        if clock_windows():
            raise Failure("the autostarted service opened a window")
        self.note("started from the autostart command line: bus name owned, no window")

        alarm = wait_for("the alarm notification",
                         lambda: find_call(self.portal_log, "AddNotification", f"alarm-{alarm_uid}"),
                         max(30, ring.timestamp() - time.time() + 30))
        late = alarm["time"] - ring.timestamp()
        body = alarm["notification"]
        self.note(f"alarm notification arrived {late:+.1f}s from {ring:%H:%M}: title={body.get('title')!r} "
                  f"priority={body.get('priority')!r} category={body.get('category')!r} "
                  f"buttons={[b.get('label') for b in body.get('buttons', [])]}")
        if late < -1 or late > 5:
            raise Failure(f"alarm notification was {late:+.1f}s off")
        if body.get("priority") != "urgent":
            raise Failure("the alarm notification is not urgent")
        actions = {button.get("action") for button in body.get("buttons", [])}
        if not {"app.clock-stop", "app.clock-snooze"} <= actions:
            raise Failure(f"the alarm notification lacks Stop and Snooze: {actions}")

        time.sleep(3)
        gdbus("--dest", "org.freedesktop.impl.portal.desktop.lumatest", "--object-path", "/org/projectluma/PortalTest",
              "--method", "org.projectluma.PortalTest.InvokeAction", self.args.app_id, f"alarm-{alarm_uid}",
              "app.clock-stop", f"[<'{alarm_uid}'>]")
        wait_for("the alarm notification to be withdrawn",
                 lambda: find_call(self.portal_log, "RemoveNotification", f"alarm-{alarm_uid}"), 20)
        self.note("pressed Stop: notification withdrawn")
        state = json.loads((self.data_dir / "state.json").read_text())
        if state["alarms"][0]["enabled"]:
            raise Failure("the one-time alarm is still on after ringing")
        wait_for("Clock to exit with nothing left to ring", lambda: not name_owned(self.args.app_id), 30)
        self.note("the one-time alarm is off, and Clock exited with nothing left to ring")
        wait_for("the autostart entry to be removed", lambda: not self.autostart.exists(), 20)
        self.note("the Background portal removed the autostart entry")
        if login.poll() is None:
            login.wait(timeout=20)

    def run_handoff(self) -> None:
        self.start_portals()
        now = datetime.now().astimezone()
        ring = (now + timedelta(seconds=25)).replace(second=0, microsecond=0) + timedelta(minutes=1)
        uid = uuid.uuid4().hex
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.data_dir / "state.json").write_text(json.dumps({"world": [], "timers": [], "alarms": [
            {"uid": uid, "label": "Old alarm", "hour": ring.hour, "minute": ring.minute, "days": [0, 1, 2, 3, 4, 5, 6],
             "enabled": True, "sound": "Chime", "snooze_minutes": 9, "ring_seconds": 120}]}))
        units = self.config_home / "systemd/user"
        units.mkdir(parents=True, exist_ok=True)
        for suffix in (".timer", ".service"):
            (units / f"prairie-alarm-{uid}{suffix}").write_text("[Unit]\nDescription=Project Luma alarm\n")
        self.note(f"an older Clock's alarm for {ring:%H:%M}: record plus prairie-alarm-{uid[:8]}… unit files, Clock not running")
        if name_owned(self.args.app_id):
            raise Failure("Clock is already running")
        time.sleep(max(0.0, ring.timestamp() - time.time()))
        started = time.time()
        result = subprocess.run(["prairie-clock-alarm", "alarm", uid], capture_output=True, text=True, timeout=60)
        (self.evidence / "prairie-clock-alarm.log").write_text(result.stdout + result.stderr)
        if result.returncode != 0:
            raise Failure(f"prairie-clock-alarm exited {result.returncode}: {result.stderr.strip()[-300:]}")
        self.note(f"the unit's command ran at {datetime.now():%H:%M:%S} and returned in {time.time() - started:.1f}s")
        alarm = wait_for("the handed-over alarm's notification",
                         lambda: find_call(self.portal_log, "AddNotification", f"alarm-{uid}"), 30)
        self.note(f"Clock started by D-Bus activation and rang {alarm['time'] - ring.timestamp():+.1f}s from {ring:%H:%M}")
        left = sorted(path.name for path in units.glob("prairie-*"))
        if left:
            raise Failure(f"old unit files were not withdrawn: {left}")
        self.note("the old unit files are gone; the alarm record is kept")
        time.sleep(2)
        gdbus("--dest", "org.freedesktop.impl.portal.desktop.lumatest", "--object-path", "/org/projectluma/PortalTest",
              "--method", "org.projectluma.PortalTest.InvokeAction", self.args.app_id, f"alarm-{uid}",
              "app.clock-stop", f"[<'{uid}'>]")
        wait_for("the notification to be withdrawn", lambda: find_call(self.portal_log, "RemoveNotification", f"alarm-{uid}"), 20)
        state = json.loads((self.data_dir / "state.json").read_text())
        if not state["alarms"] or not state["alarms"][0]["enabled"]:
            raise Failure("the repeating alarm was removed or turned off")
        if not name_owned(self.args.app_id):
            raise Failure("Clock exited with a repeating alarm still set")
        self.note("pressed Stop: withdrawn; the repeating alarm stays on and Clock keeps running for its next time")

    def cleanup(self) -> None:
        for process in reversed(self.processes):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=10)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    pass
        if self.args.launch == "flatpak":
            subprocess.run(["flatpak", "kill", self.args.app_id], capture_output=True, timeout=20)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--launch", choices=("native", "flatpak"), required=True)
    parser.add_argument("--app-id", default=APP_ID)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--portal", default="/usr/libexec/xdg-desktop-portal")
    parser.add_argument("--handoff", action="store_true")
    args = parser.parse_args()
    if args.handoff and args.launch != "native":
        parser.error("--handoff is the operating system's migration; use it with --launch native")
    session = Session(args)
    try:
        session.run_handoff() if args.handoff else session.run()
        verdict = "PASS"
    except Failure as error:
        session.note(f"FAIL: {error}")
        verdict = "FAIL"
    finally:
        session.cleanup()
        (session.evidence / "summary.txt").write_text("\n".join(session.summary) + "\n")
    print(f"clock-alarm-session ({args.launch}{', handoff' if args.handoff else ''}): {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
