#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""A TV keeps its apps prepared: which apps, how each is started, and the
Python side of the prepared-launch protocol (luma_appkit.prepare)."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APPKIT = REPO_ROOT / "src/luma-platform/appkit"
PREWARM = REPO_ROOT / "src/luma-platform/apphost/native-prewarm.py"


def load_prewarm():
    loader = importlib.machinery.SourceFileLoader("luma_native_app_prewarm", str(PREWARM))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


prewarm = load_prewarm()


def launcher(folder: Path, app_id: str, *lines: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{app_id}.desktop").write_text("\n".join(["[Desktop Entry]", "Type=Application", *lines]) + "\n")


class TvApps(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.user, self.system = root / "user", root / "system"
        self.dirs = [self.user, self.system]
        self.manifest = root / "tv.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_manifest_order_and_only_tv_apps(self):
        launcher(self.system, "org.example.Music", "X-Luma-TV=true", "X-Flatpak=org.example.Music")
        launcher(self.system, "org.example.Notes", "Exec=notes")              # not on the TV home
        launcher(self.system, "org.example.Shell", "X-Luma-TV=true", "Exec=/usr/bin/example-shell %U")
        self.manifest.write_text(json.dumps(["org.example.Shell", "org.example.Notes", "../escape",
                                             "org.example.Missing", "org.example.Music"]))
        self.assertEqual(prewarm.tv_apps(self.manifest, self.dirs), ["org.example.Shell", "org.example.Music"])

    def test_a_flatpaks_tv_keys_come_from_its_deploy(self):
        # Flatpak's export drops the X-Luma-TV keys; the deployed file keeps them.
        launcher(self.system, "org.example.Music", "X-Flatpak=org.example.Music", "Exec=flatpak run org.example.Music")
        deploy = Path(self.temp.name) / "deploy" / "org.example.Music.desktop"
        launcher(deploy.parent, "org.example.Music", "X-Luma-TV=true", "X-Luma-TV-Tile=org.example.Music-tv")
        self.manifest.write_text(json.dumps(["org.example.Music"]))
        self.assertEqual(prewarm.tv_apps(self.manifest, self.dirs), [])
        original = prewarm.flatpak_deploys
        prewarm.flatpak_deploys = lambda app_id: [deploy]
        try:
            self.assertEqual(prewarm.tv_apps(self.manifest, self.dirs), ["org.example.Music"])
            keys = prewarm.tv_launcher("org.example.Music", self.dirs)
            self.assertEqual((keys["X-Luma-TV"], keys["X-Flatpak"]), ("true", "org.example.Music"))
        finally:
            prewarm.flatpak_deploys = original

    def test_the_users_launcher_wins(self):
        launcher(self.system, "org.example.Music", "X-Luma-TV=true")
        launcher(self.user, "org.example.Music", "X-Luma-TV=false")
        self.manifest.write_text(json.dumps(["org.example.Music"]))
        self.assertEqual(prewarm.tv_apps(self.manifest, self.dirs), [])

    def test_a_broken_manifest_prepares_nothing(self):
        self.manifest.write_text("{")
        self.assertEqual(prewarm.tv_apps(self.manifest, self.dirs), [])
        self.assertEqual(prewarm.tv_apps(self.manifest.with_name("absent.json"), self.dirs), [])

    def test_commands(self):
        launcher(self.system, "org.example.Music", "X-Luma-TV=true", "X-Flatpak=org.example.Music",
                 "Exec=/usr/bin/flatpak run --branch=stable org.example.Music")
        launcher(self.system, "org.example.Shell", "X-Luma-TV=true", "Exec=example-shell %U")
        launcher(self.system, "org.example.Notes", "Exec=notes")
        original = prewarm.application_dirs
        prewarm.application_dirs = lambda: self.dirs
        try:
            self.assertEqual(prewarm.prepared_command("org.example.Music"),
                             ["flatpak", "run", "--env=LUMA_APP_PREPARE=1", "org.example.Music",
                              "--gapplication-service"])
            self.assertEqual(prewarm.prepared_command("org.example.Shell", launchers=str(self.system / "none")),
                             ["/usr/bin/env", "LUMA_APP_PREPARE=1", "example-shell", "--gapplication-service"])
            # Launched through the app host: prepared as its own launcher.
            launchers = self.system / "luma-app-launchers"
            launchers.mkdir()
            (launchers / "example-shell").write_text("#!/bin/sh\n")
            self.assertEqual(prewarm.prepared_command("org.example.Shell", launchers=str(launchers)),
                             ["/usr/bin/env", "LUMA_APP_PREPARE=1", str(launchers / "example-shell"),
                              "--gapplication-service"])
            self.assertIsNone(prewarm.prepared_command("org.example.Notes"))
            self.assertEqual(prewarm.prepared("org.example.Notes"), 75)
        finally:
            prewarm.application_dirs = original

    def test_app_ids(self):
        self.assertTrue(prewarm.valid_app_id("org.projectluma.Tide"))
        for bad in ("Tide", "org..Tide", "org/projectluma/Tide", "org.projectluma.Tide;rm", ""):
            self.assertFalse(prewarm.valid_app_id(bad), bad)

    def test_shipped_manifest_and_unit(self):
        apps = json.loads((REPO_ROOT / "src/luma-platform/apphost/tv.json").read_text())
        self.assertEqual(apps[0], "org.projectluma.Tide")
        self.assertTrue(all(prewarm.valid_app_id(app) for app in apps))
        unit = (REPO_ROOT / "src/luma-platform/apphost/luma-app-prepared@.service").read_text()
        self.assertIn("ExecCondition=/usr/libexec/luma-native-app-prewarm --wanted %I", unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("RestartPreventExitStatus=75", unit)


APP = textwrap.dedent("""
    import sys
    sys.path.insert(0, {appkit!r})
    import luma_appkit  # installs the prepare hooks under LUMA_APP_PREPARE=1
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    record = {record!r}

    def activate(app):
        window = app.get_active_window()
        if window is None:
            window = Gtk.Window(application=app)
            window.connect("map", lambda *_: open(record, "a").write("mapped\\n"))
            window.connect("realize", lambda *_: open(record, "a").write("realized\\n"))
            open(record, "a").write("built\\n")
        window.present()

    app = Gtk.Application(application_id={app_id!r})
    app.connect("activate", activate)
    # Some apps drop their argv (run([])): prepared, they are services anyway.
    sys.exit(app.run([] if "--drop-argv" in sys.argv else sys.argv))
""")


@unittest.skipUnless(os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"),
                     "needs a Wayland session and its bus")
class PreparedPythonApp(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("XDG_RUNTIME_DIR"))
        root = Path(self.temp.name)
        self.record = root / "record"
        self.socket_path = root / "ready.sock"
        self.ready = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.ready.settimeout(15)
        self.ready.bind(str(self.socket_path))
        self.app_id = "org.projectluma.PrepareTest" + uuid.uuid4().hex[:12]
        script = root / "app.py"
        script.write_text(APP.format(appkit=str(APPKIT), record=str(self.record), app_id=self.app_id))
        env = dict(os.environ, LUMA_APP_PREPARE="1", LUMA_APP_PREPARE_NOTIFY=str(self.socket_path))
        argv = ["--drop-argv"] if "drop_argv" in self.id() else ["--gapplication-service"]
        self.child = subprocess.Popen([sys.executable, str(script), *argv], env=env)

    def tearDown(self):
        self.child.terminate()
        try:
            self.child.wait(5)
        except subprocess.TimeoutExpired:
            self.child.kill()
        self.ready.close()
        self.temp.cleanup()

    def lines(self):
        return self.record.read_text().split() if self.record.exists() else []

    def test_built_unseen_then_a_launch_only_presents(self):
        self.assertEqual(int(self.ready.recv(64)), self.child.pid)
        time.sleep(0.5)
        self.assertEqual(self.lines(), ["built", "realized"])  # the window exists, nobody sees it
        path = "/" + self.app_id.replace(".", "/")
        subprocess.run(["gdbus", "call", "--session", "--dest", self.app_id, "--object-path", path,
                        "--method", "org.freedesktop.Application.Activate", "{}"],
                       check=True, capture_output=True, timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and "mapped" not in self.lines():
            time.sleep(0.1)
        # The launch showed the window it already had: no second window, no start,
        # on a surface made at the launch for the real output (not at preparation).
        self.assertEqual(self.lines(), ["built", "realized", "realized", "mapped"])
        self.assertIsNone(self.child.poll())

    def test_drop_argv_an_app_that_drops_its_argv_still_waits_unseen(self):
        # run([]) would activate the app at its own start; prepared, it must not show.
        self.assertEqual(int(self.ready.recv(64)), self.child.pid)
        time.sleep(1.0)
        self.assertEqual(self.lines(), ["built", "realized"])
        self.assertIsNone(self.child.poll())


if __name__ == "__main__":
    unittest.main()
