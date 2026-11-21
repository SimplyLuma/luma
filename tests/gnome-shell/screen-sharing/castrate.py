#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""How many frames a window stream delivers, read the way an encoder reads it.

  castrate.py WINDOW_ID SECONDS OUT.json [CURSOR_MODE]

Records the window through org.gnome.Mutter.ScreenCast RecordWindow (what
luma-portal asks Mutter for), reads every buffer with pipewiresrc for SECONDS
and writes {"frames", "seconds", "fps"}. CURSOR_MODE: 0 hidden, 1 embedded,
2 metadata (default 1, as a browser asks).
"""

import json
import subprocess
import sys

from gi.repository import Gio, GLib

window_id, seconds, out = int(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
cursor_mode = int(sys.argv[4]) if len(sys.argv) > 4 else 1
NAME = "org.gnome.Mutter.ScreenCast"

bus = Gio.bus_get_sync(Gio.BusType.SESSION)
loop = GLib.MainLoop()
node = [None]


def call(path, iface, method, params):
    return bus.call_sync(NAME, path, iface, method, params, None,
                         Gio.DBusCallFlags.NONE, -1, None).unpack()


session = call("/org/gnome/Mutter/ScreenCast", "org.gnome.Mutter.ScreenCast",
               "CreateSession", GLib.Variant("(a{sv})", ({},)))[0]
stream = call(session, "org.gnome.Mutter.ScreenCast.Session", "RecordWindow",
              GLib.Variant("(a{sv})", ({"window-id": GLib.Variant("t", window_id),
                                        "cursor-mode": GLib.Variant("u", cursor_mode)},)))[0]


def added(_c, _s, _p, _i, _sig, params):
    node[0] = params.unpack()[0]
    loop.quit()


bus.signal_subscribe(NAME, "org.gnome.Mutter.ScreenCast.Stream", "PipeWireStreamAdded",
                     stream, None, Gio.DBusSignalFlags.NONE, added)
call(session, "org.gnome.Mutter.ScreenCast.Session", "Start", None)
GLib.timeout_add_seconds(10, loop.quit)
loop.run()

def serial(node_id):
    """The node's object.serial, which pipewiresrc's target-object takes."""
    for _ in range(20):
        dump = subprocess.run(["pw-dump"], capture_output=True, text=True).stdout
        for obj in json.loads(dump or "[]"):
            if obj.get("id") == node_id:
                return obj.get("info", {}).get("props", {}).get("object.serial")
        GLib.usleep(250000)
    return None


result = {"frames": 0, "seconds": seconds, "node": node[0]}
if node[0] is not None:
    result["serial"] = serial(node[0])
    run = subprocess.run(
        ["timeout", "-s", "INT", str(seconds), "gst-launch-1.0", "-e", "-v",
         "pipewiresrc", f"target-object={result['serial'] or node[0]}", "always-copy=true", "!",
         "fakesink", "silent=false", "sync=false"],
        capture_output=True, text=True)
    result["frames"] = sum(1 for line in run.stdout.splitlines() if "chain" in line)
    result["error"] = run.stderr[-300:] if result["frames"] == 0 else None
result["fps"] = round(result["frames"] / seconds, 1)
call(session, "org.gnome.Mutter.ScreenCast.Session", "Stop", None)
with open(out, "w") as f:
    json.dump(result, f)
