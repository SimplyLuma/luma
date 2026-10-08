# SPDX-License-Identifier: Apache-2.0

"""Maps.

The map is the window; everything else floats on it. The window itself is the
Application Kit's, so Maps wears the same frame, identity and controls as every
other Luma application.

The footer chip is the one thing here that is a promise rather than a
decoration. It reads the active tile provider's `local` flag and says what is
true: the map is on your machine, or it is coming from a named host. Nothing
sets that flag but a source that genuinely reads off local disk.
"""

from __future__ import annotations

import json
import os
import re
import pathlib
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402
if os.environ.get("LUMA_MAPS_FIXTURE"):
    Shumate = None
else:
    gi.require_version("Shumate", "1.0")
    from gi.repository import Shumate  # noqa: E402

from luma_appkit import (
    ActionCenter, AppWindow, BarAction, BarReadout, BarSearch, BarTile, BarTiles, BarWidget, Card, Command,
    CommandGroup, CommandRegistry, CornerPill, Favourite,
    FavouritesStrip, IconOnlyButton, Island, IslandSplitView, ModeSwitch, NavigationSidebar, PanelField,
    PanelHeading, PanelRow, Person, RowLead,
    ShareSheet, ShareSubject, ShareTarget, SharePanel, SidebarRow, SidebarToggle, Toast, ToastHost,
    add_style_sheet, apply_type, icons, install_appkit, install_lumaui,
)
from luma_appkit.action_center import make_control
from . import providers as provider_config
from .providers import Providers, RoutingProvider, SearchProvider, TileProvider
from .fixture import Guide, address_offer, drive_minutes, favorite_minutes, from_environment
from .guides import GuideHeader, GuideStrip, cover_area, load_photo, number_badge
from .model import MapState, fixture_state, route_progress
from .search import PlaceSearch, SearchOutcome
from .style import recolour
from .store import Place, PlaceStore
from .location import LocationRequest
from .camera import camera_for_places, fixture_view_for_places, nearby_cluster


ICON_NAME = "org.projectluma.Maps"
APP_ID = ICON_NAME + (".LumaUIPreview" if os.environ.get("LUMA_MAPS_PREVIEW") == "1" else "")
PANEL_MIN_WIDTH = 244
PANEL_MAX_WIDTH = 312
PANEL_SHARE = 0.30
OSM_COPYRIGHT = "https://openstreetmap.org/copyright"


# Layers this renderer would not accept, kept so the report can name them
# rather than the map quietly missing things.
DROPPED_LAYERS: list[tuple[str, str]] = []


def _supported_style(style: dict) -> dict:
    """Drop the layers this libshumate cannot draw, and only those.

    A style written for MapLibre may use a layer type or an expression form
    that libshumate has not implemented — 3D building extrusions, for one. The
    whole style is offered first; only if it is refused is each layer tried on
    its own, so the cost is paid once, when the style is first fetched.
    """
    try:
        Shumate.VectorRenderer.new("probe", json.dumps(style))
        return style
    except Exception:
        pass
    keep = []
    for layer in style.get("layers", []):
        trial = dict(style, layers=[layer])
        try:
            Shumate.VectorRenderer.new("probe", json.dumps(trial))
        except Exception as error:
            DROPPED_LAYERS.append((layer.get("id", "?"), str(error)))
            continue
        keep.append(layer)
    style["layers"] = keep
    return style


def _single_source(style: dict) -> dict:
    """Reduce a style to one data source.

    libshumate 1.6.3 refuses a style with more than one: "ShumateVectorRenderer
    does not currently support multiple data sources". Liberty carries two — the
    vector map and a shaded-relief raster underlay — so the vector source is
    kept and the relief is dropped along with the layers that drew it. The map
    is the same map; it loses a hillshade at low zoom.
    """
    sources = style.get("sources", {})
    if len(sources) <= 1:
        return style
    preferred = next((name for name, entry in sources.items()
                      if entry.get("type") == "vector"), next(iter(sources)))
    style["sources"] = {preferred: sources[preferred]}
    style["layers"] = [
        layer for layer in style.get("layers", [])
        if layer.get("source", preferred) == preferred
    ]
    return style


