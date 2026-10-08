# SPDX-License-Identifier: Apache-2.0
"""Weather: kit frame, sidebar, place search, sky cards and app forecast data.

Weather-only graphics live in weather_charts and use kit color roles.
Fixture mode branches before opening any store, settings or location database.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Adw, Gdk, GLib, Gtk

from luma_appkit import (
    ActionCenter, BarAction, BarTile, BarTiles, PanelChoices, PanelRow, SPACER, panel_list,
    AppWindow, Command, CommandGroup, CommandRegistry, ContentLitCard, EmptyState,
    SidebarRow, NavigationSidebar, PlaceSearch, ScrollView, attach_context_menu,
    SidebarFoot, SidebarToggle, Toast, ToastHost, Island, TypeLabel, add_style_sheet,
    icons, install_appkit, install_lumaui, lumaui_tokens, apply_type,
)
from . import weather_phone
from .weather_backend import PlaceStore, cached_forecast, fetch_forecast, local_now, needs_refresh, new_place
from .weather_charts import WeatherSky, WeatherHours, WeatherPrecipitationSpace, ForecastDay, ForecastGraphic
from .weather_data import plans_for, snapshot_from_forecast, weather_icon
from .weather_layout import ForecastLayout
from .weather_places import WeatherPlaces
from .weather_fixture import fixture_from_environment
from .weather_model import Place, UNITS_IMPERIAL, UNITS_METRIC, measurement_from_locale, resolve_units
from .weather_model import parse_time
from .weather_observation import MAX_AGE_SECONDS, fetch_alerts, fetch_observation

ICON_NAME = 'org.projectluma.Weather'
# The Prairie preview launcher overrides APP_ID before main(); the application
# and window must both consume it while the production icon name stays stable.
APP_ID = ICON_NAME
from .weather_parts import day_rows, glyph, hour_strip, label  # noqa: E402,F401  (kept importable from here)
WIDGET_NAMES = ('wx-sidebar', 'wx-foot', 'wx-search', 'wx-island', 'wx-sky', 'wx-hero', 'wx-hours', 'wx-grid', 'wx-days')


class WeatherWindow(AppWindow):
    def __init__(self, application):
        self.fixture = fixture_from_environment()
        self.store = None if self.fixture else PlaceStore()
        self._setup_save_failed = False
        if self.store is not None:
            from .setup_location import read_setup_location
            try:
                self.store.seed_setup_place(read_setup_location())
            except OSError:
                self._setup_save_failed = True
        self.store_lock = threading.RLock()
        self.places = self.store.list() if self.store else ()
        self._removed = None
        self._newly_added = ''
        self.snapshots = {}
        self.selected = self.fixture.selected if self.fixture else ''
        self.generation = 0
        self._refresh_serial = {}
        self._observation_lock = threading.Lock()
        self._observation_checked = {}
        self._observation_cache = {}
        self._alert_checked = {}
        self._alert_cache = {}
        self.phone = False
        self.phone_state = weather_phone.PhoneState()
        if self.fixture:
            # Conformance states: which phone view, and the radar's quarter hour.
            self.phone_state.view = os.environ.get('LUMA_WEATHER_VIEW', 'today')
            self.phone_state.radar_step = int(os.environ.get('LUMA_WEATHER_RADAR_STEP', '0'))
        self._radar = None
        self._radar_source = 0
        self.closed = False
        self._rendering = False
        self._fixture_started = False
        self._refresh_source = 0
        self.search_provider = self.fixture or WeatherPlaces()
        self.units = UNITS_IMPERIAL if self.fixture else measurement_from_locale(os.environ.get('LC_MEASUREMENT', os.environ.get('LANG', '')))
        if self.store is not None:
            self.units = resolve_units(self.store.unit_setting(), self.units)
        commands = CommandRegistry((CommandGroup('', (
            Command('weather.find', 'Add a city', self._focus_search, 'search', shortcut=('Ctrl', 'F')),
            Command('weather.refresh', 'Refresh', self._reload, 'refresh-cw'),
            Command('weather.remove', 'Remove city', self._remove_selected, 'trash-2'),
            Command('weather.undo', 'Undo removal', self.undo_removal, 'rotate-ccw', shortcut=('Ctrl', 'Z')),
            Command('weather.sidebar', 'Show or hide sidebar', lambda:self.toggle.toggle(), 'panel-left'),
            Command('weather.units', 'Temperature units', lambda: None, 'thermometer', children=(
                Command('weather.units.celsius', 'Celsius (°C)', lambda: self._set_unit('C'), checked=lambda: self._unit_key() == 'C'),
                Command('weather.units.fahrenheit', 'Fahrenheit (°F)', lambda: self._set_unit('F'), checked=lambda: self._unit_key() == 'F'))),
            Command('weather.quit', 'Quit Weather', self.close, "log-out", shortcut=('Ctrl', 'Q')),
        )),))
        super().__init__(application=application, app_id=APP_ID, title='Weather', icon_name=ICON_NAME,
                         commands=commands, default_width=1180, default_height=740,
                         minimum_width=360, minimum_height=420)
        body = Gtk.Box(spacing=0, hexpand=True, vexpand=True)
        self.sidebar = NavigationSidebar(variant='resources')
        self.sidebar.set_name('wx-sidebar')
        self.sidebar.set_size_request(250 - lumaui_tokens.SIDEBAR["gutter"], -1)
        self.sidebar.list.connect('row-activated', self._activate_place)
        self.sidebar.list.connect('row-selected', self._select_place)
        self.foot = PlaceSearch(self.search_provider, on_pick=self._add_place, placeholder='Add a city or ZIP',
                                exclude=None if self.fixture else lambda p: any(
                                    p.latitude == saved.latitude and p.longitude == saved.longitude
                                    for saved in self.places))
        self.foot.set_name('wx-place-search')
        self.foot.entry.set_name('wx-search')
        self.sidebar_foot = SidebarFoot(search=self.foot)
        self.sidebar_foot.set_name('wx-foot')
        self.sidebar.append_footer(self.sidebar_foot)
        body.append(self.sidebar)
        self.toggle = SidebarToggle(self.sidebar, drawer_below=701, phone_enabled=False)
        self.toggle.set_name('wx-sidebar-toggle')
        self.toggle.set_control_visible(False)
        self.set_leading(self.toggle)

        self.sky = WeatherSky()
        self.page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.page.set_margin_top(36)
        self.page.set_margin_bottom(40)
        self.page.set_margin_start(28)
        self.page.set_margin_end(28)
        clamp = Adw.Clamp(maximum_size=860, tightening_threshold=860, child=self.page)
        self.scroll = ScrollView(clamp)
        self.scroll.set_name('wx-body')
        overlay = Gtk.Overlay(child=self.sky)
        overlay.add_overlay(self.scroll)
        self.empty = EmptyState('No cities', 'Add a city to see its forecast.',
                                'lumaui-cloud-symbolic',
                                primary=('Add a city', lambda: self._open_add_card()))
        self.detail_stack = Gtk.Stack(hexpand=True, vexpand=True)
        self.detail_stack.add_named(self.empty, 'empty')
        self.detail_stack.add_named(overlay, 'forecast')
        self.island = Island()
        self.island.set_name('wx-island')
        self.island.set_hexpand(True)
        self.island.append(self.detail_stack)
        self.host = ToastHost(self.island)
        body.append(self.host)
        self.set_body(body)
        # The phone's bar (v71 #wx-phbar): how you look, Alerts and Places. Hidden on a computer.
        self.center = ActionCenter()
        self.center.set_name('wx-phone-bar')
        self.center.attach(self.host)
        # v71 `bleed` / `statusInk`: on a phone the sky runs to the bezel under the clock;
        # the page adds 12px below the reserved44px status region (56px total).
        self.set_phone_bleed(True)
        self.forecast_layout = ForecastLayout()
        self.forecast_layout.set_name('wx-grid')
        self.forecast_layout.set_margin_top(12)
        self._render()
        if self._setup_save_failed:
            Toast.show(self.host, 'Your setup city could not be saved. Add a city to try again.', kind='warning')
        # libadwaita applies one breakpoint at a time (the last that matches), so each
        # carries every setter of its range. v71's tiers: regular > 900, compact
        # 560-900 (`@container win (max-width: 900px)`: one forecast column, a 210
        # sidebar; 700: the sidebar folds behind ☰), phone < 560 (wxBodyPhone).
        # Below 1000 the 1.1:1 forecast columns give the two-card metrics grid less
        # than its 310 px minimum (293 px at 952), so GTK stacks from 1000, not 900.
        stacked = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 1000px'))
        stacked.add_setter(self.forecast_layout, 'stacked', True)
        self.add_breakpoint(stacked)
        compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 900px'))
        compact.add_setter(self.sidebar, 'width-request', 210 - lumaui_tokens.SIDEBAR['gutter'])
        compact.add_setter(self.forecast_layout, 'stacked', True)
        self.add_breakpoint(compact)
        # 720: ☰ appears (v71 `.wxwin .tbar .navbtn`), the sidebar still open; 700: it folds behind ☰.
        # TODO(v71-kit SidebarToggle as ☰ before the identity): K-NAV "AppWindow / SidebarToggle integration".
        toggled = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 720px'))
        toggled.add_setter(self.sidebar, 'width-request', 210 - lumaui_tokens.SIDEBAR['gutter'])
        toggled.add_setter(self.toggle, 'visible', True)
        toggled.add_setter(self.forecast_layout, 'stacked', True)
        self.add_breakpoint(toggled)
        folded = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 700px'))
        folded.add_setter(self.sidebar, 'width-request', 242)
        folded.add_setter(self.toggle, 'visible', True)
        folded.add_setter(self.forecast_layout, 'stacked', True)
        self.add_breakpoint(folded)
        # A phone: no sidebar and no ☰ (Places is in the bar); the page insets itself
        # under the clock (v71 .wxpad2: 56 16, 12 apart; the top is set in _set_phone, with the
        # status inset). The room above the bar is the kit's one safe area (bar height + 20),
        # so v71's 150 bottom padding is not repeated.
        phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 559px'))
        phone.add_setter(self.toggle, 'visible', False)
        # Set directly: the hidden ☰ never maps, so its own width watch can't fold the column.
        phone.add_setter(self.sidebar, 'visible', False)
        phone.add_setter(self.page, 'margin-start', 16)
        phone.add_setter(self.page, 'margin-end', 16)
        phone.add_setter(self.page, 'spacing', 12)
        phone.connect('apply', lambda _b: self._set_phone(True))
        phone.connect('unapply', lambda _b: self._set_phone(False))
        self.add_breakpoint(phone)
        self.connect('close-request', self._close)
        self._reload()

        if not self.fixture:
            self._refresh_source = GLib.timeout_add_seconds(60, self._refresh_minute)

    def _refresh_minute(self):
        if self.closed or self.fixture:
            return False
        # Reuse the cache expiry policy and generation guard. A failed fetch
        # keeps the displayed forecast instead of clearing it via _reload().
        for place in tuple(self.places):
            self._queue_refresh(self.generation, place)
        return True

    def _queue_refresh(self, generation, place):
        serial = self._refresh_serial.get(place.uid, 0) + 1
        self._refresh_serial[place.uid] = serial
        threading.Thread(target=self._refresh_place, args=(generation, place, serial),
                         daemon=True, name='weather-forecast').start()

    def _close(self, *_):
        self.closed = True
        self.generation += 1
        if self._refresh_source:
            GLib.source_remove(self._refresh_source)
            self._refresh_source = 0
        self._stop_radar()
        self.foot.close_list()
        if hasattr(self, 'phone_search'): self.phone_search.close_list()
        return False

    def _focus_search(self):
        if self.phone:
            # A phone has no sidebar: its places are the bar's Places.
            self._open_places()
            return
        if self.get_width() <= lumaui_tokens.PHONE_MAX_WIDTH and not self.sidebar.get_mapped():
            self.toggle.toggle()
        self.foot.entry.grab_focus()

    def _open_add_card(self):
        # v70's shared Places search is the add surface on both widths.
        self._focus_search()

    def _rebuild_rows_keeping_selection(self):
        self.generation += 1
        with self.store_lock:
            if self.fixture:
                self.places = self.fixture.load()
            else:
                self.store.reload()
                self.places = self.store.list()
        if self.selected not in {p.uid for p in self.places}:
            self.selected = self.places[0].uid if self.places else ''
        self._render_sidebar()
        self._render()

    def _fetch_done(self, place, forecast):
        snapshot = snapshot_from_forecast(forecast, self.units) if forecast else None
        return self._refreshed(self.generation, place.uid, snapshot)

    def _remove_selected(self):
        place = next((p for p in self.places if p.uid == self.selected), None)
        if place is not None:
            self._remove(place)

    def _remove(self, place):
        if self.fixture:
            index = next((i for i, p in enumerate(self.places) if p.uid == place.uid), -1)
            removed = place if index >= 0 else None
            sample = self.fixture.samples.pop(place.uid, None)
        else:
            try:
                with self.store_lock:
                    index, removed = self.store.remove(place.uid)
            except (OSError, ValueError):
                self._toast('The city was not removed.', kind='error')
                return
            sample = None
        if removed is None:
            return
        self._removed = (index, removed, sample)
        self._rebuild_rows_keeping_selection()
        where = self if self.get_width() <= lumaui_tokens.PHONE_MAX_WIDTH else self.host
        Toast.show(where, f'Removed {removed.name}', kind='place', undo=self.undo_removal)

    def undo_removal(self):
        if self._removed is None:
            return
        index, place, sample = self._removed
        if self.fixture:
            items = list(self.fixture.samples.items())
            if place.uid not in self.fixture.samples:
                items.insert(index, (place.uid, sample))
                self.fixture.samples = dict(items)
        else:
            try:
                with self.store_lock:
                    self.store.reload()
                    if self.store.index_of(place.uid) < 0:
                        self.store.insert(index, place)
            except (OSError, ValueError):
                self._toast('The city was not restored.', kind='error')
                return
        self._removed = None
        self.selected = place.uid
        self._rebuild_rows_keeping_selection()
        if not self.fixture:
            self._reload()

    def _toast(self, message, *, kind):
        where = self if self.get_width() <= lumaui_tokens.PHONE_MAX_WIDTH else self.host
        return Toast.show(where, message, kind=kind)

    def _reload(self):
        self.generation += 1
        generation = self.generation
        def read():
            try:
                if self.fixture:
                    places = self.fixture.load()
                    snapshots = {p.uid: self.fixture.snapshot(p) for p in places}
                else:
                    with self.store_lock:
                        self.store = self.store or PlaceStore()
                        self.store.reload()
                        places = self.store.list()
                    snapshots = {}
                    for p in places:
                        cached = cached_forecast(p)
                        if cached:
                            snapshots[p.uid] = snapshot_from_forecast(cached, self.units)
                GLib.idle_add(self._loaded, generation, places, snapshots, '')
            except (OSError, ValueError) as error:
                GLib.idle_add(self._loaded, generation, (), {}, str(error))
        threading.Thread(target=read, daemon=True, name='weather-load').start()

    def _loaded(self, generation, places, snapshots, error):
        if generation != self.generation or self.closed:
            return False
        self.places, self.snapshots = places, snapshots
        if self.selected not in {p.uid for p in places}:
            self.selected = places[0].uid if places else ''
        self._render_sidebar()
        self._render()
        if self._newly_added:
            GLib.idle_add(self._show_added)
        if error:
            self._toast('Weather could not be loaded.', kind='error')
        if self.fixture and not self._fixture_started:
            self._fixture_started = True
            panel = os.environ.get('LUMA_WEATHER_PANEL')
            if panel in ('view', 'alerts', 'places'):
                GLib.timeout_add(400, self._fixture_panel, panel)
            if os.environ.get('LUMA_WEATHER_DRAWER') == '1':
                GLib.timeout_add(300, self._fixture_drawer)
            if os.environ.get('LUMA_WEATHER_SELECT'):
                GLib.timeout_add(300, self._fixture_drawer)
                GLib.timeout_add(450, self._fixture_select)
            query = os.environ.get('LUMA_WEATHER_QUERY')
            if query:
                self.foot.set_text(query)
                GLib.timeout_add(300, self._fixture_search)
            if os.environ.get('LUMA_WEATHER_SCROLL') == 'bottom':
                GLib.timeout_add(300, self._scroll_bottom)
        elif not self.fixture:
            for place in places:
                self._queue_refresh(generation, place)
        return False

    def _fixture_drawer(self):
        if not self.closed and not self.phone and self.get_width() <= lumaui_tokens.PHONE_MAX_WIDTH and not self.sidebar.get_mapped():
            self.toggle.toggle()
        return False

    def _fixture_panel(self, panel):
        if not self.closed and self.phone:
            if panel == 'view':
                self.center.grow('view', self._view_panel(), anchor=self._bar_widget(self._view))
            elif panel == 'alerts':
                self._open_alerts()
            else:
                self._open_places()
        return False

    def _fixture_select(self):
        if self.closed:
            return False
        uid = os.environ.get('LUMA_WEATHER_SELECT')
        row = self.sidebar.list.get_first_child()
        while row is not None:
            if row.place.uid == uid:
                self.sidebar.list.select_row(row)
                self.sidebar.list.emit('row-activated', row)
                assert self.selected == uid, 'Weather fixture place selection failed'
                if self.get_width() <= lumaui_tokens.PHONE_MAX_WIDTH and not self.phone:
                    assert not self.toggle.shown, 'Weather fixture Places drawer did not close'
                return False
            row = row.get_next_sibling()
        raise AssertionError(f'Weather fixture place not found: {uid}')

    def _fixture_search(self):
        if not self.closed:
            self.foot.entry.grab_focus()
            self.foot.search_now()
            if os.environ.get('LUMA_WEATHER_PICK') == '1':
                GLib.timeout_add(50, self._fixture_pick)
        return False

    def _fixture_pick(self):
        if self.closed:
            return False
        if self.foot.searching:
            return True
        self.foot.pick()
        return False

    def _scroll_bottom(self):
        adjustment = self.scroll.get_vadjustment()
        adjustment.set_value(adjustment.get_upper())
        return False

    def _refresh_place(self, generation, place, serial=None):
        def publish(forecast):
            snapshot = snapshot_from_forecast(forecast, self.units)
            GLib.idle_add(self._refreshed, generation, place.uid, snapshot, serial)

        try:
            cached = cached_forecast(place)
            if needs_refresh(cached):
                try:
                    forecast = fetch_forecast(place, on_forecast=publish)
                except (OSError, ValueError):
                    if cached is None:
                        raise
                    # Keep actual cached data, but render its city clock and
                    # remaining forecast hours for the current minute.
                    forecast = cached
            else:
                forecast = cached
            # Real weather is usable before optional station observations,
            # alerts and calendar plans arrive. Those requests can take much
            # longer than the essential MET forecast on a mobile network.
            publish(forecast)
            observation = self._current_observation(place)
            alerts = self._current_alerts(place)
            snapshot = snapshot_from_forecast(forecast, self.units, observation=observation, alerts=alerts)
            if self.places and place.uid == self.places[0].uid:
                snapshot = self._with_plans(place, snapshot)
        except (OSError, ValueError):
            snapshot = None
        GLib.idle_add(self._refreshed, generation, place.uid, snapshot, serial)

    def _current_observation(self, place):
        """Poll NWS at most every ten minutes; never reuse an old reading."""
        with self._observation_lock:
            last = self._observation_checked.get(place.uid, 0)
            cached = self._observation_cache.get(place.uid)
            if time.monotonic() - last >= 10 * 60:
                self._observation_checked[place.uid] = time.monotonic()
                refresh = True
            else:
                refresh = False
        if refresh:
            try:
                cached = fetch_observation(place)
            except (OSError, ValueError, KeyError, TypeError):
                cached = None
            with self._observation_lock:
                self._observation_cache[place.uid] = cached
        observed_at = parse_time(cached.observed_at) if cached else None
        if observed_at is None or not 0 <= (datetime.now(timezone.utc) - observed_at).total_seconds() <= MAX_AGE_SECONDS:
            return None
        return cached

    def _current_alerts(self, place):
        """NWS warnings, polled at most every ten minutes; outside its coverage there are none."""
        with self._observation_lock:
            if time.monotonic() - self._alert_checked.get(place.uid, -1e9) < 10 * 60:
                return self._alert_cache.get(place.uid, ())
            self._alert_checked[place.uid] = time.monotonic()
        try:
            alerts = fetch_alerts(place, local_now(place))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            alerts = ()
        with self._observation_lock:
            self._alert_cache[place.uid] = alerts
        return alerts

    @staticmethod
    def _with_plans(place, snapshot):
        """Your day: the rest of today's calendar, read (never written) from Calendar's own backend.

        Only for your first place, the one the phone page is built around.
        """
        from dataclasses import replace
        moment = local_now(place)
        try:
            from . import calendar_backend
            start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
            events = calendar_backend.list_events(start, start.replace(hour=23, minute=59, second=59),
                                                  timeout=getattr(calendar_backend, 'BACKGROUND_CONNECT_TIMEOUT_SECONDS', 4))
        except Exception:  # noqa: BLE001 - no calendar (no EDS, no sources) means no plans, not no weather
            return snapshot
        return replace(snapshot, plans=plans_for(snapshot, events, moment))

    def _refreshed(self, generation, uid, snapshot, serial=None):
        if self.closed or generation != self.generation:
            return False
        if serial is not None and serial != self._refresh_serial.get(uid):
            return False
        if snapshot is not None:
            self.snapshots[uid] = snapshot
            self._render_sidebar()
            if self.selected == uid:
                self._render()
        elif uid not in self.snapshots and uid == self.selected:
            self._render(message='Forecast unavailable. Try again when connected.')
        return False

    def _add_place(self, result):
        def add():
            try:
                if self.fixture:
                    place = self.fixture.add(result)
                else:
                    with self.store_lock:
                        self.store = self.store or PlaceStore()
                        place = self.store.add(new_place(result))
                error = ''
            except (OSError, ValueError) as failure:
                place, error = None, str(failure)
            GLib.idle_add(self._added, place, error)
        threading.Thread(target=add, daemon=True, name='weather-add-place').start()

    def _added(self, place, error):
        if self.closed:
            return False
        if error:
            self._toast('The city was not saved.', kind='error')
        else:
            self.selected = place.uid
            self._newly_added = place.uid
            self._reload()
            self._toast(f'Added {place.name}, {place.region}' if place.region else f'Added {place.name}', kind='place')
        return False

    def _show_added(self):
        uid, self._newly_added = self._newly_added, ''
        if self.closed or uid != self.selected:
            return False
        self.scroll.get_vadjustment().set_value(0)
        if self.get_width() <= lumaui_tokens.PHONE_MAX_WIDTH and self.toggle.shown:
            self.toggle.toggle()
        return False

    def _render_sidebar(self):
        self._rendering = True
        self.sidebar.clear()
        for place in self.places:
            snap = self.snapshots.get(place.uid)
            subtitle = f'{snap.location} · {snap.condition}' if snap else 'Loading weather…'
            trail = None
            if snap:
                trail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
                temperature = label(snap.temperature, 'weather-place-temperature')
                temperature.set_halign(Gtk.Align.END)
                trail.append(temperature)
                high_low = label(f'{snap.high} / {snap.low}', 'weather-place-range')
                high_low.set_halign(Gtk.Align.END)
                trail.append(high_low)
            face = Gtk.CenterBox(width_request=30, height_request=30)
            face.set_center_widget(glyph(snap.glyph) if snap else icons.image('cloud'))
            row = SidebarRow(place.name, subtitle=subtitle, lead=face, trail=trail,
                             title_icon='navigation' if snap and snap.my_location else None)
            row.place = place
            row.set_name(f'wx-place-{place.uid}')
            self._install_city_menu(row, place)
            # TODO(kit-request weather-05-content-lit-card-and-roles.md): readout type metrics.
            self.sidebar.append_row(row)
            if place.uid == self.selected:
                self.sidebar.list.select_row(row)
        self._rendering = False

    def _install_city_menu(self, row, place):
        registry = CommandRegistry((CommandGroup('', (
            Command('city.remove', 'Remove city', lambda: self._remove(place),
                    'trash-2', destructive=True),
        )),))
        attach_context_menu(row, registry, variant='desktop')

    def _select_place(self, _list, row):
        if self._rendering or row is None:
            return
        self.selected = row.place.uid
        self._render()
        self.scroll.get_vadjustment().set_value(0)

    def _activate_place(self, _list, row):
        self._select_place(_list, row)

    @staticmethod
    def _clear(box):
        while child := box.get_first_child():
            box.remove(child)

    def _render(self, message=None):
        self._clear(self.page)
        self._radar = None
        self.detail_stack.set_visible_child_name('forecast' if self.places else 'empty')
        self._render_bar()
        if not self.places:
            return
        snapshot = self.snapshots.get(self.selected)
        if snapshot is None:
            self.page.append(label(message or 'Loading weather…', center=True))
            return
        self.sky.set_sky(snapshot.sky)
        if self.phone:
            self._render_phone(snapshot)
            return
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        hero.set_name('wx-hero')
        hero.add_css_class('wx-hero')
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        heading.set_margin_top(8)
        heading.set_margin_bottom(26)
        hero.append(heading)
        location = Gtk.Box(spacing=5, halign=Gtk.Align.CENTER)
        location.add_css_class('wx-location')
        if snapshot.my_location:
            location.append(icons.image('navigation', pixel_size=11))
        location.append(label(snapshot.location.upper(), 'weather-location'))
        heading.append(location)
        city = label(snapshot.place.name, 'weather-city', center=True, name='wx-name')
        city.set_margin_top(2)
        heading.append(city)
        temperature = label(snapshot.temperature, 'weather-temperature', center=True, name='wx-temperature')
        temperature.set_margin_start(12)
        heading.append(temperature)
        condition = label(snapshot.condition, 'weather-condition', center=True,
                          name='wx-condition', wrap=True)
        condition.set_margin_top(6)
        heading.append(condition)
        high_low = [f'H {snapshot.high}' if snapshot.high else '', f'L {snapshot.low}' if snapshot.low else '']
        if any(high_low):
            high_low_label = label(' · '.join(value for value in high_low if value), 'weather-high-low', center=True)
            high_low_label.set_margin_top(2)
            heading.append(high_low_label)
        if snapshot.provenance:
            source = label(snapshot.provenance, 'weather-description', center=True, wrap=True)
            source.set_margin_top(8)
            heading.append(source)
        self.page.append(hero)
        hours = ContentLitCard(night=snapshot.sky == "night")
        hours.set_name('wx-hours')
        summary = label(snapshot.sentence, 'weather-summary', wrap=True)
        summary.add_css_class('wx-summary')
        summary.set_margin_bottom(10)
        hours.append(summary)
        hours.append(hour_strip(snapshot.hours))
        self.page.append(hours)
        grid = self.forecast_layout
        self._clear(grid)
        days = ContentLitCard(night=snapshot.sky == "night")
        days.set_name('wx-days')
        days.append(self._heading('calendar', 'Ten days'))
        day_rows(days, snapshot)
        days.set_hexpand(True)
        days.set_valign(Gtk.Align.FILL)
        small = Gtk.Grid(column_homogeneous=True, column_spacing=12, row_spacing=12,
                         hexpand=True, vexpand=True)
        for index, metric in enumerate(snapshot.metrics):
            card = ContentLitCard(night=snapshot.sky == "night")
            card.set_size_request(-1, 132)
            card.set_vexpand(True)
            card.set_name(f'wx-{metric.key}')
            card.append(self._heading(metric.icon, metric.title))
            if metric.key == 'wind':
                line = Gtk.Box(spacing=12)
                line.append(ForecastGraphic('wind', value=metric.position))
                text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
                text.append(label(metric.value, 'weather-metric'))
                text.append(label(metric.description, 'weather-wind-description', wrap=True))
                line.append(text)
                card.append(line)
            else:
                card.append(label(metric.value, 'weather-metric', wrap=True))
                if metric.key == 'sun':
                    arc = ForecastGraphic('sun', value=metric.position)
                    arc.set_margin_top(6)
                    card.append(arc)
                description = label(metric.description, 'weather-description', wrap=True)
                description.set_margin_top(4)
                card.append(description)
                if metric.maximum:
                    track = ForecastGraphic('index', value=metric.position, maximum=metric.maximum)
                    track.set_margin_top(8) # 12px above the 5px track; marker and outline overhang 4px
                    card.append(track)
            small.attach(card, index % 2, index // 2, 1, 1)
        grid.append(days)
        grid.append(small)
        self.page.append(grid)

    # ── the phone (v71 wxBodyPhone / wxBarPhone) ────────────────────────────

    def _set_phone(self, phone):
        if phone == self.phone:
            return
        self.phone = phone
        self.toggle.set_phone_enabled(False)
        self.page.set_margin_top(12 + self.status_inset if phone else 36)
        if not phone:
            self._stop_radar()
        # Crossing 560 redraws in the other shape (changelog: "redraw on crossing the phone width").
        self._render()
        self.scroll.get_vadjustment().set_value(0)

    def _render_phone(self, snapshot):
        state, view = self.phone_state, self.phone_state.view
        if view == 'hourly':
            parts = weather_phone.hourly(snapshot)
        elif view == 'days':
            parts = weather_phone.ten_days(snapshot)
        elif view == 'radar':
            parts = weather_phone.radar(snapshot, state, sample=bool(self.fixture),
                                        on_play=self._toggle_radar, on_step=self._radar_step)
            self._radar = parts[-1]
        else:
            parts = weather_phone.today(snapshot, lambda _button: self._open_places())
        for part in parts:
            self.page.append(part)

    def _render_bar(self):
        if not self.phone:
            self.center.hide_bar()
            return
        if not self.places:
            self._pin = BarAction('map-pin', 'Add a city', keep_label=True,
                                  panel=self._places_panel, key='places')
            self.center.show_bar([self._pin])
            return
        name, icon = weather_phone.view_name(self.phone_state.view)
        alerts = self.snapshots.get(self.selected).alerts if self.snapshots.get(self.selected) else ()
        # v71 #wx-phbar: how you look (it grows into the four views), then Alerts and Places, smaller,
        # because you set them once. Each grows the bar into its panel; a second tap folds it.
        self._view = BarAction(icon, name, dropdown=True, dropdown_size='view',
                               panel=self._view_panel, key='view')
        self._bell = BarAction('bell', tooltip='Alerts',
                               badge=len(alerts) or None, panel=self._alerts_panel, key='alerts')
        self._pin = BarAction('map-pin', tooltip='Places', panel=self._places_panel, key='places')
        self.center.show_bar([self._view, SPACER, self._bell, self._pin], fill=True)

    def _bar_widget(self, item):
        child = self.center.bar_row.get_first_child()
        while child is not None:
            if getattr(child, 'bar_item', None) is item:
                return child
            child = child.get_next_sibling()
        return self.center

    def _view_panel(self):
        return BarTiles([BarTile(icon, name, lambda key=key: self._show_view(key), on=key == self.phone_state.view)
                         for key, name, icon in weather_phone.VIEWS], columns=len(weather_phone.VIEWS))

    def _show_view(self, key):
        if key != 'radar':
            self._stop_radar()
        self.phone_state.view = key
        self._render()
        self.scroll.get_vadjustment().set_value(0)

    def _alerts_panel(self):
        snapshot = self.snapshots.get(self.selected)
        alerts = snapshot.alerts if snapshot else ()
        rows = ['Now'] + [weather_phone.alert_card(a, small=True) for a in alerts] if alerts else \
            [label('No warnings where you are.', 'weather-phone-subtitle', name='wx-no-alerts')]
        rows.append('Tell me about')
        # Kept for this session: Weather sends no notifications yet, so nothing is stored.
        for key, name, sub, _default in weather_phone.NOTIFY:
            rows.append(PanelRow(name, subtitle=sub, toggle=self.phone_state.notify[key],
                                 on_toggle=lambda on, key=key: self.phone_state.notify.__setitem__(key, on)))
        return panel_list(rows, label='Alerts')

    def _places_panel(self):
        if hasattr(self, 'phone_search'): self.phone_search.close_list()
        self.phone_search = PlaceSearch(self.search_provider, on_pick=self._add_place,
                                        placeholder='Add a city or ZIP',
                                        exclude=None if self.fixture else lambda p: any(
                                            p.latitude == saved.latitude and p.longitude == saved.longitude
                                            for saved in self.places))
        self.phone_search.entry.set_name('wx-phone-search')
        rows = [self.phone_search]
        for place in self.places:
            snapshot = self.snapshots.get(place.uid)
            if snapshot is None:
                rows.append(PanelRow(place.name, icon='cloud', subtitle='Loading weather…',
                                     current=place.uid == self.selected,
                                     on_activate=lambda uid=place.uid: self._pick_place(uid)))
                continue
            title, subtitle, value, _icon = weather_phone.place_row_parts(snapshot)
            rows.append(PanelRow(title, lead=glyph(snapshot.glyph, size=24), subtitle=subtitle, detail=value,
                                 current=place.uid == self.selected,
                                 on_activate=lambda uid=place.uid: self._pick_place(uid)))
        rows += ['Units', PanelChoices([('F', '°F'), ('C', '°C')], selected=self._unit_key(), on_choose=self._set_unit)]
        return panel_list(rows, label='Places')

    def _open_places(self, anchor=None):
        """The hero's place button (and Ctrl+F on a phone) grow the bar into Places, as its pin does."""
        if self.phone and self.center.grown != 'places':
            self.center.grow('places', self._places_panel(), anchor=self._bar_widget(self._pin))

    def _open_alerts(self, anchor=None):
        if self.phone and self.center.grown != 'alerts':
            self.center.grow('alerts', self._alerts_panel(), anchor=self._bar_widget(self._bell))

    def _pick_place(self, uid):
        self.selected = uid
        self._render_sidebar()
        self._render()
        self.scroll.get_vadjustment().set_value(0)

    def _unit_key(self):
        if self.fixture:
            return self.fixture.unit
        return 'F' if self.units == UNITS_IMPERIAL else 'C'

    def _set_unit(self, key):
        if key == self._unit_key():
            return
        if key not in ('C', 'F'): raise ValueError('Choose Celsius or Fahrenheit.')
        if self.fixture:
            self.fixture.unit = key
        else:
            try:
                with self.store_lock: self.store.set_units(UNITS_IMPERIAL if key == 'F' else UNITS_METRIC)
            except (OSError, ValueError) as error:
                Toast.show(self.host, str(error), kind='error')
                return
            self.units = UNITS_IMPERIAL if key == 'F' else UNITS_METRIC
        self._reload()

    def _toggle_radar(self):
        state = self.phone_state
        state.radar_playing = not state.radar_playing
        if state.radar_playing and not self._radar_source:
            self._radar_source = GLib.timeout_add(600, self._radar_tick)
        elif not state.radar_playing:
            self._stop_radar()
        self._render()

    def _radar_tick(self):
        if self.closed or not self.phone or self.phone_state.view != 'radar' or self._radar is None:
            self._radar_source = 0
            self.phone_state.radar_playing = False
            return False
        self._radar.radar_scale.set_value((self.phone_state.radar_step + 1) % (weather_phone.RADAR_STEPS + 1))
        return True

    def _radar_step(self, step):
        self.phone_state.radar_step = step
        if self._radar is not None:
            self._radar.radar_canvas.set_step(step)
            self._radar.radar_when.label.set_label(f'+{step * 15} min' if step else 'Now')

    def _stop_radar(self):
        self.phone_state.radar_playing = False
        if self._radar_source:
            GLib.source_remove(self._radar_source)
            self._radar_source = 0

    @staticmethod
    def _heading(icon, title):
        line = Gtk.Box(spacing=6)
        if 'weather_heading' in lumaui_tokens.TYPE_SCALE:
            apply_type(line, 'weather-heading')
        line.set_margin_bottom(10)
        line.append(icons.image(icon, pixel_size=13))
        line.append(label(title.upper(), 'weather-heading'))
        return line


class WeatherApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_startup(self):
        Adw.Application.do_startup(self)
        install_appkit()
        install_lumaui()
        if Gdk.Display.get_default():
            add_style_sheet(os.environ.get('LUMA_WEATHER_STYLE_PATH', '/usr/share/prairie-core/weather.css'))

    def do_activate(self):
        (self.props.active_window or WeatherWindow(self)).present()


def main():
    return WeatherApplication().run([])


if __name__ == '__main__':
    raise SystemExit(main())
