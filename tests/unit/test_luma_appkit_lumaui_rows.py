# SPDX-License-Identifier: Apache-2.0
"""LumaUI rows family (KB-A): navigation rows and sidebar variants (SB1), type
roles (TY1), ProgressLine (DT3), AvatarStack and presence (SB3), FavouritesStrip
(SB2), Mark (SB4) and rich menu rows (MN1).

`Sources` and `Pure` run anywhere; `Parts` builds real widgets on stock GTK 4
with the kit's sheets loaded and measures them against the numbers read off
luma-next-70.html (skipped without a display).
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPKIT = ROOT / "src/luma-platform/appkit"
KIT = APPKIT / "luma_appkit"
SHEET = APPKIT / "luma-appkit-rows.css"
FRAGMENT = json.loads((ROOT / "config/shared/design-tokens.d/rows.json").read_text())["lumaui"]
GALLERY = ROOT / "src/luma-platform/tools/lumaui-gallery/pages_rows.py"

PARTS = ("Navigation row slots", "Sidebar variants", "Avatar stack", "Mark", "Mark picker", "Progress line",
         "Favourites strip", "Rich menu rows", "Details pane additions", "App icon", "Note card")
EXPORTS = ("RowAction", "RowLead", "SIDEBAR_VARIANTS", "SIDEBAR_WIDTHS", "SidebarRow", "SidebarSection",
           "append_section", "apply_sidebar_variant", "type_font", "type_metrics", "PROGRESS_SIZES", "PROGRESS_TONES",
           "ProgressLine", "AvatarStack", "GroupFace", "PresenceFace", "Favourite", "FavouritesStrip", "MARK_HUES",
           "Mark", "MarkButton", "MarkPicker", "MarkValue", "MenuSection", "RichMenuItem", "APP_ICON_SIZES", "AppIcon", "NoteCard")

sys.path.insert(0, str(APPKIT))
try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, GLib, Gtk

    HAVE_GTK = True
    HAVE_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
except (ImportError, ValueError):
    HAVE_GTK = HAVE_DISPLAY = False


class Sources(unittest.TestCase):
    def test_every_part_has_a_banner_and_every_name_is_exported(self):
        css = SHEET.read_text()
        for part in PARTS:
            self.assertRegex(css, rf"/\* LumaUI: {re.escape(part)}\b", part)
        init = (KIT / "__init__.py").read_text()
        block = init[init.index("# ── LumaUI rows (KB-A)"):]
        for name in EXPORTS:
            self.assertIn(f'"{name}"', block, name)

    def test_the_sheet_uses_tokens_not_colours(self):
        css = re.sub(r"/\*.*?\*/", "", SHEET.read_text(), flags=re.S)
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b", "colours are @luma_* tokens")
        self.assertNotRegex(css, r"\brgba?\(", "colours are @luma_* tokens")
        self.assertEqual(css.count("{"), css.count("}"), "the sheet's braces balance")
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)

    def test_python_fallbacks_equal_the_fragment(self):
        from luma_appkit import rows_tokens
        for group, values in rows_tokens._FALLBACK.items():
            for key, value in values.items():
                self.assertEqual(FRAGMENT[group][key], value, f"{group}.{key}")

    def test_generated_tokens_carry_the_fragment(self):
        from luma_appkit import lumaui_tokens
        self.assertEqual(lumaui_tokens.NAV_PEOPLE["width"], 292)
        self.assertIn("mono", lumaui_tokens.TYPE_SCALE)
        tokens = (APPKIT / "luma-appkit-tokens.css").read_text()
        for name in ("--lumaui-nav-people-row-gap", "--lumaui-progress-hero-height", "--lumaui-mark-dot",
                     "@define-color luma_progress_fill", "@define-color luma_mark_violet", ".lumaui-t-mono"):
            self.assertIn(name, tokens)

    def test_v70_numbers(self):
        people, tree, lead = FRAGMENT["nav_people"], FRAGMENT["nav_tree"], FRAGMENT["row_lead"]
        # #m-side .crow: 292 wide, 9/10 padding, 12 gap, 12 round, a 46 face: 64 tall.
        self.assertEqual((people["width"], people["row_padding_y"], people["row_gap"], lead["face_large"]),
                         (292, 9, 12, 46))
        self.assertEqual(2 * people["row_padding_y"] + lead["face_large"], 64)
        self.assertEqual((tree["row_height"], tree["indent"], tree["twisty"]), (32, 16, 18))
        self.assertEqual(FRAGMENT["nav_destinations"]["row_height"], 36)
        self.assertEqual((FRAGMENT["progress"]["row_height"], FRAGMENT["progress"]["hero_height"]), (4, 8))
        self.assertEqual(FRAGMENT["type_scale"]["mono"]["size"], 13.5)

    def test_gallery_pages(self):
        text = GALLERY.read_text()
        for key in ("rows-people", "rows-destinations", "rows-resources", "rows-files", "rows-tree", "rows-type",
                    "rows-progress", "rows-stack", "rows-favourites", "rows-mark"):
            self.assertIn(f'"{key}"', text)

    def test_icons_the_family_names_are_imported(self):
        from luma_appkit.rows_mark import MARK_ICONS
        actions = ROOT / "assets/icon-theme/Prairie/symbolic/actions"
        for name in (*MARK_ICONS, "chevron-right", "pin", "image", "phone-missed", "phone-outgoing"):
            self.assertTrue((actions / f"lumaui-{name}-symbolic.svg").is_file(), name)


@unittest.skipUnless(HAVE_GTK, "GTK 4 introspection is not installed")
class Pure(unittest.TestCase):
    def test_sidebar_widths(self):
        from luma_appkit.rows_navigation import sidebar_width
        self.assertEqual([sidebar_width(v) for v in ("people", "destinations", "resources", "files", "tree")],
                         [292, 224, 248, 236, 284])
        self.assertEqual(sidebar_width("files", "wide"), 292)
        with self.assertRaises(ValueError):
            sidebar_width("chats")
        with self.assertRaises(ValueError):
            sidebar_width("people", "300")

    def test_stack_label(self):
        from luma_appkit.rows_people import stack_label
        self.assertEqual(stack_label(["Priya Raman"]), "Priya")
        self.assertEqual(stack_label(["Priya Raman", "Sam Ortiz"]), "Priya and Sam")
        self.assertEqual(stack_label(["A B", "C D", "E F", "G H", "I J"]), "A, C and 3 others")

    def test_mark_values_refuse_what_the_picker_cannot_make(self):
        from luma_appkit.rows_mark import MarkValue
        self.assertEqual(MarkValue().spoken, "Blue dot")
        self.assertEqual(MarkValue("icon", "green", "book").spoken, "Green book mark")
        for bad in ({"kind": "star"}, {"hue": "magenta"}, {"kind": "icon"}, {"kind": "emoji"}):
            with self.assertRaises(ValueError):
                MarkValue(**bad)

    def test_type_roles(self):
        from luma_appkit import TYPE_ROLES
        from luma_appkit.rows_type import type_font, type_metrics
        for role in ("reading", "assistant-title", "temperature", "place", "condition", "album-title", "section-day",
                     "dial", "mono"):
            self.assertIn(role, TYPE_ROLES)
        mono = type_font("mono")
        self.assertEqual(mono.get_family(), "IBM Plex Mono")
        self.assertTrue(mono.get_size_is_absolute())
        self.assertAlmostEqual(mono.get_size() / 1024, 13.5)
        self.assertEqual(type_metrics("reading")["line_height"], 24.75)
        self.assertAlmostEqual(type_font("album-title", phone=True).get_size() / 1024, 34)
        with self.assertRaises(ValueError):
            type_metrics("huge")


@unittest.skipUnless(HAVE_DISPLAY, "needs a display")
class Parts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from luma_appkit import lumaui
        lumaui.install(Gdk.Display.get_default())  # the kit's own loader brings the family sheet

    def setUp(self):
        from luma_appkit import rows_favourites, rows_mark, rows_navigation, rows_people, rows_progress
        self.nav, self.people, self.mark = rows_navigation, rows_people, rows_mark
        self.progress, self.fav = rows_progress, rows_favourites
        self.windows: list[Gtk.Window] = []

    def tearDown(self):
        for window in self.windows:
            window.destroy()

    def _spin(self, ms: int = 120) -> None:
        loop = GLib.MainLoop()
        GLib.timeout_add(ms, loop.quit)
        loop.run()

    def _show(self, child: Gtk.Widget, width: int = 900, height: int = 700) -> Gtk.Window:
        window = Gtk.Window(default_width=width, default_height=height)
        window.set_child(child)
        self.windows.append(window)
        window.present()
        self._spin()
        return window

    def _sidebar(self, variant: str, width: str | None = None) -> Gtk.Box:
        """A sidebar with NavigationSidebar's classes and list (widgets.py needs the Luma typelibs)."""
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.START, valign=Gtk.Align.START)
        for name in ("luma-navigation-sidebar", "luma-sidebar-surface"):
            sidebar.add_css_class(name)
        sidebar.list = Gtk.ListBox()
        sidebar.list.add_css_class("luma-navigation-list")
        sidebar.append(sidebar.list)
        self.nav.apply_sidebar_variant(sidebar, variant, width=width)
        return sidebar

    @staticmethod
    def _box(widget: Gtk.Widget, relative: Gtk.Widget) -> tuple[float, float, float, float]:
        """`widget`'s border box from `relative`'s border-box corner (compute_bounds counts from the content box)."""
        parent = relative.get_parent()
        ok, rect = widget.compute_bounds(parent)
        ok2, origin = relative.compute_bounds(parent)
        assert ok and ok2
        return (round(rect.get_x() - origin.get_x(), 1), round(rect.get_y() - origin.get_y(), 1),
                round(rect.get_width(), 1), round(rect.get_height(), 1))

    def _outer(self, widget: Gtk.Widget) -> tuple[float, float]:
        """A widget's border box (GTK's get_width is its content box, inside the padding)."""
        ok, rect = widget.compute_bounds(widget)
        assert ok
        return round(rect.get_width(), 1), round(rect.get_height(), 1)

    # ── SB1 ───────────────────────────────────────────────────────────────

    def test_file_sidebar_outside_width_in_app_window(self):
        from luma_appkit.structure_sidebar import SidebarFoot
        try:
            from luma_appkit.widgets import NavigationSidebar
        except ValueError as missing:  # widgets.py needs the Luma typelibs (the ThinkPad has them)
            self.skipTest(str(missing))

        for window_width in (1180, 390):
            sidebar = NavigationSidebar(variant="files", width="narrow")
            sidebar.append_section("Today")
            sidebar.append_row(self.nav.SidebarRow("Offsite-hero.jpg",
                lead=self.nav.RowLead.thumbnail(kind="image"), subtitle="2.1 MB"))
            sidebar.append_footer(SidebarFoot(search="Search",
                add=("Open a file", "folder-open", lambda: None)))
            body = Gtk.Box()
            body.add_css_class("luma-window-body")
            body.append(sidebar)
            body.append(Gtk.Box(hexpand=True))
            window = Gtk.Window(default_width=window_width, default_height=740)
            window.add_css_class("luma-app-window")
            window.set_child(body)
            self.windows.append(window)
            window.present()
            self._spin()
            ok, bounds = sidebar.compute_bounds(body)
            self.assertTrue(ok)
            self.assertEqual(round(bounds.get_width()), 236, f"{window_width}px app window")

    def test_people_row_matches_messages(self):
        RowLead, Row = self.nav.RowLead, self.nav.SidebarRow
        sidebar = self._sidebar("people")
        row = Row("Launch crew", lead=RowLead.group(["Priya Raman", "Sam Ortiz"]), meta="9:41",
                  subtitle="Sam: I'll bring the banner", attention=True, trail=3)
        plain = Row("Priya Raman", lead=RowLead.face("Priya Raman", online=True), meta="Tue", subtitle="Photos",
                    pinned=True)
        sidebar.list.append(row)
        sidebar.list.append(plain)
        self._show(sidebar)
        self.assertEqual(self._outer(sidebar)[0], 292)
        # v70 .crow: 64 tall, the 46 face at 10,9, the text from 68.
        self.assertEqual(self._outer(row)[1], 64)
        self.assertEqual(self._box(row.lead, row)[:3], (10.0, 9.0, 46.0))
        self.assertEqual(self._box(row.title_label, row)[0], 68.0)
        title, meta = self._box(row.title_label, row), self._box(row.meta_label, row)
        self.assertGreater(meta[0], title[0] + 40, "the time sits at the end of line 1")
        self.assertTrue(row.has_css_class("attention"))
        self.assertTrue(row._badge.has_css_class("attention"), "an unread count is in the accent")
        self.assertIs(row._trail_box.get_parent(), row._second, "with a time, the count sits on line 2")
        self.assertIn("3 unread", _label(row))
        # the group's two faces, 0.7 of the lead, corners opposite
        members = [c for c in _children(row.lead)]
        self.assertEqual([self._box(m, row.lead)[:3] for m in members], [(0.0, 0.0, 32.0), (14.0, 14.0, 32.0)])
        # presence: a 12 px dot at the face's lower right
        self.assertEqual(self._box(plain.lead.dot, plain.lead), (35.0, 35.0, 12.0, 12.0))
        self.assertTrue(plain._pin.get_visible(), "a pinned row with no count shows the pin")
        plain.set_count(2)
        self.assertFalse(plain._pin.get_visible(), "a count replaces the pin")

    def test_settings_sidebar_leaves_the_island_at_236(self):
        """v70 .cfside: 236 outside; its own right padding is the gap, so the frame's 8 comes out of it."""
        from luma_appkit.rows_navigation import apply_sidebar_variant
        sidebar = Gtk.Box()
        apply_sidebar_variant(sidebar, "settings")
        self.assertEqual(sidebar.get_size_request()[0], 236)
        self.assertTrue(sidebar.has_css_class("lumaui-sidebar-settings"))

    def test_compact_account_card_is_settings_card(self):
        from luma_appkit.content_cards import AccountCard
        card = AccountCard("Nick", compact=True)
        self.assertTrue(card.has_css_class("compact"))
        avatar = card.get_child().get_first_child()
        self.assertEqual(avatar.get_size_request()[0] if avatar.get_size_request()[0] > 0 else avatar.size, 34)

    def test_medium_faces_make_phone_rows(self):
        sidebar = self._sidebar("people", "regular")
        row = self.nav.SidebarRow("Sam Ortiz", lead=self.nav.RowLead.face("Sam Ortiz", size="medium"),
                                  subtitle="Voice · missed", subtitle_icon="phone-missed", attention="missed",
                                  trail="9:02")
        sidebar.list.append(row)
        self._show(sidebar)
        self.assertEqual(self._outer(sidebar)[0], 272)
        # v70 .pnrow: 8 + 38 + 8. GTK rounds each text line up to a whole pixel where CSS keeps the
        # fraction, so two lines can come to a pixel more than the 38 face (the conform rule for a line).
        self.assertAlmostEqual(self._outer(row)[1], 54, delta=1, msg="v70 .pnrow: 8 + 38 + 8")
        self.assertEqual(self._box(row.lead, row)[:3], (8.0, 8.0, 38.0))
        self.assertEqual(self._box(row.title_label, row)[0], 57.0)
        self.assertTrue(row.has_css_class("missed"))
        self.assertIs(row._trail_box.get_parent(), row._line, "without a time on line 1 the trail ends the row")

    def test_destination_resource_and_file_rows(self):
        RowLead, Row = self.nav.RowLead, self.nav.SidebarRow
        cases = (("destinations", Row("Today", lead=RowLead.icon("sun"), trail=4), 36, (10.0, 10.0, 16.0)),
                 ("resources", Row("Processor", lead=RowLead.well("cpu"), subtitle="12%"), None, (10.0, None, 32.0)),
                 ("files", Row("IMG_2041.jpg", lead=RowLead.thumbnail(kind="missing"), subtitle="4.2 MB",
                               missing=True), 46, (6.0, 6.0, 42.0)))
        for variant, row, height, lead in cases:
            sidebar = self._sidebar(variant)
            sidebar.list.append(row)
            self._show(sidebar)
            box = self._box(row.lead, row)
            if height is not None:
                self.assertAlmostEqual(self._outer(row)[1], height, delta=1, msg=variant)  # whole-pixel text lines
            self.assertEqual(box[0], lead[0], variant)
            self.assertEqual(box[2], lead[2], variant)
            if lead[1] is not None:
                self.assertEqual(box[1], lead[1], variant)
        self.assertTrue(cases[2][1].has_css_class("missing"))

    def test_tree_rows_indent_open_and_take_drops(self):
        Row, MarkValue = self.nav.SidebarRow, self.mark.MarkValue
        opened, dropped = [], []
        sidebar = self._sidebar("tree")
        folder = Row("Work", folder=True, expanded=False, mark=MarkValue(hue="violet"), trail=12,
                     on_expand=opened.append, drag="f:work", on_drop=lambda v, w: dropped.append((v, w)))
        leaf = Row("Launch plan", depth=1, expanded=None, mark=MarkValue("icon", "blue", "file-text"), drag="n:1",
                   on_drop=lambda v, w: dropped.append((v, w)))
        sidebar.list.append(folder)
        sidebar.list.append(leaf)
        self._show(sidebar)
        self.assertEqual(self._outer(sidebar)[0], 284)
        self.assertEqual(self._outer(folder)[1], 32)
        self.assertEqual(self._box(folder.twisty, folder)[:3], (4.0, 7.0, 18.0))
        self.assertEqual(self._box(leaf, sidebar.list)[0] - self._box(folder, sidebar.list)[0], 16.0,
                         "v70 .trow: the row steps in 16 per level")
        self.assertEqual(self._box(folder.mark_button, folder)[0], 26.0, "v70: the mark at 26")
        self.assertEqual(self._box(folder.mark_button, folder)[2], 22.0)
        folder.emit("activate")
        self.assertTrue(folder.expanded, "a folder opens from its row")
        folder.emit("activate")
        folder._key(None, Gdk.KEY_Right, 0, 0)
        self.assertTrue(folder.expanded)
        self.assertTrue(folder.has_css_class("open"))
        folder._key(None, Gdk.KEY_Left, 0, 0)
        self.assertEqual(opened, [True, False, True, False])
        self.assertEqual([folder.drop_place(y) for y in (2, 16, 30)], ["before", "inside", "after"])
        self.assertEqual([leaf.drop_place(y) for y in (2, 30)], ["before", "after"], "a page with no children")
        self.assertTrue(folder._dropped(None, "n:1", 0, 16))
        self.assertFalse(folder._dropped(None, "f:work", 0, 16), "a row never drops on itself")
        self.assertEqual(dropped, [("n:1", "inside")])

    def test_sections_carry_an_action(self):
        pressed = []
        sidebar = self._sidebar("destinations")
        sidebar.list.append(self.nav.SidebarRow("Today", lead=self.nav.RowLead.icon("sun")))
        section = self.nav.append_section(sidebar, "Lists", action=("plus", "New list", lambda: pressed.append(1)))
        self.assertTrue(section.has_css_class("separated"))
        section.action_button.emit("clicked")
        self.assertEqual(pressed, [1])
        self.assertFalse(section.get_selectable())

    def test_row_refuses_misuse(self):
        Row = self.nav.SidebarRow
        with self.assertRaises(ValueError):
            Row("x", attention="urgent")
        with self.assertRaises(TypeError):
            Row("x", trail=True)
        with self.assertRaises(ValueError):
            self.nav.RowLead.face("x", size="huge")

    # ── SB3, SB2, SB4, DT3 ────────────────────────────────────────────────

    def test_avatar_stack(self):
        stack = self.people.AvatarStack(["Priya Raman", "Sam Ortiz", "Ana Lima"], size="row")
        big = self.people.AvatarStack(["A B", "C D", "E F", "G H", "I J"], size="header", max=4, live={"A B"})
        box = Gtk.Box()
        box.append(stack)
        box.append(big)
        self._show(box)
        self.assertEqual(self._outer(stack)[0], 3 * 18 - 2 * 6, "v70 .tkstk: 18 faces overlapping 6")
        faces = list(_children(big))
        self.assertEqual(len(faces), 4)
        self.assertEqual(faces[-1].get_label(), "+2")
        self.assertTrue(faces[0].has_css_class("live"))
        self.assertEqual(_label(big), "A, C and 3 others, 1 here now")
        with self.assertRaises(ValueError):
            self.people.AvatarStack([], size="huge")

    def test_progress_line(self):
        lines = {size: self.progress.ProgressLine(0.5, size=size) for size in ("row", "tile", "meter", "hero")}
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=200, halign=Gtk.Align.START)
        for line in lines.values():
            box.append(line)
        self._show(box)
        self.assertEqual({k: self._outer(v)[1] for k, v in lines.items()}, {"row": 4, "tile": 3, "meter": 4, "hero": 8})
        self.assertEqual(lines["row"].fill.get_width(), 100)
        lines["row"].set_fraction(1.4)
        self.assertEqual(lines["row"].fraction, 1.0)
        self._spin(500)
        self.assertEqual(lines["row"].fill.get_width(), 200)
        with self.assertRaises(ValueError):
            self.progress.ProgressLine(0.1, tone="blue")

    def test_favourites_strip(self):
        opened = []
        strip = self.fav.FavouritesStrip([("Priya", "Priya Raman", None), ("Sam", "Sam Ortiz", None)],
                                         on_open=opened.append)
        places = self.fav.FavouritesStrip([self.fav.Favourite("Home", icon="house", sub="12 min")], kind="places")
        strip.set_size_request(268, -1)
        places.set_size_request(252, -1)
        places.set_halign(Gtk.Align.START)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.START)
        box.append(strip)
        box.append(places)
        self._show(box)
        # v70 .pnfavs: 4 columns of 64 with 4 between; .mpfavs: 3 of 80 with 6.
        self.assertEqual(self._outer(strip.buttons[0])[0], 64)
        self.assertEqual(self._outer(places.buttons[0])[0], 80)
        strip.buttons[1].emit("clicked")
        self.assertEqual(opened[0].person, "Sam Ortiz")
        self.assertEqual(_label(places.buttons[0]), "Home, 12 min")

    def test_mark_and_picker(self):
        MarkValue = self.mark.MarkValue
        picked = []
        button = self.mark.MarkButton(MarkValue(hue="orange"), on_change=picked.append)
        from luma_appkit.structure_layers import LayerHost
        host = LayerHost(Gtk.Box(), name="window")
        box = Gtk.Box()
        box.append(button)
        host.set_child(box)
        self._show(host)
        self.assertEqual(self._outer(button)[0], 22)
        self.assertEqual(self._outer(button.mark)[0], 18)
        picker = button.open()
        self.assertTrue(button.has_css_class("open"))
        self.assertIs(picker.get_parent(), host)
        picker._pick_hue("teal")
        self.assertEqual(picked[-1], MarkValue(hue="teal"))
        self.assertIs(picker.get_parent(), host, "a colour keeps the picker open")
        picker.tabs["icon"].set_active(True)
        picker.search.set_text("book")
        picker._searched(picker.search)
        self.assertTrue(all("book" in b.get_tooltip_text() for b in picker.grid_buttons))
        picker.grid_buttons[0].emit("clicked")
        self.assertEqual(picked[-1].kind, "icon")
        self.assertIsNone(picker.get_parent(), "an icon closes it")
        self.assertFalse(button.has_css_class("open"))
        self.assertEqual(button.value.hue, "teal")

    # ── DP1 ───────────────────────────────────────────────────────────────

    def test_details_pane_closable_main_back_and_counts(self):
        from luma_appkit.content_badges import CountBadge
        from luma_appkit.structure_details import DetailsPane
        from luma_appkit.structure_layers import LayerHost
        backs = []
        day = DetailsPane("Tuesday", main=True, closable=True)
        side = DetailsPane("Details", main=True)
        body = Gtk.Box()
        body.append(day)
        body.append(side)
        host = LayerHost(body, name="window")
        self._show(host, 1100)
        self.assertTrue(day.shown and day.close_button.get_visible())
        self.assertFalse(side.close_button.get_visible(), "a main pane has no close unless closable")
        day.close()
        self.assertFalse(day.shown, "a closable main pane closes")
        day.show(subject="anything")
        self.assertFalse(day.shown, "and stays closed while the subject changes")
        day.show(open=True)
        self.assertTrue(day.shown)
        side.close()
        self.assertTrue(side.shown, "a main pane that is not closable stays")
        day.set_back("Monday", lambda: backs.append(1))
        self.assertTrue(day.back_button.get_visible())
        self.assertTrue(day.back_button.get_parent().has_css_class("with-back"))
        self.assertEqual(self._box(day.back_button, day.back_button.get_parent())[0], 10.0, "v70 .calback: 6 into the inset")
        day.back_button.emit("clicked")
        self.assertEqual(backs, [1])
        day.set_back(None)
        self.assertFalse(day.back_button.get_visible())
        people = day.add_section("People", count=4)
        steps = day.add_section("Steps", count="3 of 5")
        self.assertTrue(any(isinstance(c, CountBadge) for c in _children(people)))
        self.assertEqual(_label(steps), "Steps, 3 of 5")
        with self.assertRaises(TypeError):
            day.add_section("x", count=True)

    # ── ID1, ID2, ID3 ─────────────────────────────────────────────────────

    def test_app_icon_note_card_and_lit_glow(self):
        from luma_appkit.content_cards import ContentLitHeader
        from luma_appkit.rows_identity import AppIcon, NoteCard, app_monogram
        from luma_appkit.rows_lit import glow_colours
        opened = []
        mono = AppIcon(name="Kiln", size=56, category="create")
        note = NoteCard("Launch plan", "Freeze strings on the 10th", on_activate=lambda: opened.append(1))
        lit = ContentLitHeader(tone="luma")
        tide = ContentLitHeader(hues=(330, 250))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.START)
        for widget in (mono, note, lit, tide):
            box.append(widget)
        self._show(box)
        self.assertEqual(self._outer(mono), (56, 56))
        self.assertTrue(mono.has_css_class("mono"))
        self.assertEqual((mono.monogram, mono.hue), ("Ki", 25), "the category's hue")
        self.assertEqual(app_monogram("  2048!"), "20")
        self.assertEqual(AppIcon(name="Blender", size=48, hue=30).hue, 30.0)
        with self.assertRaises(ValueError):
            AppIcon(name="x", size=50)  # v70 sizes only
        note.emit("clicked")
        self.assertEqual(opened, [1])
        self.assertEqual(_label(note), "Launch plan, Freeze strings on the 10th")
        self.assertTrue(lit.glow.get_visible() and not lit.wash.get_visible())
        self.assertEqual(lit.tone, "luma")
        self.assertTrue(tide.glow.get_visible())
        self.assertEqual(len(glow_colours((330, 250), False, True)), 2)
        self.assertAlmostEqual(glow_colours(None, True, True)[0].alpha, 0.75)
        plain = ContentLitHeader(tone="media")
        self.assertFalse(plain.glow.get_visible(), "a category wash has no glow")
        with self.assertRaises(ValueError):
            ContentLitHeader(tone="sunset")

    # ── MN1 ───────────────────────────────────────────────────────────────

    def test_rich_menu_adjacent_trailing_actions_use_one_token_width(self):
        from luma_appkit.rows_menu import RichMenuItem
        from luma_appkit.rows_navigation import RowAction

        events = []
        row = RichMenuItem(
            "zsh", icon="terminal", rename=True,
            on_rename=lambda name: events.append(("rename", name)),
            trail=[RowAction("x", "Close session", lambda: events.append("closed"))],
        ).menu_widget(lambda: events.append("close menu"))
        self._show(row)
        pencil, close = row.trail_buttons
        action_box = pencil.get_parent()
        self.assertIs(action_box, close.get_parent())
        self.assertTrue(action_box.has_css_class("lumaui-menu-actions"))
        first = self._box(pencil, row)
        second = self._box(close, row)
        text = self._box(row._text, row)
        self.assertEqual((first[2], second[2]), (22, 22))
        self.assertAlmostEqual(first[0] - (text[0] + text[2]), 10, delta=1,
                               msg="the shared row gap still separates text and the action group")
        self.assertAlmostEqual(second[0] - first[0], 22, delta=1,
                               msg="v70 .tsesspop .tx buttons touch without the row's 10px gap")
        self.assertEqual(pencil.get_property("tooltip-text"), "Rename zsh")
        self.assertEqual(close.get_property("tooltip-text"), "Close session")
        close.emit("clicked")
        self._spin()
        self.assertEqual(events, ["close menu", "closed"])

    def test_rich_menu_rows(self):
        from luma_appkit.action_bubble import FloatingMenu
        from luma_appkit.rows_menu import MenuSection, RichMenuItem
        from luma_appkit.rows_navigation import RowAction
        from luma_appkit.structure_layers import LayerHost
        events = []
        anchor = Gtk.Button(label="Menu")
        host = LayerHost(anchor, name="window")
        self._show(host)
        menu = FloatingMenu([
            RichMenuItem("Grid", icon="grid-3x3", toggle=False, on_toggle=lambda on: events.append(("grid", on))),
            RichMenuItem("Timer", icon="timer", value="3 s", on_activate=lambda: events.append("timer")),
            RichMenuItem("zsh", icon="terminal", subtitle="~/Projects", rename=True,
                         on_rename=lambda name: events.append(("rename", name)),
                         trail=[RowAction("x", "Close session", lambda: events.append("closed"))]),
            RichMenuItem("Importing", icon="download", busy=True),
            None,
            MenuSection(Gtk.Label(label="Import card"), label="Import"),
        ]).popup(anchor)
        self._spin()
        grid, timer, zsh, busy = menu.buttons[:4]
        self.assertEqual(self._outer(timer)[1], 36, "v70 .crmi: 36 tall")
        grid.emit("clicked")
        self.assertTrue(grid.switch.get_active())
        self.assertTrue(menu.is_open, "a switch keeps the menu open")
        self.assertEqual(events, [("grid", True)])
        zsh.start_rename()
        zsh.entry.set_text("build")
        zsh.entry.emit("activate")
        self.assertEqual(events[-1], ("rename", "build"))
        self.assertEqual(zsh.title.get_label(), "build")
        self.assertFalse(busy.get_sensitive(), "a busy row does nothing")
        timer.emit("clicked")
        self.assertFalse(menu.is_open, "a value row closes the menu")
        self._spin()
        self.assertEqual(events[-1], "timer")
        with self.assertRaises(ValueError):
            RichMenuItem("x", toggle=True, value="y")
        with self.assertRaises(ValueError):
            RichMenuItem("x", rename=True)

    def test_rich_rows_present_in_the_drawer(self):
        from luma_appkit.rows_menu import RichMenuItem
        from luma_appkit.structure_drawer import MenuDrawer
        from luma_appkit.structure_layers import LayerHost
        anchor = Gtk.Button(label="Menu")
        host = LayerHost(anchor, name="window")
        self._show(host, 375, 700)
        drawer = MenuDrawer.present_items(anchor, [RichMenuItem("Grid", toggle=True)])
        rows = [w for w in _walk(drawer) if w.has_css_class("lumaui-menu-rich")]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].has_css_class("drawer"))
        drawer.close()


def _walk(widget: Gtk.Widget):
    yield widget
    for child in _children(widget):
        yield from _walk(child)


def _children(widget: Gtk.Widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def _label(widget: Gtk.Widget) -> str:
    """The accessible label a widget last set (GTK has no public getter; the kit keeps it)."""
    return getattr(widget, "_lumaui_spoken", None) or _SPOKEN.get(id(widget), "")


_SPOKEN: dict[int, str] = {}
if HAVE_GTK:
    _original = Gtk.Accessible.update_property

    def _remember(self, properties, values):  # noqa: ANN001
        for prop, value in zip(properties, values):
            if prop == Gtk.AccessibleProperty.LABEL:
                _SPOKEN[id(self)] = value
        return _original(self, properties, values)

    Gtk.Accessible.update_property = _remember


if __name__ == "__main__":
    unittest.main()
