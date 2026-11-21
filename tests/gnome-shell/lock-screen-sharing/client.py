#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""A host application sharing one window through the real portal, with frames.

  client.py TAG [--restore TOKEN]

It asks org.freedesktop.portal.ScreenCast for a window (persist_mode 2, so
the portal hands back a restore token), and once streaming pulls every frame
from its PipeWire node -- what the application receives. Each frame's arrival time (CLOCK_MONOTONIC,
the Shell's clock) goes to $LOCK_OUT/frames-TAG.txt; every step, the token
and a session Closed go to $LOCK_OUT/client-TAG.json. It runs until
$LOCK_OUT/stop exists.
"""

import json
import os
import subprocess
import sys
import threading

import gi

gi.require_version("Gio", "2.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib, Gst  # noqa: E402

OUT = os.environ.get("LOCK_OUT", "/tmp")
PORTAL = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"
REQUEST = "org.freedesktop.portal.Request"

tag = sys.argv[1]
def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


restore = arg("--restore")
types = int(arg("--types", "2"))
snapshot = int(arg("--snapshot", "0"))

Gst.init(None)
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
loop = GLib.MainLoop()
record = {"tag": tag, "restore": restore, "steps": []}
sender = bus.get_unique_name()[1:].replace(".", "_")
safe = "".join(c if c.isalnum() else "_" for c in tag)
counter = [0]
session_handle = [None]
frames = open(f"{OUT}/frames-{tag}.txt", "a", buffering=1)
pipeline = [None]


def step(name, **data):
    record["steps"].append({"step": name, "t": GLib.get_monotonic_time(), **data})
    print(f"[client {tag}] {name} {data}", flush=True)
    with open(f"{OUT}/client-{tag}.json", "w") as f:
        json.dump(record, f, indent=1, default=str)


def token():
    counter[0] += 1
    return f"lock{safe}{counter[0]}"


def request(method, params_before, options, then):
    handle_token = token()
    options["handle_token"] = GLib.Variant("s", handle_token)
    path = f"/org/freedesktop/portal/desktop/request/{sender}/{handle_token}"

    def on_response(_c, _s, _p, _i, _sig, parameters, _d):
        bus.signal_unsubscribe(sub[0])
        response, results = parameters.unpack()
        then(response, results)

    sub = [bus.signal_subscribe(PORTAL, REQUEST, "Response", path, None,
                                Gio.DBusSignalFlags.NONE, on_response, None)]
    sig = "".join(v[0] for v in params_before) + "a{sv}"
    values = tuple(v[1] for v in params_before) + (options,)
    bus.call_sync(PORTAL, PATH, SCREENCAST, method, GLib.Variant(f"({sig})", values), None,
                  Gio.DBusCallFlags.NONE, -1, None)


count = [0]


def save_png(sample):
    """The frame as the application has it, for measure checks."""
    from PIL import Image
    caps = sample.get_caps().get_structure(0)
    width, height = caps.get_value("width"), caps.get_value("height")
    fmt = caps.get_value("format")
    buffer = sample.get_buffer()
    ok, info = buffer.map(Gst.MapFlags.READ)
    if not ok:
        return
    stride = len(info.data) // height
    mode = {"BGRx": "BGRX", "BGRA": "BGRA", "RGBx": "RGBX", "RGBA": "RGBA", "xRGB": "XRGB"}.get(fmt, "BGRX")
    Image.frombuffer("RGBA" if "A" in mode else "RGB", (width, height), bytes(info.data), "raw",
                     mode, stride, 1).convert("RGB").save(f"{OUT}/stream-{tag}.png")
    buffer.unmap(info)
    step("snapshot", width=width, height=height, format=fmt)


def on_sample(sink):
    sample = sink.emit("pull-sample")
    if sample is not None:
        receiving[0] = True
        frames.write(f"{GLib.get_monotonic_time()}\n")
        count[0] += 1
        if snapshot and count[0] == snapshot:
            save_png(sample)
    return Gst.FlowReturn.OK


target = [None]
tries = [0]
receiving = [False]


def on_bus_message(_bus, message):
    if message.type == Gst.MessageType.WARNING:
        print(f"[client {tag}] gst warning {message.parse_warning()[0].message}", flush=True)
    if message.type == Gst.MessageType.STATE_CHANGED and message.src == pipeline[0]:
        print(f"[client {tag}] state {message.parse_state_changed()[1].value_nick}", flush=True)
    if message.type in (Gst.MessageType.ERROR, Gst.MessageType.EOS):
        detail = message.parse_error()[0].message if message.type == Gst.MessageType.ERROR else "eos"
        # A node the portal has just announced may not be on the remote yet:
        # connecting is retried until the first frame, never after it.
        if not receiving[0] and tries[0] < 20:
            tries[0] += 1
            print(f"[client {tag}] retry {tries[0]}: {detail}", flush=True)
            pipeline[0].set_state(Gst.State.NULL)
            GLib.timeout_add(500, lambda: (consume(target[0]), GLib.SOURCE_REMOVE)[1])
            return True
        step("stream-ended", detail=detail)
    return True


def link(node):
    """Link Mutter's node to ours. The session manager in a bare container
    session does not link video streams, so the client links its own."""
    def done(proc, result):
        ok, out, err = proc.communicate_utf8_finish(result)
        if proc.get_exit_status() == 0:
            step("linked", node=node)
        elif tries[0] < 40:
            tries[0] += 1
            GLib.timeout_add(250, lambda: (link(node), GLib.SOURCE_REMOVE)[1])
        else:
            step("stream-ended", detail=(err or out or "").strip())
    proc = Gio.Subprocess.new(["pw-link", str(node), f"lock-consumer-{safe}"],
                              Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
    proc.communicate_utf8_async(None, None, done)


def consume(node):
    target[0] = node
    p = Gst.parse_launch(
        "pipewiresrc autoconnect=false always-copy=true "
        f"stream-properties=props,node.name=lock-consumer-{safe} ! "
        "appsink name=sink emit-signals=true sync=false max-buffers=2 drop=true")
    p.get_by_name("sink").connect("new-sample", on_sample)
    watch = p.get_bus()
    watch.add_signal_watch()
    watch.connect("message", on_bus_message)
    pipeline[0] = p
    print(f"[client {tag}] consuming node {node}", flush=True)
    # pipewiresrc blocks going to PLAYING until its stream is linked, and
    # linking needs the main loop: start it from a thread.
    threading.Thread(target=lambda: p.set_state(Gst.State.PLAYING), daemon=True).start()
    GLib.timeout_add(300, lambda: (link(node), GLib.SOURCE_REMOVE)[1])


def start():
    def started(response, results):
        streams = results.get("streams", []) if response == 0 else []
        step("start", response=response, nodes=[n for n, _p in streams],
             token=results.get("restore_token"))
        if response != 0 or not streams:
            return
        consume(streams[0][0])
    request("Start", [("o", session_handle[0]), ("s", "")], {}, started)


def select():
    options = {
        "types": GLib.Variant("u", types),
        "multiple": GLib.Variant("b", False),
        "cursor_mode": GLib.Variant("u", 1),
        "persist_mode": GLib.Variant("u", 2),
    }
    if restore:
        options["restore_token"] = GLib.Variant("s", restore)

    def selected(response, _results):
        step("select-sources", response=response)
        if response == 0:
            start()
    request("SelectSources", [("o", session_handle[0])], options, selected)


def create():
    def created(response, results):
        step("create-session", response=response)
        if response != 0:
            return
        session_handle[0] = results["session_handle"]
        bus.signal_subscribe(PORTAL, "org.freedesktop.portal.Session", "Closed",
                             session_handle[0], None, Gio.DBusSignalFlags.NONE,
                             lambda *_a: step("session-closed"), None)
        select()
    request("CreateSession", [], {"session_handle_token": GLib.Variant("s", token())}, created)


def poll_stop():
    if os.path.exists(f"{OUT}/stop"):
        loop.quit()
        return GLib.SOURCE_REMOVE
    return GLib.SOURCE_CONTINUE


step("begin")
create()
GLib.timeout_add(200, poll_stop)
loop.run()
if pipeline[0] is not None:
    pipeline[0].set_state(Gst.State.NULL)
step("end")
