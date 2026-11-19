# SPDX-License-Identifier: MPL-2.0
"""App identity, capture matching and the mixer model.

The app key rule exists twice, in Python (luma_audio_devices.apps) and in the
WirePlumber script's Lua library; both are checked against
fixtures/app-keys.json so the mixer, the remembered volumes and the capture
sessions always agree about which app a stream belongs to."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import _paths
from luma_audio_devices import apps
from luma_audio_devices.graph import Graph
from luma_audio_devices.levels import meter_position
from luma_audio_devices.mixer import Mixer

VECTORS = json.loads((_paths.FIXTURES / "app-keys.json").read_text(encoding="utf-8"))
LUA = shutil.which("lua") or shutil.which("lua5.4")
LIB = next(p for p in (
    _paths.COMPONENT / "data" / "wireplumber" / "scripts" / "lib" / "luma-app-audio.lua",
    Path("/usr/share/wireplumber/scripts/lib/luma-app-audio.lua"),
) if p.is_file())


def lua_literal(value):
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "{" + ", ".join(lua_literal(v) for v in value) + "}"
    return "{" + ", ".join(f"[{json.dumps(k)}] = {lua_literal(v)}" for k, v in value.items()) + "}"


class AppKeys(unittest.TestCase):
    def test_python_vectors(self):
        for vector in VECTORS:
            with self.subTest(vector["case"]):
                key, _ = apps.app_key(vector["props"], vector.get("client"))
                self.assertEqual(key, vector["key"])
                self.assertEqual(apps.is_system_sound(vector["props"]), vector["system"])
                self.assertEqual(apps.is_app_playback(vector["props"]), vector["playback"])

    @unittest.skipUnless(LUA, "lua is not installed")
    def test_lua_vectors_match(self):
        lines = [f'package.path = {json.dumps(str(LIB.parent) + "/?.lua;")} .. package.path',
                 'local app = require("luma-app-audio")']
        for vector in VECTORS:
            props, client = lua_literal(vector["props"]), lua_literal(vector.get("client") or {})
            lines.append(f"print(tostring(app.app_key({props}, {client})))")
            lines.append(f"print(tostring(app.is_system_sound({props})))")
            lines.append(f"print(tostring(app.is_app_playback({props})))")
        with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False) as handle:
            handle.write("\n".join(lines) + "\n")
        try:
            run = subprocess.run([LUA, handle.name], capture_output=True, text=True)
        finally:
            Path(handle.name).unlink()
        self.assertEqual(run.returncode, 0, run.stderr)
        output = run.stdout.splitlines()
        for index, vector in enumerate(VECTORS):
            with self.subTest(vector["case"]):
                key, system, playback = output[index * 3:index * 3 + 3]
                self.assertEqual(key, "nil" if vector["key"] is None else vector["key"])
                self.assertEqual(system, str(vector["system"]).lower())
                self.assertEqual(playback, str(vector["playback"]).lower())

    @unittest.skipUnless(LUA, "lua is not installed")
    def test_lua_capture_requests(self):
        script = f'''package.path = {json.dumps(str(LIB.parent) + "/?.lua;")} .. package.path
local app = require("luma-app-audio")
local c = app.capture_from_request({{mode = "apps", apps = {{"org.a", ""}}, requester = "org.me"}})
print(app.capture_includes(c, "org.a"), app.capture_includes(c, "org.b"))
local s = app.capture_from_request({{mode = "system", requester = "org.me"}})
print(app.capture_includes(s, "org.a"), app.capture_includes(s, "org.me"))
local i = app.capture_from_request({{mode = "system", requester = "org.me", include_self = true}})
print(app.capture_includes(i, "org.me"))
print(select(2, app.capture_from_request({{mode = "apps", apps = {{}}}})))
print(select(2, app.capture_from_request({{mode = "everything"}})))
print(app.is_portal_remote({{["pipewire.access.portal.media_roles"] = ""}}),
      app.is_portal_remote({{["pipewire.access.portal.media_roles"] = "", ["pipewire.access.portal.is_portal"] = "yes"}}),
      app.is_portal_remote({{["pipewire.access.portal.app_id"] = "org.flatpak.Direct"}}))
print(string.format("%.3f %.3f", app.cubic_to_linear(0.5), app.linear_to_cubic(0.125)))
'''
        with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False) as handle:
            handle.write(script)
        try:
            run = subprocess.run([LUA, handle.name], capture_output=True, text=True)
        finally:
            Path(handle.name).unlink()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.split("\n"), [
            "true\tfalse", "true\tfalse", "true", "no apps chosen", "unknown mode",
            "true\tfalse\tfalse", "0.125 0.500", ""])


class Capture(unittest.TestCase):
    def test_includes(self):
        chosen = {"mode": "apps", "apps": ["org.a"], "requester": "org.me"}
        self.assertTrue(apps.capture_includes(chosen, "org.a"))
        self.assertFalse(apps.capture_includes(chosen, "org.b"))
        everything = {"mode": "system", "requester": "org.me"}
        self.assertTrue(apps.capture_includes(everything, "org.b"))
        self.assertFalse(apps.capture_includes(everything, "org.me"))
        self.assertTrue(apps.capture_includes(dict(everything, include_self=True), "org.me"))
        self.assertFalse(apps.capture_includes({"mode": "nothing"}, "org.a"))

    def test_portal_remote(self):
        self.assertTrue(apps.is_portal_remote({"pipewire.access.portal.media_roles": ""}))
        self.assertFalse(apps.is_portal_remote({"pipewire.access.portal.app_id": "org.x"}))
        self.assertFalse(apps.is_portal_remote({"pipewire.access.portal.media_roles": "",
                                                "pipewire.access.portal.is_portal": "yes"}))

    def test_volume_scales(self):
        self.assertAlmostEqual(apps.cubic_to_linear(0.5), 0.125)
        self.assertAlmostEqual(apps.linear_to_cubic(0.125), 0.5)
        self.assertEqual(apps.stream_volume([0.2, 0.7, "x"]), 0.7)
        self.assertIsNone(apps.stream_volume([]))
        self.assertEqual(meter_position(0.0), 1.0)
        self.assertEqual(meter_position(-60.0), 0.0)
        self.assertEqual(meter_position(float("-inf")), 0.0)
        self.assertAlmostEqual(meter_position(-30.0), 0.5)


class _Info:
    def __init__(self, desktop_id, name, icon=None, commandline=None, wm_class=None):
        self._id, self._name, self._icon, self._cmd, self._wm = desktop_id, name, icon, commandline, wm_class

    def get_id(self): return self._id
    def get_name(self): return self._name
    def get_display_name(self): return self._name
    def get_commandline(self): return self._cmd
    def get_executable(self): return (self._cmd or "").split(" ")[0] or None
    def get_startup_wm_class(self): return self._wm

    def get_icon(self):
        if self._icon is None:
            return None
        icon = self._icon

        class _Icon:
            def to_string(self): return icon
        return _Icon()


class _Gio:
    class AppInfo:
        entries = [
            _Info("org.mozilla.firefox.desktop", "Firefox", "org.mozilla.firefox",
                  "/usr/bin/flatpak run --branch=stable org.mozilla.firefox %u"),
            _Info("mpv.desktop", "mpv Media Player", "mpv", "mpv --player-operation-mode=pseudo-gui -- %U"),
            _Info("org.gnome.Rhythmbox3.desktop", "Rhythmbox", "org.gnome.Rhythmbox3", "rhythmbox %U"),
            _Info("spotify.desktop", "Spotify", "spotify-client", "env LD_PRELOAD= spotify %U", "Spotify"),
        ]

        @staticmethod
        def get_all():
            return _Gio.AppInfo.entries

    class AppInfoMonitor:
        @staticmethod
        def get():
            raise RuntimeError("no monitor in tests")


class Identity(unittest.TestCase):
    def setUp(self):
        self.index = apps.DesktopIndex(gio=_Gio)

    def test_flatpak(self):
        props = {"pipewire.access.portal.app_id": "org.mozilla.firefox", "application.name": "Firefox"}
        found = self.index.identify("org.mozilla.firefox", props)
        self.assertEqual((found.name, found.icon, found.desktop_id),
                         ("Firefox", "org.mozilla.firefox", "org.mozilla.firefox.desktop"))

    def test_binary_and_wm_class(self):
        self.assertEqual(self.index.identify("mpv", {"application.process.binary": "mpv"}).name,
                         "mpv Media Player")
        self.assertEqual(self.index.identify("Spotify", {"application.name": "Spotify"}).name, "Spotify")

    def test_unknown_app_keeps_its_own_name(self):
        found = self.index.identify("ToneA", {"application.name": "ToneA",
                                              "application.process.binary": "gst-launch-1.0"})
        self.assertEqual((found.name, found.icon, found.desktop_id), ("ToneA", apps.FALLBACK_ICON, ""))

    def test_system(self):
        self.assertEqual(self.index.identify(apps.SYSTEM_KEY, {}).name, "System sounds")


class _Control:
    def __init__(self):
        self.commands = []
        self.app_audio_ready = True

    def app_audio_command(self, key, value):
        self.commands.append((key, value))
        return True


def node(oid, props, state="running", volumes=(1.0, 1.0), mute=False):
    return {"id": oid, "type": "PipeWire:Interface:Node",
            "info": {"state": state, "props": dict(props, **{"object.serial": oid + 1000}),
                     "params": {"Props": [{"channelVolumes": list(volumes), "mute": mute}]}}}


class MixerModel(unittest.TestCase):
    def graph(self):
        graph = Graph()
        graph.apply([
            {"id": 5, "type": "PipeWire:Interface:Client", "info": {"props": {"application.process.binary": "paplay"}}},
            node(40, {"media.class": "Stream/Output/Audio", "application.name": "ToneA"}, volumes=(0.125, 0.125)),
            node(41, {"media.class": "Stream/Output/Audio", "application.name": "ToneA"}, state="idle",
                 volumes=(0.0, 0.0), mute=True),
            node(42, {"media.class": "Stream/Output/Audio", "client.id": "5"}, mute=True),
            node(43, {"media.class": "Stream/Output/Audio", "media.role": "Notification",
                      "application.name": "GNOME Shell"}),
            node(44, {"media.class": "Stream/Input/Audio", "application.name": "Recorder"}),
            node(45, {"media.class": "Stream/Output/Audio", "stream.monitor": "true", "application.name": "x"}),
            node(60, {"media.class": "Audio/Sink", "node.name": "speaker-b"}),
            {"id": 2, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"},
             "metadata": [{"subject": 40, "key": "target.object", "value": "1060"}]},
        ])
        return graph

    def test_rows(self):
        mixer = Mixer(None, _Control())
        self.assertTrue(mixer.update(self.graph()))
        rows = {row["id"]: row for row in mixer.rows()}
        self.assertEqual(list(rows), ["@system", "paplay", "ToneA"])
        self.assertEqual(rows["@system"]["name"], "System sounds")
        tone = rows["ToneA"]
        self.assertEqual(tone["streams"], [40, 41])
        self.assertAlmostEqual(tone["volume"], 0.5, places=3)
        self.assertFalse(tone["muted"])
        self.assertTrue(tone["playing"])
        self.assertEqual(tone["output"], "speaker-b")
        self.assertTrue(rows["paplay"]["muted"])
        self.assertFalse(mixer.update(self.graph()))

    def test_commands(self):
        control = _Control()
        mixer = Mixer(None, control)
        mixer.set_volume("ToneA", 0.5)
        mixer.set_volume("ToneA", 9)
        mixer.set_mute("@system", True)
        mixer.set_output("ToneA", "")
        self.assertEqual(control.commands, [
            ("app.volume:ToneA", {"volume": 0.125}),
            ("app.volume:ToneA", {"volume": 1.5 ** 3}),
            ("app.volume:@system", {"mute": True}),
            ("app.target:ToneA", {"name": None}),
        ])


if __name__ == "__main__":
    unittest.main()
