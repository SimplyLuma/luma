# SPDX-License-Identifier: Apache-2.0
"""LumaUI glyphs: imported intact, and never named without being imported.

The kit, the gallery and the applications name Lucide glyphs by their Lucide
name (`icon="trash-2"`, `lumaui_icon("share-2")`, `icons.image("copy")`) or
by the theme name (`"lumaui-copy-symbolic"`). Each must exist as
assets/icon-theme/Prairie/symbolic/actions/lumaui-<name>-symbolic.svg, or
GTK draws the missing-image placeholder at run time.
"""
from __future__ import annotations

import ast
import importlib.util
import io
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTIONS = ROOT / "assets/icon-theme/Prairie/symbolic/actions"
KIT = ROOT / "src/luma-platform/appkit/luma_appkit"
# Part families (luma-appkit-<family>.css) keep their modules as <family>_*.py.
FAMILY_PREFIXES = tuple(f"{p.name[len('luma-appkit-'):-len('.css')]}_"
                        for p in (ROOT / "src/luma-platform/appkit").glob("luma-appkit-*.css")
                        if re.fullmatch(r"luma-appkit-[a-z][a-z0-9_]*\.css", p.name)
                        and p.name[len("luma-appkit-"):-len(".css")] not in ("base", "tokens", "dark", "frost", "glass")) + (
    "rows_", "bar_", "media_", "image_", "chart_", "data_", "creative_")
# LumaUI's own modules and the gallery, where a bare Lucide name means a LumaUI
# glyph; applications, where only the explicit LumaUI spellings do.
LUMAUI_SOURCES = [*(p for p in KIT.glob("*.py") if p.stem in ("lumaui", "icons") or
                    p.stem.startswith(("structure_", "action_", "content_", *FAMILY_PREFIXES))),
                  *(ROOT / "src/luma-platform/tools/lumaui-gallery").glob("*.py")]
APP_SOURCES = [*(ROOT / "src/prairie-core/prairie_apps").glob("*.py"), *(ROOT / "src").rglob("*.css")]
LUCIDE = r"([a-z0-9]+(?:-[a-z0-9]+)*)"
APP_PATTERNS = (
    re.compile(r"""["']lumaui-""" + LUCIDE + r"""-symbolic["']"""),
    re.compile(r"""\blumaui_icon\(\s*["']""" + LUCIDE + r"""["']"""),
    re.compile(r"""-gtk-icontheme\(["']lumaui-""" + LUCIDE + r"""-symbolic["']\)"""),
)
PATTERNS = (
    re.compile(r"""["']lumaui-""" + LUCIDE + r"""-symbolic["']"""),
    re.compile(r"""\b(?:lumaui_icon|icon_name|icons\.image|image)\(\s*["']""" + LUCIDE + r"""["']"""),
    re.compile(r"""\b(?:icon|glyph)\s*=\s*["']""" + LUCIDE + r"""["']"""),
    re.compile(r"""\bStackedButton\(\s*["']""" + LUCIDE + r"""["']"""),
    re.compile(r"""-gtk-icontheme\(["']lumaui-""" + LUCIDE + r"""-symbolic["']\)"""),
)
# Tuples in the kit and gallery that pair a glyph with a label: ("heart", "love").
TABLE_ENTRY = re.compile(r"""\(\s*["']""" + LUCIDE + r"""["']\s*,\s*["'](?:good|accent|love|muted|warning|danger)["']\s*\)""")


def _available() -> set[str]:
    return {p.name[len("lumaui-"):-len("-symbolic.svg")] for p in ACTIONS.glob("lumaui-*-symbolic.svg")}


def _table_glyphs(text: str) -> set[str]:
    parsed = ast.parse(text)
    parents = {child: node for node in ast.walk(parsed) for child in ast.iter_child_nodes(node)}
    names = set()
    for node in ast.walk(parsed):
        # Icon tables contain nested (glyph, tone) pairs. A flat tuple of tone
        # values in a loop or membership check does not name any glyph.
        if not isinstance(node, ast.Tuple) or not isinstance(parents.get(node), (ast.List, ast.Tuple)) or len(node.elts) != 2:
            continue
        first, second = node.elts[:2]
        if isinstance(first, ast.Constant) and isinstance(first.value, str) and isinstance(second, ast.Constant) and second.value in ("good", "accent", "love", "muted", "warning", "danger"):
            names.add(first.value)
    return names


class Icons(unittest.TestCase):
    def test_tone_values_are_not_icon_table_entries(self):
        self.assertEqual(_table_glyphs('tones = ("red", "accent"); icons = [("missing-glyph", "accent")]'), {"missing-glyph"})

    def test_import_is_intact(self):
        spec = importlib.util.spec_from_file_location("imp", ROOT / "assets/icon-theme/tools/import-lumaui-icons.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = module.check()
        self.assertEqual(status, 0, err.getvalue())

    def test_share_export_upload_glyphs(self):
        for name in ("share-2", "share", "upload"):
            self.assertIn(name, _available())

    def test_prairie_and_notes_glyphs(self):
        # Prairie's droplet and Notes' "All notes" notebook (kit-requests notes-01).
        for name in ("droplet", "notebook"):
            self.assertIn(name, _available())

    def test_weather_condition_glyphs(self):
        for name in ("cloud-rain", "cloud-sun", "cloud-moon", "cloud-fog",
                     "cloud-lightning", "snowflake"):
            self.assertIn(name, _available())

    def test_every_named_glyph_exists(self):
        available = _available()
        missing: list[str] = []
        for path in LUMAUI_SOURCES + APP_SOURCES:
            text = path.read_text(encoding="utf-8", errors="ignore")
            names = set()
            lumaui_source = path in LUMAUI_SOURCES
            for pattern in PATTERNS if lumaui_source else APP_PATTERNS:
                names |= set(pattern.findall(text))
            if lumaui_source:
                names |= _table_glyphs(text)
            # Kit and gallery pages list glyphs in their own tables too.
            if "lumaui-gallery" in str(path):
                # PAGES: (key, title, glyph, family, module); toggles: (key, glyph, Label).
                names |= set(re.findall(r'''\(\s*"[a-z]+",\s*"[^"]+",\s*"([a-z0-9-]+)",\s*"(?:structure|action|content)"''', text))
                names |= set(re.findall(r'''\(\s*"[a-z-]+",\s*"([a-z0-9-]+)",\s*"[A-Z]''', text))
                names |= set(re.findall(r'''_button\("[^"]+",\s*"([a-z0-9-]+)"''', text))
            names.discard("")
            missing += [f"{path.relative_to(ROOT)}: {name}" for name in sorted(names - available)]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
