#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from xml.etree import ElementTree


REPO_ROOT = Path(__file__).resolve().parents[2]
NOTES_ICON = REPO_ROOT / "src/prairie-core/data/org.projectluma.Notes.svg"
NOTES_SOURCE = REPO_ROOT / "src/prairie-core/prairie_apps/notes.py"
APPKIT = REPO_ROOT / "src/luma-platform/appkit"
APPKIT_MESON = REPO_ROOT / "src/luma-platform/meson.build"
PLATFORM_SPEC = REPO_ROOT / "packaging/rpm/luma-developer-platform.spec"
APPROVED_SHA256 = "a5d86f8bfd6b71d62c22995e4a9ae72c612b3e448c255f4332fa5a07d7217740"
DESIGN_CENTER_RENDER_ASSET_SHA256 = {
    "luma-format-bold-symbolic.svg": "4982111456fcc124c24e303d4a31170507dea8be0dc00653aa775abbc50f23bc",
    "luma-format-italic-symbolic.svg": "16ed557169505fe58ab7e46f44cf6a4d3234e22f1f38d634ee4dcf56f8d61f68",
    "luma-format-underline-symbolic.svg": "88adc18d5e6a26b9a4c64f265c31f6384b7689df67122a42e98a438d09fcb0d2",
    "luma-format-heading-symbolic.svg": "d92a56964ab22e4ab9a63291396a8e7f7a3bd38acdfc308aaad84806be7e588d",
    "luma-format-quote-symbolic.svg": "a7ac917a8e3afcc8fe88c8551c7dc8951944a77e7d04f8b71d624c16f41ee16b",
    "luma-format-list-symbolic.svg": "d3eb9d1fee5d3de0e10d645664c5dc5b63c41bde9567ace480fa69cdb0c6c64c",
    "luma-format-list-ordered-symbolic.svg": "0f46d4c04286d13c59bcade5d56fcca8dfa8aa8091de0a70d106c53485818b17",
    "luma-format-link-symbolic.svg": "9292683cc854c215e4b0e67387e9ef5f5a6c32276b094234c9ea5333f6067ee5",
    "luma-plus-symbolic.svg": "b72ca74024a789e041658d41a7ecd24f05c04cd2af1ec80984992e273393c4b9",
    "luma-chevron-down-symbolic.svg": "498bae42b42a8824749e60e14a030976f36ab5b886e975db18ef900732776e6d",
    "luma-window-minimize-symbolic.svg": "498bae42b42a8824749e60e14a030976f36ab5b886e975db18ef900732776e6d",
    "luma-window-maximize-symbolic.svg": "e6c86f1354e5bd9299a13638c0e42922b72e32070828ce51009aaa8e32544a34",
    "luma-window-close-symbolic.svg": "b945e51a3faaef374af4e537e3f3b7ca77a473f7d89af27916ea36b042da2b3f",
}


class NotesAssetTests(unittest.TestCase):
    def test_notes_icon_is_the_approved_v3_artwork(self) -> None:
        self.assertEqual(hashlib.sha256(NOTES_ICON.read_bytes()).hexdigest(), APPROVED_SHA256)

    def test_notes_uses_the_canonical_appkit_controls(self) -> None:
        # The formatting toolbar lives in its own module, shared by the page
        # and file windows; the icons it names are part of Notes all the same.
        source = NOTES_SOURCE.read_text(encoding="utf-8") + (
            NOTES_SOURCE.parent / "notes_editor.py").read_text(encoding="utf-8")
        self.assertIn('add_css_class("notes-new-button")', source)
        self.assertNotIn('add_css_class("notes-sidebar-header")', source)
        self.assertIn("SIDEBAR_WIDTH = 178", source)
        self.assertIn("PRIMARY_ISLAND_GAP = 9", source)
        self.assertIn("sidebar.set_margin_end(PRIMARY_ISLAND_GAP)", source)
        self.assertIn("_last_saved_label(self.current.modified_at)", source)
        self.assertIn('_apply_named_color(tag, "luma_blue")', source)
        self.assertNotIn('foreground="#527fae"', source)
        self.assertNotIn('foreground="#757e87"', source)
        for icon in (
            "bold", "italic", "underline", "heading", "quote", "list",
            "list-ordered", "link",
        ):
            name = f"luma-format-{icon}-symbolic"
            self.assertIn(name, source)
            self.assertTrue((APPKIT / "icons" / f"{name}.svg").is_file())

    def test_appkit_has_a_real_dark_appearance_provider(self) -> None:
        dark = (APPKIT / "luma-appkit-dark.css").read_text(encoding="utf-8")
        dark_tokens = (APPKIT / "luma-appkit-dark-tokens.css").read_text(encoding="utf-8")
        base = (APPKIT / "luma-appkit.css").read_text(encoding="utf-8")
        widgets = (APPKIT / "luma_appkit/widgets.py").read_text(encoding="utf-8")
        self.assertIn("luma-appkit-dark-tokens.css", dark)
        self.assertIn('connect("notify::dark", load_for_appearance)', widgets)
        self.assertIn('"luma-appkit-dark.css"', widgets)
        self.assertIn("@define-color luma_menu #252a30;", dark_tokens)
        self.assertIn("background: @luma_menu;", base)

    def test_appkit_icons_are_the_locked_design_center_render_assets(self) -> None:
        # GTK's symbolic mask ignores SVG strokes. These production assets are
        # the Design Center vectors with strokes expanded to equivalent fills.
        for name, expected in DESIGN_CENTER_RENDER_ASSET_SHA256.items():
            icon = APPKIT / "icons" / name
            self.assertEqual(hashlib.sha256(icon.read_bytes()).hexdigest(), expected, name)
            ElementTree.parse(icon)

    def test_appkit_symbolic_icons_install_in_a_declared_hicolor_directory(self) -> None:
        meson = APPKIT_MESON.read_text(encoding="utf-8")
        spec = PLATFORM_SPEC.read_text(encoding="utf-8")
        self.assertIn("icons/hicolor/scalable/actions", meson)
        self.assertNotIn("icons/hicolor/symbolic/actions", meson)
        self.assertIn("icons/hicolor/scalable/actions/luma-*.svg", spec)
        self.assertNotIn("icons/hicolor/symbolic/actions/luma-*.svg", spec)


if __name__ == "__main__":
    unittest.main()
