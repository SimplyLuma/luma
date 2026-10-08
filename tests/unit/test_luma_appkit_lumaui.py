# SPDX-License-Identifier: Apache-2.0
"""LumaUI F1: tokens, sheets, module layout and the small parts.

The first half reads source and generated files and runs anywhere. The second
half builds real widgets on stock GTK 4 and runs wherever a display is
available (it never presents a window); it is skipped otherwise.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPKIT = ROOT / "src/luma-platform/appkit"
KIT = APPKIT / "luma_appkit"
BASE = APPKIT / "luma-appkit-base.css"
TOKEN_SHEETS = [APPKIT / f"luma-appkit{s}-tokens.css" for s in ("", "-dark", "-frost", "-glass", "-high-contrast")]
ACTIONS = ROOT / "assets/icon-theme/Prairie/symbolic/actions"
TOKENS = json.loads((ROOT / "config/shared/design-tokens.json").read_text())["lumaui"]

F1_PARTS = ("Type scale", "Count badge", "Category pill", "Stacked button", "Layers (scrim, modal card, drawer)",
            "Destructive dialog", "Toast")
LUMAUI_MODULES = ("lumaui", "icons", "structure_layers", "action_toast", "action_stack", "action_dialog",
                  "content_badges", "content_type")
# F3's action and content modules (tests/unit/test_luma_appkit_lumaui_f3.py).
F3_MODULES = ("action_center", "action_bubble", "content_file", "content_contact", "content_cards",
              "content_place", "content_field", "content_message")
LUMAUI_MODULES += F3_MODULES
STUBS = ("structure_details", "structure_sidebar", "structure_table", "structure_trail", "structure_placement",
         "structure_drawer")


def _generator():
    spec = importlib.util.spec_from_file_location("gen", ROOT / "scripts/developer/generate-luma-platform-tokens.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lumaui_css() -> str:
    text = BASE.read_text()
    return text[text.index("/* ══ LumaUI (v70)"):]


def _all(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _all(child)
        child = child.get_next_sibling()


class Tokens(unittest.TestCase):
    def test_generated_sheets_and_python_are_current(self):
        gen = _generator()
        expected = {
            APPKIT / "luma-appkit-tokens.css": gen.render_appkit("light"),
            APPKIT / "luma-appkit-dark-tokens.css": gen.render_appkit("dark"),
            APPKIT / "luma-appkit-frost-tokens.css": gen.render_translucent_appkit("frost"),
            APPKIT / "luma-appkit-glass-tokens.css": gen.render_translucent_appkit("glass"),
            APPKIT / "luma-appkit-high-contrast-tokens.css": gen.render_high_contrast(),
            KIT / "lumaui_tokens.py": gen.render_lumaui_python(),
        }
        for path, text in expected.items():
            self.assertEqual(path.read_text(), text, f"{path.name} is stale: run generate-luma-platform-tokens.py")

    def test_every_colour_and_metric_the_parts_use_is_defined_in_every_treatment(self):
        css = _lumaui_css()
        colours = set(re.findall(r"@(luma_[a-z0-9_]+)", css))
        variables = set(re.findall(r"var\((--lumaui-[a-z0-9-]+)\)", css))
        self.assertTrue(colours and variables)
        for sheet in TOKEN_SHEETS:
            # Treatment sheets override the common light layer installed first.
            text = TOKEN_SHEETS[0].read_text() + "\n" + sheet.read_text()
            defined = set(re.findall(r"@define-color (luma_[a-z0-9_]+)", text))
            declared = set(re.findall(r"(--lumaui-[a-z0-9-]+):", text))
            self.assertEqual(sorted(colours - defined), [], f"{sheet.name} lacks colours")
            self.assertEqual(sorted(variables - declared), [], f"{sheet.name} lacks metrics")

    def test_categories_and_type_roles_match_the_spec(self):
        self.assertEqual(TOKENS["category"]["order"], ["create", "work", "media", "play", "tools"])
        self.assertEqual({k: v for k, v in TOKENS["category"]["hues"].items()},
                         {"create": 25, "work": 250, "media": 330, "play": 150, "tools": 200})
        roles = [k for k, v in TOKENS["type_scale"].items() if isinstance(v, dict)]
        self.assertEqual(roles, ["display", "hero", "title_1", "title_2", "lead", "body", "caption", "label", "numeric"])
        self.assertTrue(TOKENS["type_scale"]["display"]["tabular"] and TOKENS["type_scale"]["numeric"]["tabular"])
        css = _lumaui_css()
        for key in TOKENS["category"]["order"]:
            self.assertIn(f"label.lumaui-category.{key} {{ background: @luma_cat_{key}_bg; color: @luma_cat_{key}_ink; }}", css)
        for role in roles:
            self.assertIn(f".lumaui-t-{role.replace('_', '-')} {{", css)
        for rule in (".lumaui-t-display {", ".lumaui-t-numeric {", "label.lumaui-count {"):  # tabular figures
            block = css[css.index(rule):]
            self.assertIn('font-feature-settings: "tnum"', block[:block.index("}")], rule)

    def test_category_text_passes_aa_in_light_and_dark(self):
        def luminance(hex_colour: str) -> float:
            channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        for family in ("light", "dark"):
            colours = TOKENS["colors"][family]
            for key in TOKENS["category"]["order"]:
                a, b = luminance(colours[f"cat_{key}_bg"]), luminance(colours[f"cat_{key}_ink"])
                ratio = (max(a, b) + 0.05) / (min(a, b) + 0.05)
                self.assertGreaterEqual(ratio, 6.0, f"{family} {key}: {ratio:.2f}:1")

    def test_motion_is_the_v70_set(self):
        motion = TOKENS["motion"]
        self.assertEqual(motion["ease"], [0.3, 0.7, 0.2, 1])
        self.assertEqual(motion["spring"], [0.3, 1.3, 0.5, 1])
        self.assertEqual((motion["toast_ms"], motion["toast_undo_ms"], motion["toast_in_ms"]), (2400, 4000, 300))
        self.assertEqual(TOKENS["count_badge"]["attention_cap"], 99)


class Layout(unittest.TestCase):
    def test_modules_exist_with_docstrings_and_all(self):
        for name in LUMAUI_MODULES + STUBS:
            path = KIT / f"{name}.py"
            self.assertTrue(path.is_file(), name)
            tree = ast.parse(path.read_text())
            self.assertTrue(ast.get_docstring(tree), f"{name} has no docstring")
        for name in STUBS:
            text = (KIT / f"{name}.py").read_text()
            if re.search(r"^__all__: list\[str\] = \[\]$", text, re.M):  # still a stub: it says who builds it
                self.assertRegex(text, r"\((F2|F3) builds this")

    def test_every_f1_part_has_a_css_banner(self):
        css = _lumaui_css()
        for part in F1_PARTS:
            self.assertIn(f"/* LumaUI: {part} */", css)

    def test_reduced_motion_swaps_movement_for_a_fade(self):
        css = _lumaui_css()
        block = css[css.index("@media (prefers-reduced-motion: reduce)"):]
        for selector in ("box.lumaui-toast", ".lumaui-modal", ".lumaui-modal.nudge", "button.lumaui-stacked-button:hover"):
            self.assertIn(selector, block)
        self.assertIn("transform: none", block)

    def test_parts_run_on_stock_gtk(self):
        # No Luma-only typelib, no import of widgets.py (which needs one) at import time.
        for name in LUMAUI_MODULES + STUBS:
            code = (KIT / f"{name}.py").read_text()
            self.assertNotRegex(code, r"require_version\(\"(LumaUI|LumaAppearance)\"", name)
            top = [n for n in ast.parse(code).body if isinstance(n, (ast.Import, ast.ImportFrom))]
            self.assertFalse(any(isinstance(n, ast.ImportFrom) and n.module == "widgets" for n in top), name)

    def test_parts_name_no_colours_in_python(self):
        # A literal colour names its channels; lumaui.oklch_rgba formats colours it computes from tokens.
        literal = re.compile(r"""["']#[0-9a-fA-F]{3,8}["']|rgba?\(\s*[\d.]""")
        for name in LUMAUI_MODULES:
            self.assertIsNone(literal.search((KIT / f"{name}.py").read_text()), name)

    def test_applications_do_not_restyle_kit_parts(self):
        # A design change must reach every app: no app sheet selects a LumaUI part. The kit's own sheets
        # are the shared ones (appkit) and LumaUI-1's (C), which draws the C kit's own layer-host drawers.
        kit_sheets = ("luma-platform/appkit", "luma-platform/ui/luma-ui.css")
        sheets = list((ROOT / "src").rglob("*.css"))
        offenders = []
        for path in sheets:
            if any(k in str(path) for k in kit_sheets):
                continue
            css = re.sub(r"/\*.*?\*/", "", path.read_text(errors="ignore"), flags=re.S)
            for selectors, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
                for selector in selectors.split(","):
                    if not re.search(r"\.lumaui-[a-z]", selector):
                        continue
                    # App roots and app-specific classes scope geometry adapters;
                    # a bare kit selector would change every consumer of the part.
                    scoped = re.search(r"#[\w-]+|\.(?!(?:lumaui|luma)-)[a-z][\w-]+", selector)
                    if not scoped or re.search(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(", declarations):
                        offenders.append(f"{path.relative_to(ROOT)}: {selector.strip()}")
        self.assertEqual(offenders, [])

    def test_gallery_is_a_dev_tool_that_does_not_ship(self):
        gallery = ROOT / "src/luma-platform/tools/lumaui-gallery/lumaui_gallery.py"
        self.assertTrue(gallery.is_file())
        for packaging in (ROOT / "packaging/rpm/luma-developer-platform.spec", ROOT / "src/luma-platform/meson.build"):
            self.assertNotIn("lumaui-gallery", packaging.read_text())

    def test_api_level_is_declared(self):
        self.assertRegex((KIT / "lumaui.py").read_text(), r"(?m)^LUMAUI_API_LEVEL = \d+$")


# ── widgets on stock GTK ────────────────────────────────────────────────────

sys.path.insert(0, str(APPKIT))
try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gtk

    HAVE_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
except (ImportError, ValueError):
    HAVE_DISPLAY = False


@unittest.skipUnless(HAVE_DISPLAY, "needs GTK 4 and a display")
class Parts(unittest.TestCase):
    def setUp(self):
        from luma_appkit import content_badges, action_stack, action_toast, action_dialog, content_type, structure_layers
        self.badges, self.stack, self.toast = content_badges, action_stack, action_toast
        self.dialog, self.type, self.layers = action_dialog, content_type, structure_layers

    def _window(self, child=None):
        window = Gtk.Window()
        window.set_child(child or Gtk.Box())
        return window

    def test_count_text(self):
        count_text = self.badges.count_text
        self.assertEqual([count_text(0), count_text(-3), count_text(7), count_text(1284)], ["", "", "7", "1,284"])
        self.assertEqual([count_text(99, True), count_text(100, True), count_text(1284, True)], ["99", "99+", "99+"])

    def test_count_badge_hides_at_zero_and_marks_attention(self):
        badge = self.badges.CountBadge(0)
        self.assertFalse(badge.get_visible())
        badge.set_count(120)
        self.assertTrue(badge.get_visible())
        self.assertEqual(badge.get_label(), "120")
        badge.props.attention = True
        self.assertEqual(badge.get_label(), "99+")
        self.assertTrue(badge.has_css_class("attention") and badge.has_css_class("lumaui-count"))

    def test_category_pill_takes_the_category_colour_only(self):
        pill = self.badges.CategoryPill("media")
        self.assertEqual(pill.get_label(), "Media")
        self.assertTrue(pill.has_css_class("media"))
        pill.set_category("education")
        self.assertEqual((pill.get_label(), pill.category), ("Education", "work"))
        self.assertFalse(pill.has_css_class("media"))

    def test_type_roles(self):
        label = Gtk.Label()
        self.type.apply_type(label, "caption")
        self.type.apply_type(label, "title_1")
        self.assertTrue(label.has_css_class("lumaui-t-title-1"))
        self.assertFalse(label.has_css_class("lumaui-t-caption"))
        with self.assertRaises(ValueError):
            self.type.apply_type(label, "headline")
        clock = self.type.TypeLabel("9:41", role="display", unit=":07 AM")
        self.assertTrue(clock.unit.get_visible() and clock.label.has_css_class("lumaui-t-display"))

    def test_display_role_uses_reference_line_box_and_releases_it_on_role_change(self):
        from gi.repository import Pango
        from luma_appkit.rows_type import type_metrics

        label = Gtk.Label(label="0")
        self.type.apply_type(label, "display")
        line_heights = [attr.as_int().value for attr in label.get_attributes().get_attributes()
                        if attr.klass.type == Pango.AttrType.ABSOLUTE_LINE_HEIGHT]
        self.assertEqual(line_heights, [round(type_metrics("display")["line_height"] * Pango.SCALE)])
        self.assertEqual(type_metrics("display")["line_height"], 79.2)
        self.type.apply_type(label, "numeric")
        self.assertFalse(any(attr.klass.type in (Pango.AttrType.LINE_HEIGHT, Pango.AttrType.ABSOLUTE_LINE_HEIGHT)
                             for attr in label.get_attributes().get_attributes()))

    def test_display_role_line_box_survives_app_window_body_default(self):
        from gi.repository import GLib
        from luma_appkit.rows_type import type_metrics

        display = Gdk.Display.get_default()
        tokens = Gtk.CssProvider()
        base = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(display, tokens, Gtk.STYLE_PROVIDER_PRIORITY_THEME)
        Gtk.StyleContext.add_provider_for_display(display, base, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        window = Gtk.Window()
        try:
            tokens.load_from_path(str(APPKIT / "luma-appkit-tokens.css"))
            base.load_from_path(str(BASE))
            window.add_css_class("luma-app-window")
            label = self.type.apply_type(Gtk.Label(label="0"), "display")
            window.set_child(label)
            window.present()
            context = GLib.MainContext.default()
            for _ in range(30):
                while context.pending():
                    context.iteration(False)
            self.assertTrue(label.get_mapped())
            self.assertAlmostEqual(label.measure(Gtk.Orientation.VERTICAL, -1).natural,
                                   type_metrics("display")["line_height"], delta=1.5)
        finally:
            window.close()
            Gtk.StyleContext.remove_provider_for_display(display, base)
            Gtk.StyleContext.remove_provider_for_display(display, tokens)

    def test_stacked_buttons_come_in_pairs_or_trios(self):
        make = lambda n: [self.stack.StackedButton("pencil", f"B{i}") for i in range(n)]
        for bad in (1, 4):
            with self.assertRaises(ValueError):
                self.stack.StackedButtons(make(bad))
        row = self.stack.StackedButtons(make(3), small=True)
        self.assertTrue(row.get_homogeneous() and row.has_css_class("lumaui-stack-small"))
        danger = self.stack.StackedButton("trash-2", "Delete", danger=True)
        self.assertTrue(danger.has_css_class("danger"))

    def test_toast_one_at_a_time_with_the_right_timeouts(self):
        Toast, host = self.toast.Toast, self.toast.ToastHost(Gtk.Box())
        window = self._window(host)
        first = Toast.show(host, "Copied", kind="copied")
        self.assertEqual(first.timeout_ms, 2400)
        undone = []
        second = Toast.show(host, "Conversation deleted", kind="deleted", undo=lambda: undone.append(1))
        self.assertEqual(second.timeout_ms, 4000)
        self.assertIsNone(first._host, "a new toast replaces the one showing")
        self.assertIs(second.get_parent(), host)
        second._on_undo(None)
        self.assertEqual(undone, [1])
        fixed = []
        failure = Toast.show(host, "Google signed you out", kind="error", action=("Sign in again", lambda: fixed.append(1)))
        words = [w.get_label() for w in _all(failure) if isinstance(w, Gtk.Button)]
        self.assertEqual(words, ["Sign in again"])
        self.assertEqual(failure.timeout_ms, 4000)
        failure._on_undo(None)
        self.assertEqual(fixed, [1])
        with self.assertRaises(ValueError):
            Toast.show(host, "Both", undo=lambda: None, action=("Fix", lambda: None))
        busy = Toast.show(host, "Preparing export…", busy=True)
        self.assertEqual(busy.timeout_ms, 0)
        busy.dismiss()
        with self.assertRaises(ValueError):
            Toast.show(host, "Hello", kind="celebration")
        self.assertEqual(Gtk.AccessibleRole.STATUS, busy.get_accessible_role())
        window.destroy()

    def test_toast_without_a_host_uses_the_window(self):
        button = Gtk.Button()
        window = self._window(button)
        toast = self.toast.Toast.show(button, "Saved", kind="saved")
        host = window.get_child()
        self.assertIsInstance(host, self.layers.LayerHost)
        self.assertIs(toast.get_parent(), host)
        self.assertIs(self.layers.LayerHost.install(window), host, "installs once")
        window.destroy()

    def test_destructive_dialog_refuses_vague_words(self):
        Dialog = self.dialog.DestructiveDialog
        with self.assertRaises(ValueError):
            Dialog(title="Are you sure?", body="x")
        with self.assertRaises(ValueError):
            Dialog(title="Delete it?", body="x", action="OK")

    def test_destructive_dialog_cancel_escape_confirm(self):
        Dialog = self.dialog.DestructiveDialog
        button = Gtk.Button()
        window = self._window(button)
        seen = []
        dialog = Dialog.ask(button, title="Delete this conversation?", body="It’s removed from this computer.",
                            option="Also delete for everyone", on_confirm=lambda also: seen.append(("ok", also)),
                            on_cancel=lambda: seen.append("cancel"))
        host = window.get_child()
        self.assertIs(host.modal, dialog.handle)
        self.assertEqual(dialog.get_accessible_role(), Gtk.AccessibleRole.ALERT_DIALOG)
        self.assertFalse(host.get_child().get_can_focus(), "the window behind can't take focus")
        # Tab stays inside: option → Cancel → action → option.
        order = self.layers._focusables(dialog)
        self.assertEqual(order, [dialog.option, dialog.cancel_button, dialog.action_button])
        self.assertTrue(host._modal_key(None, Gdk.KEY_Escape, 0, 0, dialog.handle))
        self.assertEqual(seen, ["cancel"])
        self.assertIsNone(host.modal)
        self.assertTrue(host.get_child().get_can_focus())
        again = Dialog.ask(button, title="Delete this conversation?", body="Gone.", option="Also delete for everyone",
                           on_confirm=lambda also: seen.append(("ok", also)))
        again.option.set_active(True)
        again.action_button.emit("clicked")
        self.assertEqual(seen[-1], ("ok", True))
        window.destroy()

    def test_destructive_dialog_takes_the_bar_frame_at_phone_width(self):
        """v71 lConfirm on a phone: the bar's frame (16 a side, 34 up), words left, no icon, two buttons side by side."""
        Dialog = self.dialog.DestructiveDialog
        stage = self.layers.LayerHost(Gtk.Button(), name="window")
        window = self._window(stage)
        stage.get_width = lambda: 375  # measured as a phone-width window would be
        dialog = Dialog.ask(stage.get_child(), title="Leave Launch crew?", body="You won’t get new messages.",
                            action="Leave", icon="log-out")
        self.assertTrue(dialog.has_css_class("frame"))
        self.assertFalse(dialog.has_css_class("drawer") or dialog.handle.drawer, "no grabber; the scrim nudges")
        self.assertEqual(dialog.buttons.get_orientation(), Gtk.Orientation.HORIZONTAL)
        self.assertIs(dialog.buttons.get_first_child(), dialog.cancel_button, "Cancel, then the red action")
        self.assertEqual((dialog.get_margin_start(), dialog.get_margin_end(), dialog.get_margin_bottom()), (16, 16, 34))
        self.assertEqual(dialog.get_valign(), Gtk.Align.END)
        self.assertEqual(dialog.title_label.get_xalign(), 0.0)
        window.destroy()

if __name__ == "__main__":
    unittest.main()
