#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""A host application asking to share the screen, the way a browser does.

It speaks to the real org.freedesktop.portal.ScreenCast frontend, so the
request goes xdg-desktop-portal -> luma-portal -> the Shell's picker -> Mutter,
exactly as an application's would. It records what came back and, once
streaming, pulls frames from the PipeWire node through the remote the portal
opened for it -- which is what the application actually receives, so it is
what shows whether the violet edge is in the stream.

  client.py TAG TYPES [--grab SECONDS...] [--direct APP_ID]

TYPES is the portal's bitmask (1 monitor, 2 window, 3 both). --direct calls
the backend with a chosen app id instead of going through the frontend, to
show a resolved requester; a host caller has no app id to resolve.
Writes $ORACLE_OUT/client-TAG.json and stream-TAG-N.png.
"""

import json
import os
import subprocess
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

OUT = os.environ.get("ORACLE_OUT", "/oracle/out")
PORTAL = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"
REQUEST = "org.freedesktop.portal.Request"

tag = sys.argv[1]
types = int(sys.argv[2])
grabs = []
direct = None
args = sys.argv[3:]
while args:
    flag = args.pop(0)
    if flag == "--grab":
        grabs.append(float(args.pop(0)))
    elif flag == "--direct":
        direct = args.pop(0)

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
loop = GLib.MainLoop()
record = {"tag": tag, "types": types, "direct": direct, "steps": []}
sender = bus.get_unique_name()[1:].replace(".", "_")
counter = 0


def save():
    with open(f"{OUT}/client-{tag}.json", "w") as f:
        json.dump(record, f, indent=1, default=str)


def step(name, **data):
    record["steps"].append({"step": name, "t": GLib.get_monotonic_time() / 1e6, **data})
    print(f"[client {tag}] {name} {data}", flush=True)
    save()


# Portal tokens and object path elements allow only [A-Za-z0-9_].
safe = "".join(c if c.isalnum() else "_" for c in tag)


def token():
    global counter
    counter += 1
    return f"luma{safe}{counter}"


def request(method, params_before, options, then):
    """Call a portal method and wait for its Request::Response."""
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
    bus.call_sync(PORTAL, PATH, SCREENCAST, method,
                  GLib.Variant(f"({sig})", values), None,
                  Gio.DBusCallFlags.NONE, -1, None)


def grab(node, index, delay):
    def later():
        # The remote the portal opened for this session: the application's
        # own view of PipeWire, and nothing else on it.
        reply, fds = bus.call_with_unix_fd_list_sync(
            PORTAL, PATH, SCREENCAST, "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (session_handle[0], {})),
            GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, -1, None, None)
        fd = fds.get(reply.unpack()[0])
        target = f"{OUT}/stream-{tag}-{index}.png"
        result = subprocess.run(
            ["gst-launch-1.0", "-q", "pipewiresrc", f"fd={fd}", f"path={node}",
             "num-buffers=4", "!", "videoconvert", "!", "pngenc", "snapshot=false",
             "!", "multifilesink", f"location={target}.%d"],
            pass_fds=(fd,), capture_output=True, text=True, timeout=30)
        os.close(fd)
        frames = sorted(p for p in os.listdir(OUT) if p.startswith(f"stream-{tag}-{index}.png."))
        if frames:
            os.replace(f"{OUT}/{frames[-1]}", target)
            for extra in frames[:-1]:
                os.remove(f"{OUT}/{extra}")
        step("grabbed", index=index, file=target if frames else None,
             rc=result.returncode, err=result.stderr[-400:])
        return GLib.SOURCE_REMOVE
    GLib.timeout_add(int(delay * 1000), later)


session_handle = [None]


def on_closed(*_a):
    step("session-closed")
    GLib.timeout_add(500, lambda: loop.quit())


def start():
    def started(response, results):
        streams = results.get("streams", []) if response == 0 else []
        step("start", response=response,
             streams=[{"node": n, "props": {k: str(v) for k, v in p.items()}} for n, p in streams])
        if response != 0:
            loop.quit()
            return
        for i, delay in enumerate(grabs):
            grab(streams[0][0], i, delay)
    request("Start", [("o", session_handle[0]), ("s", "")], {}, started)


def select():
    def selected(response, _results):
        step("select-sources", response=response)
        if response != 0:
            loop.quit()
            return
        start()
    request("SelectSources", [("o", session_handle[0])], {
        "types": GLib.Variant("u", types),
        "multiple": GLib.Variant("b", False),
        "cursor_mode": GLib.Variant("u", 2),
    }, selected)


def create():
    def created(response, results):
        step("create-session", response=response)
        if response != 0:
            loop.quit()
            return
        session_handle[0] = results["session_handle"]
        bus.signal_subscribe(PORTAL, "org.freedesktop.portal.Session", "Closed",
                             session_handle[0], None, Gio.DBusSignalFlags.NONE,
                             on_closed, None)
        select()
    request("CreateSession", [], {"session_handle_token": GLib.Variant("s", token())},
            created)


def direct_call():
    """The backend directly, with an app id, to show a resolved requester."""
    impl = "org.freedesktop.impl.portal.desktop.luma"
    iface = "org.freedesktop.impl.portal.ScreenCast"
    handle = f"/org/freedesktop/portal/desktop/session/luma/{safe}"
    bus.call_sync(impl, PATH, iface, "CreateSession",
                  GLib.Variant("(oosa{sv})",
                               ("/r/1", handle, direct, {})),
                  None, Gio.DBusCallFlags.NONE, -1, None)
    bus.call_sync(impl, PATH, iface, "SelectSources",
                  GLib.Variant("(oosa{sv})", ("/r/2", handle, direct,
                                              {"types": GLib.Variant("u", types)})),
                  None, Gio.DBusCallFlags.NONE, -1, None)

    def done(source, result):
        try:
            response, results = source.call_finish(result).unpack()
            step("start", response=response, keys=list(results.keys()))
        except GLib.Error as error:
            step("start-error", error=error.message)
        loop.quit()
    bus.call(impl, PATH, iface, "Start",
             GLib.Variant("(oossa{sv})", ("/r/3", handle, direct, "", {})),
             None, Gio.DBusCallFlags.NONE, -1, None, done)


step("begin")
if direct:
    direct_call()
else:
    create()
GLib.timeout_add_seconds(int(os.environ.get("CLIENT_TIMEOUT", "90")), lambda: loop.quit())
loop.run()
step("end")
