#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mock org.projectluma.Cast1 for Shell UI tests.

Implements the published contract (src/luma-cast/data/dbus/org.projectluma.Cast1.xml)
with canned devices, plus a test-only org.projectluma.Cast1.Mock interface the
render oracle drives: device presets, session states, code prompts, failures.
It is disposable test tooling, never a service implementation.
"""
import os
import sys
import time

from gi.repository import Gio, GLib

HERE = os.path.dirname(os.path.abspath(__file__))
BUS_NAME = "org.projectluma.Cast1"
PATH = "/org/projectluma/Cast1"

MOCK_XML = """
<node>
  <interface name="org.projectluma.Cast1.Mock">
    <method name="SetPreset"><arg type="s" direction="in"/></method>
    <method name="SetState"><arg type="s" direction="in"/></method>
    <method name="EmitCode"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="u" direction="in"/></method>
    <method name="End"><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
    <method name="SetCaptured"><arg type="as" direction="in"/></method>
    <method name="SetConnectBehaviour"><arg type="s" direction="in"/></method>
    <method name="CurtainLog"><arg type="as" direction="out"/></method>
  </interface>
</node>
"""

NOW = int(time.time() * 1_000_000)
HOUR = 3600 * 1_000_000


def v(sig, value):
    return GLib.Variant(sig, value)


def device(id_, name, kind, recent=False, last_used=0, direct=False, higher_delay=False,
           choices=None, caps=("screen", "extend", "display", "app", "audio"), busy=False, pinned=False):
    d = {
        "id": v("s", id_), "name": v("s", name), "kind": v("s", kind),
        "recent": v("b", recent), "last-used": v("x", last_used),
        "direct": v("b", direct), "higher-delay": v("b", higher_delay),
        "capabilities": v("as", list(caps)), "native-width": v("i", 1920), "native-height": v("i", 1080),
        "pinned": v("b", pinned), "busy": v("b", busy), "casting": v("b", False),
        "may-ask-for-code": v("b", False),
        "last-choices": v("a{sv}", {k: v("s", val) for k, val in (choices or {}).items()}),
    }
    return d


PRESETS = {
    "none": [],
    "room": [
        device("a1b2c3d4e5f60001", "Conference Room B", "conference-room", recent=True,
               last_used=NOW - HOUR, choices={"source": "screen", "optimize": "receiver", "audio": "receiver"}),
        device("a1b2c3d4e5f60002", "Living Room TV", "tv", recent=True, last_used=NOW - 30 * HOUR,
               choices={"source": "extend", "optimize": "receiver", "audio": "receiver"}),
        device("a1b2c3d4e5f60003", "Kitchen Display", "speaker-display"),
        device("a1b2c3d4e5f60004", "Lobby Projector", "projector", direct=True),
        device("a1b2c3d4e5f60005", "Studio iMac", "computer", caps=("screen", "extend", "app")),
        device("a1b2c3d4e5f60006", "Bedroom TV", "tv", higher_delay=True, caps=("screen", "app", "audio")),
    ],
    "long": [
        device("b000000000000001", "Executive Boardroom — East Wing Presentation Display", "conference-room",
               recent=True, last_used=NOW - HOUR, direct=True, choices={"source": "screen"}),
        device("b000000000000002", "Living Room TV", "tv"),
    ],
}


class Mock:
    def __init__(self, conn):
        self.conn = conn
        self.discovering = 0
        self.preset = os.environ.get("CU_MOCK_PRESET", "room")
        self.visible = []
        self.session = None
        self.session_n = 0
        self.connect_behaviour = os.environ.get("CU_MOCK_CONNECT", "stream")
        self.curtain_log = []
        xml = open(os.path.join(HERE, "org.projectluma.Cast1.xml")).read()
        info = Gio.DBusNodeInfo.new_for_xml(xml)
        self.manager_iface = info.lookup_interface("org.projectluma.Cast1")
        self.session_iface = info.lookup_interface("org.projectluma.Cast1.Session")
        mock_iface = Gio.DBusNodeInfo.new_for_xml(MOCK_XML).lookup_interface("org.projectluma.Cast1.Mock")
        conn.register_object(PATH, self.manager_iface, self.on_manager_call, self.on_manager_get, None)
        conn.register_object(PATH, mock_iface, self.on_mock_call, None, None)

    # ── manager ──
    def props(self):
        return {
            "Discovering": v("b", self.discovering > 0),
            "Session": v("o", self.session["path"] if self.session else "/"),
            "WifiDirectAvailable": v("b", True),
            "Version": v("u", 1),
            "Capabilities": v("as", ["screen", "extend", "app", "audio"]),
        }

    def on_manager_get(self, conn, sender, path, iface, prop):
        return self.props()[prop]

    def emit_props(self, path, iface, changed):
        self.conn.emit_signal(None, path, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                              v("(sa{sv}as)", (iface, changed, [])))

    def devices(self):
        return [dict(d, rank=v("u", i)) for i, d in enumerate(self.visible)]

    def reveal(self):
        # Screens arrive one by one, the way discovery finds them.
        all_devices = PRESETS.get(self.preset, [])
        delay = int(os.environ.get("CU_MOCK_REVEAL_MS", "150"))

        def step(i=[0]):
            if self.discovering <= 0 or i[0] >= len(all_devices):
                return False
            d = all_devices[i[0]]
            i[0] += 1
            self.visible.append(d)
            self.conn.emit_signal(None, PATH, "org.projectluma.Cast1", "DevicesChanged",
                                  v("(aa{sv}as)", ([dict(d, rank=v("u", len(self.visible) - 1))], [])))
            return True
        if delay == 0:
            while step():
                pass
        else:
            GLib.timeout_add(delay, step)

    def on_manager_call(self, conn, sender, path, iface, method, params, invocation):
        if method == "StartDiscovery":
            self.discovering += 1
            if self.discovering == 1:
                self.emit_props(PATH, "org.projectluma.Cast1", {"Discovering": v("b", True)})
                if not self.visible:
                    self.reveal()
            invocation.return_value(None)
        elif method == "StopDiscovery":
            self.discovering = max(0, self.discovering - 1)
            if self.discovering == 0:
                self.emit_props(PATH, "org.projectluma.Cast1", {"Discovering": v("b", False)})
            invocation.return_value(None)
        elif method == "GetDevices":
            invocation.return_value(v("(aa{sv})", (self.devices(),)))
        elif method == "Connect":
            device_id, options = params.unpack()
            print("Connect", device_id, options, flush=True)
            dev = next((d for d in PRESETS["room"] + PRESETS["long"] if d["id"].unpack() == device_id), None)
            path = self.start_session(dev, options)
            invocation.return_value(v("(o)", (path,)))
        elif method in ("ForgetDevice", "SetPinned"):
            invocation.return_value(None)
        elif method == "GetRememberedDevices":
            invocation.return_value(v("(aa{sv})", ([dict(d, present=v("b", True)) for d in PRESETS["room"]],)))
        else:
            invocation.return_dbus_error("org.projectluma.Cast1.Error.NotSupported", method)

    # ── session ──
    def start_session(self, dev, options):
        if self.session:
            self.unregister_session()
        self.session_n += 1
        path = f"{PATH}/sessions/{self.session_n}"
        name = dev["name"].unpack() if dev else "Screen"
        source = options.get("source", "screen")
        self.session = {
            "path": path,
            "props": {
                "DeviceId": v("s", dev["id"].unpack() if dev else ""), "DeviceName": v("s", name),
                "DeviceKind": v("s", dev["kind"].unpack() if dev else "unknown"),
                "State": v("s", "connecting"), "Source": v("s", source),
                "Connector": v("s", options.get("connector", "Meta-0")), "AppId": v("s", options.get("app-id", "")),
                "Optimize": v("s", options.get("optimize", "local")), "Audio": v("s", options.get("audio", "receiver")),
                "ErrorCode": v("s", ""), "ErrorMessage": v("s", ""),
                "CapturedConnectors": v("as", [] if source == "app" else ["Meta-0"]),
                "PrivacyCurtain": v("b", False), "WindowIds": v("at", options.get("window-ids", [])),
                "AudioOutput": v("s", ""), "Resolution": v("(uu)", (1920, 1080)), "Framerate": v("d", 60.0),
                "Codec": v("s", "h264"), "HardwareEncoding": v("b", True), "StartedAt": v("x", 0),
                "Stats": v("a{sv}", {}), "Media": v("a{sv}", {}),
            },
            "curtain": 0,
        }
        self.session["reg"] = self.conn.register_object(path, self.session_iface, self.on_session_call,
                                                        self.on_session_get, None)
        self.emit_props(PATH, "org.projectluma.Cast1", {"Session": v("o", path)})
        behaviour = self.connect_behaviour
        if behaviour == "stream":
            GLib.timeout_add(400, lambda: (self.set_state("streaming"), False)[1])
        elif behaviour in ("enter", "show", "confirm"):
            def ask():
                self.set_state("pairing")
                code = "4827" if behaviour == "show" else ""
                self.conn.emit_signal(None, path, "org.projectluma.Cast1.Session", "CodeRequested",
                                      v("(ssu)", (behaviour, code, 0 if behaviour == "confirm" else 4)))
                return False
            GLib.timeout_add(300, ask)
        elif behaviour.startswith("fail:"):
            messages = {
                "no-answer": f"{name} didn't answer. Make sure it's on and connected to this network.",
                "wifi-direct-busy": "Wi-Fi Direct isn't available while this network is in use; Luma will reconnect when you stop.",
                "receiver-busy": f"{name} is showing something else. Stop it there, then try again.",
                "encoder-failed": f"Luma couldn't encode video for {name}.",
            }
            code = behaviour[5:]
            GLib.timeout_add(500, lambda: (self.end(code, messages.get(code, "")), False)[1])
        return path

    def on_session_get(self, conn, sender, path, iface, prop):
        return self.session["props"][prop]

    def set_props(self, **changed):
        if not self.session:
            return
        self.session["props"].update(changed)
        self.emit_props(self.session["path"], "org.projectluma.Cast1.Session", changed)

    def set_state(self, state):
        self.set_props(State=v("s", state))

    def end(self, code, message):
        if not self.session:
            return
        path = self.session["path"]
        self.set_props(State=v("s", "ended"), ErrorCode=v("s", code), ErrorMessage=v("s", message))
        self.conn.emit_signal(None, path, "org.projectluma.Cast1.Session", "Ended", v("(ss)", (code, message)))
        self.unregister_session()
        self.emit_props(PATH, "org.projectluma.Cast1", {"Session": v("o", "/")})

    def unregister_session(self):
        if self.session:
            self.conn.unregister_object(self.session["reg"])
            self.session = None

    def on_session_call(self, conn, sender, path, iface, method, params, invocation):
        print("Session", method, params, flush=True)
        if method == "Stop":
            invocation.return_value(None)
            self.end("", "")
        elif method == "Change":
            (options,) = params.unpack()
            mapping = {"source": "Source", "connector": "Connector", "app-id": "AppId",
                       "optimize": "Optimize", "audio": "Audio"}
            changed = {mapping[k]: v("s", val) for k, val in options.items() if k in mapping}
            if "source" in options:
                changed["CapturedConnectors"] = v("as", [] if options["source"] == "app" else ["Meta-0"])
            self.set_props(**changed)
            invocation.return_value(None)
        elif method == "SubmitCode":
            (code,) = params.unpack()
            invocation.return_value(None)
            if code == "1234":
                GLib.timeout_add(250, lambda: (self.set_state("connecting"), GLib.timeout_add(300, lambda: (self.set_state("streaming"), False)[1]), False)[2])
            else:
                GLib.timeout_add(250, lambda: (self.conn.emit_signal(None, path, "org.projectluma.Cast1.Session",
                                                                      "CodeRequested", v("(ssu)", ("enter", "", 4))), False)[1])
        elif method == "CancelPairing":
            invocation.return_value(None)
            self.end("", "")
        elif method == "SetPrivacyCurtain":
            active, reason = params.unpack()
            self.curtain_log.append(f"{time.monotonic():.3f} {active} {reason}")
            delay = int(os.environ.get("CU_MOCK_CURTAIN_MS", "60"))

            def reply():
                self.set_props(PrivacyCurtain=v("b", active))
                invocation.return_value(None)
                return False
            GLib.timeout_add(delay, reply)
        else:
            invocation.return_value(None)

    # ── test control ──
    def on_mock_call(self, conn, sender, path, iface, method, params, invocation):
        args = params.unpack()
        if method == "SetPreset":
            self.preset = args[0]
            removed = [d["id"].unpack() for d in self.visible]
            self.visible = []
            conn.emit_signal(None, PATH, "org.projectluma.Cast1", "DevicesChanged", v("(aa{sv}as)", ([], removed)))
            if self.discovering > 0:
                self.reveal()
        elif method == "SetState":
            self.set_state(args[0])
        elif method == "EmitCode":
            mode, code, digits = args
            if self.session:
                self.set_state("pairing")
                conn.emit_signal(None, self.session["path"], "org.projectluma.Cast1.Session", "CodeRequested",
                                 v("(ssu)", (mode, code, digits)))
        elif method == "End":
            self.end(*args)
        elif method == "SetCaptured":
            self.set_props(CapturedConnectors=v("as", list(args[0])))
        elif method == "SetConnectBehaviour":
            self.connect_behaviour = args[0]
        elif method == "CurtainLog":
            invocation.return_value(v("(as)", (self.curtain_log,)))
            return
        invocation.return_value(None)


def main():
    loop = GLib.MainLoop()
    holder = {}

    def acquired(conn, name):
        print("acquired", name, flush=True)

    def on_bus(conn, name):
        holder["mock"] = Mock(conn)

    Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE, on_bus, acquired,
                     lambda *a: (print("lost name", flush=True), loop.quit()))
    loop.run()


if __name__ == "__main__":
    sys.exit(main())
