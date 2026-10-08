# SPDX-License-Identifier: Apache-2.0
"""Closed identity probe. Run ONLY with dbus-run-session and Xvfb on the host.

dbus-run-session -- xvfb-run -a env LUMA_CALENDAR_PRIVATE_BUS=1 PYTHONPATH=<worktree paths> \
    python3 src/prairie-core/tests/calendar_preview_identity.py

The stand-in production service lives on that private bus. Neither application
activates or constructs a CalendarWindow; no real calendar is read or written.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import select
import subprocess
import sys

PRODUCTION = "org.projectluma.Calendar"
PREVIEW = PRODUCTION + ".LumaUIPreview"


def require_private_bus():
    if os.environ.get("LUMA_CALENDAR_PRIVATE_BUS") != "1":
        raise RuntimeError("Run inside dbus-run-session with LUMA_CALENDAR_PRIVATE_BUS=1")
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    live_bus = f"/run/user/{os.getuid()}/bus"
    if not address or live_bus in address:
        raise RuntimeError("The identity probe requires a separate private bus")


def require_test_display():
    """Never capture the desktop when xvfb-run was accidentally omitted."""
    match = re.fullmatch(r":(\d+)(?:\.\d+)?", os.environ.get("DISPLAY", ""))
    if not match:
        raise RuntimeError("Calendar captures require a local Xvfb display")
    pid = Path(f"/tmp/.X{match[1]}-lock").read_text().strip()
    if not pid.isdecimal() or Path(f"/proc/{pid}/comm").read_text().strip() != "Xvfb":
        raise RuntimeError("Calendar captures require an Xvfb-owned display")


def production_owner():
    require_private_bus()
    from gi.repository import Gio, GLib

    app = Gio.Application(application_id=PRODUCTION,
                          flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
    activations = []
    app.connect("activate", lambda *_: activations.append("activate"))
    app.connect("command-line", lambda *_: (activations.append("command-line"), 0)[1])
    assert app.register(None) and not app.get_is_remote()
    loop = GLib.MainLoop()

    def stop(*_):
        sys.stdin.readline()
        loop.quit()
        return False

    GLib.io_add_watch(0, GLib.IO_IN, stop)
    print("OWNER READY", flush=True)
    loop.run()
    print(json.dumps({"production_activations": activations}), flush=True)


def probe():
    require_private_bus()
    require_test_display()
    root = Path(__file__).resolve().parents[3]
    os.environ["LUMA_CALENDAR_FIXTURE"] = str(root / "tests/fixtures/calendar-v70.json")
    os.environ["LUMA_CALENDAR_STYLE_PATH"] = str(root / "src/prairie-core/style/calendar.css")
    from gi.repository import Gio, GLib
    from prairie_apps import calendar

    owner = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--owner"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    original = calendar.APP_ID
    try:
        assert select.select([owner.stdout], [], [], 15)[0], "Private production owner did not start"
        assert owner.stdout.readline().strip() == "OWNER READY", "Private production owner failed"
        assert original == PRODUCTION
        production = calendar.CalendarApplication()
        assert production.get_application_id() == PRODUCTION
        assert production.register(None) and production.get_is_remote(), "Stand-in owner was not recognized"

        # Exact override used by sync-lumaui-preview.sh's Prairie launcher.
        calendar.APP_ID = getattr(calendar, "APP_ID", "org.projectluma.calendar") + ".LumaUIPreview"
        preview = calendar.CalendarApplication()
        assert preview.get_application_id() == PREVIEW
        assert preview.register(None) and not preview.get_is_remote(), "Preview reached the production service"
        assert not preview.get_windows(), "Identity probe opened a window"
        bus = preview.get_dbus_connection()

        def name_owner(name):
            return bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                 "org.freedesktop.DBus", "GetNameOwner",
                                 GLib.Variant("(s)", (name,)), GLib.VariantType("(s)"),
                                 Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]

        production_name = name_owner(PRODUCTION)
        preview_name = name_owner(PREVIEW)
        assert production_name != preview_name
        preview.quit()
        assert not preview.get_windows()
        calendar.APP_ID = original
        assert calendar.CalendarApplication().get_application_id() == PRODUCTION
        stdout, stderr = owner.communicate("stop\n", timeout=15)
        assert owner.returncode == 0, stderr
        evidence = json.loads(stdout.strip())
        assert evidence["production_activations"] == [], evidence
        print(json.dumps({"result": "PASS", "production_id": PRODUCTION,
                          "preview_id": PREVIEW, "production_owner": production_name,
                          "preview_owner": preview_name, "production_activations": [],
                          "open_windows": 0, "production_id_preserved": True}))
    finally:
        calendar.APP_ID = original
        if owner.poll() is None:
            # Only this probe's direct child on the private bus.
            owner.communicate("stop\n", timeout=15)


if __name__ == "__main__":
    production_owner() if sys.argv[1:] == ["--owner"] else probe()
