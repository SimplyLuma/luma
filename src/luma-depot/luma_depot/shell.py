# SPDX-License-Identifier: Apache-2.0
"""Depot's window, redesigned (7 Nov 2026): the shell only.

The sidebar (handoff 2.2), the page stack in the rounded island, the action
bar it hosts (2.4), the floating Back (2.5), history and search in the bar
(12). Pages live in `pages/`, Depot-only composites in `parts/`, pictures in
`pictures/`, and the rules pages follow in `model/`.

Until the redesign reaches every page this window is opt-in
(`LUMA_DEPOT_REDESIGN=1`); `window.DepotWindow` stays the default.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gdk, Gtk  # noqa: E402

from luma_appkit import (  # noqa: E402
    ActionCenter, AppWindow, Command, CommandGroup, CommandRegistry, BarSearch, BarWidget, CountBadge, Island, NavigationSidebar, RowLead, ScrollView, SidebarRow,
    SidebarToggle, Toast, ToastHost, Tooltip, add_style_sheet, icons,
)

from .model.world import World, load_fixture  # noqa: E402

__all__ = ["DepotShell", "PLACES"]

ICON_NAME = "org.projectluma.Depot"
PORTAL = "https://simplyluma.com/connect/developer"

#: (key, title, Lucide glyph, group heading) exactly as 2.2: Today · Browse: Apps, Looks, Tweaks · Yours.
PLACES: tuple[tuple[str, str, str, str], ...] = (
    ("today", "Today", "sparkles", ""),
    ("apps", "Apps", "blocks", "Browse"),
    ("looks", "Looks", "palette", "Browse"),
    ("tweaks", "Tweaks", "sliders-horizontal", "Browse"),
    ("library", "Library", "layers", "Yours"),
    ("updates", "Updates", "refresh-cw", "Yours"),
)


@dataclass
class Visit:
    """Where Back returns to: the page, the item, its scroll and its filters."""

    place: str
    item: tuple[str, str] | None = None
    scroll: float = 0.0
    state: dict = field(default_factory=dict)
    query: str = ""


def default_fixture() -> Path:
    here = Path(__file__).resolve()
    for root in (here.parents[3], Path("/usr/share/luma-depot")):
        candidate = root / "tests/fixtures/depot-redesign/world.json"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("no Depot world: set LUMA_DEPOT_FIXTURE to a world.json")


class DepotShell(AppWindow):
    """The window: sidebar, island, bar."""

    def __init__(self, application: Adw.Application, world: World | None = None) -> None:
        from .model.store import SampleStore
        fixture = os.environ.get("LUMA_DEPOT_FIXTURE", "")
        if world is not None:
            self.store = SampleStore(world)
        elif fixture.endswith(".json") or os.environ.get("LUMA_DEPOT_PREVIEW") == "1":
            # The approved sample: the design fixture, a preview, a screenshot run.
            # The sample's own device is the one it runs on: the phone on a phone, else the computer (the mockup's here()).
            from luma_appkit import lumaui
            here = os.environ.get("LUMA_DEPOT_HERE") or ("fp6" if lumaui.mobile_form_factor() else "xps")
            self.store = SampleStore(load_fixture(fixture if fixture.endswith(".json") else default_fixture(),
                                                  here=here))
        else:
            from .model.live import LiveStore
            self.store = LiveStore(self._world_changed)
        self.world = self.store.world
        super().__init__(application=application, app_id=application.get_application_id() or "org.projectluma.Depot",
                         title="Depot", icon_name=ICON_NAME, commands=self._commands(), default_width=1180,
                         default_height=740, minimum_width=360, minimum_height=440)
        self.add_css_class("dq")  # not "luma-depot": the old window's sheet stays with the old window
        _install_style()

        from .pages import create_pages
        self.pages = create_pages(self)
        self.place = "today"
        self.item: tuple[str, str] | None = None
        self.history: list[Visit] = []
        self.query = ""
        self._bar_page = None
        self._search: BarSearch | None = None

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.sidebar = self._build_sidebar()
        body.append(self.sidebar)
        body.append(self._build_main())
        self.set_body(body)
        self.sidebar_toggle = SidebarToggle(self.sidebar)
        self.title_bar.pack_start(self.sidebar_toggle)

        # Phone and compact (16.1): the sidebar becomes the places sheet behind a menu button,
        # Back moves into the bar, grids narrow; each tier change redraws the page.
        from luma_appkit.structure_adapt import WidthWatch
        from luma_appkit import lumaui
        self.width_tier = "regular"
        if lumaui.mobile_form_factor():
            self._apply_tier("phone")  # before the first page, so it is drawn for the phone
        self._width_watch = WidthWatch(self.host, on_tier=self._tier_changed)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.show_place("today")
        if os.environ.get("LUMA_DEPOT_STATE"):
            GLib.idle_add(self._stage_state, os.environ["LUMA_DEPOT_STATE"])

    def _stage_state(self, spec: str) -> bool:
        """A measured state for tools/lumaui-conform: "place:apps;item:app:tide;demo:filters;query:photo"."""
        for step in filter(None, (part.strip() for part in spec.split(";"))):
            kind, _sep, value = step.partition(":")
            if kind == "place":
                self.go(value)
            elif kind == "item":
                item_kind, _s, item_id = value.partition(":")
                self.open_item(item_kind, item_id)
            elif kind == "query":
                self._search_changed(value)
            elif kind == "scroll":
                GLib.timeout_add(300, lambda v=float(value): (self.scroller.get_vadjustment().set_value(v), False)[1])
            elif kind == "demo":
                demo = getattr(self.current_page, f"demo_{value.replace('-', '_')}", None)
                if demo is not None:
                    demo()
        return False

    # ── building ──────────────────────────────────────────────────────────

    def _commands(self) -> CommandRegistry:
        places = tuple(Command(f"depot.go.{key}", title, lambda k=key: self.go(k), f"lumaui-{glyph}-symbolic",
                               shortcut=("Ctrl", str(index)))
                       for index, (key, title, glyph, _g) in enumerate(PLACES, 1))
        return CommandRegistry((
            CommandGroup("", (Command("depot.search", "Search Depot", self.focus_search, "lumaui-search-symbolic",
                                      shortcut=("Ctrl", "F")),
                              Command("depot.back", "Back", self.back, "lumaui-chevron-left-symbolic",
                                      shortcut=("Alt", "Left")))),
            CommandGroup("Browse", places),
            CommandGroup("", (Command("depot.quit", "Quit Depot", self.close, "lumaui-log-out-symbolic",
                                      shortcut=("Ctrl", "Q")),)),
        ))

    def focus_search(self) -> None:
        if self.item is not None:
            self.back()
        if self._search is not None and self._search.entry is not None:
            self._search.entry.grab_focus()

    def _build_sidebar(self) -> NavigationSidebar:
        sidebar = NavigationSidebar(variant="places")
        sidebar.set_name("dq-side")
        sidebar.add_css_class("dq-side")
        self.rows: dict[str, Gtk.Widget] = {}
        heading = ""
        for key, title, glyph, group in PLACES:
            if group != heading:
                sidebar.append_section(group, ruled=True)
                heading = group
            row = SidebarRow(title, lead=RowLead.icon(glyph))
            row.destination = key
            if key == "updates":
                self.update_badge = CountBadge(attention=True)
                row.set_trail(self.update_badge)
            sidebar.append_row(row)
            self.rows[key] = row
        sidebar.list.connect("row-activated", lambda _l, row: self.go(row.destination)
                             if hasattr(row, "destination") else None)
        # "Your apps" (platform plan, decision 9): the developer portal, in the browser, for everyone.
        from .assets import data_dir
        theme = Gtk.IconTheme.get_for_display(self.get_display() or Gdk.Display.get_default())
        theme.add_search_path(str(data_dir() / "icons"))
        portal = Gtk.Button()
        portal.add_css_class("dq-portal")
        line = Gtk.Box()
        glyph = Gtk.Image(icon_name="lumaui-code-xml-symbolic")
        line.append(glyph)
        line.append(Gtk.Label(label="Your apps", xalign=0, hexpand=True))
        portal.set_child(line)
        portal.update_property([Gtk.AccessibleProperty.LABEL], ["Your apps, opens the developer portal"])
        portal.connect("clicked", lambda _b: Gtk.UriLauncher.new(PORTAL).launch(self, None, None, None))
        sidebar.append_footer(portal)
        self._refresh_badge()
        return sidebar

    def _build_main(self) -> Gtk.Widget:
        island = Island()
        island.set_name("dq-main")
        island.set_hexpand(True)
        overlay = Gtk.Overlay(vexpand=True, hexpand=True)
        self.overlay = overlay
        self.scroller = ScrollView()
        self.scroller.set_name("dq-body")
        self.frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.frame.add_css_class("dq-page")
        self.scroller.set_child(self.frame)
        overlay.set_child(self.scroller)
        # Back floats in the top-left corner of an item page (2.5); the phone puts it in the bar.
        self.back_button = Gtk.Button(halign=Gtk.Align.START, valign=Gtk.Align.START, visible=False)
        self.back_button.add_css_class("dq-fback")
        self.back_button.set_child(icons.image("chevron-left"))
        Tooltip.attach(self.back_button, "Back")
        self.back_button.connect("clicked", lambda _b: self.back())
        overlay.add_overlay(self.back_button)
        # On a phone the title row is gone: the places' ☰ floats where the phone frame puts the row's pills
        # (16 in, 48 down: under the clock, centred in the 52 row) and opens the kit's places drawer.
        self.places_button = Gtk.Button(halign=Gtk.Align.START, valign=Gtk.Align.START, visible=False)
        self.places_button.add_css_class("dq-places")
        # The ☰ is the pill's own drawing (as the mockup's phone .lsidebtn draws it), not a separate image.
        self.places_button.update_property([Gtk.AccessibleProperty.LABEL], ["Places"])
        self.places_button.connect("clicked", lambda _b: self.sidebar_toggle.toggle())
        overlay.add_overlay(self.places_button)
        island.append(overlay)
        self.host = ToastHost(island)
        self.center = ActionCenter().attach(self.host)
        self.center.set_name("dq-bar")
        return self.host

    # ── where we are ──────────────────────────────────────────────────────

    @property
    def current_page(self):
        if self.item is not None:
            return self.pages["item"]
        if self.place == "search":
            return self.pages["search"]
        return self.pages[self.place]

    def _visit(self) -> Visit:
        page = self.current_page
        return Visit(self.place, self.item, self.scroller.get_vadjustment().get_value(), page.snapshot(), self.query)

    def go(self, place: str) -> None:
        """A sidebar place: the history clears and the place's filters reset (2.3)."""
        if self.item is not None:
            self.pages["item"].reset()
        self.history.clear()
        self.query = ""
        self.pages[place].reset()
        self.show_place(place)

    def show_place(self, place: str, *, scroll: float = 0.0) -> None:
        self.place, self.item = place, None
        self._render(scroll=scroll)

    def open_item(self, kind: str, item_id: str) -> None:
        self.history.append(self._visit())
        self.item = (kind, item_id)
        self.pages["item"].reset()
        self._render()

    def back(self) -> None:
        if self.item is not None:
            self.pages["item"].reset()  # leaving stops a Look being tried
        if not self.history:
            self.go("today")
            return
        visit = self.history.pop()
        self.place, self.item, self.query = visit.place, visit.item, visit.query
        page = self.current_page
        page.restore(visit.state)
        self._render(scroll=visit.scroll, keep_search=self.place == "search")

    # ── drawing ───────────────────────────────────────────────────────────

    def _render(self, *, scroll: float = 0.0, keep_search: bool = False) -> None:
        page = self.current_page
        sidebar_place = self.place if self.place != "search" else (self.history[-1].place if self.history else "today")
        for key, row in self.rows.items():
            if key == sidebar_place:
                self.sidebar.list.select_row(row)
        self._set_body(page.build())
        self.back_button.set_visible(self.item is not None and not self.is_phone())
        self.places_button.set_visible(self.is_phone())
        if not (keep_search and self._bar_page is not None and self._search is not None):
            self.refresh_bar()
        GLib.idle_add(lambda: (self.scroller.get_vadjustment().set_value(scroll), False)[1])

    def _set_body(self, widget: Gtk.Widget) -> None:
        while (child := self.frame.get_first_child()) is not None:
            self.frame.remove(child)
        self.frame.append(widget)

    def refresh_body(self) -> None:
        """Rebuild only the page body: the bar, its open menu and the search field's focus stay (3.11)."""
        adjustment = self.scroller.get_vadjustment()
        scroll = adjustment.get_value()
        self._set_body(self.current_page.build())
        GLib.idle_add(lambda: (adjustment.set_value(min(scroll, adjustment.get_upper())), False)[1])

    def refresh_bar(self) -> None:
        page = self.current_page
        self._bar_page = page
        items: list = []
        if self.item is not None:
            if self.is_phone():
                from luma_appkit import BarAction
                items.append(BarAction("chevron-left", tooltip="Back", on_activate=self.back))
            items += page.bar_items()
        else:
            self._search = BarSearch("Search Depot", text=self.query, span="medium", collapsed=self.is_compact(),
                                     on_change=self._search_changed)
            self._search.on_escape_empty = self._search_escaped
            items.append(self._search)
            items += page.bar_items()
        # On a phone the bar spans the width, keeping the same buttons (16.1).
        self.center.show_bar(items, fill="edge" if self.is_phone() else False)

    # ── search (12) ───────────────────────────────────────────────────────

    def _search_changed(self, text: str) -> None:
        self.query = text
        if text.strip():
            if self.place != "search":
                self.history.append(self._visit())
                self.place = "search"
            self._set_body(self.pages["search"].build())
            self.back_button.set_visible(False)
        elif self.place == "search":
            self._search_escaped()

    def _search_escaped(self) -> None:
        """Escape clears the query and returns to the page before the search."""
        self.query = ""
        if self.place == "search" and self.history:
            visit = self.history.pop()
            self.place, self.item = visit.place, visit.item
            self.current_page.restore(visit.state)
            self._set_body(self.current_page.build())
            self.refresh_bar()

    # ── the person's keys ─────────────────────────────────────────────────

    def _key(self, _controller, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_Escape:
            if self.center.grown is not None:
                self.center.fold_panel()
                return True
            lightbox = getattr(self.pages.get("item"), "lightbox_open", None)
            if lightbox and lightbox():
                return False
            if self.place == "search":
                self._search_escaped()
                return True
            return False
        if keyval in (Gdk.KEY_f, Gdk.KEY_F) and state & Gdk.ModifierType.CONTROL_MASK:
            if self._search is not None and self._search.entry is not None:
                self._search.entry.grab_focus()
                return True
        if keyval == Gdk.KEY_Left and state & Gdk.ModifierType.ALT_MASK and self.history:
            self.back()
            return True
        return False

    # ── things pages ask for ──────────────────────────────────────────────

    def try_look(self, look) -> None:
        """Wear a Look in this window only (None takes it off): a theme's class on the window."""
        for name in [c for c in self.get_css_classes() if c.startswith("dq-trying-")]:
            self.remove_css_class(name)
        if look is not None:
            self.add_css_class(f"dq-trying-{look.theme or look.kind.lower()}")

    def is_compact(self) -> bool:
        return self.is_phone() or self.width_tier == "compact"

    def is_phone(self) -> bool:
        from luma_appkit import lumaui
        return lumaui.mobile_form_factor() or self.width_tier == "phone"

    def _apply_tier(self, tier: str) -> None:
        """Phone and compact (16.1): on a phone the kit's ☰ opens the places as its drawer, and the page runs
        under the clock, insetting itself."""
        self.width_tier = tier
        phone = self.is_phone()
        self.set_phone_bleed(phone)
        lumaui_set = __import__("luma_appkit").lumaui.set_css_class
        lumaui_set(self, "dq-phone", phone)
        lumaui_set(self, "dq-compact", self.is_compact())

    def _tier_changed(self, tier: str) -> None:
        if tier == self.width_tier:
            return
        self._apply_tier(tier)
        self._render(scroll=self.scroller.get_vadjustment().get_value())

    def toast(self, message: str, kind: str = "done") -> None:
        Toast.show(self.host, message, kind=kind)

    # ── links and views (luma-depot://, appstream://, --view) ─────────────

    def _find_app(self, identifier: str):
        world = self.world
        for app in world.apps:
            if identifier in (app.app_id, app.flatpak_id, app.slug):
                return world.item("app", app.app_id)
        return None

    def open_uri(self, uri: str) -> None:
        from luma_installer.depot_links import parse
        link = parse(uri)
        if link is None:
            return
        item = self._find_app(link.target) if link.action in ("app", "install") else None
        if item is not None:
            self.open_item("app", item.id)
        else:
            self.go("apps")

    def show_view(self, view: str) -> None:
        if view.startswith("app:"):
            item = self._find_app(view.split(":", 1)[1])
            if item is not None:
                self.open_item("app", item.id)
            return
        self.go({"home": "today", "mine": "library", "updates": "updates"}.get(view, "today"))

    def done(self, world: World, words: str = "") -> None:
        """An action finished: the world changes, the bar follows (a key may flip), then the toast."""
        self.set_world(world)
        if self.center.grown is not None:
            self.center.fold_panel()
        self.refresh_bar()
        self.refresh_body()
        if words:
            self.toast(words)

    def _world_changed(self, world: World) -> None:
        """The live store reloaded: redraw what shows, unless the person is typing or a panel is grown."""
        if not hasattr(self, "pages"):
            return
        self.set_world(world)
        if self.center.grown is None and self.place != "search":
            self.refresh_body()

    def set_world(self, world: World) -> None:
        """The world changed (an install, an update): the badge and the page follow; the bar is redrawn
        only when its buttons change (an item page's key flips)."""
        self.world = world
        self._refresh_badge()

    def _refresh_badge(self) -> None:
        count = self.world.update_count()
        self.update_badge.set_count(count)
        self.update_badge.set_visible(count > 0)


def _install_style() -> None:
    from .assets import data_dir
    sheet = data_dir() / "depot-redesign.css"
    if sheet.is_file():
        add_style_sheet(str(sheet))