class MapsWindow(AppWindow):
    def __init__(self, application: Adw.Application) -> None:
        self.fixture = from_environment()
        if self.fixture is None:
            self.providers: Providers = provider_config.load()
            self.store = PlaceStore()
            self.search = PlaceSearch(self.providers.search, self.providers.user_agent)
        else:
            # The fixture never loads the user's provider overrides, place store,
            # map tiles or search endpoints. All state stays in this process.
            self.providers = Providers(
                user_agent="LumaMaps/fixture",
                tiles=(TileProvider("fixture", "v70 scene", "raster", True,
                                    "Fixture map", url_template=""),),
                search=SearchProvider("", "", "", 0, 0, True),
                routing=RoutingProvider("fixture", "", ("drive", "transit", "walk", "bike"), True, True),
                default_tile_id="fixture",
            )
            self.store = self.fixture
            self.search = None
        # Conform pins the sample route's ETA to the browser's Date.now value.
        fixture_clock = os.environ.get("LUMA_MAPS_CLOCK_MS", "") if self.fixture else ""
        self.fixture_now = datetime.fromtimestamp(int(fixture_clock) / 1000) if fixture_clock else None
        manager = Adw.StyleManager.get_default()
        self.tile_provider: TileProvider = self.providers.for_treatment(
            "dark" if manager.get_dark() else "light"
        )
        self.selected: Place | None = None
        self.results: tuple[Place, ...] = ()
        self.state = fixture_state(os.environ.get("LUMA_MAPS_STATE", ""), set(self.fixture.by_id)) \
            if self.fixture is not None else MapState()
        self.capture_navigation = bool(self.fixture) and os.environ.get("LUMA_MAPS_STATE", "").startswith("navigating:")
        self._location_request = None
        self._location_fix = None
        self._location_status = "Show where I am"
        self._location_center = False
        self._manual_view = False
        self._setting_location = False
        self._first_map = False
        self._closing = False
        self.nav_timer = 0
        self.nav_distance = 0.0
        self.nav_progress = None
        super().__init__(
            application=application,
            app_id=APP_ID,
            title="Maps",
            icon_name=ICON_NAME,
            commands=self._application_commands(),
            default_width=1060,
            default_height=720,
            minimum_width=360,
            minimum_height=380,
        )
        self.add_css_class("luma-maps")
        # v71: on a phone the map is the whole screen, under the clock; what floats on it insets itself.
        self.set_phone_bleed(True)

        self.split = IslandSplitView(collapsed=False, show_content=True)
        self.split.set_sidebar_width_unit(Adw.LengthUnit.PX)
        self.split.set_min_sidebar_width(272)
        self.split.set_max_sidebar_width(272)
        self.split.set_sidebar(Adw.NavigationPage(child=self._build_panel(), title="Places"))

        map_overlay = self.map_overlay = Gtk.Overlay(child=self._build_map())
        island = self.island = Island()
        island.set_name("mp-island")
        island.append(map_overlay)
        self.map_host = ToastHost(island)
        self.split.set_content(Adw.NavigationPage(child=self.map_host, title="Map"))
        self.set_body(self.split)

        self.corner = None
        self._place_corner()
        self.place_slot = Adw.Bin(halign=Gtk.Align.START, valign=Gtk.Align.START,
                                  margin_top=16, margin_start=16)
        self.place_slot.set_name("mp-card")
        map_overlay.add_overlay(self.place_slot)
        self.search_box: Gtk.Box | None = None   # the phone's grown search panel, refilled as you type
        self._no_regrow = False
        self.phone_search: BarSearch | None = None
        self.action_center = ActionCenter()
        self.action_center.attach(self.map_host)
        self.action_center.set_name("mp-bar")
        self._show_bar()

        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 820px"))
        narrow.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(narrow)
        self.split.connect("notify::collapsed", self._sync_sidebar_toggle)
        self._sync_sidebar_toggle()
        # v71's tiers: crossing 560 redraws the bar, the card and the corner in the other shape.
        self.tier_watch.connect("tier-changed", self._tier_changed)

        self._refresh_panel()
        self._apply_initial_state()
        # A dark desktop gets the dark map, and follows the preference live.
        Adw.StyleManager.get_default().connect("notify::dark", self._treatment_changed)
        if self.fixture is None:
            self.connect("map", self._first_mapped)
            self.connect("close-request", self._close_location)

    def _now(self) -> datetime:
        return self.fixture_now or datetime.now()

    # ── Commands ─────────────────────────────────────────────────────────

    def _application_commands(self) -> CommandRegistry:
        return CommandRegistry((
            CommandGroup("", (
                Command("maps.locate", "Show where I am", self._locate,
                        "find-location-symbolic"),
                Command('maps.sidebar', 'Show or hide sidebar', lambda: self.sidebar_toggle.toggle(), 'panel-left'),
            )),
            CommandGroup("Privacy", (
                Command("maps.clear-recents", "Clear recent places",
                        self._clear_recents, "edit-clear-symbolic"),
                Command("maps.clear-saved", "Clear saved places", self._clear_saved, "edit-clear-symbolic"),
                Command("maps.what-leaves", "What leaves this machine",
                        self._show_disclosure, "dialog-information-symbolic"),
            )),
            CommandGroup("", (
                Command("maps.about", "About Maps", self._show_about,
                        "help-about-symbolic"),
                Command("maps.quit", "Quit Maps", self.close, "application-exit-symbolic", shortcut=("Ctrl", "Q")),
            )),
        ))

    # ── The map ──────────────────────────────────────────────────────────

    def _build_map(self) -> Gtk.Widget:
        if self.fixture is not None:
            from .fixture_map import FixtureMapCanvas
            self.fixture_map = FixtureMapCanvas(self.fixture, on_select=self._open_place)
            self.fixture_map.set_layer(self.state.layer)
            self.fixture_map.set_route(self.state.directions or self.state.navigating, self.state.mode)
            return self.fixture_map
        self.simple_map = Shumate.SimpleMap()
        self.simple_map.set_name("mp-map")
        self.simple_map.set_hexpand(True)
        self.simple_map.set_vexpand(True)
        # Shumate draws its own compass and scale; v70 shows neither.
        self.simple_map.get_compass().set_visible(False)
        self.simple_map.get_scale().set_visible(False)
        self.map = self.simple_map.get_map()
        self.viewport = self.map.get_viewport()
        self.markers = Shumate.MarkerLayer.new(self.viewport)
        view = self.store.viewport
        self._manual_view = view is not None
        self.viewport.set_zoom_level(view[2] if view else 2)
        self.viewport.set_location(view[0], view[1]) if view else self.viewport.set_location(0, 0)
        self.viewport.connect("notify::latitude", self._view_changed)
        self.viewport.connect("notify::longitude", self._view_changed)
        self.viewport.connect("notify::zoom-level", self._view_changed)
        return self.simple_map

    def _apply_tile_provider(self, provider: TileProvider) -> None:
        self.tile_provider = provider
        if provider.kind == "raster":
            self._install_source(provider, self._raster_source(provider))
            return
        if provider.kind in {"vector", "pmtiles"}:
            # A vector style is a document that has to be fetched (or read off
            # disk) before anything can be drawn, so it happens on a worker and
            # the map appears when it is ready.
            threading.Thread(target=self._load_vector_source, args=(provider,),
                             daemon=True).start()

    def _install_source(self, provider: TileProvider, source) -> None:
        if self._closing or source is None or provider.id != self.tile_provider.id:
            return
        # Source installation can clamp zoom. It is not manual navigation.
        was_setting = self._setting_location
        self._setting_location = True
        try:
            self.simple_map.set_map_source(source)
        finally:
            self._setting_location = was_setting
        # A vector source is installed asynchronously. If its base layer is
        # added after the marker layer, the opaque map paints over every pin.
        # Recreate the overlay after each source change so pins remain on top.
        if self.markers.get_parent() is not None:
            self.simple_map.remove_overlay_layer(self.markers)
        self.markers = Shumate.MarkerLayer.new(self.viewport)
        self.simple_map.add_overlay_layer(self.markers)
        if self.selected is not None and self.selected.latitude is not None:
            self._show_marker(self.selected)
        elif self.results:
            self._frame_results()
        self._draw_location()
        licence = self.simple_map.get_license()
        # An ODbL obligation, not a courtesy: the attribution is always on
        # screen, and the copyright link goes with it.
        licence.set_visible(True)
        licence.append_map_source(source)

    def _raster_source(self, provider: TileProvider):
        return Shumate.RasterRenderer.new_full_from_url(
            provider.id, provider.name,
            f"{provider.attribution} · {OSM_COPYRIGHT}", OSM_COPYRIGHT,
            provider.min_zoom, provider.max_zoom, provider.tile_size,
            Shumate.MapProjection.MERCATOR, provider.url_template,
        )

    def _style_cache_path(self, provider: TileProvider) -> pathlib.Path:
        root = os.environ.get("XDG_CACHE_HOME", "") or str(pathlib.Path.home() / ".cache")
        directory = pathlib.Path(root) / "luma-maps/styles"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{provider.id}.json"

    def _load_vector_source(self, provider: TileProvider) -> None:
        style = self._style_document(provider)
        if not style:
            GLib.idle_add(lambda: (self._report_source_failure(provider), False)[1])
            return
        try:
            source = Shumate.VectorRenderer.new(provider.id, style)
        except Exception as error:
            detail = str(error)
            GLib.idle_add(
                lambda detail=detail: (self._report_source_failure(provider, detail), False)[1]
            )
            return
        GLib.idle_add(lambda: (self._install_source(provider, source), False)[1])

    def _style_document(self, provider: TileProvider) -> str:
        """The style JSON, from disk when it is there and the network when not.

        A style names its tile sources either inline or by a TileJSON URL. The
        renderer wants them inline, so a URL reference is resolved once here and
        the result cached — which is also what lets a launch with no network
        still draw a map it has drawn before.
        """
        cache = self._style_cache_path(provider)
        raw = ""
        if provider.style_url.startswith(("http://", "https://")):
            request = urllib.request.Request(
                provider.style_url, headers={"User-Agent": self.providers.user_agent}
            )
            try:
                with urllib.request.urlopen(request, timeout=12) as response:
                    raw = response.read().decode("utf-8")
            except Exception:
                raw = ""
        elif provider.style_url:
            try:
                raw = pathlib.Path(provider.style_url).expanduser().read_text(encoding="utf-8")
            except OSError:
                raw = ""
        if not raw and cache.is_file():
            return cache.read_text(encoding="utf-8")
        if not raw:
            return ""
        try:
            style = json.loads(raw)
            for source in style.get("sources", {}).values():
                if "tiles" in source or "url" not in source:
                    continue
                reference = urllib.request.Request(
                    source["url"], headers={"User-Agent": self.providers.user_agent}
                )
                with urllib.request.urlopen(reference, timeout=12) as response:
                    tilejson = json.load(response)
                for key in ("tiles", "minzoom", "maxzoom", "bounds", "attribution"):
                    if key in tilejson:
                        source[key] = tilejson[key]
                source.pop("url", None)
            treatment = provider.treatment or (
                "dark" if Adw.StyleManager.get_default().get_dark() else "light"
            )
            raw = json.dumps(_supported_style(
                recolour(_single_source(style), treatment)
            ))
        except Exception:
            pass
        try:
            cache.write_text(raw, encoding="utf-8")
        except OSError:
            pass
        return raw

    def _report_source_failure(self, provider: TileProvider, detail: str = "") -> None:
        if provider.id != self.tile_provider.id:
            return
        Toast.show(self.map_host, f"{provider.name} is unavailable: " +
                   (detail or "The style could not be fetched and nothing is cached yet."), kind="error")

    def _treatment_changed(self, manager, _pspec) -> None:
        wanted = self.providers.counterpart(
            self.tile_provider, "dark" if manager.get_dark() else "light"
        )
        if wanted.id != self.tile_provider.id:
            self._apply_tile_provider(wanted)

    # ── Panel ────────────────────────────────────────────────────────────

    def _build_panel(self) -> Gtk.Widget:
        self.sidebar = NavigationSidebar(variant="resources", width="regular")
        self.sidebar.set_name("mp-sidebar")
        self.sidebar.list.connect("row-activated", self._row_activated)
        sidebar_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        sidebar_host.append(self.sidebar)
        self.sidebar_toggle = SidebarToggle(self.sidebar, drawer_below=821, phone_enabled=False)
        self.sidebar_toggle.set_control_visible(False)
        self.sidebar_toggle.set_name("mp-sidebar-toggle")
        self.title_bar.pack_start(self.sidebar_toggle)
        return sidebar_host

    def _sync_sidebar_toggle(self, *_args) -> None:
        self.sidebar_toggle.set_control_visible(False)

    def _row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        place = getattr(row, "place", None)
        if place is not None:
            self._open_place(place)
        elif getattr(row, "address", None):
            self._toast(f"Pinned {row.address}, Oakland")
        elif getattr(row, "is_location", False):
            self._locate()

    def _refresh_panel(self) -> None:
        """What the sidebar shows (a computer) and what the phone's grown search holds, in v71's order:
        my location, Favorites, Guides, Recents; or, once you type, Address and Places."""
        self._refresh_sidebar()
        if self.search_box is not None:
            self._fill_search_box(self.search_box)

    def _location_row(self) -> SidebarRow:
        # The fixture knows its studio location. The live map does not yet have
        # portal permission, so it must not offer to share an unknown position.
        share = None
        if self.fixture is not None:
            share = make_control(BarAction("share-2", tooltip="Share my location",
                                 on_activate=lambda: self._share(self.fixture.by_id["studio"], share)))
        location = SidebarRow("My location", lead=RowLead.glyph("navigation", accent=True),
                              subtitle="Luma Studio, 2nd St" if self.fixture else self._location_status,
                              trail=share)
        location.set_name("mp-me")
        location.is_location = True
        return location

    def _refresh_sidebar(self) -> None:
        self.sidebar.clear()
        self.sidebar.list.unselect_all()
        guide = self._open_guide_record()
        if guide is not None:
            self._sidebar_guide(guide)
            return
        self.sidebar.append_row(self._location_row())
        if not self.state.query:
            if self.store.saved:
                self.sidebar.append_section("Favorites")
                favourites = FavouritesStrip(
                    [Favourite(getattr(place, "favourite", "") or place.name,
                               icon=getattr(place, "icon", "map-pin"),
                               sub=f"{favorite_minutes(place)} min" if self.fixture else None, key=place)
                     for place in self.store.saved],
                    kind="places", on_open=lambda item: self._open_place(item.key), label="Favorites")
                wrapper = Gtk.ListBoxRow(selectable=False, activatable=False)
                wrapper.set_child(favourites)
                self.sidebar.list.append(wrapper)
            if self.fixture is not None and self.fixture.guides:
                self.sidebar.append_section("Guides")
                wrapper = Gtk.ListBoxRow(selectable=False, activatable=False)
                wrapper.set_child(GuideStrip(self.fixture, self._open_guide))
                self.sidebar.list.append(wrapper)
            if self.store.recents:
                self.sidebar.append_section("Recents")
                for place in self.store.recents:
                    self.sidebar.append_row(self._place_row(place))
            return
        offer = address_offer(self.state.query) if self.fixture is not None else ""
        if offer:
            self.sidebar.append_section("Address")
            self.sidebar.append_row(self._address_row(offer))
        if self.results or not offer:
            self.sidebar.append_section("Places")
        for place in self.results:
            self.sidebar.append_row(self._place_row(place))
        if not self.results and not offer:
            self.sidebar.list.append(Gtk.ListBoxRow(child=self._none_hint(), selectable=False, activatable=False))

    @staticmethod
    def _none_hint() -> Gtk.Widget:
        empty = apply_type(Gtk.Label(label="No places match. Try a name, a kind of place or a street address.",
                                     xalign=0, wrap=True), "caption")
        empty.set_margin_start(8)
        empty.set_margin_top(6)
        return empty

    def _address_row(self, address: str) -> SidebarRow:
        row = SidebarRow(address, lead=RowLead.glyph("map-pin"), subtitle="Oakland, California")
        row.set_name("mp-row")
        row.address = address
        return row

    def _place_row(self, place: Place) -> SidebarRow:
        subtitle = place.subtitle
        if self.fixture is not None:
            subtitle = place.address if not self.state.query else place.subtitle
        icon = getattr(place, "icon", "map-pin")
        row = SidebarRow(place.name, lead=RowLead.glyph(icon), subtitle=subtitle)
        row.set_name("mp-row")
        row.place = place
        return row

    # ── Guides ───────────────────────────────────────────────────────────

    def _open_guide_record(self) -> Guide | None:
        """The guide the panel is showing: only with nothing typed (v71 `MP.guide && !MP.q`)."""
        if self.fixture is None or self.state.query or not self.state.guide:
            return None
        return self.fixture.guides_by_id.get(self.state.guide)

    def _guide_places(self, guide: Guide) -> list[Place]:
        return [self.fixture.by_id[key] for key in guide.place_ids]

    def _open_guide(self, guide: Guide | None) -> None:
        """A guide opens in the same panel and the map flies to fit its places."""
        self.state = self.state.open_guide(guide.id if guide else None)
        if guide is not None:
            self._fly_to_guide(guide)
        self._refresh_panel()

    def _fly_to_guide(self, guide: Guide) -> None:
        """The map goes to the middle of the guide's places (v71 mpFly to their centroid at .8)."""
        if self.fixture is None:
            return
        places = self._guide_places(guide)
        self.fixture_map.set_view(sum(p.map_x for p in places) / len(places),
                                  sum(p.map_y for p in places) / len(places), .8)

    def _guide_start(self, guide: Guide) -> None:
        if self.phone:
            self.action_center.fold_panel()
        self._open_place(self._guide_places(guide)[0])

    def _guide_actions(self, guide: Guide) -> Gtk.Widget:
        row = Gtk.Box(homogeneous=True)
        row.add_css_class("mp-guide-actions")
        row.append(make_control(BarAction("bookmark", "Save", filled=True,
                                          on_activate=lambda: self._toast("Saved to your Guides"))))
        row.append(make_control(BarAction("share-2", "Share", filled=True,
                                          on_activate=lambda: self._toast("Sent the guide to Messages"))))
        row.append(make_control(BarAction("navigation", "Start", primary=True,
                                          on_activate=lambda: self._guide_start(guide))))
        return row

    def _guide_line(self, place: Place) -> str:
        rating = f" · ★ {place.rating}" if getattr(place, "rating", None) is not None else ""
        return f"{place.kind}{rating} · {getattr(place, 'hours', '') or place.address}"

    def _sidebar_guide(self, guide: Guide) -> None:
        back = PanelRow("Guides", icon="chevron-left", closes=False, on_activate=lambda: self._open_guide(None))
        self.sidebar.list.append(Gtk.ListBoxRow(child=back, selectable=False, activatable=False))
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header.append(GuideHeader(self.fixture, guide))
        note = apply_type(Gtk.Label(label=guide.note, xalign=0, wrap=True), "body")
        note.add_css_class("mp-guide-note")
        header.append(note)
        self.sidebar.list.append(Gtk.ListBoxRow(child=header, selectable=False, activatable=False))
        for number, place in enumerate(self._guide_places(guide), 1):
            row = SidebarRow(place.name, lead=number_badge(number), subtitle=self._guide_line(place))
            row.set_name("mp-row")
            row.place = place
            self.sidebar.append_row(row)
        self.sidebar.list.append(Gtk.ListBoxRow(child=self._guide_actions(guide), selectable=False,
                                                activatable=False))

    # ── The phone's grown search: the same content, as bar panel parts ───

    def _search_panel(self) -> Gtk.Widget:
        self.search_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.search_box.add_css_class("mp-search-panel")
        self._fill_search_box(self.search_box)
        return self.search_box

    def _panel_place(self, place: Place, *, sub: str | None = None, lead: Gtk.Widget | None = None) -> PanelRow:
        return PanelRow(place.name, lead=lead or RowLead.glyph(getattr(place, "icon", "map-pin")),
                        subtitle=sub if sub is not None else place.subtitle,
                        on_activate=lambda p=place: self._open_place(p))

    def _fill_search_box(self, box: Gtk.Box) -> None:
        child = box.get_first_child()
        while child is not None:
            box.remove(child)
            child = box.get_first_child()
        guide = self._open_guide_record()
        if guide is not None:
            box.append(PanelRow("Guides", icon="chevron-left", closes=False,
                                on_activate=lambda: self._open_guide(None)))
            box.append(GuideHeader(self.fixture, guide))
            note = apply_type(Gtk.Label(label=guide.note, xalign=0, wrap=True), "body")
            note.add_css_class("mp-guide-note")
            box.append(note)
            for number, place in enumerate(self._guide_places(guide), 1):
                box.append(self._panel_place(place, sub=self._guide_line(place), lead=number_badge(number)))
            box.append(self._guide_actions(guide))
            return
        me = Gtk.Box()
        here = PanelRow("My location", lead=RowLead.glyph("navigation", accent=True),
                        subtitle="Luma Studio, 2nd St" if self.fixture else self._location_status,
                        on_activate=self._locate)
        here.set_hexpand(True)
        me.append(here)
        if self.fixture is not None:
            me.append(make_control(BarAction("share-2", tooltip="Share my location",
                                  on_activate=lambda: self._toast("Sharing your location"))))
        box.append(me)
        if self.state.query:
            offer = address_offer(self.state.query) if self.fixture is not None else ""
            if offer:
                box.append(PanelHeading("Address"))
                box.append(PanelRow(offer, icon="map-pin", subtitle="Oakland, California",
                                    on_activate=lambda: self._toast(f"Pinned {offer}, Oakland")))
            if self.results or not offer:
                box.append(PanelHeading("Places"))
            for place in self.results:
                box.append(self._panel_place(place))
            if not self.results and not offer:
                box.append(self._none_hint())
            return
        if self.store.saved:
            box.append(PanelHeading("Favorites"))
            box.append(BarTiles([BarTile(getattr(place, "icon", "map-pin"),
                                         getattr(place, "favourite", "") or place.name,
                                         lambda p=place: self._open_place(p),
                                         sub=f"{favorite_minutes(place)} min" if self.fixture else None, well=True,
                                         name=getattr(place, "favourite", "") or place.name)
                                 for place in self.store.saved], columns=4))
        if self.fixture is not None and self.fixture.guides:
            box.append(PanelHeading("Guides"))
            box.append(GuideStrip(self.fixture, self._open_guide))
        if self.store.recents:
            box.append(PanelHeading("Recents"))
            for place in self.store.recents:
                box.append(self._panel_place(place, sub=place.address if self.fixture else place.subtitle))

    # ── Place detail ─────────────────────────────────────────────────────

    def _apply_initial_state(self) -> None:
        if self.fixture is None:
            return
        if self.state.query:
            self._search_query(self.state.query)
            self.search_field.set_text(self.state.query)
        if self.state.place:
            self.selected = self.fixture.by_id[self.state.place]
            self._render_place()
            self.fixture_map.set_selected(self.state.place)
            if self.state.navigating:
                # The browser fixture advances its 60 ms route animation for
                # 19 ticks, then its initial fly-to finishes at the route
                # start while the moving marker stays at that tick.
                self.nav_progress = route_progress(self.fixture.route_points, 172)
                self.fixture_map.set_view(760, 900, 1.6)
                self.fixture_map.set_position(self.nav_progress.x, self.nav_progress.y)
            elif self.state.directions:
                self.fixture_map.set_view(1100, 660, .74)
            else:
                z = self.fixture_map.view["zoom"]
                self.fixture_map.set_view(self.selected.map_x - 120 / z, self.selected.map_y, max(z, .95))
        if self.state.guide:
            self._fly_to_guide(self.fixture.guides_by_id[self.state.guide])
        if self.state.navigating:
            self._show_bar()
            self.corner.set_visible(False)

    def _open_place(self, place: Place) -> None:
        self._stop_nav_timer()
        self.selected = place
        self.state = self.state.select(getattr(place, "id", place.name))
        if self.phone and self.state.query:
            # v71: picking a place on a phone closes the search and clears what was typed.
            self.state = self.state.search("")
            self.results = ()
            if self.fixture is not None:
                self.fixture_map.set_results(None)
        self.store.remember(place)
        if place.latitude is not None and place.longitude is not None:
            self.viewport.set_location(place.latitude, place.longitude)
            self.viewport.set_zoom_level(max(self.viewport.get_zoom_level(), 15))
            self._show_marker(place)
        elif self.fixture is not None:
            z = self.fixture_map.view["zoom"]
            self.fixture_map.set_view(place.map_x - 120 / z, place.map_y, max(z, .95))
            self.fixture_map.set_selected(place.id)
            self.fixture_map.set_route(False)
        self._render_place()
        self._refresh_panel()

    @property
    def phone(self) -> bool:
        """v71's phone tier: the window is under 560 wide."""
        return self.tier == "phone"

    def _tier_changed(self, _watch, _tier: str) -> None:
        """Crossing 560 redraws in the other shape: the corner, the card, the bar and the panel."""
        self._sync_sidebar_toggle()
        self._place_corner()
        self._render_place()
        if not self.phone or self.state.navigating:
            self._show_bar()
        self._refresh_panel()

    def _render_place(self) -> None:
        if self.selected is None:
            self.place_slot.set_child(None)
            if self.phone:
                self._show_bar()
            return
        if self.phone:
            # The card lives in the bar, grown (_show_bar). Only the next turn floats, at the top.
            self.place_slot.set_child(None)
            if self.state.navigating:
                self._float_guidance()
                return
            self._show_bar()
            return
        self.place_slot.set_halign(Gtk.Align.CENTER if self.state.navigating else Gtk.Align.START)
        self.place_slot.set_margin_start(0 if self.state.navigating else 16)
        self.place_slot.set_margin_end(0)
        self.place_slot.set_margin_top(16)
        card = (self._guidance_card() if self.state.navigating else
                self._directions_card() if self.state.directions else self._place_card())
        self.place_slot.set_child(card)

    def _float_guidance(self) -> None:
        """The next turn on the phone: a green card at the top on the gutter, 52 down."""
        self.place_slot.set_halign(Gtk.Align.FILL)
        self.place_slot.set_margin_start(16)
        self.place_slot.set_margin_end(16)
        self.place_slot.set_margin_top(8 + self.status_inset if self.status_inset else 16)
        self.place_slot.set_child(self._guidance_card())

    def _place_body(self, *, actions: bool) -> Gtk.Widget:
        """Name, kind and rating, hours, and the facts: a computer's card adds the four actions."""
        place = self.selected
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.add_css_class("mp-place-body")
        title = apply_type(Gtk.Label(label=place.name, xalign=0, wrap=True), "card-title")
        title.add_css_class("mp-place-title")
        body.append(title)
        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        meta.add_css_class("mp-place-meta")
        kind = place.kind
        if getattr(place, "rating", None) is not None:
            kind += " ·"
        meta.append(apply_type(Gtk.Label(label=kind, xalign=0), "body"))
        if getattr(place, "rating", None) is not None:
            rating = apply_type(Gtk.Label(label=f"★ {place.rating}", xalign=0), "body", weight=700)
            rating.add_css_class("mp-place-rating")
            meta.append(rating)
        if getattr(place, "price", ""):
            meta.append(apply_type(Gtk.Label(label=f" · {place.price}", xalign=0), "body"))
        body.append(meta)
        if getattr(place, "hours", ""):
            hours = apply_type(Gtk.Label(label=place.hours, xalign=0), "meta")
            hours.add_css_class("mp-place-hours")
            # v71: "Open until 11 PM" and "Open 24 hours" read green.
            if re.search(r"until|24", place.hours):
                hours.add_css_class("mp-hours-open")
            body.append(hours)
        if actions:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
            row.add_css_class("mp-place-actions")
            row.append(self._place_action("navigation", f"{drive_minutes(place)} min" if self.fixture
                                          else "Directions", self._show_route))
            if self.fixture is not None:
                row.append(self._place_action("phone", "Call", lambda: self._toast("Calling " + place.name)))
            row.append(self._place_action("star", "Favorites", lambda: self._toggle_saved(place),
                                          active=self.store.is_saved(place)))
            share_button = self._place_action("share-2", "Share", lambda: self._share(place, share_button))
            row.append(share_button)
            body.append(row)
        if place.address:
            body.append(self._place_fact("Address", place.address + (", Oakland" if self.fixture else "")))
        if self.fixture is not None and place.id == "loft":
            body.append(self._place_fact("On your calendar", "Launch night · Thursday, 6 PM"))
        return body

    def _place_card(self) -> Card:
        """The Maps-only photo-led card, composed on the kit's Card surface."""
        place = self.selected
        card = Card(padded=False)
        card.add_css_class("mp-place-card")
        card.set_size_request(340, -1)
        if self.fixture is not None:
            # A 150 px cover viewport: the Maps hero owns its crop.
            image = Gtk.Overlay(child=cover_area(load_photo(self.fixture, place.image), 150))
            close = make_control(BarAction("x", tooltip="Close", on_activate=self._back_to_list))
            close.add_css_class("mp-place-close")
            close.set_halign(Gtk.Align.END)
            close.set_valign(Gtk.Align.START)
            close.set_margin_top(10)
            close.set_margin_end(10)
            image.add_overlay(close)
            card.append(image)
        card.append(self._place_body(actions=True))
        return card

    def _place_panel(self) -> Gtk.Widget:
        """The picked place as the bar's grown panel (v71 .mpcard2): a 140 photo, then the words."""
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.add_css_class("mp-place-panel")
        if self.fixture is not None:
            photo = cover_area(load_photo(self.fixture, self.selected.image), 140)
            photo.add_css_class("mp-place-photo")
            panel.append(photo)
        panel.append(self._place_body(actions=False))
        return panel

    def _place_action(self, icon: str, label: str, callback, *, active: bool = False) -> Gtk.Button:
        button = Gtk.Button()
        button.add_css_class("mp-place-action")
        if active:
            button.add_css_class("lit")
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.set_valign(Gtk.Align.CENTER)
        column.append(icons.image("star-filled" if active and icon == "star" else icon))
        column.append(apply_type(Gtk.Label(label=label), "label"))
        button.set_child(column)
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        button.connect("clicked", lambda _b: callback())
        return button

    @staticmethod
    def _place_fact(caption: str, value: str) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        row.add_css_class("mp-place-fact")
        row.append(apply_type(Gtk.Label(label=caption, xalign=0), "caption", weight=400))
        row.append(apply_type(Gtk.Label(label=value, xalign=0, wrap=True), "body"))
        return row

    def _route_header(self) -> Gtk.Widget:
        """Back, From and To, Swap: the head of the route (a computer's card and the phone's grown panel)."""
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header.add_css_class("mp-route-header")
        header.append(make_control(BarAction("chevron-left", tooltip="Back", on_activate=self._back_to_place)))
        destination = Gtk.Grid(hexpand=True)
        destination.add_css_class("mp-route-destination")
        destination.set_column_spacing(8)
        destination.set_row_spacing(2)
        for row, (caption, value) in enumerate((("From", "Luma Studio"), ("To", self.selected.name))):
            destination.attach(apply_type(Gtk.Label(label=caption, xalign=0), "caption", weight=400), 0, row, 1, 1)
            destination.attach(apply_type(Gtk.Label(label=value, xalign=0), "body", weight=700), 1, row, 1, 1)
        header.append(destination)
        header.append(make_control(BarAction("arrow-down", tooltip="Swap", on_activate=lambda: self._toast("Swapped"))))
        return header

    MODE_ICONS = (("drive", "car"), ("transit", "train-front"), ("walk", "footprints"), ("bike", "bike"))

    def _route_steps(self) -> Gtk.Widget:
        steps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        steps.add_css_class("mp-route-steps")
        for index, (icon, instruction, distance) in enumerate(self.fixture.steps):
            step = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            step.add_css_class("mp-route-step")
            if index == len(self.fixture.steps) - 1:
                step.add_css_class("last")
            glyph = icons.image(icon, pixel_size=16)
            glyph.add_css_class("mp-route-step-icon")
            glyph.set_valign(Gtk.Align.CENTER)
            step.append(glyph)
            title = apply_type(Gtk.Label(label=instruction, xalign=0, hexpand=True), "body")
            step.append(title)
            if distance:
                step.append(apply_type(Gtk.Label(label=distance, xalign=1), "caption", muted=True, weight=400))
            steps.append(step)
        return steps

    def _arrival(self, minutes: int) -> str:
        return (self._now() + timedelta(minutes=minutes)).strftime("%I:%M %p").lstrip("0")

    def _directions_card(self) -> Card:
        """Maps route details on a shared card with route-specific modes and steps."""
        card = Card(padded=False)
        card.add_css_class("mp-route-card")
        card.set_size_request(340, -1)
        card.append(self._route_header())
        modes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        modes.add_css_class("mp-route-modes")
        modes.update_property([Gtk.AccessibleProperty.LABEL], ["Travel mode"])
        for key, icon in self.MODE_ICONS:
            label = f"{self.fixture.route[key]['minutes']} min"
            button = Gtk.Button()
            button.add_css_class("mp-route-mode")
            if key == self.state.mode:
                button.add_css_class("on")
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            glyph = icons.image(icon, pixel_size=17)
            glyph.set_halign(Gtk.Align.CENTER)
            content.append(glyph)
            content.append(apply_type(Gtk.Label(label=label), "label", weight=700))
            button.set_child(content)
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            button.connect("clicked", lambda _button, mode=key: self._set_travel_mode(mode))
            modes.append(button)
        card.append(modes)
        route = self.fixture.route[self.state.mode]
        summary = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        summary.add_css_class("mp-route-summary")
        readout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        minutes = apply_type(Gtk.Label(label=f"{route['minutes']} min", xalign=0), "card-title")
        minutes.add_css_class("mp-route-minutes")
        readout.append(minutes)
        via = apply_type(Gtk.Label(label=f"{route['distance']} · {route['via']}", xalign=0), "meta")
        via.add_css_class("mp-route-via")
        readout.append(via)
        traffic = " · light traffic" if self.state.mode == "drive" else ""
        eta = apply_type(Gtk.Label(label=f"Arrive {self._arrival(route['minutes'])}{traffic}", xalign=0),
                         "caption", weight=400)
        eta.add_css_class("mp-route-eta")
        readout.append(eta)
        summary.append(readout)
        summary.append(make_control(BarAction("navigation", "Go", on_activate=self._go, primary=True)))
        card.append(summary)
        card.append(self._route_steps())
        return card

    def _route_panel(self) -> Gtk.Widget:
        """The phone's directions, grown from the bar: From and To, the four modes as tiles, every step.
        The chosen mode's summary and Go are the bar's row, so no summary box sits here (v71, 30 Sep)."""
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.add_css_class("mp-route-panel")
        panel.append(self._route_header())
        panel.append(BarTiles([BarTile(icon, f"{self.fixture.route[key]['minutes']} min",
                                       lambda mode=key: self._set_travel_mode(mode),
                                       on=key == self.state.mode, closes=False, name=key.title())
                               for key, icon in self.MODE_ICONS], columns=4, size="compact"))
        panel.append(self._route_steps())
        return panel

    def _guidance_card(self) -> Card:
        """The route's active instruction on a Maps-only card surface."""
        card = Card(padded=False)
        card.add_css_class("mp-guidance-card")
        card.set_size_request(-1 if self.phone else 460, 94 if self.phone else 76)
        if self.capture_navigation:
            icon, instruction, next_step = ("corner-up-right", "In 410 ft, turn right onto 12th St",
                                            "Then turn left onto franklin st")
        elif self.nav_progress is not None:
            index = min(self.nav_progress.segment, len(self.fixture.steps) - 1)
            icon, turn, _distance = self.fixture.steps[index]
            left = max(100, round(self.nav_progress.to_turn * 3.2 / 10) * 10)
            instruction = f"In {left} ft, {turn[0].lower() + turn[1:]}"
            next_step = "Then " + self.fixture.steps[min(index + 1, len(self.fixture.steps) - 1)][1].lower()
        else:
            icon, instruction, _distance = self.fixture.steps[0]
            next_step = "Then turn right onto 12th St"
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.add_css_class("mp-guidance-row")
        icon_box = Gtk.CenterBox(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        icon_box.set_size_request(48, 48)
        symbol = icons.image(icon, pixel_size=40)
        symbol.add_css_class("mp-guidance-icon")
        icon_box.set_center_widget(symbol)
        row.append(icon_box)
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        phone_instruction = instruction.replace(" onto 12th St", " onto\n12th St") if self.phone else instruction
        title = apply_type(Gtk.Label(label=phone_instruction, xalign=0, wrap=True), "guidance")
        title.add_css_class("mp-guidance-title")
        copy.append(title)
        following = apply_type(Gtk.Label(label=next_step, xalign=0), "body")
        following.add_css_class("mp-guidance-next")
        copy.append(following)
        row.append(copy)
        card.append(row)
        return card

    def _back_to_list(self) -> None:
        self._stop_nav_timer()
        self.selected = None
        self.state = self.state.close_place()
        if self.fixture is not None:
            self.fixture_map.set_selected(None)
            self.fixture_map.set_route(False)
        self._render_place()
        self._refresh_panel()

    def _back_to_place(self) -> None:
        self.state = self.state.back_to_place()
        self._render_place()

    def _show_marker(self, place: Place) -> None:
        self.markers.remove_all()
        marker = Shumate.Marker.new()
        marker.set_location(place.latitude, place.longitude)
        marker.set_child(icons.image("map-pin"))
        self.markers.add_marker(marker)

    def _toggle_saved(self, place: Place) -> None:
        saved = self.store.is_saved(place)
        if saved:
            self.store.unsave(place)
        else:
            self.store.save(place)
        self._refresh_panel()
        self._render_place()
        Toast.show(self.map_host, "Removed from Favorites" if saved else "Added to Favorites",
                   kind="undone" if saved else "added",
                   undo=lambda: self._undo_saved(place, saved))

    def _undo_saved(self, place: Place, was_saved: bool) -> None:
        (self.store.save if was_saved else self.store.unsave)(place)
        self._refresh_panel()
        self._render_place()

    @staticmethod
    def _place_url(place: Place) -> str:
        if place.latitude is not None and place.longitude is not None:
            lat, lon = place.latitude, place.longitude
            return f"https://www.openstreetmap.org/?mlat={lat:.7f}&mlon={lon:.7f}#map=16/{lat:.7f}/{lon:.7f}"
        query = urllib.parse.quote(" ".join(part for part in (place.name, place.address) if part))
        return f"https://www.openstreetmap.org/search?query={query}"

    def _share(self, place: Place, anchor: Gtk.Widget) -> None:
        url = self._place_url(place)
        targets = [ShareTarget("open-map", "Open map", icon="globe")]
        if Gio.AppInfo.get_default_for_uri_scheme("mailto") is not None:
            targets.insert(0, ShareTarget("mail", "Email", icon="mail"))

        def chosen(choice: str, value: object) -> str | None:
            if choice == "copy-link":
                self.get_clipboard().set(url)
                return "Map link copied"
            if choice == "target":
                uri = ("mailto:?" + urllib.parse.urlencode({"subject": place.name, "body": url})
                       if value == "mail" else url)
                try:
                    Gio.AppInfo.launch_default_for_uri(uri, None)
                except GLib.Error as error:
                    self._report("Could not open the destination", str(error))
                return None
            if choice == "code":
                self._report("Code sharing is unavailable", "Copy the map link to share this place.")
            return None

        ShareSheet.present(anchor, document=ShareSubject(place.name, place.address,
                           kind="link", icon="map-pin"), targets=targets, on_choice=chosen)

    def _show_route(self) -> None:
        if self.fixture is not None:
            self.state = self.state.show_directions()
            self.fixture_map.set_view(1100, 660, .74)
            self.fixture_map.set_route(True, self.state.mode)
            self._render_place()
            return
        # Routing is configured but not yet wired to a renderer. Do not draw a
        # made-up route against Nick's real map.
        self._report("Directions are not ready", "Maps has no real route to show yet.")

    def _set_travel_mode(self, mode: str) -> None:
        self.state = self.state.set_mode(mode)
        self.fixture_map.set_route(True, mode)
        self._render_place()

    def _go(self) -> None:
        self.state = self.state.go()
        self.capture_navigation = False
        self.nav_distance = 0.0
        self.nav_progress = route_progress(self.fixture.route_points, 0)
        self.fixture_map.set_view(760, 900, 1.6)
        self.fixture_map.set_position(760, 900)
        self._render_place()
        self.corner.set_visible(False)
        self._show_bar()
        self.nav_timer = GLib.timeout_add(60, self._navigation_tick)

    def _stop_nav_timer(self) -> None:
        if self.nav_timer:
            GLib.source_remove(self.nav_timer)
            self.nav_timer = 0

    def _navigation_tick(self) -> bool:
        if not self.state.navigating:
            self.nav_timer = 0
            return False
        self.nav_distance += 9
        self.nav_progress = route_progress(self.fixture.route_points, self.nav_distance)
        progress = self.nav_progress
        self.fixture_map.set_view(progress.x, progress.y, 1.6)
        self.fixture_map.set_position(progress.x, progress.y)
        if progress.remaining <= 0:
            self.nav_timer = 0
            self._end()
            self._toast("You’ve arrived at " + self.selected.name)
            return False
        route = self.fixture.route[self.state.mode]
        minutes = max(1, round(route["minutes"] * progress.remaining))
        distance = f"{2.4 * progress.remaining:.1f} mi"
        arrival = (self._now() + timedelta(minutes=minutes)).strftime("%I:%M %p").lstrip("0")
        self.nav_readout.set(f"{minutes} min", f"{distance} · arrive {arrival}")
        self._render_place()
        return True

    def _end(self) -> None:
        self._stop_nav_timer()
        self.state = self.state.end()
        self.fixture_map.set_route(False)
        self._render_place()
        self.corner.set_visible(True)
        self._show_bar()

    def _toast(self, text: str) -> None:
        Toast.show(self.map_host, text)

    # ── Search and the two LumaUI placement surfaces ────────────────────

    def _search_query(self, text: str) -> None:
        self.state = self.state.search(text)
        if self.selected is not None:
            self.selected = None
            self.state = self.state.close_place()
            self._render_place()
        if self.fixture is not None:
            # v71 keeps every pin and leaves the map where it is while you search.
            self.results = self.fixture.find(text)
            self._refresh_panel()
            return
        if not text.strip():
            self.search.cancel()
            self.results = ()
            self.markers.remove_all()
            self._refresh_panel()
        else:
            near = (self.viewport.get_latitude(), self.viewport.get_longitude(),
                    self.viewport.get_zoom_level())
            self.search.suggest(text, lambda outcome, query=text:
                                self._search_result(outcome) if query == self.state.query else None,
                                near=near)

    def _search_result(self, outcome: SearchOutcome) -> None:
        self.results = outcome.places
        self._refresh_panel()
        self._frame_results()
        if outcome.error:
            Toast.show(self.map_host, f"Search is unavailable: {outcome.error}", kind="error")

    def _frame_results(self) -> None:
        if self.fixture is not None:
            view = fixture_view_for_places(self.results, self.fixture_map.get_width() or 760,
                                           self.fixture_map.get_height() or 560)
            if view is not None:
                self.fixture_map.set_view(*view)
            return
        self.markers.remove_all()
        for place in self.results:
            if place.latitude is None or place.longitude is None:
                continue
            marker = Shumate.Marker.new()
            marker.set_location(place.latitude, place.longitude)
            button = Gtk.Button(child=icons.image("map-pin"))
            button.add_css_class("mp-result-pin")
            button.update_property([Gtk.AccessibleProperty.LABEL], [f"Show {place.name} on map"])
            button.connect("clicked", lambda _button, p=place: self._open_place(p))
            marker.set_child(button)
            self.markers.add_marker(marker)
        camera = camera_for_places(nearby_cluster(self.results), self.map.get_width() or 760,
                                   self.map.get_height() or 560)
        if camera is not None:
            self.viewport.set_location(camera[0], camera[1])
            self.viewport.set_zoom_level(camera[2])

    LAYER_CHOICES = (("map", "Map", "map"), ("transit", "Transit", "train-front"), ("sat", "Satellite", "satellite"))

    def _place_corner(self) -> None:
        """The map's style lives top right, as v71 places it: a mode switch on a computer, and on a phone a small
        stack (Map style, whose glyph is the current style, then Where am I) that grows the bar."""
        if self.corner is not None:
            self.map_overlay.remove_overlay(self.corner)
        self.corner = self._build_corner()
        self.corner.set_name("mp-corner")
        if self.phone:
            # CornerPill already owns its 16px inset. Add only the status
            # offset needed to put the stack eight pixels below the clock.
            self.corner.set_margin_end(0)
            self.corner.set_margin_top(max(0, self.status_inset - 8))
        else:
            self.corner.set_margin_end(0)
            self.corner.set_margin_top(0)
        self.corner.set_visible(not self.state.navigating)
        self.map_overlay.add_overlay(self.corner)

    def _build_corner(self) -> CornerPill:
        if self.phone:
            icon = next(glyph for key, _name, glyph in self.LAYER_CHOICES if key == self.state.layer)
            return CornerPill(actions=((icon, "Map style", self._toggle_layers if self.fixture is not None else self._show_style_menu),
                                       ("navigation", "Where am I", self._locate)), orientation="vertical", quiet=True)
        if self.fixture is not None:
            switch = ModeSwitch(self.LAYER_CHOICES, current=self.state.layer, on_change=self._set_layer,
                                label="Map style")
            return CornerPill(modes=switch)
        # Only the configured live tile providers can honestly be offered.
        return CornerPill(actions=(("layers", "Map style", self._show_style_menu),), labelled=True)

    def _set_layer(self, layer: str) -> None:
        self.state = self.state.set_layer(layer)
        self.fixture_map.set_layer(layer)
        if self.phone:
            self._place_corner()

    def _toggle_layers(self) -> None:
        """Map style on a phone grows the bar into three tiles: Map, Transit, Satellite."""
        if self.selected is not None or self.state.navigating:
            return
        if self.action_center.grown == "layers":
            self.action_center.fold_panel()
            return
        self.state = self.state.with_panel("layers")
        self._grow_layers()

    def _grow_layers(self) -> None:
        def tiles() -> Gtk.Widget:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.append(PanelHeading("Map style"))
            box.append(BarTiles([BarTile(icon, label, lambda k=key: self._set_layer(k), on=key == self.state.layer,
                                         name=label) for key, label, icon in self.LAYER_CHOICES], columns=3))
            return box
        self.action_center.grow("layers", tiles, on_fold=self._panel_folded)

    def _panel_folded(self) -> None:
        self.state = self.state.with_panel("")

    def _show_style_menu(self) -> None:
        from luma_appkit import Menu
        commands = tuple(Command(f"maps.source.{provider.id}", provider.name,
                                 lambda source=provider: self._apply_tile_provider(source), "map")
                         for provider in self.providers.tiles if provider.usable)
        menu = Menu(CommandRegistry((CommandGroup("", commands),)))
        menu.set_parent(self.corner)
        menu.connect("closed", lambda popover: GLib.idle_add(popover.unparent))
        menu.popup()

    def _show_bar(self) -> None:
        if self.state.navigating:
            route = self.fixture.route[self.state.mode] if self.fixture else None
            minutes = 8 if self.capture_navigation else route["minutes"] if route else 0
            distance = "2.0 mi" if self.capture_navigation else route["distance"] if route else ""
            self.nav_readout = BarReadout(f"{minutes} min", f"{distance} · arrive {self._arrival(minutes)}",
                                          fill=self.phone)
            self.action_center.show_bar((self.nav_readout,
                BarAction("volume-2", tooltip="Voice", on_activate=lambda: self._toast("Voice guidance is on")),
                # v71: End is red in a computer's bar and plain ink in the phone's.
                BarAction("", "End", on_activate=self._end, danger=not self.phone, primary=not self.phone)),
                fill=self.phone)
            return
        if self.phone:
            self._phone_bar()
            return
        if not hasattr(self, "search_field"):
            self.search_field = BarSearch("Search places and addresses", text=self.state.query, wide=True,
                                          on_change=self._search_query)
        # The kit owns both the floating bar and neutral dimensional controls.
        # Keep phone actions in their existing adaptive form below.
        self.map_controls = {}
        controls = []
        for name, icon, label, callback in (
                ("zoom-in", "plus", "Zoom in", lambda: self._zoom(1)),
                ("zoom-out", "minus", "Zoom out", lambda: self._zoom(-1)),
                ("locate", "navigation", "Where am I", self._locate)):
            button = IconOnlyButton(icon, label, raised=True, on_click=callback)
            button.set_name("mp-" + name)
            self.map_controls[name] = button
            controls.append(BarWidget(button))
        self.action_center.show_bar((self.search_field, *controls))

    # ── The phone's bar: one bar, grown for whatever is open ─────────────

    PEOPLE = (Person("Priya Raman", username="priya", hue=330), Person("Nora Feld", username="nora", hue=45),
              Person("Sam Kaur", username="samk", hue=200), Person("Theo Marsh", username="theo", hue=350),
              Person("Dad", phone="+1 (510) 555-0142", hue=20))

    def _phone_bar(self) -> None:
        center, place = self.action_center, self.selected
        self.search_box = None
        if place is None:
            # "Search Maps": a kept field; focusing it grows the bar into your location, favorites, guides, recents.
            self.phone_search = BarSearch("Search Maps", label="Search places and addresses", keep=True,
                                          text=self.state.query, on_change=self._search_query)
            center.show_bar([self.phone_search], fill=True)
            GLib.idle_add(self._hook_search_focus)
            if self.state.panel == "layers":
                self._grow_layers()
            elif self.state.panel == "search":
                self._grow_search()
            return
        if self.state.directions and self.fixture is not None:
            route = self.fixture.route[self.state.mode]
            center.show_bar([BarReadout(f"{route['minutes']} min",
                                       f"{route['distance']} · arrive {self._arrival(route['minutes'])}",
                                       note="Light traffic" if self.state.mode == "drive" else None),
                             BarAction("navigation", "Go", primary=True, keep_label=True, fill=True,
                                       on_activate=self._go)], fill=True)
            center.grow("route", self._route_panel)
            return
        center.show_bar([
            BarAction("navigation", f"{drive_minutes(place)} min" if self.fixture else "Directions", primary=True,
                      keep_label=True, fill=True, on_activate=self._show_route),
            BarAction("phone", tooltip="Call", on_activate=lambda: self._toast("Sample call to " + place.name if self.fixture is not None
                                                         else "Calling is unavailable for this place")),
            BarAction("star", tooltip="Favorite", active=self.store.is_saved(place), favourite=False,
                      on_activate=lambda: self._toggle_saved(place)),
            BarAction("share-2", tooltip="Share", active=self.state.panel == "share", on_activate=self._toggle_share),
            BarAction("x", tooltip="Close", on_activate=self._back_to_list)], fill=True)
        if self.state.panel == "share":
            self._grow_share()
        else:
            center.grow("place", self._place_panel)

    def _hook_search_focus(self) -> bool:
        entry = getattr(self.phone_search, "entry", None)
        if entry is None or getattr(entry, "mp_hooked", False):
            return False
        entry.mp_hooked = True
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda *_a: self._grow_search())
        entry.add_controller(focus)
        return False

    def _grow_search(self) -> None:
        if self.action_center.grown == "search" or self._no_regrow or self.selected is not None:
            return
        self.state = self.state.with_panel("search")
        field = PanelField("search", "Search Maps", text=self.state.query, on_change=self._search_query)
        self.action_center.grow("search", self._search_panel, entry=field, on_fold=self._search_folded)

    def _search_folded(self) -> None:
        """✕ closes the search; the field in the row must not take the focus back and grow it again."""
        self.state = self.state.with_panel("")
        self.search_box = None
        self._no_regrow = True
        GLib.timeout_add(400, lambda: setattr(self, "_no_regrow", False) or False)

    def _toggle_share(self) -> None:
        if self.action_center.grown == "share":
            self.action_center.fold_panel()
            return
        self.state = self.state.with_panel("share")
        self._grow_share()

    def _grow_share(self) -> None:
        place = self.selected
        self.action_center.grow("share", lambda: SharePanel(
            people=self.PEOPLE, heading=f"Send {place.name} to", on_choice=self._shared), on_fold=self._share_folded)

    def _share_folded(self) -> None:
        """Back to the place's card once Share closes (unless something else has taken the bar)."""
        self.state = self.state.with_panel("") if self.state.panel == "share" else self.state

        def card() -> bool:
            if (self.phone and self.selected is not None and not self.state.directions
                    and not self.state.navigating and self.action_center.grown is None):
                self.action_center.grow("place", self._place_panel)
            return False
        GLib.idle_add(card)

    def _shared(self, choice: str, value: object) -> str | None:
        if choice == "send-to":
            return f"Sent to {value.name.split()[0]}"
        if choice == "copy-link":
            self.get_clipboard().set(self._place_url(self.selected))
            return "Link copied"
        return {"messages": "Sent with Messages", "mail": "Sent with Email", "nearby": "Sent with Nearby"}.get(choice)

    def _zoom(self, delta: int) -> None:
        if self.fixture is not None:
            view = self.fixture_map.view
            self.fixture_map.set_view(view["x"], view["y"], max(.4, min(3, view["zoom"] *
                                      (1.4 if delta > 0 else 1 / 1.4))))
            return
        self.viewport.set_zoom_level(self.viewport.get_zoom_level() + delta)

    def _locate(self, automatic=False) -> None:
        if self.fixture is not None:
            self.fixture_map.set_view(760, 900, 1.3)
            return
        if self._location_request is not None and self._location_request.active:
            return
        self._location_center = True
        self._location_automatic = automatic
        self._location_context = (self.selected, self.state.query, self.state.directions)
        self._location_status = "Finding your location…"
        self._refresh_panel()
        self._location_request = LocationRequest(self, self._located, self._location_failed)
        self._location_request.start()

    def _first_mapped(self, _window):
        if self._first_map:
            return
        self._first_map = True
        # Schedule after the first frame; network/style preparation and the
        # permission UI must not block constructing the window.
        def after_frame(_widget, _clock):
            GLib.idle_add(self._start_mapped)
            return False
        self.add_tick_callback(after_frame)

    def _start_mapped(self):
        if self._closing or not self.get_mapped():
            return False
        self._apply_tile_provider(self.tile_provider)
        if not self._manual_view and self.selected is None and not self.state.query and not self.state.directions:
            self._locate(automatic=True)
        return False

    def _view_changed(self, *_args):
        if not self._setting_location:
            self._manual_view = True
            self._location_center = False

    def _located(self, fix):
        self._location_fix = fix
        self._location_status = "Your current location"
        if self._location_center and self._location_context == (self.selected, self.state.query, self.state.directions):
            self._manual_view = False
            self._setting_location = True
            try:
                self.viewport.set_location(fix.latitude, fix.longitude)
                self.viewport.set_zoom_level(fix.zoom)
            finally:
                self._setting_location = False
        self._location_center = False
        self._draw_location()
        self._refresh_panel()

    def _draw_location(self):
        if self._location_fix is None:
            return
        old = getattr(self, "location_markers", None)
        if old is not None and old.get_parent() is not None:
            self.simple_map.remove_overlay_layer(old)
        self.location_markers = Shumate.MarkerLayer.new(self.viewport)
        self.simple_map.add_overlay_layer(self.location_markers)
        marker = Shumate.Marker()
        marker.set_location(self._location_fix.latitude, self._location_fix.longitude)
        marker.set_child(icons.image("navigation", pixel_size=24))
        self.location_markers.add_marker(marker)

    def _location_failed(self, message):
        self._location_center = False
        self._location_status = "Location not shared"
        self._refresh_panel()
        if not self._location_automatic:
            self._toast(message)

    def _close_location(self, *_args):
        self._closing = True
        if self._location_request is not None:
            self._location_request.close()
        if self._manual_view:
            try:
                self.store.remember_viewport(self.viewport.get_latitude(), self.viewport.get_longitude(),
                                             self.viewport.get_zoom_level())
            except (OSError, ValueError, TypeError):
                # Never replace corrupt or unwritable user data on exit.
                pass
        return False

    def _clear_recents(self) -> None:
        self.store.clear_recents()
        self._refresh_panel()

    def _clear_saved(self) -> None:
        self.store.clear_saved()
        self._refresh_panel()

    def _show_disclosure(self) -> None:
        provider = self.tile_provider
        lines = [
            f"Map tiles: {'read from this machine' if provider.local and provider.usable else provider.host or 'not configured'}.",
            f"Search-as-you-type: {self.providers.search.autocomplete_url.split('//')[-1].split('/')[0] or 'not configured'} — sent only after you stop typing for {self.providers.search.autocomplete_debounce_ms} ms.",
            f"Exact lookups: {self.providers.search.geocode_url.split('//')[-1].split('/')[0] or 'not configured'} — at most one a second.",
            "Location: requested through the system permission dialog; not stored or shared by Maps.",
            "Saved places, recents and a manually chosen map view: kept on this machine and never sent anywhere.",
            "No analytics, no telemetry, no crash reports.",
        ]
        self._report("What leaves this machine", "\n".join(lines))

    def _report(self, heading: str, body: str) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("close", "Close")
        dialog.present(self)

    def _show_about(self) -> None:
        about = Adw.AboutDialog(
            application_name="Maps", application_icon=ICON_NAME,
            developer_name="Project Luma",
            comments="Map data © OpenStreetMap contributors, ODbL.",
            website=OSM_COPYRIGHT,
        )
        about.present(self)


class MapsApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        install_appkit()
        install_lumaui()
        icon_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data/icons")
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(icon_dir)
        _install_maps_style()

    def do_activate(self) -> None:
        (self.props.active_window or MapsWindow(self)).present()


def _install_maps_style() -> None:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    add_style_sheet(
        os.environ.get("LUMA_MAPS_STYLE_PATH", os.path.join(here, "data/maps.css"))
    )


def main() -> int:
    return MapsApplication().run([])


if __name__ == "__main__":
    raise SystemExit(main())
