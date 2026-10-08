# SPDX-License-Identifier: Apache-2.0
"""LumaUI F2: the structure parts.

The first half reads source and runs anywhere: the API surface, the CSS
banners, tokens and rules every part follows. The second half builds real
widgets on stock GTK 4 wherever a display is available (it never presents a
window) and checks behaviour: open and close rules, drawers at phone width,
sorting, selection, the trail and the placement order.
"""
from __future__ import annotations

import ast
import json
import re
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPKIT = ROOT / "src/luma-platform/appkit"
KIT = APPKIT / "luma_appkit"
BASE = APPKIT / "luma-appkit-base.css"
GALLERY = ROOT / "src/luma-platform/tools/lumaui-gallery/lumaui_gallery.py"
TOKENS = json.loads((ROOT / "config/shared/design-tokens.json").read_text())["lumaui"]

MODULES = ("structure_adapt", "structure_details", "structure_sidebar", "structure_table", "structure_trail",
           "structure_placement", "structure_drawer")
PARTS = {
    "structure_details": ["AddRow", "DetailsItem", "DetailsPane", "DetailsPhotos", "DetailsRow", "DetailsFacts", "FactRow"],
    "structure_sidebar": ["FilterHeading", "SidebarFoot", "SidebarToggle"],
    "structure_table": ["Column", "SORT_DIRECTIONS", "Selection", "TableHeader"],
    "structure_trail": ["NavigationTrailBar", "PageHeader"],
    "structure_placement": ["CornerPill", "ModeSwitch"],
    "structure_drawer": ["MenuDrawer"],
}
BANNERS = ("Details pane", "Details pane item", "Details pane photos", "Add row", "Sidebar foot", "Sidebar toggle",
           "Table header", "Selection. One look: the raised chip. A path's earlier steps: the same shape, quieter.",
           "Navigation trail", "Corner pill and mode switch", "Menu drawer", "Corner pill words, the key and the edit state",
           "Fact row")
GALLERY_PAGES = ("details", "place", "table", "foot", "drawer", "trail")


def _structure_css() -> str:
    text = BASE.read_text()
    return text[text.index("/* ══ LumaUI structure (F2)"):]


class Source(unittest.TestCase):
    def test_every_part_is_exported_lazily_and_in_all(self):
        init = (KIT / "__init__.py").read_text()
        exported = set(re.findall(r'"(\w+)"', init[init.index("__all__"):init.index("]\n")]))
        for module, names in PARTS.items():
            text = (KIT / f"{module}.py").read_text()
            declared = ast.literal_eval(re.search(r"^__all__ = (\[.*?\])$", text, re.M | re.S).group(1))
            self.assertEqual(sorted(declared), sorted(names), module)
            for name in names:
                self.assertIn(name, exported)
                self.assertRegex(init, rf'if name in \{{[^}}]*"{name}"[^}}]*\}}:\n\s+from \. import {module}')

    def test_every_part_has_its_css_banner(self):
        css = _structure_css()
        for banner in BANNERS:
            self.assertIn(f"/* LumaUI: {banner}", css)

    def test_parts_name_no_colours_and_run_on_stock_gtk(self):
        literal = re.compile(r"""["']#[0-9a-fA-F]{3,8}["']|rgba?\(""")
        for name in MODULES:
            code = (KIT / f"{name}.py").read_text()
            self.assertIsNone(literal.search(code), name)
            self.assertNotRegex(code, r"require_version\(\"(LumaUI|LumaAppearance)\"", name)
            top = [n for n in ast.parse(code).body if isinstance(n, ast.ImportFrom)]
            self.assertFalse(any(n.module == "widgets" for n in top), f"{name} imports widgets.py")

    def test_structure_css_uses_only_roles_and_tokens(self):
        css = re.sub(r"/\*.*?\*/", "", _structure_css(), flags=re.S)
        self.assertIsNone(re.search(r"(?<![\w-])#[0-9a-fA-F]{3,8}\b|\brgba?\(", css))
        # Every structure metric is a token (a group under lumaui.structure).
        groups = {k.replace("_", "-") for k, v in TOKENS["structure"].items() if isinstance(v, dict)}
        for variable in set(re.findall(r"var\(--lumaui-([a-z0-9-]+)\)", css)):
            prefix = variable.split("-")[0]
            if variable.startswith(("details-", "foot-", "sidebar-toggle-", "thead-", "trail-", "modes-", "corner-", "menu-")):
                self.assertTrue(any(variable.startswith(g + "-") for g in groups), variable)
            self.assertTrue(prefix)

    def test_the_kit_sheet_is_balanced(self):
        # A lost brace (a merge that drops a block's closing line) swallows every rule after it.
        text = re.sub(r"/\*.*?\*/", "", BASE.read_text(), flags=re.S)
        depth = 0
        for index, char in enumerate(text):
            depth += {"{": 1, "}": -1}.get(char, 0)
            self.assertGreaterEqual(depth, 0, f"unbalanced }} near line {text[:index].count(chr(10)) + 1}")
        self.assertEqual(depth, 0, "a block is never closed")
        for block in re.finditer(r"@media \(prefers-reduced-motion: reduce\) \{", text):
            inner, level = text[block.end():], 1
            for position, char in enumerate(inner):
                level += {"{": 1, "}": -1}.get(char, 0)
                if level == 0:
                    self.assertNotIn("══", inner[:position])
                    break

    def test_reduced_motion_turns_slides_into_fades(self):
        css = _structure_css()
        block = css[css.index("@media (prefers-reduced-motion: reduce)"):]
        for selector in ("box.lumaui-details", ".lumaui-modal.side-drawer", "box.lumaui-details.entering"):
            self.assertIn(selector, block)

    def test_selection_is_the_raised_chip_everywhere(self):
        css = _structure_css()
        rule = css[css.index("list.lumaui-selection > row:selected"):]
        rule = rule[:rule.index("}")]
        for selector in ("listview.lumaui-selection > row:selected", "gridview.lumaui-selection > child:selected",
                         "flowbox.lumaui-selection > flowboxchild:selected", ".lumaui-selected",
                         "list.luma-navigation-list > row.luma-navigation-row:selected"):
            self.assertIn(selector, rule)
        self.assertIn("background: @luma_chip", rule)

    def test_tokens_for_python_carry_the_structure_groups(self):
        text = (KIT / "lumaui_tokens.py").read_text()
        self.assertIn("STRUCTURE = {", text)
        self.assertEqual(TOKENS["structure"]["details"]["width"], 288)
        self.assertEqual(TOKENS["structure"]["modes"]["narrow_max_width"], 820)

    def test_gallery_has_a_page_for_every_structure_part(self):
        text = GALLERY.read_text()
        builders = text[text.index("F2_BUILDERS = {"):]
        builders = builders[:builders.index("}")]
        for key in GALLERY_PAGES:
            self.assertIn(f'"{key}":', builders)
        self.assertIn("**F2_BUILDERS}", text)
        self.assertEqual(text.count("\nBUILDERS = {"), 1, "one gallery table")

    def test_tide_navigation_copy_matches_the_kit(self):
        self.assertEqual((ROOT / "src/luma-tide/luma_tide/_navigation.py").read_text(),
                         (KIT / "navigation.py").read_text())


