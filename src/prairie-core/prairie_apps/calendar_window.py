# SPDX-License-Identifier: Apache-2.0
"""Calendar's v70 composition: one island, shared details and action center."""
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import colorsys
import os
import threading
from pathlib import Path
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango
from luma_appkit import (ActionCenter, ActionEditor, AddRow, AppWindow, BarAction, BarEntry, BarPrompt,
    Command, CommandGroup, CommandRegistry, ContentLitHeader, CornerPill,
    DestructiveDialog, DetailsItem, DetailsPane, DetailsRow, Island, ModeSwitch,
    IconOnlyButton, PersonAvatar, PlaceCard, StackedButton, StackedButtons, TextButton, TextField, TitleIsland, BarTile, BarTiles, PanelRow, panel_list, MenuDrawer, LayerHost, Menu, Mark, ScrollView, Toast, ToastHost, icons)
from luma_appkit.action_bubble import FloatingMenu, MenuItem
from luma_appkit.bar_frame import BarFrame
from luma_appkit.action_center import SPACER, make_control, register_item
from .calendar_backend import CalendarUnavailable, list_sources, list_events, save_event, delete_event, local_tzid
from .calendar_data import CalendarAttendee, Event, EventDraft, sms_uri, participant_rows, contact_for_person
from .calendar_meeting import save_meeting, reply_to_event, CalendarDeliveryError
from .calendar_backup import ensure_backup
from . import calendar_backend as backend
from zoneinfo import ZoneInfo
from .calendar_fixture import CalendarFixture, ParsedEvent, duration_text, events_on, free_gaps, minute_text, parse_event, resolve_calendar
from .calendar_surface import CalendarSurface, CalendarCanvas, button, event_category, label, avatar
from luma_appkit import apply_type

APP_ID = "org.projectluma.Calendar"


class TodayKey:
    """The narrow bar's small Today key: today's date, the month in red over the day (v71 .caltd)."""

    def __init__(self, day, on_activate):
        self.day, self.on_activate = day, on_activate


def _today_key(item, _size):
    key = Gtk.Button(valign=Gtk.Align.CENTER, tooltip_text="Today")
    key.add_css_class("calendar-today-key")
    face = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    month = Gtk.Label(label=item.day.strftime("%b").upper())
    month.add_css_class("calendar-today-key-month")
    number = Gtk.Label(label=str(item.day.day))
    number.add_css_class("calendar-today-key-day")
    face.append(month)
    face.append(number)
    key.set_child(face)
    key.update_property([Gtk.AccessibleProperty.LABEL], ["Go to today"])
    key.connect("clicked", lambda _b: item.on_activate())
    return key


register_item(TodayKey, _today_key)
ALERT_CHOICES = ((None, "None"), (0, "At the start"), (10, "10 minutes before"),
                 (30, "30 minutes before"), (60, "1 hour before"), (1440, "1 day before"))


def _launch_uri(window, uri, failure_message):
    try:
        opened = Gio.AppInfo.launch_default_for_uri(uri, None)
    except GLib.Error:
        opened = False
    if not opened:
        window.toast(failure_message, kind="warning")


