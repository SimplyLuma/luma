# SPDX-License-Identifier: Apache-2.0
"""The kit and the applications draw only what is theirs.

LumaUI owns the window frame, the identity, the window controls, the gutter
and the island (ADR-052): one toolkit sheet, lumaui-toolkit.css, states them,
above whatever libadwaita the system has, so a Luma app looks the same on stock
and on Luma's patched libraries. The kit's component sheets own what is inside
an island. An application composes. This test fails the moment any other
stylesheet in the tree restates a settled measurement, because that
restatement is exactly how twelve applications came to disagree about the
corner of a window.
"""

from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Rules only the toolkit may state.
FRAME_SELECTORS = re.compile(
    r"^\s*(?:[^{}\n]*\b(?:windowcontrols|headerbar|\.luma-titlebar|\.luma-window-titlebar|"
    r"\.luma-identity-[a-z-]+|\.luma-window-body|\.luma-window-root|\.luma-window-controls)\b[^{}\n]*)\{",
    re.M,
)
# An island's own appearance: radius, ring, shadow, gutter. Only a rule whose
# subject is the island counts: `.luma-island scrolledwindow > scrollbar`
# styles a scrollbar inside an island, not the island.
ISLAND_APPEARANCE = re.compile(
    r"^\s*[^{}\n]*\.luma-(?:island|pane)\b[^{}\n\s>+~,]*\s*\{[^}]*?(?:border-radius|box-shadow|margin|padding)\s*:",
    re.M | re.S,
)
COLOUR_LITERAL = re.compile(r"(?<![\w-])#[0-9a-fA-F]{3,8}\b|\brgba?\(")
# A line may keep a literal if it says why: `/* literal: <reason> */`.
ANNOTATED = re.compile(r"/\*\s*literal:.*?\*/")
DEFINE_COLOR = re.compile(r"^\s*@define-color\s", re.M)

KIT_SHEETS = (
    "src/luma-platform/appkit/luma-appkit-base.css",
    "src/luma-platform/appkit/luma-appkit.css",
    "src/luma-platform/appkit/luma-appkit-dark.css",
)
STRUCTURE_SHEET = "src/luma-platform/ui/luma-ui.css"
# LumaUI's own toolkit: the one place the frame, the title row, the identity,
# the controls, the gutter and the islands are stated; and its palette, a
# token sheet, where the literals those rules name live.
TOOLKIT_SHEET = "src/luma-platform/appkit/lumaui-toolkit.css"
PALETTE_SHEET = "src/luma-platform/appkit/lumaui-palette.css"
ANY_COLOUR_LITERAL = re.compile(r"(?<![\w-])#[0-9a-fA-F]{3,8}\b|\brgba?\(", re.I)

# Applications in this repository. Each is added here as it is brought across
# to the kit; an application not listed is not yet claimed to conform.
APPLICATION_SHEETS: tuple[str, ...] = (
    "src/luma-tide/data/tide.css",
    "src/prairie-core/style/prairie.css",
    "src/prairie-core/style/notes.css",
    "src/prairie-core/style/contacts.css",
    "src/prairie-core/style/calendar.css",
    "src/prairie-core/style/messages.css",
    "src/prairie-core/style/phone.css",
    "src/luma-layouts/style/layouts.css",
    "src/luma-darkroom/style/darkroom.css",
    "src/luma-reel/style/reel.css",
    "src/prairie-core/style/camera.css",
    "src/prairie-core/style/photos.css",
)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _unannotated(text: str) -> str:
    """The sheet with every annotated line removed."""
    return "\n".join(line for line in text.splitlines() if not ANNOTATED.search(line))