# ── widgets on stock GTK ────────────────────────────────────────────────────

sys.path.insert(0, str(APPKIT))
try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gio, GLib, Gtk

    HAVE_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
except (ImportError, ValueError):
    HAVE_DISPLAY = False


def _descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _descendants(child)
        child = child.get_next_sibling()


def _spin(ms: int = 0) -> None:
    """Run the main loop until nothing is pending and `ms` have passed."""
    context = GLib.MainContext.default()
    end = time.monotonic() + ms / 1000
    while True:
        while context.pending():
            context.iteration(False)
        if time.monotonic() >= end:
            return
        time.sleep(0.01)


@unittest.skipUnless(HAVE_DISPLAY, "needs GTK 4 and a display")
class Parts(unittest.TestCase):
    def setUp(self):
        from luma_appkit import (structure_adapt, structure_details, structure_drawer, structure_layers,
                                 structure_placement, structure_sidebar, structure_table, structure_trail)
        self.adapt, self.details, self.drawer = structure_adapt, structure_details, structure_drawer
        self.layers, self.placement, self.sidebar = structure_layers, structure_placement, structure_sidebar
        self.table, self.trail = structure_table, structure_trail
        self.windows: list[Gtk.Window] = []

    def tearDown(self):
        for window in self.windows:
            window.destroy()

    def _stage(self, child: Gtk.Widget, width: int = 1100):
        """A window whose "window" layer host measures `width` (a phone at 375)."""
        stage = self.layers.LayerHost(child, name="window")
        stage.get_width = lambda: width
        stage.get_height = lambda: 700
        window = Gtk.Window()
        window.set_child(stage)
        self.windows.append(window)
        return stage

    def test_undimmed_phone_sheet_preserves_drawer_dismissal_and_focus_contract(self):
        background = Gtk.Button(label="Map")
        stage = self._stage(background, width=375)
        cancelled = []
        normal = stage.present_modal(Gtk.Button(label="Default"), drawer=True)
        self.assertTrue(normal.scrim.has_css_class("lumaui-scrim"))
        normal.close(quiet=True)
        sheet = Gtk.Button(label="Place")
        handle = stage.present_modal(sheet, drawer=True, scrim=False,
                                     on_cancel=lambda: cancelled.append(True))
        self.assertTrue(handle.drawer and sheet.has_css_class("drawer"))
        self.assertFalse(handle.scrim.has_css_class("lumaui-scrim"))
        self.assertFalse(background.get_can_target())
        self.assertIs(handle.scrim.get_parent(), stage)
        handle.cancel()
        self.assertEqual(cancelled, [True])
        self.assertIsNone(stage.modal)
        self.assertTrue(background.get_can_target())

    # ── details pane ──────────────────────────────────────────────────────

    def _pane(self, width: int = 1100, **kwargs):
        body = Gtk.Box()
        body.append(Gtk.Button(label="Info"))
        pane = self.details.DetailsPane("Details", **kwargs)
        body.append(pane)
        stage = self._stage(body, width)
        return pane, stage

    def test_mode_switch_chip_covers_the_whole_current_button_at_first_layout(self):
        # Nick, 26 Sep (Calendar, Phone): the chip was a half pill behind the icon.
        switch = self.placement.ModeSwitch([("flow", "Flow", "list"), ("week", "Week", "columns-3"),
                                            ("month", "Month", "layout-grid")], current="flow")
        width = switch.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        switch.allocate(width, 36, -1, None)
        for key in ("flow", "month"):
            switch.set_current(key)
            switch._place(False)
            switch.allocate(width, 36, -1, None)
            button = switch.buttons[key]
            ok, b = button.compute_bounds(switch.row)
            ok2, c = switch.indicator.compute_bounds(switch.row)
            self.assertTrue(ok and ok2)
            self.assertEqual((round(c.get_x()), round(c.get_width())), (round(b.get_x()), round(b.get_width())), key)
            self.assertGreater(c.get_width(), 40, "the chip covers the icon and the label")

    def test_a_long_fact_value_wraps_by_word_at_the_panes_width(self):
        # Nick, 26 Sep: "Calendar | H-o-li-d-a-ys" at 288. The value takes the room, the key its own.
        pane, _stage = self._pane()
        mark = Gtk.Box()
        mark.set_size_request(8, 8)
        facts = pane.add_facts([("Calendar", "Holidays in United States", mark), ("Alert", "10 minutes before the event starts")])
        facts.measure(Gtk.Orientation.VERTICAL, 288 - 32)
        facts.allocate(288 - 32, 400, -1, None)
        values = [w for w in _descendants(facts) if isinstance(w, Gtk.Label) and w.has_css_class("lumaui-details-value")]
        self.assertEqual(len(values), 2)
        for label in values:
            self.assertGreater(label.get_width(), 90, f"{label.get_label()!r} squeezed to {label.get_width()} px")

    def test_details_pane_is_closed_until_info_and_needs_a_subject(self):
        pane, _stage = self._pane()
        self.assertFalse(pane.props.shown or pane.sheet.get_visible())
        self.assertFalse(pane.show(open=True), "nothing to describe: it stays closed")
        self.assertTrue(pane.show(open=True, subject="photo-1"))
        self.assertTrue(pane.sheet.get_visible() and pane.props.shown)

    def test_details_pane_leading_header_widget(self):
        pane, _stage = self._pane()
        icon = Gtk.Image.new_from_icon_name("document-open-symbolic")
        pane.set_leading(icon)
        self.assertIs(icon.get_parent(), pane.header)
        self.assertIs(icon.get_next_sibling(), pane.title_label)
        self.assertTrue(icon.has_css_class("lumaui-details-leading"))
        pane.set_leading(icon)
        replacement = Gtk.Label(label="T")
        pane.set_leading(replacement)
        self.assertIsNone(icon.get_parent())
        self.assertIs(replacement.get_next_sibling(), pane.title_label)
        pane.set_leading(None)
        self.assertIsNone(replacement.get_parent())
        entry = Gtk.Entry()
        pane.set_title_content(entry)
        self.assertFalse(pane.title_label.get_visible())
        self.assertIs(entry.get_parent(), pane.header)
        self.assertIs(entry.get_next_sibling(), pane.close_button)
        pane.set_title_content(None)
        self.assertTrue(pane.title_label.get_visible())
        self.assertIsNone(entry.get_parent())

    def test_details_pane_keeps_the_wish_across_subjects_and_closes_when_the_subject_goes(self):
        pane, _stage = self._pane()
        closed = []
        pane.on_close = lambda: closed.append(1)
        pane.show(open=True, subject="photo-1")
        self.assertTrue(pane.show(subject="photo-2"), "stepping to the next photo keeps it open")
        self.assertFalse(pane.show(subject=None), "its subject went away: it closes itself")
        self.assertFalse(pane.show(subject="photo-3"), "and forgets the wish")
        pane.show(open=True)
        pane.close()
        self.assertEqual(closed, [1])
        self.assertFalse(pane.sheet.get_visible())

    def test_details_pane_esc_closes_unless_typing_or_main(self):
        pane, _stage = self._pane()
        pane.show(open=True, subject="x")
        entry = Gtk.Entry()
        pane.add(entry)
        pane.get_root().set_focus(entry.get_delegate() if hasattr(entry, "get_delegate") else entry)
        self.assertFalse(pane._key(None, Gdk.KEY_Escape, 0, 0), "Esc belongs to the text field")
        pane.get_root().set_focus(pane.close_button)
        self.assertTrue(pane._key(None, Gdk.KEY_Escape, 0, 0))
        self.assertFalse(pane.props.shown)
        main, _s = self._pane(main=True)
        self.assertTrue(main.props.shown and not main.close_button.get_visible())
        main.close()
        self.assertFalse(main._key(None, Gdk.KEY_Escape, 0, 0))
        self.assertTrue(main.show(subject=None), "a main pane stays without a subject")

    def test_details_pane_info_button_follows_the_pane(self):
        pane, _stage = self._pane()
        info = pane.info_button()
        info.set_active(True)
        self.assertFalse(info.get_active(), "no subject: Info cannot open it")
        pane.show(subject="x")
        info.set_active(True)
        self.assertTrue(pane.props.shown)
        pane.close()
        self.assertFalse(info.get_active())

    def test_details_pane_content_parts(self):
        pane, _stage = self._pane()
        pane.add_hero("Launch crew", "4 people")
        pane.add_section("Conversation", action=("View all", lambda: None))
        facts = pane.add_facts([("Created", "Sep 2"), ("Encryption", "End-to-end")])
        row = self.details.DetailsRow("Priya Raman", "@priya", actions=[("phone", "Call", lambda: None)])
        added = []
        pane.add_list([row, self.details.DetailsItem("Press kit review", "Tuesday", icon="mail"),
                       self.details.AddRow("Add people", shortcut="Ctrl+N", on_activate=lambda: added.append(1))])
        with self.assertRaises(TypeError):
            pane.add_list([Gtk.Label()])
        with self.assertRaises(ValueError):
            self.details.DetailsItem("x", icon="mail", lead=Gtk.Label())
        value = facts.get_first_child().get_last_child()
        self.assertTrue(value.get_selectable() and value.has_css_class("lumaui-details-value"))
        self.assertTrue(row.actions.get_visible() and row.has_css_class("lumaui-details-row"))
        photos = pane.add_photos([Gdk.MemoryTexture.new(1, 1, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(b"\0\0\0\xff"), 4)] * 4)
        self.assertEqual(len(self.adapt.children(photos)), 4)
        self.assertEqual(photos.get_child_at(0, 1) is not None, True, "three a row: the fourth starts a new row")
        pane.clear()
        self.assertIsNone(pane.body.get_first_child())

    def test_details_pane_is_a_drawer_at_phone_width(self):
        pane, stage = self._pane(width=375)
        pane.show(open=True, subject="x")
        self.assertFalse(pane.is_drawer, "a pane in a view that is not showing never floats over the window")
        self.assertIsNone(stage.modal)
        pane.get_mapped = lambda: True  # its view shows (the test never presents a window)
        pane._reflow()
        self.assertTrue(pane.is_drawer)
        self.assertIs(stage.modal, pane._drawer)
        self.assertIs(pane.sheet.get_parent(), stage)
        self.assertTrue(pane.sheet.has_css_class("drawer") and not pane.sheet.has_css_class("luma-island"))
        self.assertTrue(pane.handle.get_visible())
        pane._drawer.cancel()  # a tap outside, a swipe down or Esc
        self.assertFalse(pane.props.shown)
        _spin(400)
        self.assertIs(pane.sheet.get_parent(), pane, "the sheet comes home after the fade")
        self.assertTrue(pane.sheet.has_css_class("luma-island"))
        # Widening the window puts an open drawer back beside the island.
        pane.show(open=True, subject="x")
        stage.get_width = lambda: 1100
        pane._reflow()
        self.assertFalse(pane.is_drawer)
        self.assertIs(pane.sheet.get_parent(), pane)
        self.assertTrue(pane.sheet.get_visible())

    def test_details_drawer_mapping_does_not_present_a_second_modal(self):
        pane, stage = self._pane(width=375)
        pane.show(open=True, subject="x")
        pane.get_mapped = lambda: True
        original = stage.present_modal
        presentations = []
        def mapped_during_present(*args, **kwargs):
            presentations.append(True)
            pane._reflow()
            return original(*args, **kwargs)
        stage.present_modal = mapped_during_present
        pane._reflow()
        self.assertEqual(len(presentations), 1)
        self.assertIs(stage.modal, pane._drawer)
        self.assertFalse(pane._presenting_drawer)
        stage.get_width = lambda: 1100
        pane._reflow()
        self.assertFalse(pane.is_drawer)
        self.assertIs(pane.sheet.get_parent(), pane)

    # ── menu drawer ───────────────────────────────────────────────────────

    def _registry(self, ran):
        from luma_appkit.commands import Command, CommandGroup, CommandRegistry
        return CommandRegistry([
            CommandGroup("Show", (Command("all", "All", lambda: ran.append("all"), checked=lambda: True, count=214),
                                  Command("fav", "Favourites", lambda: ran.append("fav"), count=0))),
            CommandGroup(None, (Command("sort", "Sort by", lambda: None, children=(
                Command("name", "Name", lambda: ran.append("name")),)),
                Command("del", "Delete", lambda: ran.append("del"), destructive=True))),
        ])

    def test_a_shared_menu_is_a_drawer_at_phone_width(self):
        from luma_appkit.menus import command_popover
        ran = []
        button = Gtk.Button()
        stage = self._stage(button, width=375)
        menu = command_popover(self._registry(ran))
        closed = []
        menu.connect("closed", lambda *_a: closed.append(1))
        menu.set_parent(button)
        menu.popup()
        drawer = stage.modal.card
        self.assertIsInstance(drawer, self.drawer.MenuDrawer)
        self.assertFalse(menu.get_visible(), "no popover at phone width")
        page = drawer.pages.get_visible_child()
        rows = [w for w in self.adapt.children(page) if isinstance(w, Gtk.Button)]
        self.assertEqual([r.has_css_class("danger") for r in rows], [False, False, False, True])
        badges = [w for w in _walk(rows[0]) if w.has_css_class("lumaui-count")]
        self.assertEqual(badges[0].get_label(), "214")
        self.assertFalse([w for w in _walk(rows[1]) if w.has_css_class("lumaui-count") and w.get_visible()],
                         "nothing for 0")
        rows[2].emit("clicked")  # a submenu opens as a page with Back
        sub = drawer.pages.get_visible_child()
        self.assertTrue(self.adapt.children(sub)[0].has_css_class("back"))
        self.adapt.children(sub)[1].emit("clicked")
        _spin(50)
        self.assertEqual(ran, ["name"])
        self.assertIsNone(stage.modal, "choosing closes the drawer")
        self.assertEqual(closed, [1], "the menu reports it closed")

    def test_a_menu_is_a_popover_on_a_computer(self):
        from luma_appkit.menus import command_popover
        button = Gtk.Button()
        stage = self._stage(button, width=1100)
        menu = command_popover(self._registry([]))
        menu.set_parent(button)
        self.assertFalse(menu._present_as_drawer())
        self.assertIsNone(stage.modal)

    def test_menu_drawer_from_a_gio_menu_model(self):
        button = Gtk.Button()
        stage = self._stage(button, width=375)
        seen = []
        group = Gio.SimpleActionGroup()
        action = Gio.SimpleAction.new("copy", None)
        action.connect("activate", lambda *_a: seen.append("copy"))
        group.add_action(action)
        button.insert_action_group("doc", group)
        model = Gio.Menu()
        section = Gio.Menu()
        section.append("_Copy", "doc.copy")
        model.append_section("Edit", section)
        drawer = self.drawer.MenuDrawer.present_model(button, model)
        page = drawer.pages.get_visible_child()
        rows = [w for w in self.adapt.children(page) if isinstance(w, Gtk.Button)]
        self.assertEqual(len(rows), 1)
        rows[0].emit("clicked")
        _spin(50)
        self.assertEqual(seen, ["copy"])
        self.assertIsNone(stage.modal)

    def test_floating_menu_rises_from_the_bar_at_phone_width(self):
        # v71 second pass: every menu on a phone rises from the bar's place (the bar's frame), not a drawer.
        from luma_appkit.action_bubble import FloatingMenu, MenuItem
        from luma_appkit.bar_frame import BarFrame
        from luma_appkit.bar_panel import PanelRow
        button = Gtk.Button()
        stage = self._stage(button, width=375)
        ran = []
        menu = FloatingMenu(["Open in", MenuItem("Stage", icon="square-arrow-out-up-right",
                                                 note="Default", selected=True, on_activate=lambda: ran.append(1)),
                             None, MenuItem("Other app…", icon="app-window")], label="Open in")
        menu.popup(button)
        self.assertTrue(menu.is_open)
        frame = stage.modal.card
        self.assertIsInstance(frame, BarFrame, "one place for every LumaUI menu on a phone: the bar's frame")
        rows = [w for w in _walk(frame.content) if isinstance(w, PanelRow)]
        self.assertTrue(rows[0].has_css_class("on"))
        self.assertTrue([w for w in _walk(rows[0]) if isinstance(w, Gtk.Label) and w.get_label() == "Default"])
        rows[0].emit("clicked")
        _spin(50)
        self.assertEqual(ran, [1])
        self.assertFalse(menu.is_open)

    # ── sidebar foot and toggle ───────────────────────────────────────────

    def test_sidebar_foot_filters_add_and_search(self):
        Foot = self.sidebar.SidebarFoot
        with self.assertRaises(ValueError):
            Foot(search="Search", filters=[("all", "All", "layers")])
        with self.assertRaises(ValueError):
            Foot(search="Search", filters=[("all", "All"), ("fav", "Favourites")])
        chosen, searched, added = [], [], []
        foot = Foot(search="Search people", on_search=searched.append,
                    filters=[("all", "All", "users", 214), ("fav", "Favourites", "star", 12)],
                    on_filter=chosen.append, add=("Add a contact", "user-plus", lambda: added.append(1)))
        self.assertIsNotNone(foot.filter_button)
        self.assertIsNotNone(foot.add_button, "Contacts has both: the filter and Add")
        self.assertFalse(foot.heading.get_visible() or foot.filter_button.has_css_class("on"))
        registry = foot.filter_registry()
        self.assertEqual([c.count for c in registry.groups[0].commands], [214, 12])
        registry.invoke("lumaui-filter.fav")
        self.assertEqual(chosen, ["fav"])
        self.assertTrue(foot.filter_button.has_css_class("on") and foot.heading.get_visible())
        self.assertEqual(foot.heading.label.get_label(), "Favourites")
        foot.heading.show_all.emit("clicked")
        self.assertEqual((chosen, foot.filter), (["fav", "all"], "all"))
        foot.set_filter_counts({"fav": 13})
        self.assertEqual(foot.filter_registry().groups[0].commands[1].count, 13)
        foot.entry.set_text("pri")
        self.assertEqual(searched[-1], "pri")
        self.assertTrue(foot._key(None, Gdk.KEY_Escape, 0, 0))
        self.assertEqual(foot.text, "")
        foot.add_button.emit("clicked")
        self.assertEqual(added, [1])

    def test_sidebar_toggle_hides_and_shows_with_f9(self):
        body = Gtk.Box()
        sidebar = Gtk.Box()
        sidebar.add_css_class("luma-island")
        body.append(sidebar)
        body.append(Gtk.Label(label="content", hexpand=True))
        self._stage(body)
        toggle = self.sidebar.SidebarToggle(sidebar)
        revealer = sidebar.get_parent()
        self.assertIsInstance(revealer, Gtk.Revealer)
        self.assertIs(revealer.get_parent(), body)
        self.assertTrue(toggle.shown and revealer.get_reveal_child())
        toggle.toggle()
        self.assertFalse(toggle.get_active() or revealer.get_reveal_child())
        self.assertEqual(toggle.get_tooltip_text(), "Show sidebar (F9)")
        shortcuts = [c for c in _controllers(toggle) if isinstance(c, Gtk.ShortcutController)]
        self.assertEqual(shortcuts[0].get_item(0).get_trigger().to_string(), "F9")
        self.assertEqual(shortcuts[0].get_scope(), Gtk.ShortcutScope.GLOBAL)

    def test_sidebar_toggle_opens_a_drawer_at_phone_width(self):
        body = Gtk.Box()
        sidebar = Gtk.Box()
        sidebar.add_css_class("luma-island")
        rows = Gtk.ListBox()
        rows.append(Gtk.Label(label="Home"))
        sidebar.append(rows)
        body.append(sidebar)
        stage = self._stage(body, width=375)
        toggle = self.sidebar.SidebarToggle(sidebar)
        toggle._reflow()
        toggle.toggle()
        self.assertIsNotNone(stage.modal)
        card = stage.modal.card
        self.assertTrue(card.has_css_class("side-drawer") and sidebar.get_parent() is card)
        self.assertFalse(sidebar.has_css_class("luma-island"), "the drawer is the surface")
        self.assertTrue(stage.modal.scrim.has_css_class("drawer"))
        rows.emit("row-activated", rows.get_row_at_index(0))  # choosing a place closes it
        self.assertIsNone(stage.modal)
        _spin(400)
        self.assertIsInstance(sidebar.get_parent(), Gtk.Revealer)
        self.assertTrue(sidebar.has_css_class("luma-island"))

    def test_sidebar_toggle_opens_people_full_width_at_phone_width(self):
        # v70 .mwin.navopen .msidebar: a list of people is the whole window less its gutter; places stay a 264 drawer.
        for variant, full in (("people", True), (None, False)):
            body = Gtk.Box()
            sidebar = Gtk.Box()
            sidebar.variant = variant
            rows = Gtk.ListBox()
            rows.append(Gtk.Label(label="Ada"))
            sidebar.append(rows)
            body.append(sidebar)
            stage = self._stage(body, width=375)
            toggle = self.sidebar.SidebarToggle(sidebar)
            toggle._reflow()
            toggle.toggle()
            card = stage.modal.card
            self.assertEqual(card.has_css_class("full"), full)
            self.assertEqual(card.get_halign(), Gtk.Align.FILL if full else Gtk.Align.START)
            toggle.toggle()
            _spin(300)

    def test_sidebar_toggle_drives_an_overlay_split_view(self):
        try:
            gi.require_version("Adw", "1")
            from gi.repository import Adw
        except (ImportError, ValueError):
            self.skipTest("no libadwaita")
        split = Adw.OverlaySplitView(sidebar=Gtk.Box(), content=Gtk.Box())
        self._stage(split)
        toggle = self.sidebar.SidebarToggle(split)
        toggle.toggle()
        self.assertFalse(split.get_show_sidebar())
        toggle.toggle()
        self.assertTrue(split.get_show_sidebar())

    # ── table header and selection ────────────────────────────────────────

    def test_table_header_sorts_and_reverses(self):
        Column = self.table.Column
        sorts = []
        header = self.table.TableHeader([Column(None, "", width=32), Column("title", "Title", expand=True),
                                         ("album", "Album"), ("time", "Time", "d")],
                                        sort=("title", "ascending"), on_sort=lambda k, d: sorts.append((k, d)))
        self.assertTrue(header.columns[3].end)
        title = header.cells[1]
        self.assertTrue(title.has_css_class("active") and title.arrow.get_visible())
        self.assertEqual(header.activate_column("title"), ("title", "descending"), "again reverses")
        self.assertEqual(header.activate_column("album"), ("album", "ascending"))
        self.assertEqual(sorts, [("title", "descending"), ("album", "ascending")])
        self.assertFalse(title.has_css_class("active") or title.arrow.get_visible())
        self.assertIsInstance(header.cells[0], Gtk.Label, "a column without a key is a plain label")
        self.assertEqual(header.cells[1].get_accessible_role(), Gtk.AccessibleRole.COLUMN_HEADER)
        with self.assertRaises(ValueError):
            header.set_sort("nope")
        with self.assertRaises(ValueError):
            self.table.TableHeader([("a", "A"), ("a", "B")])
        row = Gtk.Box()
        for _ in range(4):
            row.append(Gtk.Label())
        header.align(row)
        self.assertTrue(self.adapt.children(row)[1].get_hexpand())
        with self.assertRaises(ValueError):
            header.align(Gtk.Box())

    def test_column_view_header_follows_the_sorter(self):
        store = Gio.ListStore.new(Gtk.StringObject)
        view = Gtk.ColumnView(model=Gtk.SingleSelection(model=Gtk.SortListModel(model=store)))
        for key in ("name", "size"):
            column = Gtk.ColumnViewColumn(title=key.title(), factory=Gtk.SignalListItemFactory())
            column.set_id(key)
            column.set_sorter(Gtk.StringSorter())
            view.append_column(column)
        self._stage(view)
        header = self.table.TableHeader.for_column_view(view)
        self.assertTrue(view.has_css_class("lumaui-table") and view.has_css_class("lumaui-selection"))
        view.sort_by_column(view.get_columns().get_item(1), Gtk.SortType.DESCENDING)
        self.assertEqual(header.sync(), ("size", "descending"))
        titles = header.titles()
        self.assertEqual(len(titles), 2)
        self.assertTrue(titles[1].has_css_class("active") and not titles[0].has_css_class("active"))

    def test_selection_is_one_chip_and_a_quieter_trail(self):
        Selection = self.table.Selection
        listbox = Selection.apply(Gtk.ListBox())
        self.assertTrue(listbox.has_css_class("lumaui-selection"))
        with self.assertRaises(TypeError):
            Selection.apply(Gtk.Box())
        step = Gtk.Button()
        Selection.trail(step)
        self.assertTrue(step.has_css_class("lumaui-trail"))
        Selection.mark(step)
        self.assertTrue(step.has_css_class("lumaui-selected") and not step.has_css_class("lumaui-trail"))

    # ── navigation trail ──────────────────────────────────────────────────

    def test_trail_bar_follows_the_trail(self):
        from luma_appkit.navigation import NavigationTrail, Place
        trail = NavigationTrail(Place("albums", "Albums"))
        bar = self.trail.NavigationTrailBar(trail, meta=["12 albums", "214 songs"])
        self.assertFalse(bar.back.get_visible(), "nowhere to go back to")
        self.assertEqual(bar.title.get_label(), "Albums")
        trail.open(Place("artist", "The Beach Boys"))
        self.assertTrue(bar.back.get_visible() and bar.back.has_css_class("icon-only"))
        self.assertEqual(bar.back.get_tooltip_text(), "Back to Albums")
        self.assertEqual(bar.title.get_label(), "The Beach Boys")
        dots = [w for w in self.adapt.children(bar.meta_box) if w.has_css_class("lumaui-trail-dot")]
        self.assertEqual(len(dots), 2, "a dot before each meta item after a title")
        bar.back.emit("clicked")
        self.assertEqual(trail.current.title, "Albums")
        pane = self.trail.NavigationTrailBar(trail, title=False)
        trail.open(Place("source", "Navidrome"))
        self.assertEqual(pane.back_label.get_label(), "Albums")
        self.assertTrue(pane.back_label.get_visible() and not pane.title.get_visible())
        pane._stop()
        trail.back()
        self.assertEqual(pane.back_label.get_label(), "Albums", "a removed listener no longer follows")

    # ── corner pill and modes ─────────────────────────────────────────────

    def test_mode_switch(self):
        Modes = self.placement.ModeSwitch
        with self.assertRaises(ValueError):
            Modes([("view", "View", "eye")])
        changed = []
        modes = Modes([("view", "View", "eye"), ("markup", "Mark up", "pen-line"), ("adjust", "Adjust", "sliders-horizontal")],
                      on_change=changed.append)
        self.assertEqual(modes.current, "view")
        modes.buttons["markup"].set_active(True)
        self.assertEqual((modes.current, changed), ("markup", ["markup"]))
        modes.set_current("adjust")
        self.assertEqual(changed, ["markup"], "set_current does not call back")
        modes._width_changed(700)
        visible = {k: b.label_widget.get_visible() for k, b in modes.buttons.items()}
        self.assertEqual(visible, {"view": False, "markup": False, "adjust": True}, "narrow: only the current label")
        modes._width_changed(1200)
        self.assertTrue(all(b.label_widget.get_visible() for b in modes.buttons.values()))

    def test_corner_pill_orders_what_the_app_has(self):
        pane, _stage = self._pane()
        pane.show(subject="photo")
        from luma_appkit.commands import Command, CommandGroup, CommandRegistry
        more = CommandRegistry([CommandGroup(None, (Command("x", "Rename", lambda: None),))])
        corner = self.placement.CornerPill(
            more=more, info=pane, states=[("heart", "Favourite", False, lambda on: None)],
            actions=[("pencil", "Edit", lambda: None)], share=lambda b: None, open_in=lambda b: None,
            modes=self.placement.ModeSwitch([("view", "View", "eye"), ("markup", "Mark up", "pen-line")]))
        # v70 pCorner, "the LumaUI order": Share · Favourite · Information · Edit · ··· (KB1)
        self.assertEqual(list(corner.controls), ["modes", "open_in", "share", "states.0", "info", "actions.0", "more"])
        self.assertEqual(self.adapt.children(corner.normal), list(corner.controls.values()))
        corner.controls["info"].set_active(True)
        self.assertTrue(pane.props.shown)
        with self.assertRaises(ValueError):
            self.placement.CornerPill()

    def test_corner_pill_words_key_and_edit_state(self):
        with self.assertRaises(ValueError):
            self.placement.CornerPill(actions=[("pencil", "Edit", lambda: None)], primary="Save")
        seen = []
        corner = self.placement.CornerPill(share=lambda b: None, actions=[("pencil", "Edit", lambda: seen.append("edit"))],
                                           primary="Edit", labelled=True, more=self._registry([]))
        body = Gtk.Box()
        body.append(corner)
        stage = self._stage(body)
        share, edit = corner.controls["share"], corner.controls["actions.0"]
        self.assertTrue(share.has_css_class("labelled") and share.label_widget.get_label() == "Share")
        self.assertTrue(edit.has_css_class("primary") and not share.has_css_class("primary"))
        stage.get_width = lambda: 375
        corner._collapse()
        self.assertFalse(share.label_widget.get_visible(), "icon only at phone width")
        self.assertTrue(edit.label_widget.get_visible(), "the key keeps its word")
        corner.edit(on_cancel=lambda: seen.append("cancel"), on_done=lambda: seen.append("done"))
        self.assertTrue(corner.is_editing and not corner.normal.get_visible())
        self.assertTrue(corner.done_button.has_css_class("primary"))
        corner.done_button.emit("clicked")
        self.assertFalse(corner.is_editing)
        corner.edit(on_cancel=lambda: seen.append("cancel"))
        corner.cancel_button.emit("clicked")
        self.assertEqual(seen, ["done", "cancel"])
        plain = self.placement.CornerPill(actions=[("pencil", "Edit", lambda: None)])
        self.assertFalse(plain.controls["actions.0"].has_css_class("labelled"), "icon buttons unless asked")

    def test_fact_row_copies_and_confirms(self):
        pane, stage = self._pane()
        pane.show(open=True, subject="x")
        row = self.details.FactRow("phone", "Mobile", "+1 816 555 0142")
        pane.add_list([row])
        self.assertTrue(row.value_label.get_selectable())
        row.copy()
        _spin(50)
        toasts = [w for w in _walk(stage) if w.has_css_class("lumaui-toast")]
        self.assertTrue(toasts, "a toast confirms the copy")
        self.assertIsNone(self.details.FactRow("link", "Your link", "x", copy=False).copy_button)


