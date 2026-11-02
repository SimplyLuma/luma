#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""TV Settings' backends: the shell-state keys it writes, the shell piece that applies Screen
edges, and LumaUI's idle hook following the idle delay."""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = REPO_ROOT / "src/luma-shell-state/org.project_luma.shell-state.gschema.xml"
EDGES = REPO_ROOT / "src/luma-shell-presentation/tvScreenEdges.js"
TV = REPO_ROOT / "src/luma-shell-presentation/tv.js"
SPEC = (REPO_ROOT / "patches/gnome-shell/0000-luma-fedora-spec.patch").read_text()
BUILD = (REPO_ROOT / "scripts/packages/build-gnome-shell.sh").read_text()
PATCH = (REPO_ROOT / "patches/gnome-shell/0312-tv-screen-edges.patch").read_text()


def key(name):
    for k in ET.parse(SCHEMA).getroot().iter("key"):
        if k.get("name") == name:
            return k
    return None


class SchemaKeys(unittest.TestCase):
    def test_idle_delay_is_additive(self):
        k = key("tv-idle-delay")
        self.assertEqual(k.get("type"), "i")
        self.assertEqual(k.findtext("default"), "-1")
        r = k.find("range")
        self.assertEqual((r.get("min"), r.get("max")), ("-1", "86400"))

    def test_screen_edges_is_additive(self):
        k = key("tv-screen-edges")
        self.assertEqual(k.get("type"), "u")
        self.assertEqual(k.findtext("default"), "0")
        r = k.find("range")
        self.assertEqual((r.get("min"), r.get("max")), ("0", "10"))

    def test_schema_compiles(self):
        with tempfile.TemporaryDirectory() as out:
            subprocess.run(["glib-compile-schemas", "--strict", "--targetdir", out, str(SCHEMA.parent)],
                           check=True, capture_output=True)


class ScreenEdges(unittest.TestCase):
    def test_shell_piece_is_registered(self):
        self.assertIn("<file>ui/lumaPresentation/tvScreenEdges.js</file>", PATCH)
        self.assertIn("0312-tv-screen-edges.patch", SPEC)
        self.assertIn("cp %{SOURCE9047} js/ui/lumaPresentation/tvScreenEdges.js", SPEC)
        self.assertIn("tvScreenEdges.js", BUILD)
        self.assertIn("0312-tv-screen-edges.patch", BUILD)

    def test_tv_presentation_owns_it_for_the_posture_only(self):
        text = TV.read_text()
        self.assertIn("import {TvScreenEdges} from './tvScreenEdges.js';", text)
        self.assertRegex(text, r"this\._edges = new TvScreenEdges\(this\._settings, Main\.uiGroup\);\n\s+this\._edges\.enable\(\);")
        self.assertIn("this._edges?.destroy();", text)

    def test_scale(self):
        script = (f"import('{EDGES.as_uri()}').then(m => console.log(JSON.stringify("
                  "[0, 5, 10, 11, -3, 'x'].map(m.edgeScale))))")
        try:
            out = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout
        except FileNotFoundError:
            self.skipTest("node is not installed")
        self.assertEqual(out.strip(), "[1,0.95,0.9,0.9,1,1]")


class IdleHook(unittest.TestCase):
    def test_tv_idle_reads_the_key(self):
        text = (REPO_ROOT / "src/luma-platform/appkit/luma_appkit/tv_idle.py").read_text()
        self.assertIn('get_int("tv-idle-delay")', text)
        self.assertIn('changed::tv-idle-delay', text)
        self.assertRegex(text, r"delay <= 0")


if __name__ == "__main__":
    unittest.main()
