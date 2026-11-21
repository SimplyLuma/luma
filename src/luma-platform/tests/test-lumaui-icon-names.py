#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Every Lucide glyph LumaUI names resolves to an imported Prairie icon.

Scans luma_appkit and the gallery for the names given to MenuItem, Command,
image(), icon_name(), resolve(), IconOnlyButton and any ``icon=`` keyword,
including names bound from a literal table by a loop or comprehension
(``for direction, label, icon in (("left", "Move left", "arrow-left"), ...)``),
then checks each against the imported glyphs where the kit finds them. The Dock
tile's More menu is also built for real and every row's icon resolved.
"""
from __future__ import annotations

import ast
import os
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLATFORM = HERE.parent
SOURCES = [PLATFORM / "appkit/luma_appkit", PLATFORM / "tools/lumaui-gallery"]
LUCIDE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
#: Calls whose first positional argument is a glyph name.
FIRST_ARG = {"image", "icon_name", "resolve", "lumaui_icon", "IconOnlyButton"}
#: Calls whose glyph is a later positional argument: Command(id, label, run, icon).
POSITIONAL = {"Command": 3}
ICON_NAMES = {"icon", "glyph", "icon_name"}


def glyph_dirs() -> list[Path]:
    """Where the kit finds its glyphs (luma_appkit.icons.search_paths): LUMA_APPKIT_ICON_PATH, the folder
    beside the package (an installed or tarball kit), the checkout's Prairie theme."""
    sys.path.insert(0, str(PLATFORM / "appkit"))
    from luma_appkit import icons

    return icons.search_paths()


def glyph_file(name: str) -> Path | None:
    theme = name if name.endswith("-symbolic") else f"lumaui-{name}-symbolic"
    return next((d / f"{theme}.svg" for d in glyph_dirs() if (d / f"{theme}.svg").is_file()), None)


def _const(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _table_names(target: ast.AST, iterable: ast.AST) -> list[str]:
    """Glyphs a loop binds to an icon-named variable from a literal table of tuples."""
    if not isinstance(target, ast.Tuple) or not isinstance(iterable, (ast.Tuple, ast.List)):
        return []
    slots = [i for i, elt in enumerate(target.elts) if isinstance(elt, ast.Name) and elt.id in ICON_NAMES]
    found = []
    for row in iterable.elts:
        if isinstance(row, (ast.Tuple, ast.List)):
            for i in slots:
                if i < len(row.elts) and (value := _const(row.elts[i])):
                    found.append(value)
    return found


def named_glyphs(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if func in FIRST_ARG and node.args and (value := _const(node.args[0])):
                found.append((value, node.lineno))
            if func in POSITIONAL and len(node.args) > POSITIONAL[func] and (value := _const(node.args[POSITIONAL[func]])):
                found.append((value, node.lineno))
            for keyword in node.keywords:
                if keyword.arg == "icon" and (value := _const(keyword.value)):
                    found.append((value, node.lineno))
        elif isinstance(node, (ast.For, ast.comprehension)):
            found += [(value, getattr(node, "lineno", node.iter.lineno)) for value in _table_names(node.target, node.iter)]
    return [(name, line) for name, line in found if LUCIDE.match(name.removesuffix("-symbolic"))]


class IconNames(unittest.TestCase):
    def test_every_named_glyph_is_imported(self):
        self.assertTrue(glyph_dirs(), "no glyph folder found")
        missing = []
        for source in SOURCES:
            for path in sorted(source.rglob("*.py")):
                for name, line in named_glyphs(path):
                    # Theme names that are not LumaUI glyphs (Adwaita's, an app's own) are not ours to check.
                    if name.endswith("-symbolic") and not name.startswith("lumaui-"):
                        continue
                    if glyph_file(name) is None:
                        missing.append(f"{path.relative_to(PLATFORM)}:{line}: {name}")
        self.assertEqual(missing, [], "glyphs never imported (assets/icon-theme/tools/import-lumaui-icons.py)")

    def test_scanner_sees_loop_tables(self):
        tree = ast.parse('for d, label, icon in (("left", "Move left", "arrow-left"),):\n    pass\n')
        loop = tree.body[0]
        self.assertEqual(_table_names(loop.target, loop.iter), ["arrow-left"])

    def test_dock_more_menu_glyphs_resolve(self):
        os.environ.setdefault("GSETTINGS_BACKEND", "memory")
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        if not Gtk.init_check():
            self.skipTest("no display")
        sys.path.insert(0, str(PLATFORM / "appkit"))
        from luma_appkit import action_bubble, icons
        from luma_appkit.structure_dock import Dock, DockTile

        tiles = [DockTile(k, k.title(), "square", Gtk.Label(label=k), main=k == "c", closable=True)
                 for k in ("a", "b", "c", "d", "e")]
        dock = Dock(tiles, layout=Dock.split("h", 0.3, "a", Dock.split("h", 0.5,
                    Dock.split("v", 0.5, "b", Dock.split("v", 0.5, "c", "d")), "e")))
        window = Gtk.Window(child=dock, default_width=900, default_height=700)
        window.present()
        from gi.repository import GLib

        context = GLib.MainContext.default()
        import time

        end = time.monotonic() + 0.4
        while time.monotonic() < end:
            while context.pending():
                context.iteration(False)
            time.sleep(0.005)
        rows: list = []
        original = action_bubble.FloatingMenu

        class Capture:
            def __init__(self, items, **_):
                rows.extend(r for r in items if r is not None)

            def popup(self, *_):
                pass

        action_bubble.FloatingMenu = Capture
        try:
            for tile in tiles:
                tile._more(tile.more_button)
        finally:
            action_bubble.FloatingMenu = original
            window.destroy()
        self.assertTrue(any(r.label == "Move left" for r in rows))
        unresolved = sorted({r.icon for r in rows if r.icon and not icons.resolve(r.icon).startswith("lumaui-")})
        self.assertEqual(unresolved, [])


if __name__ == "__main__":
    unittest.main()
