# SPDX-License-Identifier: Apache-2.0
"""LumaUI action bar, v71 (K-BAR): the bar grows, More, menus and confirms from the bar, the held row,
the field that replaces the row, the bar's frame on a phone, and one safe area.

Builds real widgets on stock GTK 4 and needs a display (it never presents a window); skipped otherwise.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPKIT = ROOT / "src/luma-platform/appkit"
TOKENS = json.loads((ROOT / "config/shared/design-tokens.json").read_text())["lumaui"]
sys.path.insert(0, str(APPKIT))

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, GLib, Gtk

    HAVE_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
except (ImportError, ValueError):
    HAVE_DISPLAY = False


class Numbers(unittest.TestCase):
    def test_v71_metrics(self):
        ac = TOKENS["action_center"]
        self.assertEqual((ac["phone_control"], ac["phone_control_radius"], ac["phone_bar_padding"],
                          ac["phone_bar_radius"], ac["phone_bar_bottom"], ac["phone_bar_gap"]), (48, 20, 6, 26, 34, 2))
        self.assertEqual((ac["grow_width"], ac["grow_side"], ac["phone_side_margin"]), (380, 12, 12))
        self.assertEqual((ac["frame_side"], ac["frame_bottom"], ac["frame_radius"], ac["frame_menu_room"],
                          ac["frame_sheet_room"]), (16, 34, 26, 140, 120))
        self.assertEqual(ac["safe_gap"], 20)

    def test_css_blocks(self):
        css = (APPKIT / "luma-appkit-bar.css").read_text()
        for banner in ("/* LumaUI: Action bar on a phone (v71) */", "/* LumaUI: Grown bar (v71) */",
                       "/* LumaUI: Bar frame (v71) */"):
            self.assertIn(banner, css)
        self.assertIn("box.lumaui-ac-row > * { margin-left: 0; margin-right: 0; }", css,
                      "bar children carry no margins on a phone")


@unittest.skipUnless(HAVE_DISPLAY, "needs GTK 4 and a display")
class Bar(unittest.TestCase):
    def setUp(self):
        from luma_appkit import action_bubble, action_center, action_dialog, action_toast, bar_frame, bar_panel, menus
        self.ac, self.bubble, self.dialog, self.toast = action_center, action_bubble, action_dialog, action_toast
        self.frame, self.panel, self.menus = bar_frame, bar_panel, menus
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.destroy()

    def _window(self, child, width=1000, height=700):
        window = Gtk.Window()
        window.set_default_size(width, height)
        window.set_child(child)
        self.windows.append(window)
        return window

    @staticmethod
    def _spin(ms=40):
        loop = GLib.MainLoop()
        GLib.timeout_add(ms, loop.quit)
        loop.run()

    def _center(self, width=1000):
        host = self.toast.ToastHost(Gtk.Box())
        self._window(host)
        host.get_width = lambda: width
        center = self.ac.ActionCenter().attach(host)
        return center, host

    def test_grow_toggles_and_takes_the_standard_width(self):
        ac = self.ac
        center, _host = self._center(1000)
        info = ac.BarAction("info", tooltip="Details", panel=lambda: Gtk.Label(label="Facts"))
        center.show_bar([info, ac.BarAction("share-2", tooltip="Share")])
        button = center.bar_row.get_first_child()
        changes = []
        center.connect("panel-changed", lambda _c, key: changes.append(key))
        self.assertEqual(center.layout(1000)[1], -1, "a bar is as wide as its buttons until it grows")
        button.emit("clicked")
        self.assertEqual(center.grown, "info")
        self.assertTrue(center.panel.get_visible())
        self.assertTrue(button.has_css_class("on"), "the button that grew it is the raised chip")
        self.assertEqual(center.state, "bar", "the row stays: growing is not a new state")
        self.assertEqual(center.layout(1000)[1], 380, "grown in a window: min(380, window − 24)")
        self.assertEqual(center.layout(300)[1], 276, "grown on a phone: the width less 12 a side")
        self.assertEqual(center.layout(300)[2], 120, "a grown phone panel stops 120 below the top")
        button.emit("clicked")
        self.assertIsNone(center.grown, "tapping the same button again folds it")
        self.assertFalse(center.panel.get_visible())
        self.assertFalse(button.has_css_class("on"))
        self.assertEqual(changes, ["info", ""])

    def test_grown_wide_bar_keeps_its_width(self):
        ac = self.ac
        center, _host = self._center(1000)
        center.show_bar([ac.BarPrompt("Reply…"), ac.BarAction("ellipsis", tooltip="More", panel=Gtk.Label)])
        center.grow("more", Gtk.Label(label="x"))
        self.assertEqual(center.layout(1000)[1], 700, "the row's layout holds: a wide bar never narrows to 380")

    def test_panel_rows_fold_after_acting(self):
        ac = self.ac
        center, _host = self._center()
        done = []
        center.show_bar([ac.BarAction("ellipsis", tooltip="More")])
        rows = self.panel.panel_list(["Arrange", self.bubble.MenuItem("Rename", icon="pencil",
                                                                       on_activate=lambda: done.append("r")),
                                      None, ac.BarAction("trash-2", "Delete", danger=True)])
        center.grow("more", rows)
        rename = [c for c in _children(rows) if isinstance(c, self.panel.PanelRow)][0]
        rename.emit("clicked")
        self.assertEqual(done, ["r"])
        self.assertIsNone(center.grown, "a panel closes after an action in it")
        delete = [c for c in _children(rows) if isinstance(c, self.panel.PanelRow)][-1]
        self.assertTrue(delete.has_css_class("danger"))

    def test_panel_heading_action_folds_then_acts(self):
        ac = self.ac
        center, _host = self._center()
        done = []
        center.show_bar([ac.BarAction("ellipsis", tooltip="More")])
        bare = self.panel.PanelHeading("Albums")
        self.assertEqual(bare.label.get_label(), "Albums")
        self.assertIsNone(bare.action_button, "a heading has no key unless it is given an action")
        heading = self.panel.PanelHeading("Albums", action=ac.BarAction(
            "plus", "New album", on_activate=lambda: done.append(center.grown)))
        center.grow("more", heading)
        self.assertEqual(heading.label.get_label(), "Albums")
        self.assertTrue(heading.action_button.has_css_class("lumaui-panel-heading-action"))
        heading.action_button.emit("clicked")
        self.assertEqual(done, [None], "the key folds the panel, then acts (so it can grow another)")

    def test_zoom_range_steps(self):
        from luma_appkit import ZoomControl

        center, _host = self._center()
        asked = []
        zoom = ZoomControl(96, asked.append, kind="range", minimum=96, maximum=340, steps=True)
        center.show_bar([zoom])
        self.assertTrue(zoom.widget.has_css_class("steps"))
        self.assertTrue(zoom.widget.get_visible(), "the panel's size control stays on a phone")
        zoom.in_key.emit("clicked")
        self.assertAlmostEqual(asked[-1], 96 + (340 - 96) / 6)
        zoom.out_key.emit("clicked")
        zoom.out_key.emit("clicked")
        self.assertEqual(asked[-1], 96, "the keys stop at the ends")
        with self.assertRaises(ValueError):
            ZoomControl(1.0, print, kind="well", steps=True)

    def test_escape_and_new_bar_fold(self):
        ac = self.ac
        center, _host = self._center()
        center.show_bar([ac.BarAction("ellipsis", tooltip="More")])
        center.grow("more", Gtk.Label(label="x"))
        self.assertTrue(center._panel_key_pressed(None, Gdk.KEY_Escape, 0, 0))
        self.assertIsNone(center.grown)
        center.grow("more", Gtk.Label(label="x"))
        center.show_bar([ac.BarAction("share-2", tooltip="Share")])
        self.assertIsNone(center.grown, "a new bar folds the panel")

    def test_entry_replaces_the_row(self):
        ac = self.ac
        center, _host = self._center()
        center.show_bar([ac.BarAction("plus", tooltip="Add")])
        field = self.panel.PanelField("search", "To:")
        center.grow("new", Gtk.Label(label="Suggestions"), entry=field)
        self.assertFalse(center.bar_row.get_visible(), "a field that is the point of a panel replaces the row")
        self.assertTrue(center.entry_row.get_visible())
        self.assertIs(center.entry_row.get_first_child(), field)
        close = center.entry_row.get_last_child()
        self.assertEqual(close.bar_item.icon, "x")
        close.emit("clicked")
        self.assertTrue(center.bar_row.get_visible())
        self.assertIsNone(center.grown)

    def test_more_moves_what_does_not_fit_on_a_phone(self):
        ac = self.ac
        center, _host = self._center(300)
        center._phone = True
        center.show_bar([ac.BarAction("folder-plus", tooltip="New folder")] +
                        [ac.BarAction(icon, tooltip=icon) for icon in ("copy", "scissors", "pencil", "star", "tag")] +
                        [ac.BarAction("", "Done", primary=True)])
        center._fit()
        hidden = [c.bar_item.icon for c in center._overflow if hasattr(c, "bar_item")]
        self.assertTrue(hidden, "a bar that doesn't fit moves items into ⋯")
        self.assertEqual(hidden[0], "tag", "single actions leave first, from the end")
        self.assertNotIn("folder-plus", hidden, "the first control stays")
        children = _children(center.bar_row)
        self.assertIs(children[-2], center._more_button, "⋯ sits before the key")
        self.assertEqual(children[-1].bar_item.label, "Done", "the key stays")
        panel = center._more_panel()
        words = [c.label for c in _children(panel) if isinstance(c, self.panel.PanelRow)]
        self.assertEqual(words[0], "tag")

    def test_more_lists_explicit_items(self):
        ac = self.ac
        center, _host = self._center()
        center.show_bar([ac.BarAction("share-2", tooltip="Share")],
                        more=[self.bubble.MenuItem("Print", icon="printer")])
        self.assertIsNotNone(center._more_button)
        center._more_button.emit("clicked")
        self.assertEqual(center.grown, "more")
        self.assertTrue(center._more_button.has_css_class("on"))

    def test_phone_glyph_only_and_shared_row(self):
        ac = self.ac
        center, _host = self._center(300)
        center.show_bar([ac.BarAction("share-2", "Share"), ac.BarAction("pencil", "Edit", primary=True, keep_label=True),
                         ac.BarAction("trash-2", tooltip="Delete")])
        center._apply_width()
        share, edit, _delete = _children(center.bar_row)
        self.assertTrue(center._phone)
        self.assertFalse(share.bar_words.get_visible(), "on a phone an icon-and-word button shows its icon alone")
        self.assertTrue(edit.bar_words.get_visible(), "a key that keeps its words keeps them")
        center.grow("x", Gtk.Label(label="x"))
        self.assertTrue(edit.get_hexpand(), "grown, the labelled key takes the rest")
        self.assertFalse(share.get_hexpand(), "icon buttons stay 48")

    def test_hold_row(self):
        ac = self.ac
        center, _host = self._center()
        released = []
        center.show_bar([ac.BarAction("share-2", tooltip="Share")])
        center.hold("press-release.write", "file-text", on_release=lambda: released.append(1))
        self.assertTrue(center.hold_row.get_visible())
        self.assertEqual(center.held, "press-release.write")
        prompt = _labels(center.entry_row)
        self.assertIn("Where are we moving this?", prompt)
        center.hold_destination("Launch", action=ac.BarAction("", "Move here"))
        self.assertIn("Launch", _labels(center.entry_row))
        self.assertEqual(center.layout(1000)[1], 380, "holding grows the bar")
        stop = center.hold_row.get_last_child()
        stop.emit("clicked")
        self.assertEqual(released, [1])
        self.assertIsNone(center.held)
        self.assertTrue(center.bar_row.get_visible())

    def test_clearing_is_x(self):
        ac = self.ac
        with self.assertRaises(ValueError):
            ac.BarContext("image", "3 photos", clear_icon="check")
        chip = ac.make_control(ac.BarChip("3 photos", icon="image", on_dismiss=lambda: None))
        close = chip.get_last_child()
        self.assertEqual(close.get_tooltip_text(), "Clear selection")

    def test_confirm_in_bar(self):
        ac = self.ac
        center, _host = self._center()
        done = []
        center.show_bar([ac.BarAction("trash-2", tooltip="Delete")])
        panel = self.dialog.DestructiveDialog.in_bar(center, title="Delete 3 photos?", body="They're gone for good.",
                                                     on_confirm=lambda: done.append("ok"))
        self.assertEqual(center.grown, "confirm")
        panel.action_button.emit("clicked")
        self.assertEqual(done, ["ok"])
        self.assertIsNone(center.grown)
        with self.assertRaises(ValueError):
            self.dialog.DestructiveDialog.in_bar(center, title="Are you sure?", body="x")

    def test_bar_menu(self):
        ac = self.ac
        center, host = self._center(1000)
        center.show_bar([ac.BarAction("ellipsis", tooltip="More")])
        button = center.bar_row.get_first_child()
        items = [self.bubble.MenuItem("Rename", icon="pencil")]
        shown = self.menus.bar_menu(button, items)
        self.assertIsInstance(shown, self.bubble.FloatingMenu, "on a computer a menu floats by its button")
        shown.close()
        window = host.get_root()
        window_host = self.frame.LayerHost.window_host(host)
        window_host.get_width = lambda: 375
        shown = self.menus.bar_menu(button, items)
        self.assertNotIsInstance(shown, self.bubble.FloatingMenu)
        self.assertTrue(center.grown and center.grown.startswith("menu:"), "on a phone a bar's menu grows the bar")
        center.fold()
        outside = Gtk.Button()
        frame = self.menus.bar_menu(window_host.get_child() if window_host.get_child() is not None else outside, items)
        self.assertIsInstance(frame, self.frame.BarFrame, "from elsewhere a menu takes the bar's frame")
        self.assertEqual((frame.get_margin_start(), frame.get_margin_end(), frame.get_margin_bottom()), (16, 16, 34))
        frame.close()
        self.assertIsNotNone(window)

    def test_floating_menu_on_a_phone_is_the_bar_frame(self):
        stage = self.frame.LayerHost(Gtk.Button(), name="window")
        self._window(stage)
        stage.get_width = lambda: 375
        menu = self.bubble.FloatingMenu([self.bubble.MenuItem("Today", icon="calendar")], title="Due")
        menu.popup(stage.get_child())
        self.assertIsInstance(menu._drawer, self.frame.BarFrame)
        self.assertTrue(menu.is_open)
        menu.close()
        self.assertFalse(menu.is_open)

    def test_safe_area_targets(self):
        scroller = Gtk.ScrolledWindow()
        content = Gtk.Box()
        scroller.set_child(content)
        target = self.ac._safe_target(scroller)
        self.assertIs(target[0], content)
        self.ac._set_room(*target, 88)
        self.assertEqual(content.get_margin_bottom(), 88)
        text = Gtk.ScrolledWindow(child=Gtk.TextView())
        self.assertEqual(self.ac._safe_target(text)[1], "text")

    # ── second wave: two-row bar, search, tiles, sheet, share, badge, toasts, camera frame ──

    def test_a_bar_stands_in_by_its_own_side(self):
        ac = self.ac
        center, _host = self._center(616)
        center.show_bar([ac.BarPrompt("Reply to Priya and Nora…")])
        center.set_side(24)
        self.assertEqual(center.layout(616)[1], 568, "v71 Charlie: min(720, 616 - 48)")

    def test_two_row_bar_with_a_prompt_foot(self):
        # v71 Charlie's phone thread bar: the thread's keys, then the reply well (reply glyph) and Attach.
        ac = self.ac
        center, _host = self._center(375)
        center.set_editor(ac.ActionEditor("Reply", "reply", primary=ac.BarAction("send-horizontal", "Send")))
        center.show_bar([ac.BarAction("archive", tooltip="Archive")],
                        entry=ac.BarPrompt("Reply to Priya and Nora…", icon="reply",
                                           tools=[ac.BarAction("paperclip", tooltip="Attach")]))
        foot = []
        child = center.foot_row.get_first_child()
        while child is not None:
            foot.append(child)
            child = child.get_next_sibling()
        self.assertIs(foot[0], center.prompt_button)
        self.assertEqual(len(foot), 2)
        self.assertTrue(center.bar.has_css_class("two-row"))
        center.prompt_button.emit("clicked")
        self.assertEqual(center.state, "editor")

    def test_two_row_bar(self):
        from luma_appkit.bar_entry import BarEntry
        ac = self.ac
        center, _host = self._center(375)
        add = BarEntry("quick", icon="plus", placeholder="Add a task")
        center.show_bar([ac.BarAction("list", "Today", dropdown=True, panel=Gtk.Label), ac.SPACER,
                         ac.BarAction("search", tooltip="Search")], entry=add)
        self.assertTrue(center.foot_row.get_visible(), "the field is the bottom row under a hairline")
        self.assertTrue(center.bar.has_css_class("two-row"))
        self.assertEqual(center.layout(375)[1], 343, "a two-row bar on a phone: the 16 gutter")
        center.grow("today", Gtk.Label(label="Lists"))
        self.assertTrue(center.foot_row.get_visible(), "a panel grows above row 1; the field stays last")
        center.fold()
        found = center.search(lambda _t: None)
        self.assertFalse(center.foot_row.get_visible(), "search replaces the bottom row")
        self.assertTrue(center.bar_row.get_visible(), "row 1 stays")
        self.assertTrue(center.searching)
        center.close_search()
        self.assertTrue(center.foot_row.get_visible())
        self.assertIsNotNone(found)
        center.set_foot(None)
        self.assertFalse(center.foot_row.get_visible())

    def test_dropdown_keeps_its_words(self):
        ac = self.ac
        center, _host = self._center(375)
        center.show_bar([ac.BarAction("calendar", "Month", dropdown=True, panel=Gtk.Label)])
        center._apply_width()
        button = center.bar_row.get_first_child()
        self.assertTrue(button.has_css_class("dropdown") and button.has_css_class("keep-label"))
        self.assertTrue(button.bar_dropdown_words.get_visible())
        button.emit("clicked")
        self.assertTrue(button.has_css_class("on"))

    def test_search_in_the_bar(self):
        from luma_appkit.bar_items import BarSearch
        ac = self.ac
        center, _host = self._center(375)
        seen, closed = [], []
        glyph = center.search(seen.append, collapsed=True, on_close=lambda: closed.append(1))
        self.assertIsInstance(glyph, BarSearch)
        self.assertEqual(glyph.placeholder, "Search", "the placeholder is just Search")
        center.show_bar([glyph, ac.BarAction("plus", tooltip="Add")])
        center.open_search(glyph)
        self.assertFalse(center.bar_row.get_visible(), "the field replaces the row")
        _item, field = center._search
        self.assertTrue(field.keep)
        self.assertFalse(field.clear_button.get_visible(), "✕ only once there's text")
        field.entry.set_text("pri")
        self.assertEqual(seen[-1], "pri")
        self.assertTrue(field.clear_button.get_visible())
        close = center.entry_row.get_last_child()
        close.emit("clicked")
        self.assertEqual(seen[-1], "", "✕ clears the search")
        self.assertEqual(closed, [1])
        self.assertTrue(center.bar_row.get_visible(), "and gives the row back")

    def test_tiles_fold_the_panel(self):
        ac = self.ac
        center, _host = self._center()
        picked = []
        center.show_bar([ac.BarAction("paperclip", tooltip="Attach")])
        tiles = self.panel.BarTiles([self.panel.BarTile("image", "Photo", lambda: picked.append("p"), on=True),
                                     self.panel.BarTile("file", "File"), self.panel.BarTile("map-pin", "Location")],
                                    columns=3)
        center.grow("attach", tiles)
        self.assertTrue(tiles.buttons[0].has_css_class("on"))
        tiles.buttons[0].emit("clicked")
        self.assertEqual(picked, ["p"])
        self.assertIsNone(center.grown)
        with self.assertRaises(ValueError):
            self.panel.BarTiles([], size="huge")

    def test_panel_row_extras(self):
        row = self.panel.PanelRow("Launch crew", lead=Gtk.Box(), subtitle="Shared with Priya", count=3, current=True)
        self.assertTrue(row.has_css_class("current") and row.has_css_class("two-line"))
        self.assertIn("3", _labels(row))

    def test_sheet(self):
        ac = self.ac
        center, host = self._center(1000)
        center.show_bar([ac.BarAction("plus", tooltip="Add")])
        window_host = self.frame.LayerHost.window_host(host)
        window_host.get_width = lambda: 375
        window_host.get_height = lambda: 800
        sheet = center.sheet(Gtk.Label(label="Provider"), "Add a provider")
        self.assertEqual(sheet.kind, "sheet")
        self.assertEqual(sheet.get_valign(), Gtk.Align.END, "on a phone a sheet rises from the bar's place")
        self.assertEqual(sheet.scroller.get_max_content_height(), 800 - 120 - 34)
        sheet.close()
        window_host.get_width = lambda: 1000
        card = center.sheet(Gtk.Label(label="Provider"), "Add a provider")
        self.assertTrue(card.has_css_class("centred"))
        card.close()

    def test_share_panel(self):
        from luma_appkit.bar_share import SharePanel
        from luma_appkit.content_contact import Person
        ac = self.ac
        center, _host = self._center()
        chosen = []
        center.show_bar([ac.BarAction("share-2", tooltip="Share",
                                      panel=lambda: SharePanel(people=[Person("Priya Raman")],
                                                               on_choice=lambda c, v: chosen.append(c)))])
        center.bar_row.get_first_child().emit("clicked")
        panel = center.panel.get_child().get_child()
        self.assertIsInstance(panel, SharePanel)
        panel.tiles.buttons[2].emit("clicked")
        self.assertEqual(chosen, ["copy-link"])
        self.assertIsNone(center.grown, "after sending or copying the panel closes")

    def test_bar_menu_badge(self):
        from luma_appkit.bar_items import BarMenu
        menu = BarMenu("Discover", [], icon="compass", badge=2)
        widget = self.ac.make_control(menu)
        self.assertTrue(menu._badge.get_visible())
        self.assertTrue(menu._badge.has_css_class("lumaui-bar-menu-badge"))
        css = (APPKIT / "luma-appkit-bar.css").read_text()
        self.assertIn("button.lumaui-bar-menu label.lumaui-bar-menu-badge {", css)
        self.assertIn("background: @luma_bar_badge_red", css.rsplit("button.lumaui-bar-menu label.lumaui-bar-menu-badge {", 1)[1][:300],
                      "Depot's update count is red, not the accent")
        menu.set_badge(0)
        self.assertFalse(menu._badge.get_visible())
        self.assertIsNotNone(widget)

    def test_toasts_at_the_top(self):
        host = self.toast.ToastHost(Gtk.Box(), edge="top", offset=96)
        self._window(host)
        toast = self.toast.Toast.show(host.get_child(), "Saved to Photos")
        self.assertEqual(toast.get_valign(), Gtk.Align.START)
        self.assertEqual(toast.get_margin_top(), 96)
        toast.dismiss()
        with self.assertRaises(ValueError):
            host.set_edge("left")

    def test_camera_frame_options(self):
        stage = self.frame.LayerHost(Gtk.Button(), name="window")
        self._window(stage)
        stage.get_width = lambda: 375
        stage.get_height = lambda: 800
        self.frame.BarFrame.configure(stage.get_child(), bottom=160, max_fraction=0.6, opaque=True)
        menu = self.bubble.FloatingMenu([self.bubble.MenuItem("4:3")])
        menu.popup(stage.get_child())
        frame = menu._drawer
        self.assertEqual(frame.get_margin_bottom(), 160)
        self.assertEqual(frame.scroller.get_max_content_height(), 480)
        self.assertTrue(frame.has_css_class("opaque"))
        menu.close()

    # ── wave 4: the app requests ──

    def test_fill_bar_shares_the_width(self):
        ac = self.ac
        center, _host = self._center(375)
        center.show_bar([ac.BarAction("share-2", tooltip="Share"), ac.BarAction("star", tooltip="Favorite"),
                         ac.BarAction("scissors", tooltip="Trim"), ac.BarAction("ellipsis", tooltip="More")], fill=True)
        self.assertEqual(center.layout(375)[1], 343, "a full-width bar spans the 16 gutter")
        self.assertTrue(all(c.get_hexpand() for c in _children(center.bar_row)), "its icon buttons share it")
        center.show_bar([ac.BarAction("", "Cancel", fill=True), ac.BarAction("", "Install", primary=True, fill=True)],
                        fill=True)
        self.assertTrue(all(c.get_hexpand() for c in _children(center.bar_row)), "two decisions share it")

    def test_card_bar_spans_the_gutter_and_the_labelled_key_takes_the_rest(self):
        # kit-requests contacts-04: v71 `#c-phbar.cphbar` is left/right 16 on a phone; `.bt.cedit` is flex 1,
        # Share/Favorite/More stay 48; editing, Cancel and Done are flex 1 each. `fill=True` is the span.
        ac = self.ac
        center, _host = self._center(375)
        center.show_bar([ac.BarAction("pencil", "Edit", keep_label=True), ac.BarAction("share-2", tooltip="Share"),
                         ac.BarAction("star", tooltip="Favorite"), ac.BarAction("ellipsis", tooltip="More")],
                        fill=True)
        self.assertEqual(center.layout(375)[1], 343, "the width less the 16 gutter, though nothing grew")
        edit, *icons = _children(center.bar_row)
        self.assertTrue(edit.get_hexpand(), "the labelled key takes the spare width")
        self.assertFalse(any(c.get_hexpand() for c in icons), "the icon keys stay 48")
        self.assertTrue(edit.has_css_class("labelled") and not edit.has_css_class("glyph-only"))
        center.show_bar([ac.BarAction("", "Cancel", fill=True), ac.BarAction("", "Done", primary=True, fill=True)],
                        fill=True)
        self.assertTrue(all(c.get_hexpand() for c in _children(center.bar_row)), "Cancel and Done are halves")

    def test_lit_star_is_the_favourite_star(self):
        # kit-requests contacts-07: v71 `.cphbar .cfavb.on` is yellow, filled, no chip.
        ac = self.ac
        star = ac.make_control(ac.BarAction("star", tooltip="Favorite", active=True))
        self.assertTrue(star.has_css_class("favourite") and star.has_css_class("on"))
        self.assertEqual(star.get_child().get_icon_name(), "lumaui-star-filled-symbolic")
        off = ac.make_control(ac.BarAction("star", tooltip="Favorite"))
        self.assertEqual(off.get_child().get_icon_name(), "lumaui-star-symbolic")
        off.add_css_class("on")  # an app lights it later
        self.assertEqual(off.get_child().get_icon_name(), "lumaui-star-filled-symbolic")
        off.remove_css_class("on")
        self.assertEqual(off.get_child().get_icon_name(), "lumaui-star-symbolic")
        other = ac.make_control(ac.BarAction("heart", tooltip="Love", active=True))
        self.assertFalse(other.has_css_class("favourite"))
        css = (APPKIT / "luma-appkit-bar.css").read_text()
        self.assertIn("bar.favourite.on:not(.primary):not(.record) { background: none; box-shadow: none; "
                      "color: @luma_favourite; }", css, "it beats the phone's raised chip")

    def test_icon_only_primary_keeps_the_keys_ink_on_the_frame(self):
        # kit-requests messages-05: `.lumaui-action-center.on-frame ... .icon` (0,3,1) used to beat `.primary`
        # (0,2,1), so Messages' New message glyph was the frame's grey on the light key.
        import re
        css = (APPKIT / "luma-appkit-base.css").read_text()
        rules = [m for m in re.finditer(r"^([^{}\n]*on-frame button\.lumaui-bar-button\.icon[^{]*)\{([^}]*)\}", css, re.M)]
        self.assertTrue(rules)
        for rule in rules:
            self.assertIn(":not(.primary)", rule.group(1), "the frame's icon ink leaves the primary key alone")
        key = self.ac.make_control(self.ac.BarAction("square-pen", tooltip="New message", primary=True))
        self.assertTrue(key.has_css_class("icon") and key.has_css_class("primary"))
        self.assertIn("button.lumaui-bar-button.primary {\n  padding: 0 14px; font-weight: 600; color: @luma_key_ink;", css)

    def test_search_well_opens_on_tap(self):
        # kit-requests notes-03: v71 `.nlq` is a well with the glyph and "Search"; tapped, the row is the field.
        from luma_appkit.bar_items import BarSearch
        ac = self.ac
        center, _host = self._center(375)
        search = BarSearch(opens=True)
        center.show_bar([search, ac.BarAction("square-pen", tooltip="New", primary=True)])
        well = search.widget
        self.assertTrue(search.keep and well.has_css_class("opens") and well.bar_phone_wide, "the kept well")
        self.assertTrue(well.get_hexpand())
        self.assertEqual(well.word.get_label(), "Search")
        self.assertTrue(well.word.get_visible() and not search.entry.get_visible(), "its word, not a field")
        self.assertEqual(center.layout(375)[1], 351, "a phone bar spans its width less 12 a side")
        self.assertFalse(center.searching)
        search._tapped(well, search.entry)
        self.assertTrue(center.searching, "tapping it opens the search")
        self.assertTrue(center.entry_row.get_visible() and not center.bar_row.get_visible(), "the row is the field")
        closed = []
        search.on_close = lambda: closed.append(1)
        center.close_search()
        self.assertEqual(closed, [1])
        self.assertTrue(center.bar_row.get_visible())

    def test_head_row_above_the_bar_row(self):
        # kit-requests tide-01: v71 `tidePhBar()` is `${panel}${mini}<div class="tprow">`, one glass.
        ac = self.ac
        center, _host = self._center(375)
        mini = Gtk.Label(label="Mini player")
        center.show_bar([ac.BarAction("", "Albums", dropdown=True), ac.SPACER, ac.BarAction("search", tooltip="Search")],
                        head=mini, fill=True)
        order = _children(center.bar)
        self.assertLess(order.index(center.panel), order.index(center.head_row), "a grown panel rises above the head")
        self.assertLess(order.index(center.head_row), order.index(center.bar_row), "the head is above the row")
        self.assertIs(mini.get_parent(), center.head_row)
        self.assertTrue(center.head_row.get_visible() and center.head_row.has_css_class("lumaui-ac-head"))
        self.assertEqual(center.layout(375)[1], 343, "the 16 gutter")
        center.grow("volume", Gtk.Label(label="Volume"))
        self.assertTrue(center.head_row.get_visible() and center.bar_row.get_visible(), "head and row stay")
        center.fold_panel()
        center.search(lambda _t: None)
        self.assertTrue(center.searching)
        self.assertTrue(center.head_row.get_visible(), "the head stays when the row becomes the field")
        self.assertFalse(center.bar_row.get_visible())
        center.close_search()
        other = Gtk.Label(label="Other")
        center.set_head(other)
        self.assertIs(other.get_parent(), center.head_row)
        self.assertIsNone(mini.get_parent())
        center.set_head(None)
        self.assertFalse(center.head_row.get_visible())
        center.show_bar([ac.BarAction("plus", tooltip="Add")], head=other)
        center.show_bar([ac.BarAction("plus", tooltip="Add")])
        self.assertFalse(center.head_row.get_visible(), "a new bar has no head unless it is given one")

    def test_clock_words_in_a_desktop_bar(self):
        # kit-requests clock-02 (1): v71 `.ckwin .fbar .bt`: 78 wide, the secondary word a well, the running key red.
        ac = self.ac
        center, _host = self._center(1000)
        center.show_bar([ac.SEPARATOR, ac.BarAction("", "Lap"), ac.BarAction("", "Stop", primary=True, danger=True)])
        self.assertTrue(center.bar_row.has_css_class("word-pair"))
        lap, stop = [c for c in _children(center.bar_row) if c.has_css_class("text")]
        self.assertTrue(stop.has_css_class("destructive"), "primary + danger is the red key")
        center.show_bar([ac.BarAction("plus", "New alarm", primary=True)])
        self.assertFalse(center.bar_row.has_css_class("word-pair"), "a glyph key is not a word pair")
        center.show_bar([ac.BarAction("", "Done", primary=True)])
        self.assertFalse(center.bar_row.has_css_class("word-pair"), "a lone word is not a pair")
        phone, _host = self._center(375)
        phone.show_bar([ac.BarAction("", "Cancel"), ac.BarAction("", "Done", primary=True)])
        self.assertFalse(phone.bar_row.has_css_class("word-pair"), "a phone has its own rules")
        css = (APPKIT / "luma-appkit-bar.css").read_text()
        self.assertIn("button.lumaui-bar-button.text { min-width: 54px; }", css, "78 border box less 12 + 12")
        self.assertIn("text:not(.primary) {\n  background: @luma_well;", css, "the secondary word is the well")

    def test_tight_inset_is_v71s_base_bar_bottom(self):
        # kit-requests clock-02 (2): `.ckwin .fbar` keeps the base `bottom: 16px`; most apps say 24.
        ac = self.ac
        host = self.toast.ToastHost(Gtk.Box())
        self._window(host)
        center = ac.ActionCenter().attach(host, inset="tight")
        center.show_bar([ac.BarAction("plus", tooltip="Add")])
        self.assertEqual(center.layout(1000)[3], 16)
        self.assertEqual(center.layout(375)[3], 34, "a phone keeps its own 34")
        self.assertEqual(self._center(1000)[0].layout(1000)[3], 24, "the default is unchanged")
        with self.assertRaises(ValueError):
            ac.ActionCenter().attach(host, inset="loose")

    def test_grown_entry_can_close_with_a_word(self):
        # kit-requests clock-02 (4): v71 Clock's Add a city shows the word Cancel beside the field, not ✕.
        ac = self.ac
        center, _host = self._center(1000)
        center.show_bar([ac.BarAction("plus", tooltip="Add")])
        center.grow("add", Gtk.Label(label="matches"), entry=Gtk.Entry(), close_word="Cancel")
        close = _children(center.entry_row)[-1]
        self.assertTrue(close.has_css_class("text"))
        self.assertEqual(close.get_child().get_label(), "Cancel")
        center.fold_panel()
        center.show_bar([ac.BarAction("plus", tooltip="Add")])
        center.grow("add", Gtk.Label(label="matches"), entry=Gtk.Entry())
        self.assertTrue(_children(center.entry_row)[-1].has_css_class("icon"), "the default stays ✕")

    def test_show_panel(self):
        ac = self.ac
        center, _host = self._center(375)
        back = []
        center.show_panel(self.panel.BarTiles([self.panel.BarTile("reply", "Reply")], size="compact"),
                          on_dismiss=lambda: back.append(1))
        self.assertFalse(center.bar_row.get_visible(), "no row")
        self.assertTrue(center.foot_row.get_visible())
        self.assertTrue(center._panel_key_pressed(None, Gdk.KEY_Escape, 0, 0))
        self.assertEqual(back, [1])
        center.show_bar([ac.BarAction("plus", tooltip="Add")])
        self.assertTrue(center.bar_row.get_visible())
        self.assertFalse(center.foot_row.get_visible())

    def test_red_key_badge_record_dot(self):
        ac = self.ac
        red = ac.make_control(ac.BarAction("", "Remove", primary=True, danger=True))
        self.assertTrue(red.has_css_class("destructive"))
        bell = ac.make_control(ac.BarAction("bell", tooltip="Alerts", badge=1))
        self.assertTrue(bell.bar_badge.get_visible())
        ac.set_badge(bell, 0)
        self.assertFalse(bell.bar_badge.get_visible())
        unread = ac.make_control(ac.BarAction("mail-check", tooltip="Show only unread", badge=2))
        self.assertIs(unread.bar_button.get_accessible_role(), Gtk.AccessibleRole.BUTTON)
        editor = ac.ActionEditor("Reply", "reply", primary=ac.BarAction("send-horizontal", "Send"), keep_hint=True)
        self.assertTrue(editor.keep_hint and editor.hint.has_css_class("kept"))
        self.assertTrue(editor.has_css_class("keeps-foot"))
        self.assertFalse(ac.ActionEditor("Reply", "reply").hint.has_css_class("kept"))
        voicemail = ac.make_control(ac.BarAction("voicemail", tooltip="Voicemail", badge=2, badge_tone="red"))
        self.assertTrue(voicemail.bar_badge.has_css_class("red"), "Phone's voicemail count is red")
        switch = ac.make_control(ac.BarAction("reply-all", "All", dropdown=True, dropdown_size="chip", panel=Gtk.Label))
        from luma_appkit.action_bubble import MenuItem
        menu_switch = ac.make_control(ac.BarAction("reply-all", "All", dropdown=True, dropdown_size="chip",
                                                   menu=[MenuItem("Reply all", icon="reply-all")]))
        self.assertIsNotNone(menu_switch)
        with self.assertRaises(ValueError):
            ac.make_control(ac.BarAction("reply", "Reply", panel=Gtk.Label, menu=[MenuItem("x")]))
        self.assertTrue(switch.has_css_class("chip-picker"))
        unread = ac.make_control(ac.BarAction("mail-check", tooltip="Show only unread", badge=2, badge_tone="accent"))
        self.assertTrue(unread.bar_badge.has_css_class("accent"), "Charlie's unread count is the accent's")
        sessions = ac.make_control(ac.BarAction("house", tooltip="Sessions", badge=2, badge_tone="neutral"))
        self.assertTrue(sessions.bar_badge.has_css_class("neutral"))
        self.assertFalse(sessions.bar_badge.has_css_class("attention"))
        flag = ac.make_control(ac.BarAction("star", tooltip="Flag", active=True, favourite=False))
        self.assertFalse(_has_class(flag, "favourite"))
        with self.assertRaises(ValueError):
            ac.make_control(ac.BarAction("bell", tooltip="Alerts", badge=1, badge_tone="pink"))
        record = ac.make_control(ac.BarAction("record-dot", "Record", primary=True))
        self.assertTrue(_has_class(record, "lumaui-record-dot"))

    def test_live_chip_is_a_well_on_a_phone(self):
        ac = self.ac
        center, _host = self._center(375)
        center.show_bar([ac.BarChip("Recording", meta="0:12", live=True), ac.BarAction("pause", tooltip="Pause"),
                         ac.BarAction("", "Done", primary=True)])
        center._apply_width()
        chip = center.bar_row.get_first_child()
        self.assertTrue(chip.has_css_class("well") and chip.get_hexpand())

    def test_progress_item(self):
        from luma_appkit.bar_items import BarProgress
        ac = self.ac
        center, _host = self._center(375)
        cancelled = []
        progress = BarProgress(0.25, "Downloading from Flathub", on_cancel=lambda: cancelled.append(1))
        center.show_bar([progress])
        progress.set_fraction(0.5)
        progress.set_label("Installing")
        self.assertEqual(progress._bar.get_fraction(), 0.5)
        self.assertEqual(progress._text.get_label(), "Installing")

    def test_panel_switch_choices_key_and_rich_rows(self):
        ac = self.ac
        center, _host = self._center()
        center.show_bar([ac.BarAction("plus", tooltip="New")])
        toggled, chosen, keyed = [], [], []
        switch = self.panel.PanelSwitch("sparkles", "Reduce background noise", active=False, on_toggle=toggled.append)
        choices = self.panel.PanelChoices([("phone", "This phone", "smartphone"), ("studio", "Studio", "headphones")],
                                          on_choose=chosen.append)
        key = self.panel.PanelKey("Start recording", record=True, on_activate=lambda: keyed.append(1))
        center.grow("new", self.panel.panel_list(["New memo", choices, switch, key]))
        switch.emit("clicked")
        self.assertEqual(toggled, [True])
        self.assertEqual(center.grown, "new", "a switch row leaves the panel open")
        choices.buttons["studio"].emit("clicked")
        self.assertEqual(chosen, ["studio"])
        self.assertTrue(choices.buttons["studio"].has_css_class("on"))
        key.emit("clicked")
        self.assertEqual(keyed, [1])
        self.assertIsNone(center.grown)
        row = self.panel.PanelRow("Kansas City", icon="map-pin", detail="66°")
        self.assertIn("66°", _labels(row))

    def test_rich_menu_rows_keep_their_widgets(self):
        class Rich(self.bubble.MenuItem):
            def menu_widget(self, close):
                return Gtk.Label(label="rich")
        column = self.panel.panel_list([Rich("Alerts")])
        self.assertIn("rich", _labels(column), "a RichMenuItem draws itself in a panel")

    def test_bar_menu_with_a_panel(self):
        from luma_appkit.bar_items import BarMenu
        ac = self.ac
        center, _host = self._center()
        picker = BarMenu("Today", icon="sun", panel=lambda: Gtk.Label(label="views"))
        center.show_bar([picker])
        picker.widget.emit("clicked")
        self.assertTrue(center.grown and picker.widget.has_css_class("on"))
        picker.widget.emit("clicked")
        self.assertIsNone(center.grown)

    def test_icon_tabs_on_a_phone(self):
        from luma_appkit.structure_placement import ModeSwitch
        ac = self.ac
        center, _host = self._center(375)
        places = ModeSwitch([("world", "World", "globe"), ("alarms", "Alarms", "alarm-clock", 2),
                             ("stopwatch", "Stopwatch", "timer")], current="world")
        center.show_bar([], modes=places)
        places._narrow = True
        places._labels()
        self.assertFalse(any(b.label_widget.get_visible() for b in places.buttons.values()),
                         "places in a phone bar are icon-only tabs")

    def test_edit_layout(self):
        ac = self.ac
        center, _host = self._center(1000)
        modes, done, cancelled, reverted = [], [], [], []
        editor = ac.ActionEditor("Edit", "sliders-horizontal",
                                 modes=[("looks", "Looks", "palette"), ("light", "Light", "sun"),
                                        ("colour", "Color", "droplet"), ("crop", "Crop", "crop")],
                                 mode="light", on_mode=modes.append,
                                 tools=[ac.BarAction("wand-sparkles", "Auto")], body=Gtk.Label(label="sliders"),
                                 primary=ac.BarAction("", "Done", on_activate=lambda: done.append(1)),
                                 on_discard=lambda: cancelled.append(1),
                                 revert=ac.BarAction("rotate-ccw", "Revert to original", sensitive=False,
                                                     on_activate=lambda: reverted.append(1)))
        center.set_editor(editor)
        center.show_bar([ac.BarPrompt("Edit")])
        center.grow()
        self.assertEqual(center.layout(1000)[1], 640, "the edit layout is 640 wide in a window")
        self.assertIsNotNone(editor.mode_switch, "the modes are a switch in the header")
        self.assertFalse(editor.revert_button.get_sensitive())
        editor.set_revertable(True)
        self.assertTrue(editor.revert_button.get_sensitive() and editor.revert_tile.get_sensitive())
        editor.cancel_button.emit("clicked")
        self.assertEqual(cancelled, [1], "Cancel is on_discard")
        editor.set_phone(True)
        self.assertTrue(all(p.get_visible() for p in editor._phone_parts))
        self.assertFalse(any(p.get_visible() for p in editor._desk_parts))
        editor._tiles["crop"].emit("clicked")
        self.assertEqual(modes, ["crop"])
        self.assertTrue(editor._tiles["crop"].has_css_class("on"))
        self.assertEqual(editor.mode_switch.current, "crop", "the switch follows the tiles")

    def test_a_lone_place_switch_is_a_tab_bar_on_a_phone(self):
        from luma_appkit.structure_placement import ModeSwitch
        from luma_appkit.structure_tabs import TabBar
        chosen = []
        places = ModeSwitch([("world", "World", "globe"), ("alarms", "Alarms", "alarm-clock"),
                             ("stopwatch", "Stopwatch", "timer"), ("timer", "Timer", "hourglass")],
                            current="world", on_change=chosen.append)
        center, host = self._center(375)
        center.show_bar([], modes=places)
        self.assertIsInstance(center.tab_bar, TabBar, "3+ peer places with icons are the tab bar on a phone")
        self.assertEqual(center.layout(375)[1], 351, "the tab bar spans the width less 12 a side")
        center.tab_bar.tabs["timer"].set_active(True)
        self.assertEqual(chosen, ["timer"])
        self.assertEqual(places.current, "timer")
        places.set_current("alarms")
        self.assertEqual(center.tab_bar.current, "alarms", "the app's set_current moves the tab")
        compact, _h = self._center(375)
        compact.show_bar([], modes=ModeSwitch([("a", "A", "globe"), ("b", "B", "timer"), ("c", "C", "sun")]),
                         tabs="compact")
        self.assertIsNone(compact.tab_bar, "Clock keeps icon-only tabs in its bar")
        wide, _h2 = self._center(1000)
        wide.show_bar([], modes=ModeSwitch([("a", "A", "globe"), ("b", "B", "timer"), ("c", "C", "sun")]))
        self.assertIsNone(wide.tab_bar, "a window keeps the switch")

    def test_tab_bar_as_a_bar_item(self):
        from luma_appkit.bar_items import BarSearch
        from luma_appkit.structure_tabs import TabBar
        center, _host = self._center(375)
        tabs = TabBar([("keypad", "Keypad", "grid-3x3"), ("recents", "Recents", "clock")], compact=True)
        center.show_bar([BarSearch(collapsed=True), tabs])
        self.assertIs(tabs.get_parent(), center.bar_row)

    def test_a_spacer_keeps_the_grown_row(self):
        ac = self.ac
        center, _host = self._center(375)
        center.show_bar([ac.BarAction("calendar", "Month", dropdown=True, panel=Gtk.Label), ac.SPACER,
                         ac.BarAction("", "Today", primary=True)])
        center.bar_row.get_first_child().emit("clicked")
        self.assertFalse(center.bar_row.get_first_child().get_hexpand(), "the picker keeps its width; the spacer takes the rest")


def _children(widget):
    found, child = [], widget.get_first_child()
    while child is not None:
        found.append(child)
        child = child.get_next_sibling()
    return found


def _labels(widget):
    found = []
    if isinstance(widget, Gtk.Label):
        found.append(widget.get_label())
    child = widget.get_first_child()
    while child is not None:
        found += _labels(child)
        child = child.get_next_sibling()
    return found


def _has_class(widget, name):
    if widget.has_css_class(name):
        return True
    child = widget.get_first_child()
    while child is not None:
        if _has_class(child, name):
            return True
        child = child.get_next_sibling()
    return False


if __name__ == "__main__":
    unittest.main()
