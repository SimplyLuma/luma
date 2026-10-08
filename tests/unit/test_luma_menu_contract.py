# SPDX-License-Identifier: Apache-2.0
"""The menu's measured contract.

Every menu in Luma is the toolkit's `GtkPopoverMenu`, drawn to the numbers in
docs/design/context-menu-contract.md by the libadwaita stylesheet (patch
0024). The kit's part is the bridge from the command model: a registry
becomes a menu model with the right sections, shortcuts, states and custom
rows. These tests hold both halves to the contract: the bridge structurally,
on any display; the drawn numbers only where Luma's toolkit is installed,
since they live in the toolkit and not in the kit.

They need a display and skip cleanly without one.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APPKIT_ROOT = REPO_ROOT / "src/luma-platform/appkit"
TOOLKIT_PATCH = REPO_ROOT / "patches/libadwaita/0024-luma-menu-contract.patch"
sys.path.insert(0, str(APPKIT_ROOT))

# What each variant renders at, in pixels, ring to ring, and the padding its
# container carries — a dock tile's menu and a submenu are tighter.
VARIANT_WIDTHS = {"app": 220, "desktop": 220, "dock": 186, "submenu": 220}
VARIANT_PADDING = {"app": 8, "desktop": 8, "dock": 6, "submenu": 6}
ROW_HEIGHT = 32
CONTAINER_RADIUS = 15
CONTAINER_PADDING = 8


def _descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _descendants(child)
        child = child.get_next_sibling()


def _namespaces():
    """The toolkit namespaces, without opening a display.

    Building the menu model is arithmetic on a `Gio.Menu`: it needs the GTK
    typelib for the kit to import, but no display and no `Gtk.init`. Keeping
    the two apart is why the model half of the contract runs on a build
    machine at all; requiring a display for all of it meant ten tests that
    printed OK and proved nothing.
    """
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk
    return Gtk, Adw


def _kit():
    """The toolkit, on a display, or a skip that says there is not one.

    `Gtk.init_check()` is not that question: on GTK 4 in a headless container
    it returns True with no default display, and the next `Adw.init()` then
    takes the whole suite down with a segmentation fault. The display itself
    is the only honest answer.
    """
    Gtk, Adw = _namespaces()
    from gi.repository import Gdk
    Gtk.init_check()
    if Gdk.Display.get_default() is None:
        raise unittest.SkipTest(
            "no display: GTK cannot realise a widget here, so the drawn half "
            "of the contract cannot be measured (run under Xvfb to include it)"
        )
    Adw.init()
    return Gtk, Adw


def _luma_toolkit() -> bool:
    """Whether the running libadwaita carries the menu contract.

    The drawn numbers are the toolkit's; on a machine with stock libadwaita
    they are not there to measure, and asserting them would test the wrong
    thing. Set LUMA_TOOLKIT_MENU=1 where the Luma toolkit is installed.
    """
    return os.environ.get("LUMA_TOOLKIT_MENU") == "1"


class MenuModelMixin:
    """Shared fixture: a registry of commands the contract talks about."""

    def _registry(self):
        from luma_appkit import Command, CommandGroup, CommandRegistry
        return CommandRegistry((
            CommandGroup("Organize", (
                Command("one", "Clean up", lambda: None),
                Command("two", "Show view options", lambda: None, shortcut=("Ctrl", "J")),
            )),
        ))


class MenuBridgeTests(MenuModelMixin, unittest.TestCase):
    """The registry becomes the menu model the contract describes.

    No display: a menu model, its actions and its accelerators are the kit's
    arithmetic, and they are checked wherever the toolkit can be imported.
    """

    def setUp(self) -> None:
        try:
            self.Gtk, _adw = _namespaces()
        except (ImportError, ValueError) as error:
            raise unittest.SkipTest(
                f"Luma's toolkit is not importable here ({error}); install "
                "gtk4 and libadwaita to check the menu model"
            )
        from gi.repository import Gio
        self.Gio = Gio

    def _model_items(self, model, section=None):
        for i in range(model.get_n_items()):
            sub = model.get_item_link(i, self.Gio.MENU_LINK_SECTION)
            if sub is not None:
                yield from self._model_items(sub, i)
            else:
                yield model, i

    def test_groups_become_named_sections(self) -> None:
        from luma_appkit import command_menu
        model = command_menu(self._registry())
        self.assertEqual(model.get_n_items(), 1)
        label = model.get_item_attribute_value(0, "label", None)
        self.assertEqual(label.get_string(), "Organize")
        section = model.get_item_link(0, self.Gio.MENU_LINK_SECTION)
        self.assertEqual(section.get_n_items(), 2)

    def test_shortcuts_ride_on_the_item(self) -> None:
        """A shortcut is an accelerator the toolkit draws; no application accel needed."""
        from luma_appkit import accelerator, command_menu
        self.assertEqual(accelerator(("Ctrl", "J")), "<Control>j")
        self.assertEqual(accelerator(("Ctrl", "Shift", "S")), "<Control><Shift>s")
        self.assertEqual(accelerator(("F2",)), "F2")
        self.assertEqual(accelerator(("Delete",)), "Delete")
        model = command_menu(self._registry())
        section = model.get_item_link(0, self.Gio.MENU_LINK_SECTION)
        self.assertEqual(section.get_item_attribute_value(1, "accel", None).get_string(), "<Control>j")

    def test_disabled_rows_are_kept(self) -> None:
        """A disabled command keeps its place as an insensitive row."""
        from luma_appkit import Command, CommandGroup, CommandRegistry, command_actions
        registry = CommandRegistry((CommandGroup(None, (
            Command("go", "Go", lambda: None),
            Command("wait", "Wait", lambda: None, enabled=lambda: False),
        )),))
        actions = command_actions(registry)
        self.assertTrue(actions.get_action_enabled("go"))
        self.assertFalse(actions.get_action_enabled("wait"))
        self.assertTrue(actions.has_action("wait"), "the disabled command was dropped")

    def test_a_checked_command_is_a_check_row(self) -> None:
        from luma_appkit import Command, CommandGroup, CommandRegistry, command_actions
        registry = CommandRegistry((CommandGroup("Sort by", (
            Command("name", "Name", lambda: None, checked=lambda: True),
            Command("date", "Date", lambda: None, checked=lambda: False),
        )),))
        actions = command_actions(registry)
        self.assertTrue(actions.get_action_state("name").get_boolean())
        self.assertFalse(actions.get_action_state("date").get_boolean())

    def test_shortcuts_are_this_keyboards(self) -> None:
        from luma_appkit import modifier_text
        self.assertEqual(modifier_text("cmd"), "Ctrl")
        self.assertEqual(modifier_text("⌘"), "Ctrl")
        self.assertEqual(modifier_text("super"), "Super")
        self.assertEqual(modifier_text("⌫"), "Backspace")


class MenuWidgetTests(MenuModelMixin, unittest.TestCase):
    """The widget half: what the kit hands back is the toolkit's own menu.

    These realise widgets, so they need a display and are skipped, loudly,
    where there is not one.
    """

    def setUp(self) -> None:
        try:
            self.Gtk, _adw = _kit()
        except (ImportError, ValueError) as error:
            raise unittest.SkipTest(
                f"Luma's toolkit is not importable here ({error}); install "
                "gtk4 and libadwaita to check the menu widgets"
            )

    def test_it_is_the_toolkits_menu(self) -> None:
        """What the kit hands back is a GtkPopoverMenu, not a private widget."""
        from luma_appkit import command_popover
        menu = command_popover(self._registry(), variant="desktop")
        self.assertIsInstance(menu, self.Gtk.PopoverMenu)
        self.assertTrue(menu.has_css_class("luma-menu-desktop"))
        self.assertFalse(menu.get_has_arrow())

    def test_what_the_model_cannot_say_is_a_custom_row(self) -> None:
        """A destructive command and a described choice are the kit's rows, inside the toolkit's menu."""
        from luma_appkit import Command, CommandGroup, CommandRegistry, Menu
        registry = CommandRegistry((
            CommandGroup("Sort by", (Command("name", "Name", lambda: None, description="Alphabetical"),)),
            CommandGroup(None, (Command("trash", "Move to Trash", lambda: None, destructive=True, shortcut=("Delete",)),)),
        ))
        menu = Menu(registry, variant="desktop")
        rows = [w for w in _descendants(menu) if w.has_css_class("luma-menu-row")]
        self.assertEqual(len(rows), 2)
        self.assertTrue(any(r.has_css_class("two-line") for r in rows), "the described choice is not two-line")
        self.assertTrue(any(r.has_css_class("luma-menu-destructive") for r in rows), "the destructive row is not marked")

    def test_submenus_open_beside(self) -> None:
        """A submenu is nested beside its row, as the contract draws, not slid into the same page."""
        from luma_appkit import Command, CommandGroup, CommandRegistry, Menu
        registry = CommandRegistry((CommandGroup(None, (
            Command("sort", "Sort by", lambda: None, children=(
                Command("sort.name", "Name", lambda: None), Command("sort.date", "Date", lambda: None))),
        )),))
        menu = Menu(registry)
        self.assertEqual(menu.get_flags(), self.Gtk.PopoverMenuFlags.NESTED)


class MenuDrawnTests(unittest.TestCase):
    """The numbers the toolkit draws, measured where the toolkit is Luma's."""

    def setUp(self) -> None:
        if not _luma_toolkit():
            raise unittest.SkipTest("Luma's toolkit is not installed here (LUMA_TOOLKIT_MENU=1 where it is)")
        try:
            self.Gtk, _adw = _kit()
        except (ImportError, ValueError, unittest.SkipTest) as error:
            raise unittest.SkipTest(str(error))
        from luma_appkit import install_appkit
        install_appkit()

    def _registry(self):
        from luma_appkit import Command, CommandGroup, CommandRegistry
        return CommandRegistry((CommandGroup("Organize", (
            Command("one", "Clean up", lambda: None),
            Command("two", "Show view options", lambda: None, shortcut=("Ctrl", "J")),
        )),))

    def _realized(self, menu):
        window = self.Gtk.Window()
        anchor = self.Gtk.Label(label="anchor")
        window.set_child(anchor)
        menu.set_parent(anchor)
        window.present()
        from gi.repository import GLib
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)
        return window

    def test_variant_widths(self) -> None:
        from luma_appkit import Menu
        for variant, width in VARIANT_WIDTHS.items():
            with self.subTest(variant=variant):
                menu = Menu(self._registry(), variant=variant)
                window = self._realized(menu)
                contents = menu.get_first_child()
                _minimum, natural, _mb, _nb = contents.measure(self.Gtk.Orientation.HORIZONTAL, -1)
                self.assertEqual(natural, width, f"{variant} renders at {natural}px, not {width}px")
                menu.unparent(); window.destroy()

    def test_rows_are_thirty_two(self) -> None:
        from luma_appkit import Menu
        menu = Menu(self._registry(), variant="desktop")
        window = self._realized(menu)
        rows = [w for w in _descendants(menu) if type(w).__name__ == "ModelButton"]
        self.assertTrue(rows, "the menu built no rows")
        for row in rows:
            _minimum, natural, _mb, _nb = row.measure(self.Gtk.Orientation.VERTICAL, -1)
            self.assertGreaterEqual(natural, ROW_HEIGHT)
            self.assertLessEqual(natural, ROW_HEIGHT + 2)
        menu.unparent(); window.destroy()


