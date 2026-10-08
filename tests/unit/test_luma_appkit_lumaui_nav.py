# SPDX-License-Identifier: Apache-2.0
"""LumaUI v71 navigation (K-NAV): tiers, the title island and the phone shapes.

The first half reads source and runs anywhere; the second builds real widgets
on stock GTK 4 where a display is available (it never presents a window).
"""
from __future__ import annotations

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
FRAGMENT = json.loads((ROOT / "config/shared/design-tokens.d/structure-nav.json").read_text())["lumaui"]

sys.path.insert(0, str(APPKIT))
try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, GLib, Gtk

    HAVE_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
except (ImportError, ValueError):
    HAVE_DISPLAY = False


def _descendants(widget):
    for child in _children(widget):
        yield child
        yield from _descendants(child)


def _children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def _spin(ms: int = 0) -> None:
    context = GLib.MainContext.default()
    end = time.monotonic() + ms / 1000
    while True:
        while context.pending():
            context.iteration(False)
        if time.monotonic() >= end:
            return
        time.sleep(0.01)


class Source(unittest.TestCase):
    def test_parts_are_exported(self):
        init = (KIT / "__init__.py").read_text()
        for name in ("TitleIsland", "ListFirst", "TabBar", "PageHeader", "WidthWatch", "TIERS", "tier"):
            self.assertIn(f'"{name}"', init[init.index("__all__"):init.index("]\n")])
        import luma_appkit
        for name in ("TitleIsland", "WidthWatch", "TIERS", "tier"):
            self.assertTrue(getattr(luma_appkit, name))

    def test_phone_frame_is_the_toolkits(self):
        toolkit = (APPKIT / "lumaui-toolkit.css").read_text()
        block = toolkit[toolkit.index("K-NAV (v71): the phone frame"):]
        for needle in ("window.luma-app-window.lumaui-phone-device { border-radius: 0; box-shadow: none; }",
                       "var(--lumaui-phone-frame-status)", "var(--lumaui-phone-frame-row-top)"):
            self.assertIn(needle, block)
        widgets = (KIT / "widgets.py").read_text()
        self.assertIn('self.add_css_class("lumaui-phone-device")', widgets)
        self.assertIn("view.set_extend_content_to_top_edge(True)", widgets)

    def test_tiers_follow_v71(self):
        from luma_appkit import lumaui_tokens as tokens
        from luma_appkit.structure_adapt import TIERS, tier_for
        self.assertEqual(tokens.PHONE_MAX_WIDTH, FRAGMENT["tiers"]["phone_below"] - 1)
        self.assertEqual(TIERS, ("phone", "compact", "regular"))
        self.assertEqual([tier_for(w) for w in (320, 559, 560, 900, 901, 1600)],
                         ["phone", "phone", "compact", "compact", "regular", "regular"])

    def test_title_island_css_uses_its_tokens(self):
        css = BASE.read_text()
        self.assertIn("/* LumaUI: Title island */", css)
        block = css[css.index("/* LumaUI: Title island */"):]
        block = block[:block.index("/* LumaUI: structure reduced motion")]
        for key in ("height", "radius", "lead", "glyph", "title-size", "subtitle-size", "button-width"):
            self.assertIn(f"var(--lumaui-title-island-{key})", block)
        self.assertNotRegex(block, r"(?<![\w-])(48|52|320|360)px")
        tokens_css = (APPKIT / "luma-appkit-tokens.css").read_text()
        self.assertIn("--lumaui-title-island-height: 48px;", tokens_css)
        self.assertIn("--lumaui-gutter-phone: 16px;", tokens_css)


