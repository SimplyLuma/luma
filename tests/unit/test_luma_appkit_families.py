# SPDX-License-Identifier: Apache-2.0
"""LumaUI part families: token fragments (design-tokens.d), family sheets and gallery pages.

Family builders add a part family as files of their own, so they never edit the
same lines: config/shared/design-tokens.d/<family>.json, luma-appkit-<family>.css
(with optional -dark/-frost/-glass/-high-contrast variants) and
tools/lumaui-gallery/pages_<family>.py. These tests hold the rules the kit keeper
promises them.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
APPKIT = ROOT / "src/luma-platform/appkit"
FRAGMENTS = ROOT / "config/shared/design-tokens.d"
GALLERY = ROOT / "src/luma-platform/tools/lumaui-gallery"


def _generator():
    spec = importlib.util.spec_from_file_location("gen_families", ROOT / "scripts/developer/generate-luma-platform-tokens.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Fragments(unittest.TestCase):
    def test_a_fragment_adds_but_never_changes_a_value(self):
        gen = _generator()
        document = {"lumaui": {"card": {"radius": 16}, "colors": {"light": {"ink": "#000"}}}}
        gen._merge(document, {"lumaui": {"rows": {"height": 44}, "colors": {"light": {"rows_ink": "#111"}}}}, "t.json")
        self.assertEqual(document["lumaui"]["rows"], {"height": 44})
        self.assertEqual(document["lumaui"]["colors"]["light"], {"ink": "#000", "rows_ink": "#111"})
        with self.assertRaises(SystemExit):
            gen._merge(document, {"lumaui": {"card": {"radius": 12}}}, "t.json")

    def test_fragment_groups_become_css_variables_python_and_type_classes(self):
        gen = _generator()
        with tempfile.TemporaryDirectory() as folder:
            import shutil
            for real in FRAGMENTS.glob("*.json") if FRAGMENTS.is_dir() else []:
                shutil.copy(real, folder)  # the generator may rely on the real fragments
            Path(folder, "zz.json").write_text(json.dumps({"lumaui": {
                "famtest": {"row_height": 44, "entry": {"height": 30}},
                "type_scale": {"famtest_reading": {"size": 15, "weight": 400, "line_height": 1.65, "tracking_em": 0}}}}))
            with mock.patch.object(gen, "FRAGMENTS", Path(folder)):
                css = gen.render_lumaui_metrics()
                python = gen.render_lumaui_python()
        self.assertIn("--lumaui-famtest-row-height: 44px;", css)
        self.assertIn("--lumaui-famtest-entry-height: 30px;", css)
        self.assertIn(".lumaui-t-famtest-reading {", css)
        self.assertIn("FAMTEST = {'row_height': 44, 'entry': {'height': 30}}", python)

    def test_every_fragment_is_shaped_like_the_main_file(self):
        for path in sorted(FRAGMENTS.glob("*.json")) if FRAGMENTS.is_dir() else []:
            data = json.loads(path.read_text())
            self.assertEqual({k for k in data if not k.startswith("_")}, {"lumaui"}, f"{path.name}: a fragment holds only a lumaui object")


class FamilySheets(unittest.TestCase):
    def setUp(self):
        import sys
        if str(APPKIT) not in sys.path:
            sys.path.insert(0, str(APPKIT))

    def test_discovery(self):
        try:
            from luma_appkit import lumaui
        except (ImportError, ValueError) as error:
            self.skipTest(f"GTK not available: {error}")
        with tempfile.TemporaryDirectory() as folder:
            for name in ("luma-appkit-base.css", "luma-appkit-tokens.css", "luma-appkit-dark.css",
                         "luma-appkit-dark-tokens.css", "luma-appkit-high-contrast.css",
                         "luma-appkit-rows.css", "luma-appkit-rows-dark.css", "luma-appkit-bar.css",
                         "luma-appkit-orphan-dark.css"):
                Path(folder, name).write_text("")
            with mock.patch.dict(os.environ, {"LUMA_APPKIT_BASE_PATH": str(Path(folder, "luma-appkit-base.css"))}):
                found = lumaui.family_sheets()
        self.assertEqual(sorted(found), ["bar", "rows"])
        self.assertEqual(sorted(found["rows"]), ["", "dark"])

    def test_the_build_installs_family_sheets(self):
        meson = (ROOT / "src/luma-platform/meson.build").read_text()
        self.assertIn("lumaui_family_css", meson)


class KitGuard(unittest.TestCase):
    """What a bad merge leaves behind: conflict markers, broken JSON, unbalanced braces."""

    KIT = [ROOT / "src/luma-platform/appkit", ROOT / "src/luma-platform/ui", ROOT / "config/shared",
           ROOT / "tools/lumaui-conform", ROOT / "src/luma-platform/tools/lumaui-gallery", ROOT / "docs/developer/kit"]

    def _files(self, *patterns):
        for folder in self.KIT:
            for pattern in patterns:
                yield from (p for p in folder.rglob(pattern) if "node_modules" not in p.parts and "_build" not in str(p))

    def test_no_conflict_markers_in_kit_files(self):
        marker = re.compile(r"^(<{7}|={7}|>{7})( |$)", re.M)
        found = [str(p.relative_to(ROOT)) for p in self._files("*.py", "*.css", "*.json", "*.js", "*.c", "*.h", "*.md", "*.sh")
                 if marker.search(p.read_text(encoding="utf-8", errors="replace"))]
        self.assertEqual(found, [])

    def test_kit_json_parses(self):
        for path in [ROOT / "config/shared/design-tokens.json", ROOT / "tools/lumaui-conform/accepted-deviations.json",
                     *sorted((ROOT / "config/shared/design-tokens.d").glob("*.json")),
                     *sorted((ROOT / "tools/lumaui-conform/scenarios").glob("*.json"))]:
            with self.subTest(path=path.name):
                json.loads(path.read_text(encoding="utf-8"))

    def test_accepted_deviations_are_documented(self):
        data = json.loads((ROOT / "tools/lumaui-conform/accepted-deviations.json").read_text(encoding="utf-8"))
        doc = (ROOT / "docs/developer/kit/lumaui-deviations.md").read_text(encoding="utf-8")
        ids = [d["id"] for d in data["deviations"]]
        self.assertEqual(len(ids), len(set(ids)), "deviation ids are unique")
        for deviation in data["deviations"]:
            self.assertTrue(deviation.get("doc", "").startswith("lumaui-deviations.md"), deviation["id"])

    def test_every_kit_sheet_balances_its_braces(self):
        for path in self._files("*.css"):
            text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
            with self.subTest(sheet=path.name):
                self.assertEqual(text.count("{"), text.count("}"))


class SheetsParse(unittest.TestCase):
    def test_every_kit_sheet_parses_without_errors(self):
        # GTK drops a rule it can't parse (":has()", a stray token) without failing; catch it here.
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            from gi.repository import Gtk
        except (ImportError, ValueError) as error:
            self.skipTest(f"GTK not available: {error}")
        for path in sorted(APPKIT.glob("*.css")):
            errors = []
            provider = Gtk.CssProvider()
            provider.connect("parsing-error", lambda _p, section, error, e=errors: e.append(
                f"{section.get_start_location().lines + 1}: {error.message}"))
            provider.load_from_path(str(path))
            self.assertEqual([m for m in errors if "import" not in m.lower()], [], path.name)


class MediaContext(unittest.TestCase):
    def setUp(self):
        import sys
        if str(APPKIT) not in sys.path:
            sys.path.insert(0, str(APPKIT))
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            from gi.repository import Gtk  # noqa: F401
            from luma_appkit import lumaui  # noqa: F401
        except (ImportError, ValueError) as error:
            self.skipTest(f"GTK not available: {error}")

    def test_media_context_marks_a_region_and_reaches_its_descendants(self):
        from gi.repository import Gtk
        from luma_appkit import lumaui
        region, inner, outside = Gtk.Box(), Gtk.Button(), Gtk.Button()
        region.append(inner)
        lumaui.media_context(region)
        self.assertTrue(region.has_css_class("lumaui-media"))
        self.assertTrue(lumaui.in_media_context(inner))
        self.assertFalse(lumaui.in_media_context(outside))
        lumaui.media_context(region, on=False)
        self.assertFalse(lumaui.in_media_context(inner))

    def test_bar_action_note(self):
        from luma_appkit.action_center import BarAction, make_control
        button = make_control(BarAction("zap", tooltip="Flash", note="A"))
        self.assertTrue(button.has_css_class("noted") and button.has_css_class("icon"))


class OldCallForms(unittest.TestCase):
    """Applications not ported to LumaUI run on this kit too; their call forms keep working."""

    def test_a_positional_shortcut_is_a_shortcut(self):
        import sys
        if str(APPKIT) not in sys.path:
            sys.path.insert(0, str(APPKIT))
        from luma_appkit.commands import Command
        old = Command("photos.import", "Import Files…", lambda: None, "document-open-symbolic", ("Ctrl", "Shift", "I"))
        self.assertEqual((old.shortcut, old.description), (("Ctrl", "Shift", "I"), ""))
        self.assertEqual(Command("q", "Quit", lambda: None, shortcut=["Ctrl", "Q"]).shortcut, ("Ctrl", "Q"))
        self.assertEqual(Command("d", "Duplicate", lambda: None, description="Copy it").description, "Copy it")


class GalleryPages(unittest.TestCase):
    def test_the_gallery_loads_pages_modules(self):
        text = (GALLERY / "lumaui_gallery.py").read_text()
        self.assertIn('HERE.glob("pages_*.py")', text)
        for path in GALLERY.glob("pages_*.py"):
            self.assertTrue(re.search(r"^BUILDERS\s*[:=]", path.read_text(), re.M), f"{path.name} exports BUILDERS")


if __name__ == "__main__":
    unittest.main()