class CalendarWindow(AppWindow):
    def __init__(self, application):
        path = os.environ.get("LUMA_CALENDAR_FIXTURE")
        self.fixture = CalendarFixture(path) if path else None
        self.today = self.fixture.today if self.fixture else date.today()
        self.anchor = self.selected_day = self.today
        self.selected_event = None
        self.view = "month"
        if self.fixture:
            # Fixture states (the conform scenario): the view and month a capture opens on.
            self.view = os.environ.get("LUMA_CALENDAR_VIEW", "") or self.view
            if os.environ.get("LUMA_CALENDAR_ANCHOR"):
                self.anchor = date.fromisoformat(os.environ["LUMA_CALENDAR_ANCHOR"])
        self.narrow = False
        self.bar_panel = ""
        self._rebuilding = False
        self.sources = ()
        self.events = ()
        self.hidden_sources = set()
        self.loaded = False
        self._load_error = ""
        self._pane_requested = False
        self.generation = 0
        self.draft = None
        self.editing_event = None
        self.duplicate_origin = None
        self.draft_source = ""
        self._state_applied = False
        self._closing = False
        self.people = ()
        self.answers = {}
        self._rsvp_pending = set()
        self._saving = False
        super().__init__(application=application, app_id=application.get_application_id(), title="Calendar", icon_name=APP_ID,
                         default_width=1180, default_height=740, minimum_width=360, minimum_height=420,
                         commands=self._commands())
        self.set_phone_bleed(True)
        # Bare navigation keys belong to this window's focus-aware handler.
        # Application accelerators otherwise consume letters in event fields.
        # Keep the command model (including menu shortcut hints) unchanged.
        for name in ("today", "flow", "week", "month"):
            application.set_accels_for_action(f"app.calendar-{name}", [])
        body = Gtk.Box(hexpand=True, vexpand=True)
        self.host = ToastHost()
        self.host.set_hexpand(True)
        self.island = Island()
        self.island.set_name("cal-main")
        self.island.set_hexpand(True)
        self.host.set_child(self.island)
        self.overlay = Gtk.Overlay(hexpand=True, vexpand=True)
        self.island.append(self.overlay)
        self.surface = CalendarSurface(self)
        self.scroll = ScrollView(self.surface, fade_top_end=70, fade_top_always=True)
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        self.scroll.set_name("cal-body")
        self.overlay.set_child(Gtk.Box(hexpand=True, vexpand=True))
        # TODO(kit-request calendar-04-measured-kit-parity.md): Calendar ambient wash API.
        self.light = ContentLitHeader(tone="work")
        self.light.set_name("cal-light")
        self.overlay.add_overlay(self.light)
        self.overlay.add_overlay(self.scroll)
        self.overlay.set_measure_overlay(self.scroll, True)
        self.heading = TextButton("", on_click=self.date_menu, style="raised")
        self.heading.set_name("cal-jump")
        self.heading.add_css_class("calendar-heading")
        self.heading.set_halign(Gtk.Align.START)
        self.heading.set_valign(Gtk.Align.START)
        self.heading_island = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.heading_island.set_name("cal-head")
        self.heading_island.set_halign(Gtk.Align.START)
        self.heading_island.set_valign(Gtk.Align.START)
        self.heading_island.set_margin_start(16)
        self.heading_island.set_margin_top(16)
        self.heading_island.append(self.heading)
        self.overlay.add_overlay(self.heading_island)
        # Narrow (under 560): one title island, the month (tap: the month picker) and, past a
        # full-height hairline, the calendars as their dots (tap: the calendars). The kit's
        # TitleIsland shows only at phone tier; the desktop head and corner pill hide there.
        self.title_island = TitleIsland(lead=None, on_title=self.date_menu)
        self.head_dots = Gtk.Box(spacing=0, valign=Gtk.Align.CENTER)
        self.head_calendars = Gtk.Button(tooltip_text="Calendars")
        self.head_calendars.set_child(self.head_dots)
        self.head_calendars.update_property([Gtk.AccessibleProperty.LABEL], ["Calendars"])
        self.head_calendars.connect("clicked", lambda _b: self.calendars_menu())
        self.title_island.add_trailing(self.head_calendars)
        self.pane = DetailsPane("Today", closable=True, on_close=self._pane_closed)
        self.pane.set_name("cal-det-slot")
        self.pane.sheet.set_name("cal-det")
        # DetailsPane owns the pane separation; do not add a second gutter.
        self.pane.sheet.set_margin_start(0)
        self.pane.close_button.set_name("cal-day-close")
        body.append(self.host)
        body.append(self.pane)
        self.set_body(body)
        self.title_island.float_over(self.island)
        self.modes = ModeSwitch((("flow", "Flow", "list"), ("week", "Week", "columns-3"),
                                 ("month", "Month", "layout-grid")), current=self.view,
                                on_change=self.set_view, label="View")
        for key, control in self.modes.buttons.items():
            control.set_name("cal-mode-" + key)
            # The conform harness emits clicked rather than toggled.
            control.connect("clicked", lambda _b, value=key: self.set_view(value))
        self.calendar_control=button("Calendars",self.calendars_menu,name="cal-calendars")
        self.calendar_control.add_css_class("calendar-calendars")
        controls=Gtk.Box(spacing=8)
        self.calendar_marks=CalendarCanvas(height=18)
        self.calendar_marks.set_hexpand(False)
        self.calendar_marks.set_valign(Gtk.Align.CENTER)
        self.calendar_marks.set_size_request(0,18)
        controls.append(self.calendar_marks)
        self.calendars_label=label("Calendars",weight=500)
        self.calendars_label.set_max_width_chars(-1)
        controls.append(self.calendars_label)
        self.calendar_control.set_child(controls)
        self.corner = CornerPill(modes=self.modes,people=self.calendar_control,
                                 states=(("panel-right", "Day panel", self.pane.shown, self._panel_toggled),))
        self.corner.set_name("cal-cals")
        self.panel_control = self.corner.controls["states.0"]
        self.panel_control.set_name("cal-panel")
        self.overlay.add_overlay(self.corner)
        self.pane.connect("notify::shown", self._sync_panel)
        self.flow_at = None
        self.scroll.get_vadjustment().connect("value-changed", self._flow_scrolled)
        self.center = ActionCenter().attach(self.host)
        self.center.set_name("cal-bar")
        self.center.connect("state-changed", self._center_changed)
        self.show_quick_entry()
        self._breakpoints()
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("close-request", self._close)
        self.connect("unrealize", lambda *_: self.surface.clear())
        self.loading = label("Loading calendar…", "caption")
        self.loading.set_margin_top(76)
        self.surface.append(self.loading)
        self.reload()
        if not self.fixture:
            def read_people():
                from .eds_backend import load_contacts
                records=load_contacts()
                GLib.idle_add(self._people_loaded, records)
            threading.Thread(target=read_people,daemon=True,name="calendar-people-read").start()

    def _people_loaded(self, records):
        if not self._closing:
            self.people = records
            if not self._load_error:
                self.day_details() if self.selected_event is None else self.event_details()
        return False

    def _commands(self):
        return CommandRegistry((CommandGroup("", (
            Command("calendar.new", "New event", lambda: self.new_event(), "plus", shortcut=("Ctrl", "N")),
            Command("calendar.today", "Today", self.go_today, "calendar", shortcut=("", "T")),
            Command("calendar.flow", "Flow", lambda: self.set_view("flow"), "list", shortcut=("", "F")),
            Command("calendar.week", "Week", lambda: self.set_view("week"), "columns-3", shortcut=("", "W")),
            Command("calendar.month", "Month", lambda: self.set_view("month"), "layout-grid", shortcut=("", "M")),
        )), CommandGroup("", (Command("calendar.quit", "Quit Calendar", self.close, "log-out", shortcut=("Ctrl", "Q")),))))

    def _breakpoints(self):
        compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720px"))
        # v71 calNarrow: a body under 560 draws Calendar's own narrow shapes (the month of dots,
        # three days, the title island and the two-row bar). The kit's phone tier is the same 560.
        # Adw applies the last match, so the narrow breakpoint repeats the compact one's setter.
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 559px"))
        for point in (compact, narrow):
            point.add_setter(self.surface,"compact-flow",True)
        narrow.add_setter(self.pane.sheet, "margin-start", 0)
        narrow.add_setter(self.heading_island,"visible",False)
        narrow.add_setter(self.corner,"visible",False)
        narrow.connect("apply", lambda *_: self.add_css_class("calendar-phone"))
        narrow.connect("unapply", lambda *_: self.remove_css_class("calendar-phone"))
        narrow.connect("apply", lambda *_: self._set_narrow(True))
        narrow.connect("unapply", lambda *_: self._set_narrow(False))
        self.add_breakpoint(compact)
        self.add_breakpoint(narrow)

    def _set_narrow(self, narrow):
        """Crossing 560 redraws in the other shape (v71 ResizeObserver on the window body)."""
        if narrow == self.narrow:
            return
        self.narrow = narrow
        (self.add_css_class if narrow else self.remove_css_class)("calendar-narrow")
        # The conform harness and tests find the visible head by these names.
        # The head is "cal-head" and its month button "cal-jump" at both tiers (the conform pairs and actions).
        self.heading_island.set_name("cal-head-desktop" if narrow else "cal-head")
        self.heading.set_name("cal-jump-desktop" if narrow else "cal-jump")
        self.title_island.set_name("cal-head" if narrow else "lumaui-title-island")
        self.title_island.title_button.set_name("cal-jump" if narrow else "cal-title-island-title")
        self.calendar_control.set_name("cal-calendars-desktop" if narrow else "cal-calendars")
        self.head_calendars.set_name("cal-calendars" if narrow else "cal-head-calendars")
        self.title_island.set_opacity(1)
        self.title_island.set_can_target(True)
        if narrow and self.pane.shown:
            self.pane.close()
        if self.draft is None:
            self.show_quick_entry()
        if self.loaded:
            self.refresh()
            if narrow and self.selected_event is not None and self.draft is None:
                self.show_bar_details()

    def now_minute(self):
        if self.fixture:
            return self.fixture.now_minute
        now = datetime.now()
        return now.hour * 60 + now.minute

    def reload(self):
        self.generation += 1
        generation = self.generation
        # The shown month and the picked day (the pane may still show today after moving months).
        first = min(self.anchor.replace(day=1) - timedelta(days=7), self.selected_day - timedelta(days=1))
        start = datetime.combine(first, time()).astimezone()
        end = start + timedelta(days=70)
        if self.fixture:
            GLib.idle_add(self._loaded, generation, self.fixture.sources, self.fixture.read(start, end), "")
            return
        def work():
            try:
                sources = list_sources()
                events = list_events(start, end, sources)
                reason = ""
            except Exception as error:
                sources, events, reason = (), (), str(error)
            GLib.idle_add(self._loaded, generation, sources, events, reason)
        threading.Thread(target=work, daemon=True, name="calendar-read").start()

    def _loaded(self, generation, sources, events, reason):
        if generation != self.generation or self._closing:
            return False
        self._load_error = reason
        self.sources, self.events, self.loaded = sources, events, True
        self.refresh()
        if reason:
            self._pane_requested = True
            self.pane.clear()
            message = label(reason, wrap=True)
            message.set_name("cal-load-error")
            self.pane.add(message)
            self.pane.open(self.selected_day)
        if not self._state_applied:
            self._state_applied = True
            if self.get_width() > 559:
                self.pane.open(self.selected_day)
            if self.fixture:
                selected = os.environ.get("LUMA_CALENDAR_SELECTED_EVENT", "")
                selected_day = os.environ.get("LUMA_CALENDAR_SELECTED_DAY", "")
                if selected_day:
                    self.select_day(date.fromisoformat(selected_day))
                if selected:
                    event = next((e for e in self.events if e.uid == selected), None)
                    if event:
                        self.open_event(event)
                quick=os.environ.get("LUMA_CALENDAR_QUICK","")
                if quick:
                    self.quick.set_text(quick)
                    self.preview_quick(quick)
                if os.environ.get("LUMA_CALENDAR_BAR")=="view":
                    GLib.timeout_add(300,lambda:(self.narrow and self.center.grown!="view" and self.toggle_view_panel(),False)[1])
                state=os.environ.get("LUMA_CALENDAR_STATE")
                if state=="new":
                    self.new_event(start=600)
                elif state=="parsed":
                    self.new_event(text=quick)
                if self.draft and os.environ.get("LUMA_CALENDAR_INVITEE_QUERY"):
                    self.invite_field.set_text(os.environ["LUMA_CALENDAR_INVITEE_QUERY"])
        return False

    def refresh(self):
        self.refresh_calendar_marks()
        self.surface.refresh()
        self.flow_at = None
        self.update_heading()
        self.day_details() if self.selected_event is None else self.event_details()
        if self.narrow and self.bar_panel == "details":
            self.show_bar_details()
        if self.view != "month":
            def scroll_to_time():
                if self._closing:
                    return False
                adjustment = self.scroll.get_vadjustment()
                offset=0
                time_offset = (max(420,self.now_minute()-120)-420)*.9
                if self.view=="flow":
                    track=self.surface.flow_sections.get(self.anchor)
                    if track:
                        ok,bounds=track.compute_bounds(self.surface)
                        if ok:
                            offset=bounds.origin.y+self.surface.get_margin_top()-70
                    if self.anchor != self.today:
                        time_offset = 0
                adjustment.set_value(max(0,offset+time_offset))
                self._flow_scrolled(adjustment)
                return False
            GLib.timeout_add(150,scroll_to_time)

    def refresh_calendar_marks(self):
        self.calendar_marks.clear()
        sources = [s for s in self.sources if s.uid not in self.hidden_sources][:4]
        self.calendar_marks.set_size_request(18*len(sources),18)
        for index, source in enumerate(sources):
            mark = Mark(hue=self._source_hue(source))
            mark.set_tooltip_text(source.name)
            self.calendar_marks.add(mark,0,18,index,len(sources),inset=0)

    def _source_hue(self, source):
        if self.fixture:
            return self.fixture.calendars[source.uid]["hue"]
        if source.colour:
            colour = Gdk.RGBA()
            if colour.parse(source.colour):
                hue, _lightness, saturation = colorsys.rgb_to_hls(
                    colour.red, colour.green, colour.blue)
                if saturation > .05:
                    return round(hue * 360)
        return {"blue":"blue","green":"green","violet":"violet","amber":"orange"}.get(source.tone,"blue")

    def _flow_scrolled(self, adjustment):
        self.surface.track_scroll(adjustment)
        # Narrow Month: the title island fades with the page (v71 calHeadScroll; only the month grid).
        if self.narrow:
            shift = min(max(adjustment.get_value(), 0), 90) if self.view == "month" else 0
            self.title_island.set_opacity(max(0.0, 1 - shift / 60))
            self.title_island.set_can_target(shift < 57)
        if self.view != "flow" or self._closing:
            return
        visible_day = None
        for day, section in getattr(self.surface, "flow_sections", {}).items():
            if section.get_height() == 0:
                continue
            ok, bounds = section.compute_bounds(self.surface)
            if ok and bounds.origin.y+self.surface.get_margin_top() <= adjustment.get_value() + 90:
                visible_day = day
        if visible_day is not None and visible_day != self.flow_at:
            self.flow_at = visible_day
            self.update_heading()

    def update_heading(self):
        if self.view == "month":
            title = self.anchor.strftime("%B %Y")
            count = sum(e.start.year == self.anchor.year and e.start.month == self.anchor.month
                        and e.source_uid not in self.hidden_sources for e in self.events)
            subtitle = f"{count} events"
        elif self.view == "week":
            start = self.anchor - timedelta(days=self.anchor.weekday())
            title = start.strftime("%b %-d") + " – " + (start + timedelta(days=6)).strftime("%b %-d")
            count = sum(len(events_on(self.events, start + timedelta(days=i), self.hidden_sources)) for i in range(7))
            subtitle = f"{count} events this week"
        else:
            day = self.flow_at or self.anchor
            title = day.strftime("%A, %B %-d") + (" · Today" if day == self.today else "")
            subtitle = self.day_sentence(day)
        if self.narrow:
            self._narrow_heading()
            return
        row = Gtk.Box(spacing=10)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,valign=Gtk.Align.CENTER)
        title_label = label(title, "body", weight=650)  # v71 .calhead b: 13.5/650
        title_label.set_max_width_chars(-1)
        caption = label(subtitle, "caption",weight=400)
        caption.set_max_width_chars(-1)
        # In a centred box GTK measures these for the button's height; ellipsizing labels then shrink to "…".
        for words in (title_label, caption):
            words.set_ellipsize(Pango.EllipsizeMode.NONE)
        column.append(title_label)
        column.append(caption)
        row.append(column)
        row.append(icons.image("chevron-down"))
        self.heading.set_child(row)
        self.heading.set_tooltip_text(title)

    def _narrow_heading(self):
        """The narrow title island: the month (or Flow's day) large, the year (or date) small, a chevron;
        then the calendars' dots past the hairline."""
        day = (self.flow_at or self.anchor) if self.view == "flow" else self.anchor
        big = day.strftime("%A") if self.view == "flow" else day.strftime("%B")
        small = day.strftime("%B %-d") if self.view == "flow" else str(day.year)
        row = Gtk.Box(spacing=7, valign=Gtk.Align.CENTER)
        row.add_css_class("calendar-head-narrow")
        title = label(big, "guidance", weight=700)
        title.set_max_width_chars(-1)
        title.set_hexpand(False)
        row.append(title)
        aside = label(small, "title-2", weight=500, tone="muted")
        aside.set_max_width_chars(-1)
        aside.set_hexpand(False)
        row.append(aside)
        for words in (title, aside):
            words.set_ellipsize(Pango.EllipsizeMode.NONE)
        chevron = icons.image("chevron-down", pixel_size=16)
        chevron.add_css_class("calendar-ink-muted")
        row.append(chevron)
        # The title words are drawn by the content; the island's own title names the button.
        self.title_island.set_title(f"{big} {small}. Jump to a month")
        self.title_island.set_title_content(row)
        while child := self.head_dots.get_first_child():
            self.head_dots.remove(child)
        for source in self.sources[:4]:
            dot = Mark(hue=self._source_hue(source), density="filter")
            dot.add_css_class("calendar-head-dot")
            if source.uid in self.hidden_sources:
                dot.set_opacity(.3)
            self.head_dots.append(dot)

    def set_view(self, key):
        if key == self.view:
            if self.narrow and self.center.grown == "view":
                self.center.fold_panel()
            return
        self.view = key
        self.modes.set_current(key)
        self.refresh()
        if self.narrow and self.draft is None:
            self._narrow_bar()

    def go_today(self):
        self.anchor = self.selected_day = self.today
        if self.narrow and self.center.grown == "view":
            self.center.fold_panel()
        self.reload()

    def select_day(self, day):
        if self.narrow:
            # Narrow Month: a picked day moves the agenda under the month (nothing grows).
            self.selected_day, self.selected_event = day, None
            if (day.year, day.month) != (self.anchor.year, self.anchor.month):
                self.anchor = day
                self.reload()
            else:
                self.surface.refresh()
            return
        self._pane_requested = True
        self.selected_day, self.selected_event = day, None
        self.refresh()
        self.pane.open(day)

    def open_event(self, event):
        self.selected_event = event
        self.selected_day = event.start.date()
        if self.narrow:
            self.surface.refresh()
            self.show_bar_details()
            return
        self._pane_requested = True
        self.refresh()
        self.pane.open(event.uid)

    def step(self, delta):
        """One step back or on: a month, a week (three days when narrow), or a day (v71 calStep)."""
        if self.view == "month":
            month = self.anchor.month - 1 + delta
            self.anchor = date(self.anchor.year + month // 12, month % 12 + 1, 1)
            if (self.today.year, self.today.month) == (self.anchor.year, self.anchor.month):
                self.anchor = self.today
            self.selected_day = self.anchor
        else:
            self.anchor += timedelta(days=delta * ((3 if self.narrow else 7) if self.view == "week" else 1))
        self.reload()

    def show_event(self, uid, day):
        self.anchor = day
        event = next((e for e in self.events if e.uid == uid and day in e.days), None)
        if event:
            self.open_event(event)
        else:
            self.select_day(day)
            self.reload()

    def day_sentence(self, day):
        entries = [e for e in events_on(self.events, day, self.hidden_sources) if not e.all_day]
        parts = [f"{len(entries)} {'thing' if len(entries)==1 else 'things'}" if entries else "Nothing planned"]
        if day == self.today:
            upcoming = next((e for e in entries if e.end.hour*60 + e.end.minute > self.now_minute()), None)
            if upcoming:
                parts.append(f"next: {upcoming.summary} at {minute_text(upcoming.start.hour*60+upcoming.start.minute)}")
        free = sum(end-start for start,end in free_gaps(day, entries, today=self.today, now_minute=self.now_minute()))
        if free:
            parts.append(duration_text(free) + " free")
        return " · ".join(parts)

    def day_details(self, pane=None):
        pane = pane or self.pane
        pane.clear()
        day = self.selected_day
        pane.set_title("Today" if day == self.today else day.strftime("%A"))
        pane.set_back(None)
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, margin_bottom=12)
        header.add_css_class("calendar-day-summary")
        header.append(label(day.strftime("%A, %B %-d"), "section-day", wrap=True))
        sentence = self.day_sentence(day).removeprefix("Nothing planned · ")
        header.append(label(sentence if sentence != "Nothing planned" else "No free time", "caption", wrap=True,weight=400))
        pane.add(header)
        entries = events_on(self.events, day, self.hidden_sources)
        rows = []
        if not entries:
            empty = label("Nothing on " + ("today" if day == self.today else day.strftime("%A")))
            empty.add_css_class("calendar-day-empty")
            pane.add(empty)
        for event in entries:
            stripe = Gtk.Box()
            stripe.add_css_class("calendar-event-stripe")
            stripe.add_css_class(event_category(event, self))
            when = self.range_text(event)
            if event.location:
                when += " · " + event.location
            faces = Gtk.Box()
            for name, _, email in participant_rows(event):
                faces.append(avatar(name,18,self,email=email))
            item = DetailsItem(event.summary, when, lead=stripe, trail=faces, on_activate=lambda e=event: self.open_event(e))
            item.set_name("cal-detail-event-" + event.uid)
            rows.append(item)
        add = AddRow("Add an event", icon="plus", on_activate=lambda: self.new_event(day=day, start=600))
        add.set_name("cal-new-event")
        rows.append(add)
        pane.add_list(rows)
        free = free_gaps(day, entries, today=self.today, now_minute=self.now_minute())
        if free:
            pane.add_section("Free").add_css_class("calendar-free-section")
            for start, end in free:
                when=f"{minute_text(start)} – {minute_text(end)}"
                row=button(when,lambda d=day,s=start,e=end:self.hold(d,s,e))
                content=Gtk.Box(spacing=10)
                content.append(label(when,"meta",tone="primary"))
                duration=label(duration_text(end-start),"meta")
                duration.set_hexpand(False)
                duration.set_max_width_chars(-1)
                duration.set_ellipsize(Pango.EllipsizeMode.NONE)
                content.append(duration)
                hold=label("Hold","meta",weight=600)
                hold.set_hexpand(False)
                hold.set_max_width_chars(-1)
                hold.set_ellipsize(Pango.EllipsizeMode.NONE)
                hold.add_css_class("calendar-free-hold")
                content.append(hold)
                row.set_child(content)
                row.add_css_class("calendar-free-row")
                pane.add(row)

    def range_text(self, event):
        return "All day" if event.all_day else f"{minute_text(event.start.hour*60+event.start.minute)} – {minute_text(event.end.hour*60+event.end.minute)}"

    def event_details(self, pane=None):
        pane = pane or self.pane
        event = self.selected_event
        pane.clear()
        pane.set_title("Holiday" if not event.editable else "Event")
        pane.set_back(event.start.strftime("%A"),lambda:self.select_day(event.start.date()))
        pane.back_button.set_name("cal-event-back")
        # v71: it reads top-down by importance. The title leads (22/700), the date under it in
        # ink-2 and the time in ink-3; then four real action tiles; the ⋯ menu is gone.
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, margin_top=2, margin_bottom=18)
        hero.add_css_class("calendar-event-hero")
        hero.add_css_class(event_category(event, self))
        stripe=Gtk.Box()
        stripe.add_css_class("calendar-event-stripe")
        stripe.add_css_class(event_category(event,self))
        stripe.set_margin_top(3)
        stripe.set_margin_bottom(20)
        title = label(event.summary, "intro", wrap=True)
        title.set_name("cal-event-title")
        title.add_css_class("calendar-event-title")
        title.set_margin_bottom(4)
        hero.append(title)
        for text, tone in ((event.start.strftime("%A, %B %-d"), "secondary"),
                           (self.range_text(event) + ("" if event.all_day else " · " + duration_text(int((event.end-event.start).total_seconds()/60))), "muted")):
            hero.append(label(text, "lead", wrap=True, weight=400, tone=tone))
        hero_row=Gtk.Box(spacing=12)
        hero_row.append(stripe)
        hero_row.append(hero)
        pane.add(hero_row)
        if event.editable:
            buttons = []
            for name, icon, text, callback, danger in (
                    ("edit", "pencil", "Edit", lambda: self.edit_event(event), False),
                    ("share", "share-2", "Share", lambda: self.share_event(event), False),
                    ("duplicate", "copy", "Duplicate", lambda: self.duplicate(event), False),
                    ("delete", "trash-2", "Delete", lambda: self.confirm_delete(event), True)):
                tile = StackedButton(icon, text, danger=danger, on_click=callback)
                tile.set_name("cal-event-" + name)
                buttons.append(tile)
            tiles = StackedButtons(buttons, size="tile")
            tiles.set_name("cal-event-tiles")
            pane.add(tiles)
        metadata = self.fixture.metadata.get(event.uid, {}) if self.fixture else {}
        if metadata.get("video") or (event.location.casefold()=="video call" and event.url.startswith(("http://","https://"))):
            join = TextButton("Join call", icon="video", style="key", on_click=lambda: self.join_call(event))
            join.set_name("cal-join-call")
            join.set_margin_top(10)
            pane.add(join)
        if event.participants and event.editable:
            pane.add_section("Your answer")
            mine=self.answers.get(event.uid,"Going") if self.fixture else next((p.answer for p in event.attendees if p.email.casefold()==event.user_email.casefold()),"NEEDS-ACTION")
            current={"Maybe":"maybe","Can’t go":"no","TENTATIVE":"maybe","DECLINED":"no"}.get(mine,"yes")
            answer = ModeSwitch((("yes","Going"),("maybe","Maybe"),("no","Can’t go")), current=current,
                                on_change=lambda value: self.rsvp(event,value), label="Your answer", fill=True, size="large")
            answer.set_name("cal-rsvp")
            for key,control in answer.buttons.items():
                control.set_name("cal-rsvp-"+key)
                control.connect("clicked",lambda _b,value=key:self.rsvp(event,value))
            pane.add(answer)
        pane.add_section("Details")
        source = next((s for s in self.sources if s.uid == event.source_uid), None)
        facts = [("Calendar", source.name if source else "Calendar",
                  Mark(hue=self._source_hue(source) if source else "blue"))]
        if event.editable:
            facts.append(("Alert", "The day before, 9:00 AM" if event.all_day else next((name for n,name in ALERT_CHOICES if event.alerts == (n,)), "None")))
        pane.add_facts(facts, card=True)
        if event.location and not metadata.get("video"):
            pane.add_section("Place")
            # The kit's place card, full width (v71 lPlaceCard): the pin, the place, how far, Directions.
            place = PlaceCard(event.location, metadata.get("eta", "12 min away") if self.fixture else "",
                              on_directions=lambda: self.directions(event.location))
            place.set_name("cal-event-place")
            place.set_hexpand(True)
            place.set_halign(Gtk.Align.FILL)  # TODO(v71-kit PlaceCard full width, kit-requests/calendar-02)
            pane.add(place)
        if event.participants:
            people = [("You","Organiser · " + self.answers.get(event.uid,"Going"),"")] + list(participant_rows(event)) if self.fixture else list(participant_rows(event))
            pane.add_section("People", count=len(people))
            rows = []
            for name, status, email in people:
                is_self = name == "You" if self.fixture else bool(email and email.strip().casefold() == event.user_email.strip().casefold())
                display_name = "You" if is_self else name
                if not self.fixture and email and email.strip().casefold() == event.organizer_email.strip().casefold():
                    status = "Organiser · " + status
                actions = () if is_self else (("message-square", "Message " + name.split()[0], lambda n=name,e=email: self.contact_action(n,"message",e)),
                                             ("mail", "Email " + name.split()[0], lambda n=name,e=email: self.contact_action(n,"email",e)))
                row = DetailsRow(display_name,status,lead=avatar(name,32,self,email=email),actions=actions,
                                 on_activate=None if is_self else lambda n=name:self.open_contact(n))
                row.button.set_sensitive(not is_self)
                if not is_self:
                    row.button.set_tooltip_text("Open in Contacts")
                    row.button.update_property([Gtk.AccessibleProperty.LABEL],["Open in Contacts"])
                rows.append(row)
            rows.append(AddRow("Message everyone",icon="message-square",on_activate=lambda: self.contact_action("","everyone")))
            pane.add_list(rows)
        if event.description:
            pane.add_section("Notes")
            note = label(event.description,wrap=True)
            note.add_css_class("calendar-event-meta")
            note.set_wrap_mode(Pango.WrapMode.CHAR)
            note.set_max_width_chars(38)
            note.set_width_chars(1)
            pane.add(note)
        if not event.editable:
            pane.add(label("Holidays are read-only." if self.fixture else "This calendar is read-only.",wrap=True))

    def _panel_toggled(self, shown):
        if shown != self.pane.shown:
            self._pane_requested = shown
        self.pane.open(self.selected_event.uid if self.selected_event else self.selected_day) if shown else self.pane.close()

    def _sync_panel(self, *_):
        if self.panel_control.get_active() != self.pane.shown:
            self.panel_control.set_active(self.pane.shown)

    def _pane_closed(self):
        self._pane_requested = False
        self._sync_panel()

    def calendars_menu(self):
        if self.narrow:
            self._calendars_panel()
            return
        self._calendars_popover()

    def _calendars_panel(self, regrow=False):
        """Narrow: the calendars rise from the bar as a panel: 48px rows, the colour, the count at the end."""
        def toggle(uid):
            self.toggle_calendar(uid)
            self.center.fold_panel()
            self._calendars_panel()
        rows = ["Calendars"]
        for source in self.sources:
            count = sum(e.source_uid == source.uid for e in self.events)
            rows.append(PanelRow(source.name, lead=Mark(hue=self._source_hue(source)), count=count or None,
                                 selected=source.uid not in self.hidden_sources, closes=False,
                                 on_activate=lambda uid=source.uid: toggle(uid)))
        rows += [None, PanelRow("Add a calendar", icon="plus", on_activate=self.add_calendar)]
        panel = panel_list(rows, label="Calendars")
        panel.set_name("cal-calendars-panel")
        self.center.grow("calendars", panel)

    def _calendars_popover(self):
        menu = FloatingMenu([], label="Calendars", title="Calendars")
        contents = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        contents.set_size_request(240, -1)
        handle = [None]

        def close():
            handle[0].close() if handle[0] else menu.close()

        heading = label("Calendars", "label")
        heading.set_margin_start(12)
        heading.set_margin_top(8)
        heading.set_margin_bottom(6)
        contents.append(heading)
        for source in self.sources:
            count = sum(e.source_uid == source.uid for e in self.events)
            visible = source.uid not in self.hidden_sources
            control = Gtk.Button(accessible_role=Gtk.AccessibleRole.MENU_ITEM)
            control.set_name(f"calendar.filter.{source.uid}")
            control.add_css_class("lumaui-menu-item")
            row = Gtk.Box(spacing=10, valign=Gtk.Align.CENTER)
            row.append(Mark(hue=self._source_hue(source)))
            name = label(source.name)
            name.set_max_width_chars(-1)
            row.append(name)
            if count:
                tally = Gtk.Label(label=str(count), xalign=1, width_chars=2)
                apply_type(tally, "caption")
                tally.add_css_class("calendar-ink-muted")
                row.append(tally)
            if visible:
                row.append(icons.image("check"))
            control.set_child(row)
            control.update_property([Gtk.AccessibleProperty.LABEL],
                                    [f"{source.name}, {'shown' if visible else 'hidden'}, {count} events"])
            control.connect("clicked", lambda _b, uid=source.uid: (close(), self.toggle_calendar(uid)))
            menu.buttons.append(control)
            contents.append(control)
        add = Gtk.Button(accessible_role=Gtk.AccessibleRole.MENU_ITEM)
        add.set_name("calendar.add")
        add.add_css_class("lumaui-menu-item")
        add_row = Gtk.Box(spacing=10)
        add_row.append(icons.image("plus"))
        add_row.append(label("Add a calendar"))
        add.set_child(add_row)
        add.update_property([Gtk.AccessibleProperty.LABEL], ["Add a calendar"])
        add.connect("clicked", lambda _b: (close(), self.add_calendar()))
        contents.append(add)
        menu.buttons.append(add)
        menu.append(contents)
        menu.popup(self.calendar_control, align="end")

    def toggle_calendar(self, uid):
        self.hidden_sources.symmetric_difference_update({uid})
        self.refresh()

    def date_menu(self):
        menu = FloatingMenu([],label="Go to month",width="wide")
        contents=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=0,
                         margin_start=10,margin_end=10,margin_top=10,margin_bottom=10)
        contents.add_css_class("calendar-date-menu")
        contents.set_size_request(320,-1)
        year=[self.anchor.year]
        handle=[None]
        def close():
            handle[0].close() if handle[0] else menu.close()
        def pick(day):
            close()
            self.choose_month(day)
        def draw():
            while child:=contents.get_first_child():
                contents.remove(child)
            # v71: the year is the title; Today and the arrows are grouped on the right as 36px keys.
            header=Gtk.Box(spacing=4,margin_start=6,margin_bottom=4)
            def step(delta):
                year[0]+=delta
                draw()
            heading=label(str(year[0]),"card-title")
            heading.set_name("cal-picker-year")
            heading.set_max_width_chars(-1)
            header.append(heading)
            controls = (
                (TextButton("Today", style="raised", on_click=lambda: (close(), self.go_today())), "cal-picker-today"),
                (IconOnlyButton("chevron-left", str(year[0]-1), raised=True, on_click=lambda: step(-1)), "cal-year-prev"),
                (IconOnlyButton("chevron-right", str(year[0]+1), raised=True, on_click=lambda: step(1)), "cal-year-next"),
            )
            for control, name in controls:
                control.set_name(name)
                header.append(control)
            contents.append(header)
            grid=Gtk.Grid(column_homogeneous=True,row_homogeneous=True,column_spacing=6,row_spacing=6,margin_top=8)
            for month in range(1,13):
                count=sum(e.start.year==year[0] and e.start.month==month and e.source_uid not in self.hidden_sources for e in self.events)
                name=date(year[0],month,1).strftime("%b")
                description = f"{date(year[0], month, 1).strftime('%B')}, " + (f"{count} event{'s' if count != 1 else ''}" if count else "nothing planned")
                selected = (year[0], month) == (self.anchor.year, self.anchor.month)
                control = TextButton(name, style="key" if selected else "raised",
                                     on_click=lambda m=month: pick(date(year[0],m,1)))
                control.set_name(f"cal-month-{month}")
                control.update_property([Gtk.AccessibleProperty.LABEL], [description])
                control.add_css_class("calendar-month-choice")
                control.set_size_request(-1,62)
                body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=7,halign=Gtk.Align.CENTER,valign=Gtk.Align.CENTER)
                month_name=label(name,"title-2",weight=600)
                month_name.set_max_width_chars(-1)
                month_name.set_xalign(.5)
                body.append(month_name)
                # Busyness as up to five dots instead of "25 events"; the count is in the accessible label.
                dots=Gtk.Box(spacing=3,halign=Gtk.Align.CENTER)
                level=min(5,-(-count//5)) if count else 0
                for k in range(5):
                    dot=Gtk.Box(valign=Gtk.Align.CENTER)
                    dot.set_size_request(5,5)
                    dot.add_css_class("calendar-busy-dot")
                    if k<level:
                        dot.add_css_class("filled")
                    dots.append(dot)
                body.append(dots)
                control.set_child(body)
                if (year[0],month)<(self.today.year,self.today.month):
                    control.add_css_class("past")
                if (year[0],month)==(self.today.year,self.today.month):
                    control.add_css_class("current")
                if (year[0],month)==(self.anchor.year,self.anchor.month):
                    control.add_css_class("selected")
                grid.attach(control,(month-1)%3,(month-1)//3,1,1)
                menu.buttons.append(control)
            contents.append(grid)
        draw()
        if self.narrow:
            # A phone: the picker rises in the bar's frame (16 gutter, 34 up, 26 round).
            # TODO(v71-kit kit-requests/calendar-06): FloatingMenu.popup on a phone drops appended content.
            handle[0]=BarFrame.present(self.title_island.title_button,contents,kind="menu")
            return
        menu.append(contents)
        menu.popup(self.heading,align="start")

    def choose_month(self, day):
        self.anchor = day
        if self.view == "flow":
            self.view = "month"
            self.modes.set_current("month")
        self.reload()

    VIEWS = (("flow", "Flow", "list"), ("week", "3 days", "columns-3"), ("month", "Month", "layout-grid"))

    def _narrow_bar(self):
        """v71 narrow bar, two rows: how you're looking (a dropdown that grows Flow, 3 days and Month as
        tiles) and a Today key that shows today's date; under a hairline, "Add an event", always there.
        A picked event or day grows the bar with its details; the row is then "‹ Wednesday" (or the
        views) and ✕."""
        event = self.selected_event if self.bar_panel == "details" else None
        view = next(v for v in self.VIEWS if v[0] == self.view)
        dropdown = BarAction(view[2], view[1], tooltip="View", dropdown=True, key="view", panel=self._view_tiles)
        if event is not None:
            weekday = event.start.strftime("%A")
            top = (BarAction("chevron-left", weekday, on_activate=self.back_to_day, tooltip=f"Back to {weekday}",
                             keep_label=True),
                   SPACER, BarAction("x", tooltip="Close", on_activate=self.close_bar_details))
        elif self.bar_panel == "details":
            top = (dropdown, SPACER, BarAction("x", tooltip="Close", on_activate=self.close_bar_details))
        else:
            top = (dropdown, SPACER, TodayKey(self.today, self.go_today))
        self._rebuilding = True
        try:
            self.center.show_bar(top, entry=self.quick)
        finally:
            self._rebuilding = False
        self._name_bar_parts()

    def _name_bar_parts(self):
        """Names the conform harness (and the tests) find the narrow row's controls by."""
        def walk(widget):
            item = getattr(widget, "bar_item", None)
            if isinstance(item, BarAction):
                if item.key == "view":
                    widget.set_name("cal-view")
                elif (item.tooltip or "").startswith("Back to"):
                    widget.set_name("cal-bar-back")
                elif item.tooltip == "Close":
                    widget.set_name("cal-bar-close")
            elif isinstance(item, TodayKey):
                widget.set_name("cal-today")
            child = widget.get_first_child()
            while child is not None:
                walk(child)
                child = child.get_next_sibling()
        walk(self.center)

    def _view_tiles(self):
        tiles = BarTiles([BarTile(icon, name, lambda k=key: self.set_view(k), on=self.view == key)
                          for key, name, icon in self.VIEWS], columns=3)
        tiles.set_name("cal-view-tiles")
        return tiles

    def toggle_view_panel(self):
        """The view dropdown grows Flow, 3 days and Month as tiles; tapping it again folds them."""
        self.center.grow("view", self._view_tiles, anchor=self.find_name("cal-view"))

    def show_bar_details(self):
        """Narrow: a picked event (or the day it is on) grows the bar with the same details the
        desktop pane shows (one code path), without a header; the pane is not used under 560."""
        sink = DetailsPane("Event")
        self.event_details(sink) if self.selected_event is not None else self.day_details(sink)
        body = sink.body
        holder = body.get_parent()
        if isinstance(holder, Gtk.Viewport):
            holder.set_child(None)
        elif holder is not None:
            holder.remove(body)
        body.set_name("cal-bar-details")
        body.add_css_class("calendar-bar-details")
        self.bar_panel = "details"
        self._narrow_bar()
        self.center.grow("details", body, on_fold=self._details_folded)

    def _details_folded(self):
        if self._rebuilding or self.bar_panel != "details":
            return
        # Folded by the kit (Esc, a tap beside it): back to the resting bar.
        self.bar_panel = ""
        self.selected_event = None
        GLib.idle_add(lambda: (self.draft is None and self.narrow and (self.surface.refresh(), self._narrow_bar()), False)[1])

    def back_to_day(self):
        self.selected_event = None
        self.surface.refresh()
        self.show_bar_details()

    def close_bar_details(self):
        self.bar_panel = ""
        self.selected_event = None
        self.surface.refresh()
        self._narrow_bar()

    def show_quick_entry(self):
        text = self.quick.text if getattr(self, "quick", None) is not None else ""
        self.quick = BarEntry("quick", icon="plus", span="wide", text=text,
                              placeholder="Add an event, like Lunch Fri 1pm" if self.narrow else "Add an event, like “Lunch with Priya Fri 1pm”",
                              on_change=self.preview_quick, on_submit=lambda text:self.new_event(text=text))
        if not self.narrow:
            self.center.show_bar((self.quick,))
        else:
            self._narrow_bar()
        if text:
            self.preview_quick(text)  # the read-back chips follow the text into the other shape

    def preview_quick(self,text):
        names = {key:p["name"] for key,p in self.fixture.people.items()} if self.fixture else {p.email:p.name for p in self.people if p.email}
        try:
            parsed = parse_event(text,today=self.today,now_minute=self.now_minute(),people=names)
        except ValueError as error:
            self.quick.set_chips((str(error),))
            return
        self.quick.set_chips((parsed.day.strftime("%a, %b %-d") + " · " + minute_text(parsed.start) + "–" + minute_text(parsed.end % 1440),) if parsed else ())

    def new_event(self, day=None, start=None, text=""):
        if day is None and start is None and self.draft and text in (self.draft.title,self.draft_source):
            self.center.grow()
            return
        names = {key: p["name"] for key,p in self.fixture.people.items()} if self.fixture else {p.email:p.name for p in self.people if p.email}
        try:
            parsed = parse_event(text,today=self.today,now_minute=self.now_minute(),people=names)
        except ValueError as error:
            self.toast(str(error), kind='error')
            return
        source = resolve_calendar(parsed.calendar if parsed else "",self.sources)
        if parsed:
            parsed=replace(parsed,calendar=source)
        if not source:
            self.toast("No writable calendar",kind="warning")
            return
        minute = start if start is not None else (self.now_minute()+89)//60*60
        self.real_invitees = {}
        self.invitees_changed = False
        self.draft = parsed or ParsedEvent(day or self.selected_day,minute,min(1440,minute+60),"",source)
        self.draft_source = text
        self.editing_event = None
        self.duplicate_origin = None
        self.real_invitees = {email:CalendarAttendee(names[email],email) for email in self.draft.people} if not self.fixture else {}
        self.invitees_changed = bool(self.real_invitees)
        self.build_editor()
        self.center.grow()

    def hold(self,day,start,end):
        source = resolve_calendar("personal",self.sources)
        if not source:
            self.toast("No writable calendar",kind="warning")
            return
        self.draft = ParsedEvent(day,start,min(end,start+60),"Focus time",source)
        self.editing_event = None
        self.duplicate_origin = None
        self.draft_source = ""
        self.real_invitees = {}
        self.invitees_changed = False
        self.build_editor()
        self.center.grow()

    def edit_event(self,event):
        self.editing_event = event
        self.duplicate_origin = None
        self.real_invitees = {a.email:a for a in event.attendees}
        self.invitees_changed = False
        self.draft = ParsedEvent(event.start.date(),event.start.hour*60+event.start.minute,
                                 event.end.hour*60+event.end.minute,event.summary,event.source_uid,
                                 people=tuple(self.fixture.metadata.get(event.uid,{}).get("people",())) if self.fixture else (),location=event.location,
                                 alert=event.alerts[0] if event.alerts else None)
        self.build_editor()
        self.center.grow()

    def build_editor(self):
        d = self.draft
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=0,margin_start=16,margin_end=16,margin_top=8,margin_bottom=6)
        self.title_field = self.event_entry("Event name",d.title,lambda text:self.change_draft(title=text),name="cal-draft-title",role="title-2")
        body.append(self.title_field)
        times = Gtk.Box(spacing=6)
        times.set_size_request(-1,38)
        times.append(icons.image("clock"))
        times.append(self.draft_chip(d.day.strftime("%a, %b %-d"),lambda:self.draft_menu("day"),name="cal-draft-day"))
        times.append(self.draft_chip(minute_text(d.start),lambda:self.draft_menu("start"),name="cal-draft-start"))
        times.append(label("to","small",tone="faint"))
        times.get_last_child().set_hexpand(False)
        times.get_last_child().set_max_width_chars(-1)
        times.append(self.draft_chip(minute_text(d.end),lambda:self.draft_menu("end"),name="cal-draft-end"))
        duration = label(duration_text(d.end-d.start),"small")
        duration.set_xalign(1)
        times.append(duration)
        self.draft_row(body,times)
        place=Gtk.Box(spacing=6)
        place.set_size_request(-1,38)
        place.append(icons.image("map-pin"))
        place.append(self.event_entry("Add a place or a video call",d.location,lambda text:self.change_draft(location=text),name="cal-draft-place"))
        self.draft_row(body,place)
        people = Gtk.Box(spacing=6)
        people.set_size_request(-1,38)
        people.append(icons.image("users"))
        if self.fixture:
            for key in d.people:
                name = self.fixture.people[key]["name"]
                people.append(self.invitee_chip(name,lambda k=key:self.remove_invitee(k)))
        else:
            for email,attendee in self.real_invitees.items():
                people.append(self.invitee_chip(attendee.name or email,lambda k=email:self.remove_real_invitee(k),email=email))
        self.invite_field = self.event_entry("Add someone else" if d.people or self.real_invitees else "Invite people","",self.invitee_suggestions,name="cal-draft-invitees")
        people.append(self.invite_field)
        self.draft_row(body,people)
        self.suggestions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.suggestions.add_css_class("calendar-invitee-suggestions")
        self.suggestions.set_visible(False)
        body.append(self.suggestions)
        controls = Gtk.Box(spacing=8)
        controls.set_size_request(-1,38)
        source = next((s for s in self.sources if s.uid == d.calendar),None)
        controls.append(icons.image("calendar"))
        mark = Mark(hue=self._source_hue(source) if source else "blue")
        controls.append(self.draft_chip(source.name if source else "Calendar",lambda:self.draft_menu("calendar"),name="cal-draft-calendar",lead=mark,dropdown=True))
        controls.append(Gtk.Box(hexpand=True))
        controls.append(icons.image("bell"))
        controls.append(self.draft_chip(next((n for v,n in ALERT_CHOICES if v==d.alert),"10 minutes before"),lambda:self.draft_menu("alert"),name="cal-draft-alert",dropdown=True))
        self.draft_row(body,controls)
        self.editor = ActionEditor("Edit event" if self.editing_event else "New event","calendar",
                                   summary="from “{}”" if self.draft_source else "on {}",
                                   summary_emphasis=self.draft_source or d.day.strftime("%A, %B %-d"),body=body,
                                   primary=BarAction("check","Save" if self.editing_event else "Create",on_activate=self.save_draft),
                                   on_discard=self.discard_draft,draft=lambda:self.draft.title if self.draft else "",
                                   submit_on_return=True)
        self.editor.set_name("cal-draft")
        self.center.set_editor(self.editor)

    def invitee_chip(self,name,on_remove, *, email=""):
        chip = Gtk.Box(spacing=6,valign=Gtk.Align.CENTER)
        chip.add_css_class("calendar-invitee-chip")
        chip.append(avatar(name,20,self,email=email))
        first = label(name.split()[0],"meta",weight=500,tone="primary")
        first.set_max_width_chars(-1)
        first.set_hexpand(False)
        chip.append(first)
        remove = button("",on_remove,accessible_label="Remove "+name)
        remove.add_css_class("calendar-invitee-remove")
        remove.set_size_request(18,18)
        remove.set_child(icons.image("x",pixel_size=11))
        chip.append(remove)
        return chip

    def draft_row(self,body,row):
        overlay = Gtk.Overlay(child=row)
        rule = Gtk.Box(valign=Gtk.Align.START,can_target=False)
        rule.set_size_request(-1,1)
        rule.add_css_class("calendar-hour-rule")
        overlay.add_overlay(rule)
        body.append(overlay)

    def draft_chip(self,text,callback,*,name,lead=None,dropdown=False):
        control = button(text,callback,name=name)
        control.set_valign(Gtk.Align.CENTER)
        control.add_css_class("calendar-draft-chip")
        content = Gtk.Box(spacing=4)
        if lead is not None:
            content.append(lead)
        title = label(text,"meta",weight=500,tone="primary")
        title.set_max_width_chars(-1)
        title.set_hexpand(False)
        content.append(title)
        if dropdown:
            content.append(icons.image("chevron-down",pixel_size=13))
        control.set_child(content)
        return control

    def event_entry(self,placeholder,text,on_change,*,name,role="body"):
        entry=Gtk.Entry(text=text,placeholder_text=placeholder,hexpand=True,width_chars=1)
        entry.set_has_frame(False)
        entry.set_valign(Gtk.Align.CENTER)
        entry.set_size_request(-1,36 if name=="cal-draft-title" else 32)
        entry.add_css_class("calendar-draft-field")
        entry.set_name(name)
        entry.update_property([Gtk.AccessibleProperty.LABEL],[placeholder])
        entry.set_tooltip_text(placeholder)
        apply_type(entry,role)
        entry.connect("changed",lambda e:on_change(e.get_text()))
        entry.connect("activate",lambda _e:self.save_draft())
        return entry

    def change_draft(self,**fields):
        self.draft = replace(self.draft,**fields)

    def draft_menu(self,key):
        d=self.draft
        if key=="calendar":
            choices=[(s.uid,s.name) for s in self.sources if s.writable and (not self.editing_event or s.uid==self.editing_event.source_uid)]
        elif key=="alert":
            choices=ALERT_CHOICES
        elif key=="day":
            choices=[(self.today+timedelta(days=i),"Today" if i==0 else "Tomorrow" if i==1 else (self.today+timedelta(days=i)).strftime("%a, %b %-d")) for i in range(8)]
        else:
            choices=[(i,minute_text(i)) for i in range(420,1440,30) if key!="end" or i>d.start]
        anchor=self.find_name("cal-draft-"+key)
        FloatingMenu([MenuItem(name,on_activate=lambda value=value:self.choose_draft(key,value),selected=getattr(d,key)==value)
                      for value,name in choices],label=key.title()).popup(anchor)

    def choose_draft(self,key,value):
        length=self.draft.end-self.draft.start
        self.change_draft(**{key:value})
        if key=="start":
            self.change_draft(end=min(1440,value+length))
        self.build_editor()
        self.center.grow()

    def invitee_suggestions(self,text):
        while child:=self.suggestions.get_first_child():
            self.suggestions.remove(child)
        if self.fixture and text.strip():
            for key,person in self.fixture.people.items():
                if key!="me" and key not in self.draft.people and text.casefold() in person["name"].casefold():
                    self.add_suggestion(person["name"],lambda k=key:self.add_invitee(k))
                    if len(list(self._suggestion_children())) >= 3:
                        break

        elif text.strip():
            for person in self.people:
                if person.email and person.email not in self.real_invitees and text.casefold() in person.name.casefold():
                    self.add_suggestion(person.name,lambda p=person:self.add_real_invitee(p.email,p.name),email=person.email)
                    if len(list(self._suggestion_children())) >= 3:
                        break
            import re
            email=text.strip()
            if re.fullmatch(r"[^\s@]+@[^\s@]+",email) and email not in self.real_invitees:
                self.suggestions.append(button("Invite " + email,lambda:self.add_real_invitee(email)))
        self.suggestions.set_visible(self.suggestions.get_first_child() is not None)

    def _suggestion_children(self):
        child = self.suggestions.get_first_child()
        while child is not None:
            yield child
            child = child.get_next_sibling()

    def add_suggestion(self,name,callback, *, email=""):
        control = button(name,callback)
        control.add_css_class("calendar-invitee-suggestion")
        if self.suggestions.get_first_child() is None:
            control.add_css_class("first")
        content = Gtk.Box(spacing=8)
        content.append(avatar(name,24,self,email=email))
        title = label(name)
        title.set_max_width_chars(-1)
        content.append(title)
        control.set_child(content)
        self.suggestions.append(control)

    def add_real_invitee(self,email,name=None):
        self.real_invitees[email]=CalendarAttendee(name or email,email)
        self.invitees_changed=True
        self.build_editor()
        self.center.grow()

    def remove_real_invitee(self,email):
        self.real_invitees.pop(email,None)
        self.invitees_changed=True
        self.build_editor()
        self.center.grow()

    def add_invitee(self,key):
        self.change_draft(people=(*self.draft.people,key))
        self.build_editor()
        self.center.grow()

    def remove_invitee(self,key):
        self.change_draft(people=tuple(k for k in self.draft.people if k!=key))
        self.build_editor()
        self.center.grow()

    def save_draft(self):
        if self._saving or self.draft is None:
            return
        d=self.draft
        if not d.title.strip():
            self.toast("The event needs a title",kind="warning")
            return
        old=self.editing_event
        template=old or self.duplicate_origin
        zone=ZoneInfo(template.tzid or local_tzid()) if template else ZoneInfo("UTC" if self.fixture else local_tzid())
        origin=datetime.combine(d.day,time(),zone)
        draft=EventDraft(d.calendar,d.title,origin+timedelta(minutes=d.start),origin+timedelta(minutes=d.end),
                         location=d.location,alerts=() if d.alert is None else (d.alert,),uid=old.uid if old else "",
                         description=template.description if template else "",tzid=template.tzid if template else ("UTC" if self.fixture else local_tzid()),
                         repeat=old.repeat if old else "none",rid=old.rid if old else "",instance_start=old.instance_start if old else None,
                         all_day=template.all_day if template else False,url=template.url if template else "")
        if template and template.all_day:
            duration=template.end-template.start
            draft.start=datetime.combine(d.day,time(),zone)
            draft.end=draft.start+duration-timedelta(days=1)
        if old:
            values = {"summary":d.title, "location":d.location, "alerts":draft.alerts,
                      "start":draft.start, "end":draft.end}
            draft.changed_fields = frozenset(key for key,value in values.items() if value != (old.end-timedelta(days=1) if old.all_day and key=="end" else getattr(old,key)))
        if not self.fixture and self.invitees_changed:
            draft.attendees = tuple(self.real_invitees.values())
            if draft.changed_fields is not None:
                draft.changed_fields |= {"attendees"}
        def save(scope="all"):
            self._saving=True
            self.task(lambda:self.fixture.save(draft) if self.fixture else save_meeting(draft,scope=scope),lambda uid:self._saved(uid,d))
        if old and old.recurring and not self.fixture:
            FloatingMenu([MenuItem("This event",on_activate=lambda:save("this")),
                          MenuItem("This and following events",on_activate=lambda:save("future")),
                          MenuItem("All events",on_activate=lambda:save("all"))],label="Change repeating event").popup(self.title_field)
        else:
            save()

    def _saved(self,uid,draft):
        if self.fixture:
            event=next(e for e in self.fixture.events if e.uid==uid)
            answers=dict(event.participants)
            participants=tuple((self.fixture.people[k]["name"],answers.get(self.fixture.people[k]["name"],"Going")) for k in draft.people)
            self.fixture.events=tuple(replace(e,participants=participants) if e.uid==uid else e for e in self.fixture.events)
            self.fixture.metadata[uid]={**self.fixture.metadata.get(uid,{}),"people":list(draft.people),"cal":draft.calendar}
        self.draft=None
        self.selected_event=None
        self.anchor=draft.day
        self.center.fold()
        self.show_quick_entry()
        self.reload()
        self.toast(f"Added “{draft.title}”" if not self.editing_event else "Event saved")

    def discard_draft(self):
        saved=self.draft
        context=(self.editing_event,self.duplicate_origin,self.draft_source,
                 dict(self.real_invitees),self.invitees_changed)
        self.draft=None
        self.center.fold()
        self.show_quick_entry()
        self.toast("Draft discarded",undo=lambda:self.restore_draft(saved,context=context))

    def restore_draft(self,draft,*,context=None):
        self.draft=draft
        if context is not None:
            (self.editing_event,self.duplicate_origin,self.draft_source,
             self.real_invitees,self.invitees_changed)=context
        self.build_editor()
        self.center.grow()

    def _center_changed(self,_center,state):
        if state=="editor":
            self.center.set_name("cal-draft-host")
        else:
            self.center.set_name("cal-bar")
            if self.draft:
                self.quick.set_text(self.draft_source or self.draft.title)
                self.quick.set_chips(())

    def duplicate(self,event):
        self.edit_event(event)
        self.editing_event=None
        self.duplicate_origin=event
        self.invitees_changed=bool(self.real_invitees)
        self.build_editor()
        self.center.grow()

    def confirm_delete(self,event):
        if event.recurring and not self.fixture:
            FloatingMenu([MenuItem("This event",on_activate=lambda:self.delete_with_backup(event,"this")),
                          MenuItem("This and following events",on_activate=lambda:self.delete_with_backup(event,"future")),
                          MenuItem("All events",on_activate=lambda:self.delete_with_backup(event,"all"))],label="Delete repeating event").popup(self.find_name("cal-event-delete"))
        else:
            self.delete_with_backup(event)

    def delete_with_backup(self,event,scope="all"):
        def work():
            if self.fixture:
                self.fixture.delete(event)
                return None
            ensure_backup(event.source_uid,"event-delete",backend.export_ics)
            text=backend.component_text(event.source_uid,event.uid)
            delete_event(event.source_uid,event.uid,rid=event.rid,scope=scope,instance_start=event.instance_start)
            try:
                after=backend.component_text(event.source_uid,event.uid,allow_missing=True)
            except Exception:
                after=None
            return text,after
        self.task(work,lambda text:self._deleted(event,text))

    def _deleted(self,event,text=None):
        self.selected_event=None
        self.reload()
        if not self.fixture and text[1] is None:
            self.toast("Event deleted; its Undo state could not be verified. The backup is preserved.",
                       kind="warning",undo=lambda:self.restore_deleted(event,text))
        else:
            self.toast("Event deleted",undo=lambda:self.restore_deleted(event,text))

    def restore_deleted(self,event,text=None):
        if self.fixture:
            self.fixture.events=(*self.fixture.events,event)
            self.reload()
        else:
            def work():
                before,after=text
                ensure_backup(event.source_uid,"event-restore",backend.export_ics)
                return backend.restore_deletion(event.source_uid,before,after)
            self.task(work,lambda _:(self.reload(),self.toast("Event restored")))

    def rsvp(self,event,value):
        occurrence=event.rid or (event.instance_start.isoformat() if event.instance_start else "")
        pending_key=(event.source_uid,event.uid,occurrence)
        if pending_key in self._rsvp_pending:
            return
        if self.fixture:
            answer={"yes":"Going","maybe":"Maybe","no":"Can’t go"}[value]
            if self.answers.get(event.uid)==answer:
                return
            self.answers[event.uid]=answer
            self.fixture.events=tuple(replace(e,participants=tuple((name,answer if name=="You" else status) for name,status in e.participants)) if e.uid==event.uid else e for e in self.fixture.events)
            self.selected_event=next(e for e in self.fixture.events if e.uid==event.uid)
            self.refresh()
            self.toast({"yes":"You’re going","maybe":"You might go","no":"You can’t go. We’ll let them know."}[value])
        else:
            self._rsvp_pending.add(pending_key)
            def work():
                try:
                    reply_to_event(event,value)
                finally:
                    GLib.idle_add(lambda:(self._rsvp_pending.discard(pending_key),False)[1])
            self.task(work,lambda _:(self.reload(),self.toast("Response saved")))

    def share_event(self,event):
        from .sharing_data import event_copy
        from .sharing_files import share_calendar_copy
        def ready(content):
            self.share_sheet = share_calendar_copy(self, self.pane, event.summary, content)
        if self.fixture:
            ready(event_copy(event))
        else:
            self.task(lambda: backend.export_event_ics(event.source_uid, event.uid), ready)

    def contact_action(self,name,action,email=""):
        if self.fixture:
            self.toast("Opening Messages" if action in ("message","everyone") else "Opening Mail")
            return
        from urllib.parse import quote
        person=contact_for_person(self.people,name,email)
        if action=="message":
            if not person or not person.phone:
                self.toast("No phone number is available for this person",kind="warning")
                return
            try:
                uri = sms_uri(person.phone)
            except ValueError as error:
                self.toast(str(error),kind="warning")
                return
            _launch_uri(self,uri,"Messages could not open")
            return
        people=self.selected_event.attendees if self.selected_event else ()
        if action=="everyone":
            emails=[p.email for p in people]
        elif email:
            emails=[email]
        else:
            emails=list({p.email for p in people if p.name==name})
        if action != "everyone" and len(emails)>1:
            self.toast("More than one address matches this person",kind="warning")
            return
        if not emails and person and person.email:
            emails=[person.email]
        if emails:
            _launch_uri(self,"mailto:"+quote(",".join(emails),safe="@,"),"Mail could not open")
        else:
            self.toast("No address is available for this person",kind="warning")

    def open_contact(self,name):
        if self.fixture:
            self.toast(f"Opening {name} in Contacts")
            return
        from luma_appkit.application_directory import launch
        launch('org.projectluma.Contacts.desktop', callback=lambda ok, error:
               self.toast(error or 'Contacts could not open', kind='warning') if not ok else None)

    def directions(self,location):
        if self.fixture:
            self.toast("Opening directions in Maps")
            return
        from urllib.parse import quote
        _launch_uri(self,"geo:0,0?q="+quote(location,safe=""),"Maps could not open")

    def join_call(self,event):
        if self.fixture:
            self.toast("Joining the call…")
        elif event.url.startswith(("https://","http://")):
            _launch_uri(self,event.url,"Call link could not open")

    def add_calendar(self):
        if self.fixture:
            self.toast("Subscribe to a calendar")
        else:
            from .calendar_editor import open_online_accounts
            if not open_online_accounts():
                self.toast("Calendar account settings could not open",kind="warning")

    def task(self,work,done):
        def execute():
            try:
                result,error=work(),None
            except Exception as exception:
                result,error=None,exception
            def apply():
                self._saving=False
                if not self._closing:
                    if isinstance(error,(CalendarDeliveryError,backend.CalendarPartialSave)):
                        done(error.result)
                        self.toast(str(error),kind="warning")
                    elif error:
                        self.toast(str(error),kind="error")
                    else:
                        done(result)
                return False
            GLib.idle_add(apply)
        threading.Thread(target=execute,daemon=True,name="calendar-write").start()

    def toast(self,text,**kwargs):
        Toast.show(self.host,text,**kwargs)

    def find_name(self,name):
        def find(widget):
            if widget.get_name()==name:
                return widget
            child=widget.get_first_child()
            while child:
                result=find(child)
                if result:
                    return result
                child=child.get_next_sibling()
        return find(self)

    def _key(self,_controller,key,_code,state):
        focus=self.get_focus()
        if isinstance(focus,(Gtk.Editable,Gtk.TextView)):
            return False
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        key=Gdk.keyval_to_lower(key)
        if key in (Gdk.KEY_n,Gdk.KEY_c):
            self.quick.focus()
            return True
        if key in (Gdk.KEY_1,Gdk.KEY_2,Gdk.KEY_3,Gdk.KEY_f,Gdk.KEY_w,Gdk.KEY_m):
            self.set_view({Gdk.KEY_1:"flow",Gdk.KEY_2:"week",Gdk.KEY_3:"month",
                           Gdk.KEY_f:"flow",Gdk.KEY_w:"week",Gdk.KEY_m:"month"}[key])
            return True
        if key==Gdk.KEY_t:
            self.go_today()
            return True
        if key in (Gdk.KEY_Page_Up,Gdk.KEY_Page_Down):
            self.step(-1 if key==Gdk.KEY_Page_Up else 1)
            return True
        return False

    def _close(self,*_):
        self._closing=True
        self.generation+=1
        self.surface.clear()
        self.calendar_marks.clear()
        return False