@unittest.skipUnless(HAVE_DISPLAY, "needs GTK 4 and a display")
class Parts(unittest.TestCase):
    def setUp(self):
        from luma_appkit import structure_adapt, structure_island, structure_layers, structure_sidebar
        self.adapt, self.island_mod = structure_adapt, structure_island
        self.layers, self.sidebar_mod = structure_layers, structure_sidebar
        self.windows: list[Gtk.Window] = []

    def tearDown(self):
        for window in self.windows:
            window.destroy()

    def _stage(self, child: Gtk.Widget, width: int = 1100):
        stage = self.layers.LayerHost(child, name="window")
        stage.width = width
        stage.get_width = lambda: stage.width
        stage.get_height = lambda: 800
        window = Gtk.Window()
        window.set_child(stage)
        self.windows.append(window)
        return stage

    # ── tiers ─────────────────────────────────────────────────────────────

    def test_width_watch_reports_tier_changes_once_each(self):
        page = Gtk.Box()
        stage = self._stage(page, 1100)
        seen, signalled = [], []
        watch = self.adapt.WidthWatch(page, on_tier=seen.append)
        watch.connect("tier-changed", lambda _w, t: signalled.append(t))
        watch.check()
        stage.width = 1000
        watch.check()          # still regular: nothing
        stage.width = 700
        watch.check()
        stage.width = 402
        watch.check()
        stage.width = 560
        watch.check()
        self.assertEqual(seen, ["regular", "compact", "phone", "compact"])
        self.assertEqual(signalled, seen)
        self.assertEqual(watch.tier, "compact")
        self.assertEqual(self.adapt.tier(page), "compact")

    def test_old_width_watch_calls_keep_working(self):
        page = Gtk.Box()
        stage = self._stage(page, 1100)
        crossings = []
        watch = self.adapt.WidthWatch(page, crossings.append, threshold=559)
        watch.check()
        stage.width = 500
        watch.check()
        stage.width = 520
        watch.check()
        self.assertEqual(crossings, [1100, 500])

    def test_window_tier_marks_the_window(self):
        page = Gtk.Box()
        stage = self._stage(page, 402)
        watch = self.adapt.window_tier(stage)
        self.assertIs(self.adapt.window_tier(stage), watch)
        watch.check()
        self.assertTrue(stage.has_css_class("lumaui-phone"))
        stage.width = 1200
        watch.check()
        self.assertTrue(stage.has_css_class("lumaui-regular"))
        self.assertFalse(stage.has_css_class("lumaui-phone"))

    # ── title island ──────────────────────────────────────────────────────

    def _island(self, width=402, **kwargs):
        page = Gtk.Box()
        stage = self._stage(page, width)
        island = self.island_mod.TitleIsland(**kwargs).float_over(stage)
        _spin()
        return island, stage, page

    def test_island_is_one_row_lead_hairline_title(self):
        island, _stage, _page = self._island(title="Launch", subtitle="Priya is here · Edited just now", lead="back")
        self.assertTrue(island.has_css_class("lumaui-title-island") and island.has_css_class("floating"))
        self.assertEqual(island.title_label.get_label(), "Launch")
        self.assertEqual(island.subtitle_label.get_label(), "Priya is here · Edited just now")
        self.assertTrue(island.lead_button.get_visible())
        self.assertEqual(island.lead_button.get_tooltip_text(), "Back")
        island.set_title("Launch", "")
        self.assertFalse(island.subtitle_label.get_visible())
        island.set_lead(None)
        self.assertFalse(island.lead_button.get_visible())
        self.assertTrue(island.has_css_class("no-lead"))
        with self.assertRaises(ValueError):
            island.set_lead("home")

    def test_menu_icon_tracks_drive_and_returns_after_close(self):
        from luma_appkit import icons
        island, _stage, _page = self._island(title="Drive", lead_icon="hard-drive",
                                            disclosure=True, grow=lambda: Gtk.Label(label="Drives"))
        glyph = lambda: island.lead_button.get_child().get_icon_name()
        self.assertEqual(glyph(), icons.icon_name("hard-drive"))
        self.assertFalse(island.lead_button.get_focusable())
        island.grow_into()
        self.assertEqual(glyph(), icons.icon_name("x"))
        island.set_lead_icon("usb")
        self.assertEqual(glyph(), icons.icon_name("x"))
        island.fold()
        self.assertEqual(glyph(), icons.icon_name("usb"))
        island.set_lead("back")
        self.assertEqual(glyph(), icons.icon_name("chevron-left"))
        self.assertTrue(island.lead_button.get_focusable())
        island.set_lead("menu")
        self.assertEqual(glyph(), icons.icon_name("usb"))
        island.set_lead_icon(None)
        self.assertEqual(glyph(), icons.icon_name("menu"))
        with self.assertRaises(ValueError):
            island.set_lead_icon("")

    def test_a_borrowed_sidebar_asks_for_all_its_rows(self):
        # Charlie's mailboxes: a sidebar scrolls in its own scroller, which asks for almost no height;
        # grown into the island it asks for every row (the island's panel scrolls), and gets its own back.
        home = Gtk.Box()
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroller = Gtk.ScrolledWindow(child=Gtk.Label(label="Inbox\nDrafts\nSent"))
        sidebar.append(scroller)
        home.append(sidebar)
        island, _stage, _page = self._island(title="All inboxes", lead="menu", grow=sidebar, grows="menu")
        self.assertFalse(scroller.get_propagate_natural_height())
        island.grow_into()
        self.assertTrue(scroller.get_propagate_natural_height())
        island.fold()
        island._folded()
        self.assertFalse(scroller.get_propagate_natural_height())
        self.assertIs(sidebar.get_parent(), home)

    def test_back_goes_up_and_the_title_grows_into_details(self):
        went, built = [], []

        def details():
            built.append(True)
            return Gtk.Label(label="Pin  Copy link  Move  Duplicate")

        island, stage, _page = self._island(title="Launch", lead="back", on_lead=lambda: went.append(True), grow=details)
        island.lead_button.emit("clicked")
        self.assertEqual(went, [True])
        island.title_button.emit("clicked")
        self.assertTrue(island.grown and island.has_css_class("grown") and built)
        # Actual floating bounds are checked by test_luma_appkit_island_resize.
        self.assertEqual(island.lead_button.get_tooltip_text(), "Back", "‹ stays ‹ while grown")
        # Back folds first; it does not navigate while grown.
        island.lead_button.emit("clicked")
        self.assertFalse(island.grown)
        self.assertEqual(went, [True])
        # A second tap on the title folds too.
        island.title_button.emit("clicked")
        island.title_button.emit("clicked")
        self.assertFalse(island.grown)

    def test_a_tap_beside_the_grown_island_folds_it(self):
        island, stage, _page = self._island(title="Launch", lead="back", grow=lambda: Gtk.Label(label="x"))
        island.grow_into()
        scrim = island._scrim
        self.assertIsNotNone(scrim)
        self.assertIs(scrim.get_parent(), stage)
        self.assertIs(scrim.get_next_sibling(), island, "the scrim lies under the island, over the page")
        island.fold()
        self.assertIsNone(island._scrim)

    def test_menu_borrows_the_places_and_returns_them(self):
        body = Gtk.Box()
        places = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        places.set_size_request(272, -1)
        rows = Gtk.ListBox()
        rows.append(Gtk.Label(label="Home"))
        rows.append(Gtk.Label(label="Projects"))
        places.append(rows)
        content = Gtk.Label(label="content")
        body.append(places)
        body.append(content)
        stage = self._stage(body, 402)
        island = self.island_mod.TitleIsland(title="Home", subtitle="6 items", grow=places).float_over(stage)
        _spin()
        island.lead_button.emit("clicked")
        self.assertTrue(island.grown and island.has_css_class("menu"))
        self.assertIs(island.panel.get_child().get_child() if isinstance(island.panel.get_child(), Gtk.Viewport)
                      else island.panel.get_child(), places)
        self.assertEqual(island.lead_button.get_tooltip_text(), "Close", "☰ becomes ✕")
        # Picking a place folds it, and the sidebar goes home, ahead of the content.
        rows.emit("row-activated", rows.get_row_at_index(1))
        self.assertFalse(island.grown)
        island._folded()
        self.assertIs(places.get_parent(), body)
        self.assertIs(places.get_next_sibling(), content)
        self.assertEqual(places.get_size_request()[0], 272)
        self.assertIsNone(island.lead_button.get_tooltip_text(), "☰ is part of the title's one control")
        self.assertFalse(island.lead_button.get_focusable())

    def test_phone_only_island_hides_on_a_computer(self):
        island, stage, _page = self._island(width=1100, title="Home")
        watch = self.adapt.window_tier(stage)
        watch.check()
        self.assertFalse(island.get_child_visible())
        stage.width = 402
        watch.check()
        self.assertTrue(island.get_child_visible())
        always, stage2, _p = self._island(width=1100, title="Inbox", phone_only=False, lead="back",
                                          grow=lambda: Gtk.Label(label="x"))
        self.adapt.window_tier(stage2).check()
        self.assertTrue(always.get_visible())
        always.grow_into()
        self.assertTrue(always.grown)

    def test_sidebar_toggle_hands_a_phone_to_the_island(self):
        body = Gtk.Box()
        places = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        places.append(Gtk.ListBox())
        body.append(places)
        body.append(Gtk.Label(label="content"))
        stage = self._stage(body, 402)
        island = self.island_mod.TitleIsland(title="Home").float_over(stage)
        toggle = self.sidebar_mod.SidebarToggle(places, island=island)
        bar = Gtk.Box()
        bar.append(toggle)
        body.prepend(bar)
        _spin()
        toggle._reflow()
        self.assertFalse(toggle.get_visible(), "on a phone the ☰ is the island's")
        toggle.toggle()  # F9
        self.assertTrue(island.grown)
        toggle.toggle()
        self.assertFalse(island.grown)
        stage.width = 1100
        toggle._reflow()
        self.assertTrue(toggle.get_visible())

    def test_calendar_head_has_no_lead_a_title_action_and_fades_with_the_month(self):
        picked = []
        island, stage, _page = self._island(lead=None, on_title=lambda: picked.append(True))
        month = Gtk.Label(label="September 2026")
        island.set_title_content(month)
        island.add_trailing(Gtk.Button(label="•••"))
        self.assertFalse(island.lead_button.get_visible())
        self.assertIs(month.get_parent(), island._inner)
        island.title_button.emit("clicked")
        self.assertEqual(picked, [True])
        scroller = Gtk.ScrolledWindow()
        adjustment = scroller.get_vadjustment()
        adjustment.configure(0, 0, 1000, 10, 100, 200)
        island.follow(scroller, until=90)
        adjustment.set_value(30)
        self.assertAlmostEqual(island.get_opacity(), 0.5, places=2)
        adjustment.set_value(200)
        self.assertEqual(island.get_opacity(), 0)
        self.assertFalse(island.get_can_target())
        island.set_title_content(None)
        self.assertTrue(island._text.get_visible())

    def test_phone_drawer_comes_from_the_bottom_left_corner(self):
        body = Gtk.Box()
        places = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        places.append(Gtk.ListBox())
        body.append(places)
        body.append(Gtk.Label(label="content"))
        stage = self._stage(body, 402)
        toggle = self.sidebar_mod.SidebarToggle(places)
        body.prepend(toggle)
        _spin()
        toggle.toggle()
        card = toggle._card
        self.assertTrue(card.has_css_class("phone-drawer"))
        self.assertTrue(toggle._drawer.scrim.has_css_class("phone-drawer"))
        self.assertEqual(card.get_size_request()[0], min(328, 402 - 52))
        self.assertEqual(card.get_margin_top(), 64, "level with the page title")
        self.assertEqual((card.get_halign(), card.get_valign()), (Gtk.Align.START, Gtk.Align.FILL))
        toggle.toggle()
        self.assertIsNone(toggle._drawer)

    def test_list_first_on_a_phone_side_by_side_on_a_computer(self):
        from luma_appkit.structure_listfirst import ListFirst
        rows = Gtk.ListBox()
        rows.append(Gtk.Label(label="Priya"))
        rows.append(Gtk.Label(label="Theo"))
        page = Gtk.Label(label="thread")
        host_child = Gtk.Box()
        stage = self._stage(host_child, 402)
        stack = ListFirst(rows, page, title="Messages")
        host_child.append(stack)
        watch = self.adapt.window_tier(stage)
        _spin()
        watch.check()
        self.assertTrue(stack.phone)
        self.assertIs(rows.get_parent(), stack.list_screen)
        self.assertEqual(stack.stack.get_visible_child_name(), "list")
        self.assertTrue(stack.title_label.get_visible())
        rows.emit("row-activated", rows.get_row_at_index(0))
        self.assertEqual((stack.showing, stack.stack.get_visible_child_name()), ("detail", "detail"))
        self.assertTrue(stack.back_button.get_visible())
        island = self.island_mod.TitleIsland("Priya", "Active now", lead="back")
        stack.attach_island(island)
        self.assertFalse(stack.back_button.get_visible())
        island.lead_button.emit("clicked")
        self.assertEqual(stack.showing, "list")
        stage.width = 1100
        watch.check()
        self.assertFalse(stack.phone)
        self.assertIs(rows.get_parent(), stack.split)
        self.assertIs(page.get_parent(), stack.split)
        stage.width = 402
        watch.check()
        self.assertIs(page.get_parent(), stack.detail_screen)

    def test_list_first_back_can_name_the_list(self):
        # phone-15: "‹ Recents" as one pill (v71 .pnlback); no label is the bare ‹ (.phback)
        from luma_appkit.structure_listfirst import ListFirst
        bare = ListFirst(Gtk.Box(), Gtk.Box(), title="Phone")
        self.assertFalse(bare.back_button.has_css_class("labelled"))
        named = ListFirst(Gtk.Box(), Gtk.Box(), title="Phone", back_label="Recents")
        words = [w for w in _descendants(named.back_button) if isinstance(w, Gtk.Label)]
        self.assertEqual([w.get_label() for w in words], ["Recents"])
        self.assertTrue(named.back_button.has_css_class("labelled"))
        named.set_back_label(None)
        self.assertFalse(named.back_button.has_css_class("labelled"))
        self.assertEqual([w for w in _descendants(named.back_button) if isinstance(w, Gtk.Label)], [])
        css = BASE.read_text()
        self.assertIn("button.lumaui-list-first-back.labelled { padding: 0 14px 0 8px; }", css)

    def test_sidebar_phone_title_shows_only_on_a_phone(self):
        # memos-07: the large "Memos" over the list under 560, for an app that keeps its split
        try:
            from luma_appkit.widgets import NavigationSidebar
        except ValueError as missing:  # widgets.py needs the Luma typelibs
            self.skipTest(str(missing))
        side = NavigationSidebar()
        stage = self._stage(side, 402)
        side.set_phone_title("Memos")
        watch = self.adapt.window_tier(stage)
        watch.check()
        self.assertEqual(side.phone_title_label.get_label(), "Memos")
        self.assertTrue(side.phone_title_label.get_visible())
        self.assertTrue(side.phone_title_label.has_css_class("lumaui-list-first-title"))
        self.assertIs(side.get_first_child(), side.phone_title_label)
        stage.width = 1100
        watch.check()
        self.assertFalse(side.phone_title_label.get_visible())
        stage.width = 700
        watch.check()
        self.assertFalse(side.phone_title_label.get_visible(), "compact keeps the list as it was")
        stage.width = 402
        watch.check()
        side.set_phone_title(None)
        self.assertFalse(side.phone_title_label.get_visible())

    def test_tab_bar_places_counts_and_running(self):
        from luma_appkit.structure_tabs import TabBar
        places = [("world", "World", "globe"), ("alarms", "Alarms", "alarm-clock"),
                  ("stopwatch", "Stopwatch", "timer"), ("timer", "Timer", "hourglass")]
        self.assertTrue(TabBar.wants_tabs(places))
        self.assertFalse(TabBar.wants_tabs(places[:2]))
        self.assertFalse(TabBar.wants_tabs([("a", "A", ""), ("b", "B", "x"), ("c", "C", "y")]))
        seen = []
        tabs = TabBar(places, current="world", on_change=seen.append)
        self.assertTrue(tabs.tabs["world"].get_active())
        tabs.tabs["alarms"].set_active(True)
        self.assertEqual((tabs.current, seen), ("alarms", ["alarms"]))
        self.assertFalse(tabs.tabs["world"].get_active())
        tabs.set_current("timer")
        self.assertEqual(seen, ["alarms"], "set_current is quiet unless asked")
        tabs.set_count("alarms", 3)
        self.assertIn("alarms", tabs._counts)
        tabs.set_count("alarms", 0)
        self.assertNotIn("alarms", tabs._counts)
        tabs.set_status("stopwatch", "running")
        self.assertTrue(tabs._dots["stopwatch"].get_visible())
        compact = TabBar(places, compact=True)
        self.assertTrue(compact.has_css_class("compact"))
        self.assertEqual(compact.tabs["world"].get_tooltip_text(), "World")
        stage = self._stage(Gtk.Box(), 402)
        tabs.float_over(stage)
        self.assertIs(tabs.get_parent(), stage)

    def test_page_header_stacks_its_meta_on_a_phone(self):
        from luma_appkit.structure_trail import PageHeader
        header = PageHeader(title="Appearance", meta=["Dark", "Blue accent"])
        stage = self._stage(header, 1100)
        header._watch.check()
        self.assertFalse(header.phone)
        self.assertIs(header.meta_box.get_parent(), header.bar)
        self.assertTrue(header.meta_box.get_first_child().get_visible(), "a dot after the title")
        stage.width = 402
        header._watch.check()
        self.assertTrue(header.phone and header.has_css_class("phone"))
        self.assertIs(header.meta_box.get_parent(), header, "the meta on its own line")
        self.assertFalse(header.meta_box.get_first_child().get_visible(), "no leading dot on its own line")
        header.set_title("Display")
        self.assertEqual(header.title.get_label(), "Display")
        stage.width = 1100
        header._watch.check()
        self.assertIs(header.meta_box.get_parent(), header.bar)

    def test_memos_status_dot_before_the_title(self):
        island, _stage, _page = self._island(title="New memo", subtitle="Recording · Studio", lead="back")
        self.assertFalse(island.status_dot.get_visible())
        island.set_status("record")
        self.assertTrue(island.status_dot.get_visible() and island.status_dot.has_css_class("record"))
        island.set_status("paused")
        self.assertTrue(island.status_dot.has_css_class("paused") and not island.status_dot.has_css_class("record"))
        island.set_status(None)
        self.assertFalse(island.status_dot.get_visible())
        with self.assertRaises(ValueError):
            island.set_status("live")

    def test_notes_presence_dot_before_the_subtitle(self):
        island, _stage, _page = self._island(title="Launch", subtitle="Priya is here · Edited just now", lead="back")
        self.assertFalse(island.presence_dot.get_visible())
        island.set_status("here")
        self.assertTrue(island.presence_dot.get_visible())
        self.assertFalse(island.status_dot.get_visible(), "the title's dot is Memos'")
        self.assertIs(island.presence_dot.get_next_sibling(), island.subtitle_label)
        self.assertIs(island.subtitle_row.get_parent(), island._text)
        island.set_status("record")
        self.assertFalse(island.presence_dot.get_visible())
        self.assertTrue(island.status_dot.get_visible())
        island.set_status(None)
        self.assertFalse(island.status_dot.get_visible() or island.presence_dot.get_visible())
        island.set_status("here")
        island.set_title("Launch", "")
        self.assertTrue(island.subtitle_row.get_visible(), "the dot alone still shows")
        island.set_status(None)
        self.assertFalse(island.subtitle_row.get_visible())
        self.assertIn("lumaui-title-island-presence", BASE.read_text())

    def test_island_with_nothing_to_say_is_only_its_lead(self):
        # ari-02 (☰ alone), contacts-03 (‹ alone): no title part, no hairline, until there is a title.
        for lead in ("menu", "back"):
            island, _stage, _page = self._island(title="", lead=lead)
            self.assertTrue(island.has_css_class("lead-only"))
            self.assertFalse(island.title_button.get_visible())
            self.assertTrue(island.lead_button.get_visible())
            seen = []
            island.connect("lead", lambda *_a: seen.append(1))
            island.lead_button.emit("clicked")
            self.assertEqual(seen, [1])
            island.set_title("Launch", "Today")
            self.assertTrue(island.title_button.get_visible())
            self.assertFalse(island.has_css_class("lead-only"))
            island.set_title("", "")
            self.assertTrue(island.has_css_class("lead-only") and not island.title_button.get_visible())
        # a trailing button or faces keep the title part; ☰ alone still grows into the places
        island, _stage, _page = self._island(title="", lead="menu", grow=Gtk.Label(label="places"))
        self.assertEqual(island.lead_button.get_tooltip_text(), "Places", "alone, ☰ names itself")
        self.assertTrue(island.lead_button.get_focusable())
        island.lead_button.emit("clicked")
        self.assertTrue(island.grown)
        island.fold()
        island.set_title("Home")
        self.assertFalse(island.lead_button.get_focusable(), "beside a title, ☰ is part of that one control")
        island.set_title("")
        island.add_trailing(self.island_mod.TitleIsland.button("phone", "Call", lambda: None))
        self.assertTrue(island.title_button.get_visible() and not island.has_css_class("lead-only"))

    def test_details_tally_is_not_squeezed_to_an_ellipsis(self):
        from luma_appkit.structure_details import DetailsPane
        pane = DetailsPane("Details")
        heading = pane.add_section("Steps", count="3 of 5")
        labels = [c for c in _children(heading) if isinstance(c, Gtk.Label)]
        self.assertEqual([l.get_label() for l in labels], ["Steps", "3 of 5"])
        self.assertTrue(labels[0].get_hexpand())
        self.assertEqual(labels[1].get_ellipsize(), 0)
        heading.measure(Gtk.Orientation.HORIZONTAL, -1)
        heading.allocate(256, 20, -1, None)
        self.assertGreater(labels[1].get_width(), 20)

    def test_v71_variants_star_large_modes_facts_card(self):
        from luma_appkit.structure_details import DetailsPane
        from luma_appkit.structure_placement import CornerPill, ModeSwitch
        pill = CornerPill(states=[("star", "Favorite", True, lambda _on: None)])
        self.assertTrue(pill.controls["states.0"].has_css_class("favourite"))
        answer = ModeSwitch([("yes", "Going"), ("maybe", "Maybe"), ("no", "No")], fill=True, size="large")
        self.assertTrue(answer.has_css_class("large"))
        with self.assertRaises(ValueError):
            ModeSwitch([("a", "A"), ("b", "B")], size="huge")
        pane = DetailsPane("Details")
        facts = pane.add_facts([("Calendar", "Work"), ("Alert", "10 minutes before")], card=True)
        self.assertTrue(facts.has_css_class("card"))
        row = facts.get_first_child()
        self.assertEqual(row.get_first_child().get_valign(), Gtk.Align.CENTER)

    def test_corner_pill_zoom_after_the_modes_folds_at_720(self):
        from luma_appkit.structure_placement import CornerPill, ModeSwitch
        zoom = Gtk.Scale()
        pill = CornerPill(modes=ModeSwitch([("y", "Years"), ("m", "Months")]), zoom=zoom,
                          info=lambda _s: None)
        self.assertIs(pill.controls["zoom"].get_prev_sibling(), pill.controls["modes"])
        stage = self._stage(pill, 1100)
        pill._zoom_watch.check()
        self.assertTrue(zoom.get_visible())
        pill.set_zoom_shown(False)  # Years
        self.assertFalse(zoom.get_visible())
        stage.width = 700
        pill._zoom_watch.check()
        stage.width = 1100
        pill._zoom_watch.check()
        self.assertFalse(zoom.get_visible(), "widening never overrides the app's choice")
        pill.set_zoom_shown(True)
        self.assertTrue(zoom.get_visible())
        stage.width = 700
        pill._zoom_watch.check()
        self.assertFalse(zoom.get_visible())

    def test_corner_pill_order_puts_details_last(self):
        from luma_appkit.structure_placement import CornerPill
        pill = CornerPill(actions=[("phone", "Call", lambda: None), ("video", "Video", lambda: None)],
                          states=[("pin", "Pin", False, lambda _on: None)], info=lambda _shown: None,
                          order=("people", "actions", "states", "info"))
        names = []
        child = pill.normal.get_first_child()
        while child is not None:
            names.append(next(n for n, w in pill.controls.items() if w is child))
            child = child.get_next_sibling()
        self.assertEqual(names, ["actions.0", "actions.1", "states.0", "info"])
        with self.assertRaises(ValueError):
            CornerPill(info=lambda _s: None, order=("info", "info"))
        # One control of a group in its own place: Charlie's Archive, Flag, Delete, More.
        from luma_appkit import CommandRegistry
        mail = CornerPill(actions=[("archive", "Archive", lambda: None), ("trash-2", "Delete", lambda: None)],
                          states=[("star", "Flag", False, lambda _on: None)], more=CommandRegistry(()),
                          order=("actions.0", "states", "actions.1", "more"))
        names = []
        child = mail.normal.get_first_child()
        while child is not None:
            names.append(next(n for n, w in mail.controls.items() if w is child))
            child = child.get_next_sibling()
        self.assertEqual(names, ["actions.0", "states.0", "actions.1", "more"])
        self.assertTrue(CornerPill(actions=[("archive", "Archive", lambda: None)], quiet=True).has_css_class("quiet"))
        from luma_appkit import AddRow
        self.assertTrue(AddRow("Add account", icon="plus", compact=True).has_css_class("compact"))


if __name__ == "__main__":
    unittest.main()