def _frame_rules(text: str) -> list[str]:
    """Inspect the rule's subject: a frame ancestor does not make its child a frame."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    found = []
    for match in FRAME_SELECTORS.finditer(text):
        selectors = match.group(0).rstrip("{").strip().split(",")
        for selector in selectors:
            subject = re.split(r"\s+|[>+~]", selector.strip())[-1]
            if FRAME_SELECTORS.search(subject + " {"):
                found.append(selector.strip() + " {")
    return found


class KitStatesNoFrame(unittest.TestCase):
    def test_frame_subject_checker_rejects_frame_but_accepts_component_descendant(self):
        self.assertEqual(_frame_rules("headerbar.luma-titlebar { margin: 0; }"), ["headerbar.luma-titlebar {"])
        self.assertEqual(_frame_rules("headerbar.luma-titlebar box.lumaui-corner { margin: 0; }"), [])

    def test_colour_checker_reads_declarations_instead_of_comments(self):
        css = re.sub(r"/\*.*?\*/", "", "/* light #191b1e */ label { color: #ffffff; }", flags=re.S)
        self.assertEqual(COLOUR_LITERAL.findall(css), ["#ffffff"])

    def test_kit_sheets_declare_no_frame_rules(self):
        for sheet in KIT_SHEETS:
            text = _read(sheet)
            found = _frame_rules(text)
            self.assertEqual(found, [], f"{sheet} states frame rules the toolkit owns: {found}")

    def test_kit_sheets_declare_no_island_appearance(self):
        for sheet in KIT_SHEETS:
            text = _read(sheet)
            found = [m.group(0).split("{")[0].strip() for m in ISLAND_APPEARANCE.finditer(text)]
            self.assertEqual(found, [], f"{sheet} restates an island's appearance: {found}")

    def test_kit_sheets_name_colours(self):
        # The token sheets are the one place a literal may appear.
        for sheet in KIT_SHEETS:
            text = _read(sheet)
            # Shadows are the toolkit's now, so no rgba() should remain either.
            literals = [m.group(0) for m in COLOUR_LITERAL.finditer(re.sub(r"/\*.*?\*/", "", _unannotated(text), flags=re.S))]
            self.assertEqual(literals, [], f"{sheet} writes colour instead of naming it: {literals[:6]}")

    def test_kit_widgets_draw_no_corner(self):
        widgets = _read("src/luma-platform/appkit/luma_appkit/widgets.py")
        for forbidden in ("Gtk.WindowControls", "class WindowControls", "_draw_control_glyph",
                          "luma-no-native-surfaces"):
            self.assertNotIn(forbidden, widgets)
        self.assertFalse((ROOT / "src/luma-platform/ui/luma-window-controls.c").exists(),
                         "the C kit must not carry its own window controls")

    def test_one_structural_sheet_owns_translucent_title_ancestry(self):
        structure = _read(STRUCTURE_SHEET)
        for selector in (
            "toolbarview.luma-window-toolbar-view",
            "toolbarview.luma-window-toolbar-view > .top-bar",
            "headerbar.luma-titlebar",
        ):
            self.assertIn(selector, structure)
        self.assertIn("background: transparent", structure)

        python_kit = _read("src/luma-platform/appkit/luma_appkit/widgets.py")
        c_kit = _read("src/luma-platform/ui/luma-ui-init.c")
        self.assertIn('"luma-ui.css"', python_kit)
        self.assertIn("Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2", python_kit)
        self.assertIn("GTK_STYLE_PROVIDER_PRIORITY_APPLICATION + 2U", c_kit)


class LumaUIOwnsTheFrame(unittest.TestCase):
    def test_the_toolkit_sheet_states_the_frame(self):
        toolkit = _read(TOOLKIT_SHEET)
        selectors = re.findall(r"([^{}]+)\{", toolkit)
        for part in ("headerbar.luma-titlebar", ".luma-window-body", "windowcontrols",
                     ".luma-identity-button", "window.luma-app-window"):
            self.assertTrue(any(part in rule for rule in selectors), f"the toolkit sheet does not state {part}")
        self.assertTrue(ISLAND_APPEARANCE.search(toolkit), "the toolkit sheet does not state the island")

    def test_the_toolkit_sheet_names_its_colours(self):
        toolkit = _unannotated(_read(TOOLKIT_SHEET))
        self.assertEqual([m.group(0) for m in ANY_COLOUR_LITERAL.finditer(toolkit)], [],
                         "the toolkit sheet writes colour instead of naming it")
        self.assertEqual(DEFINE_COLOR.findall(toolkit), [], "colours are defined in the palette")
        palette = _read(PALETTE_SHEET)
        for name in set(re.findall(r"@(lumaui_[a-z0-9_]+)", toolkit)):
            self.assertIn(f"@define-color {name} ", palette, f"{name} is named but never defined")

    def test_the_palette_is_generated_from_the_tokens(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "generate_tokens", ROOT / "scripts/developer/generate-luma-platform-tokens.py")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        self.assertEqual(_read(PALETTE_SHEET), generator.render_lumaui_palette(),
                         "lumaui-palette.css is stale: run generate-luma-platform-tokens.py")

    def test_the_window_sheets_say_where_they_come_from(self):
        # Both restate what patches/libadwaita adds to libadwaita's stylesheet.
        for sheet in (TOOLKIT_SHEET, PALETTE_SHEET):
            head = _read(sheet)[:400]
            self.assertTrue(head.startswith("/* SPDX-License-Identifier: LGPL-2.1-or-later"), sheet)
            self.assertIn("derived from libadwaita", head, sheet)

    def test_the_window_sheets_are_balanced(self):
        for sheet in (TOOLKIT_SHEET, PALETTE_SHEET):
            text = re.sub(r"/\*.*?\*/", "", _read(sheet), flags=re.S)
            depth = 0
            for char in text:
                depth += {"{": 1, "}": -1}.get(char, 0)
                self.assertGreaterEqual(depth, 0, sheet)
            self.assertEqual(depth, 0, f"{sheet}: a block is never closed")

    def test_modals_dim_the_window_below_its_title_row(self):
        # v70: a dialog dims the window below its title bar. AppWindow's layer
        # host wraps the content under the title row, and LayerHost.install
        # uses it rather than wrapping the whole window.
        widgets = _read("src/luma-platform/appkit/luma_appkit/widgets.py")
        self.assertIn('self.layer_host = LayerHost(self.sheet_layer, name="window")', widgets)
        self.assertIn("view.set_content(self.layer_host)", widgets)
        layers = _read("src/luma-platform/appkit/luma_appkit/structure_layers.py")
        self.assertIn('getattr(window, "layer_host", None)', layers)

    def test_both_kits_load_it_above_libadwaita(self):
        python_kit = _read("src/luma-platform/appkit/luma_appkit/window_frame.py")
        c_kit = _read("src/luma-platform/ui/luma-ui-init.c")
        self.assertIn("Gtk.STYLE_PROVIDER_PRIORITY_THEME + 1", python_kit)
        for sheet in ("lumaui-palette.css", "lumaui-toolkit.css"):
            self.assertIn(sheet, python_kit)
            self.assertIn(sheet, c_kit)
            self.assertIn(sheet, _read("src/luma-platform/ui/luma-ui.gresource.xml"))
        self.assertIn("GTK_STYLE_PROVIDER_PRIORITY_THEME + 1U", c_kit)

    def test_the_kit_builds_the_identity_the_library_stands_aside_for(self):
        # Luma's patched libadwaita builds its own identity only for a header
        # bar with no `luma-identity-button`: carrying that class is what keeps
        # a patched system from drawing a second pill.
        self.assertIn('"luma-identity-button"', _read("src/luma-platform/appkit/luma_appkit/window_frame.py"))
        self.assertIn('"luma-identity-button"', _read("src/luma-platform/ui/luma-application-window.c"))
        widgets = _read("src/luma-platform/appkit/luma_appkit/widgets.py")
        self.assertIn("window_frame.WindowIdentity(", widgets)
        self.assertIn("window_frame.own_window_frame(", widgets)


class ApplicationsCompose(unittest.TestCase):
    def test_application_sheets_conform(self):
        for sheet in APPLICATION_SHEETS:
            text = _read(sheet)
            self.assertEqual([m.group(0).strip() for m in FRAME_SELECTORS.finditer(text)], [],
                             f"{sheet} states frame rules")
            self.assertEqual([m.group(0).split('{')[0].strip() for m in ISLAND_APPEARANCE.finditer(text)], [],
                             f"{sheet} restates an island")
            self.assertEqual([m.group(0) for m in DEFINE_COLOR.finditer(text)
                              if not ANNOTATED.search(text[m.start():text.find("\n", m.start())])], [],
                             f"{sheet} defines its own colours")
            self.assertEqual([m.group(0) for m in COLOUR_LITERAL.finditer(re.sub(r"/\*.*?\*/", "", _unannotated(text), flags=re.S))], [],
                             f"{sheet} writes colour instead of naming it")


if __name__ == "__main__":
    unittest.main()