def _walk(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _walk(child)
        child = child.get_next_sibling()


def _controllers(widget):
    model = widget.observe_controllers()
    return [model.get_item(i) for i in range(model.get_n_items())]


class HoverKeepsGeometry(unittest.TestCase):
    """Hover changes paint, never size (Nick, 26 Sep: Memos' bar flickered as a hovered item grew and
    the bar re-laid out under the pointer). A hovered rule may set a size only as the same rule does
    at rest (its selector list holds the unhovered twin) or to reset one (none, 0)."""

    GEOMETRY = re.compile(r"^\s*(margin[\w-]*|padding[\w-]*|border(?:-\w+)?-width|min-width|min-height|transform"
                          r"|font-size|font-weight|letter-spacing|border-spacing|outline-width)\s*:\s*([^;]+);", re.M)

    def test_no_hover_rule_changes_a_size(self):
        for name in ("luma-appkit-base.css", "luma-appkit-bar.css", "luma-appkit-rows.css", "luma-appkit-media.css",
                     "lumaui-toolkit.css", "luma-appkit-creative.css"):
            text = re.sub(r"/\*.*?\*/", "", (APPKIT / name).read_text(), flags=re.S)
            for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
                selectors = [s.strip() for s in rule.group(1).split(",")]
                for selector in (s for s in selectors if ":hover" in s):
                    for prop, value in self.GEOMETRY.findall(rule.group(2)):
                        if value.strip() in ("none", "0", "0px") or selector.replace(":hover", "") in selectors:
                            continue
                        self.fail(f"{name}: {selector} sets {prop} on hover")


class ControlsFoldOnlyOnAPhone(unittest.TestCase):
    """Nick, 26 Sep: Calculator tiled narrow lost its window controls. Only a phone folds them."""

    def test_the_fold_is_gated_on_the_form_factor(self):
        text = (KIT / "widgets.py").read_text()
        self.assertRegex(text, r"if self\.window_controls is not None and mobile_form_factor\(\):")

    def test_form_factor_reads_the_override(self):
        import os
        from unittest import mock
        from luma_appkit.lumaui import mobile_form_factor
        with mock.patch.dict(os.environ, {"LUMA_FORM_FACTOR": "phone"}):
            self.assertTrue(mobile_form_factor())
        with mock.patch.dict(os.environ, {"LUMA_FORM_FACTOR": "desktop"}):
            self.assertFalse(mobile_form_factor())


@unittest.skipUnless(HAVE_DISPLAY, "needs GTK 4 and a display")
class Fit(unittest.TestCase):
    """An island's content never pushes the window's frame, title row or controls out: the island
    asks for little width and clips what does not fit, and overflowing() names it for conform."""

    def test_bar_menu_leaves_eight_above_the_bar(self):
        """Monitor, 26 Sep: a phone menu from the bar sat on it (gap 0); v70 leaves 8."""
        gi.require_version("Graphene", "1.0")
        from gi.repository import Graphene
        from luma_appkit.structure_drawer import MenuDrawer
        host, center, pill = Gtk.Box(), Gtk.Box(), Gtk.Box()
        host.get_height = lambda: 740
        center.bar = pill
        pill.compute_bounds = lambda _target: (True, Graphene.Rect().init(8, 664, 358, 44))
        self.assertEqual(MenuDrawer._above_bar(host, center), 740 - 664 + 8)

    def test_island_yields_and_clips_and_is_reported(self):
        from luma_appkit.lumaui import YieldingLayout, overflowing
        island = Gtk.Box()
        island.set_layout_manager(YieldingLayout(orientation=Gtk.Orientation.HORIZONTAL))
        island.set_overflow(Gtk.Overflow.HIDDEN)
        wide = Gtk.Label(label="12345678901234567890" * 4)
        island.append(wide)
        self.assertLessEqual(island.measure(Gtk.Orientation.HORIZONTAL, -1)[0], YieldingLayout.FLOOR)
        window = Gtk.Window(child=island)
        island.allocate(200, 40, -1, None)
        self.assertGreater(wide.get_width(), 200, "the content keeps its width and is clipped")
        self.assertGreater(island._lumaui_overflow, 0)
        island.get_mapped = lambda: True
        found = [line for line in overflowing(island) if "island gives it" in line]
        self.assertTrue(found)
        window.destroy()


if __name__ == "__main__":
    unittest.main()