class MenuStyleTests(unittest.TestCase):
    """The toolkit patch states the contract's numbers; this reads them back."""

    def setUp(self) -> None:
        self.patch = TOOLKIT_PATCH.read_text()
        start = self.patch.index("popover.menu {")
        self.block = self.patch[start:]

    def test_the_kit_sheet_styles_no_menu(self) -> None:
        sheet = (APPKIT_ROOT / "luma-appkit.css").read_text()
        self.assertNotIn(".luma-menu-row {", sheet)
        self.assertNotIn(".luma-menu-popover", sheet)

    def test_container_radius_and_padding(self) -> None:
        self.assertIn(f"border-radius: {CONTAINER_RADIUS}px", self.block)
        self.assertIn(f"padding: {CONTAINER_PADDING}px", self.block)

    def test_two_shadows(self) -> None:
        self.assertIn("0 4px 12px", self.block)
        self.assertIn("0 24px 56px -18px", self.block)

    def test_focus_matches_hover(self) -> None:
        rule = self.block[self.block.index("&:hover,"):self.block.index("&:active")]
        self.assertIn("&:focus-visible", rule)
        self.assertIn("outline: 0", rule)

    def test_destructive_is_calm_at_rest(self) -> None:
        start = self.block.index("button.luma-menu-row.luma-menu-destructive {")
        rest = self.block[start:self.block.index("&:hover", start)]
        self.assertIn("@luma_ink", rest)
        self.assertNotIn("@luma_menu_destructive", rest)

    def test_variant_widths_are_stated(self) -> None:
        for variant, width in VARIANT_WIDTHS.items():
            with self.subTest(variant=variant):
                patch = (REPO_ROOT / "patches/libadwaita/0033-luma-compact-menu-width.patch").read_text()
                content_width = width - 2 * VARIANT_PADDING[variant]
                if variant != "dock":
                    self.assertIn(f"+    min-width: {content_width}px;", patch) if variant == "app" else self.assertIn(f"min-width: {content_width}px", patch)

    def test_both_design_systems_carry_the_tokens(self) -> None:
        self.assertEqual(self.patch.count("@define-color luma_menu #"), 2)
        self.assertIn("#252a30", self.patch)
        self.assertIn("#f6f7f8", self.patch)


if __name__ == "__main__":
    unittest.main()
