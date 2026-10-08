# SPDX-License-Identifier: Apache-2.0

"""The event editor, the repeating-event scope question, and adding calendars.

What a person expects from any calendar editor, and what this one does:

* Dates are picked from a calendar, never typed as a format; times follow the
  system's 12- or 24-hour setting and accept what people actually type
  ("9", "930", "9:30p", "21:30", "noon").
* Moving the start keeps the length: the end follows.
* All-day hides the times, and its end date is inclusive on screen (the
  exclusive iCalendar end is the backend's business).
* A timed event carries a time zone, defaulting to the system's.
* Repeat and alert choices are the common ones; a rule or alert set Luma did
  not write is shown as kept, never silently simplified.
* A repeating event asks "only this event, this and following, or all".
"""

from __future__ import annotations

from datetime import date, datetime, time as clock_time, timedelta
import os
import re
import subprocess
import threading
import urllib.request
from zoneinfo import ZoneInfo, available_timezones

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .calendar_backend import (  # noqa: E402
    ALERTS, REPEAT_CUSTOM, REPEATS, SCOPE_ALL, SCOPE_FUTURE, SCOPE_THIS, TONES,
    CalendarSource, Event, EventDraft, local_tzid,
)

SLOT_MINUTES = 15


# ── Time, the way this person reads it ────────────────────────────────────

def uses_24_hour() -> bool:
    """GNOME's clock-format setting, then the locale."""
    override = os.environ.get("LUMA_CLOCK_HOUR_FORMAT", "").strip()
    if override in {"12", "24"}:
        return override == "24"
    try:
        schema = Gio.SettingsSchemaSource.get_default().lookup("org.gnome.desktop.interface", True)
        if schema is not None and schema.has_key("clock-format"):
            return Gio.Settings.new("org.gnome.desktop.interface").get_string("clock-format") == "24h"
    except Exception:
        pass
    try:
        from .clock_backend import uses_24_hour as locale_24_hour
        return locale_24_hour()
    except Exception:
        return False


def format_time(moment: datetime | clock_time, hour24: bool | None = None) -> str:
    hour24 = uses_24_hour() if hour24 is None else hour24
    if hour24:
        return f"{moment.hour:02d}:{moment.minute:02d}"
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def format_short_time(moment: datetime, hour24: bool | None = None) -> str:
    """For chips: 9 AM, 9:30 AM, 14:00."""
    hour24 = uses_24_hour() if hour24 is None else hour24
    if hour24:
        return f"{moment.hour:02d}:{moment.minute:02d}"
    hour = moment.hour % 12 or 12
    suffix = "AM" if moment.hour < 12 else "PM"
    return f"{hour} {suffix}" if moment.minute == 0 else f"{hour}:{moment.minute:02d} {suffix}"


def parse_time(text: str) -> clock_time | None:
    """What people type into a time field."""
    raw = (text or "").strip().lower().replace(".", ":")
    if not raw:
        return None
    if raw in {"noon", "midday"}:
        return clock_time(12, 0)
    if raw == "midnight":
        return clock_time(0, 0)
    match = re.fullmatch(r"(\d{1,2})(?::?(\d{2}))?\s*(a|am|p|pm)?", raw)
    if not match:
        return None
    hour, minute, meridiem = int(match.group(1)), int(match.group(2) or 0), match.group(3)
    if minute > 59:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem.startswith("p") else 0)
    elif hour > 23:
        return None
    return clock_time(hour, minute)


def month_first() -> bool:
    """Whether this locale writes the month before the day (en_US: 9/12/26)."""
    import locale as _locale
    try:
        _locale.setlocale(_locale.LC_TIME, "")
    except _locale.Error:
        pass
    try:
        pattern = _locale.nl_langinfo(_locale.D_FMT)
    except (AttributeError, ValueError):
        return True
    month, day = pattern.find("%m"), pattern.find("%d")
    if month < 0 or day < 0:
        return pattern.find("%b") < pattern.find("%e") if "%b" in pattern else True
    return month < day


def format_date(day: date) -> str:
    return day.strftime("%a, %b %-d, %Y" if month_first() else "%a %-d %b %Y")


def long_date(day: date) -> str:
    return day.strftime("%A, %B %-d, %Y" if month_first() else "%A %-d %B %Y")


def short_date(day: date) -> str:
    return day.strftime("%b %-d" if month_first() else "%-d %b")


def zone_names() -> list[str]:
    local = local_tzid()
    names = sorted(name for name in available_timezones()
                   if "/" in name and not name.startswith(("Etc/", "SystemV/", "posix/", "right/")))
    ordered = [local] + [name for name in names if name != local]
    if "UTC" not in ordered:
        ordered.append("UTC")
    return ordered


def zone_title(name: str) -> str:
    if "/" not in name:
        return name
    area, _, city = name.partition("/")
    return f"{city.replace('_', ' ').replace('/', ' – ')} ({area})"


# ── Small fields ──────────────────────────────────────────────────────────

class DateField(Gtk.MenuButton):
    def __init__(self, value: date, on_change) -> None:
        super().__init__()
        self.add_css_class("flat")
        self.add_css_class("cal-date-field")
        self._on_change = on_change
        self.calendar = Gtk.Calendar()
        popover = Gtk.Popover()
        popover.set_child(self.calendar)
        self.set_popover(popover)
        self.value = value
        self._show()
        self.calendar.connect("day-selected", self._picked)

    def _show(self) -> None:
        self.set_label(format_date(self.value))
        self.calendar.select_day(GLib.DateTime.new_local(self.value.year, self.value.month, self.value.day, 0, 0, 0))

    def _picked(self, calendar: Gtk.Calendar) -> None:
        picked = calendar.get_date()
        value = date(picked.get_year(), picked.get_month(), picked.get_day_of_month())
        if value != self.value:
            self.value = value
            self.set_label(format_date(value))
            self._on_change(self)
        self.get_popover().popdown()

    def set_value(self, value: date) -> None:
        self.value = value
        self._show()


class TimeField(Gtk.Box):
    """A typeable time with a list of quarter hours to pick from."""

    def __init__(self, value: clock_time, on_change) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.add_css_class("linked")
        self._on_change = on_change
        self.value = value
        self.entry = Gtk.Entry(width_chars=8, max_width_chars=9)
        self.entry.set_text(format_time(value))
        self.entry.update_property([Gtk.AccessibleProperty.LABEL], ["Time"])
        self.entry.connect("activate", lambda *_: self._commit())
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: self._commit())
        self.entry.add_controller(focus)
        self.append(self.entry)
        self.button = Gtk.MenuButton(icon_name="pan-down-symbolic")
        self.button.update_property([Gtk.AccessibleProperty.LABEL], ["Choose a time"])
        popover = Gtk.Popover()
        self.slots = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.slots.add_css_class("navigation-sidebar")
        for minutes in range(0, 24 * 60, SLOT_MINUTES):
            slot = clock_time(minutes // 60, minutes % 60)
            row = Gtk.ListBoxRow()
            row.set_child(Gtk.Label(label=format_time(slot), xalign=0, margin_start=8, margin_end=8))
            row.slot = slot
            self.slots.append(row)
        self.slots.connect("row-activated", self._picked)
        scroller = Gtk.ScrolledWindow(min_content_height=240, max_content_height=240,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.slots)
        popover.set_child(scroller)
        popover.connect("show", lambda *_: self._scroll_to_value(scroller))
        self.button.set_popover(popover)
        self.append(self.button)

    def _scroll_to_value(self, scroller: Gtk.ScrolledWindow) -> None:
        index = (self.value.hour * 60 + self.value.minute) // SLOT_MINUTES
        row = self.slots.get_row_at_index(index)
        if row is not None:
            self.slots.select_row(row)
            GLib.idle_add(lambda: (scroller.get_vadjustment().set_value(max(0, index * 34 - 90)), False)[1])

    def _picked(self, _box, row) -> None:
        self.set_value(row.slot)
        self.button.get_popover().popdown()
        self._on_change(self)

    def _commit(self) -> None:
        parsed = parse_time(self.entry.get_text())
        if parsed is None:
            self.entry.add_css_class("error")
            self.entry.set_text(format_time(self.value))
            GLib.timeout_add(1200, lambda: (self.entry.remove_css_class("error"), False)[1])
            return
        changed = parsed != self.value
        self.set_value(parsed)
        if changed:
            self._on_change(self)

    def set_value(self, value: clock_time) -> None:
        self.value = value
        self.entry.set_text(format_time(value))


def _combo(title: str, labels: list[str], selected: int = 0, *, search: bool = False) -> Adw.ComboRow:
    row = Adw.ComboRow(title=title)
    row.set_model(Gtk.StringList.new(labels))
    if search:
        row.set_enable_search(True)
        row.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
    row.set_selected(max(0, selected))
    return row


# ── The editor ────────────────────────────────────────────────────────────

class EventEditor(Adw.Dialog):
    def __init__(self, *, sources: list[CalendarSource], event: Event | None, day: date,
                 default_source: str, on_save) -> None:
        super().__init__(title="Edit Event" if event else "New Event", content_width=460, content_height=680)
        self.add_css_class("cal-editor")
        self.sources = sources
        self.event = event
        self._on_save = on_save

        if event is not None:
            start, end = event.start, event.end
            all_day = event.all_day
            if all_day:
                end = end - timedelta(days=1) if end.date() > start.date() else end
        else:
            now = datetime.now().astimezone()
            if day == now.date():
                start = (now + timedelta(minutes=30 - now.minute % 30)).replace(second=0, microsecond=0)
            else:
                start = datetime.combine(day, clock_time(9, 0)).astimezone()
            end = start + timedelta(hours=1)
            all_day = False
        self.tzid = (event.tzid if event and event.tzid else local_tzid())
        zone = ZoneInfo(self.tzid) if self.tzid != "UTC" else ZoneInfo("UTC")
        if not all_day:
            start, end = start.astimezone(zone), end.astimezone(zone)
        self._length = max(end - start, timedelta(minutes=0))

        view = Adw.ToolbarView()
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)
        self.save_button = Gtk.Button(label="Save" if event else "Add")
        self.save_button.add_css_class("suggested-action")
        self.save_button.connect("clicked", lambda *_: self._save())
        header.pack_end(self.save_button)
        view.add_top_bar(header)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                       margin_top=12, margin_bottom=18, margin_start=18, margin_end=18)

        basics = Adw.PreferencesGroup()
        self.title_row = Adw.EntryRow(title="Title", text=event.summary if event else "")
        self.title_row.connect("changed", lambda *_: self._validate())
        self.title_row.connect("entry-activated", lambda *_: self._save())
        basics.add(self.title_row)
        self.location_row = Adw.EntryRow(title="Location", text=event.location if event else "")
        basics.add(self.location_row)
        page.append(basics)

        timing = Adw.PreferencesGroup()
        self.all_day_row = Adw.SwitchRow(title="All-day", active=all_day)
        self.all_day_row.connect("notify::active", lambda *_: self._all_day_changed())
        timing.add(self.all_day_row)
        self.start_date = DateField(start.date(), self._start_changed)
        self.start_time = TimeField(start.timetz().replace(tzinfo=None), self._start_changed)
        self.end_date = DateField(end.date(), self._end_changed)
        self.end_time = TimeField(end.timetz().replace(tzinfo=None), self._end_changed)
        starts = Adw.ActionRow(title="Starts")
        starts.add_suffix(self.start_date)
        starts.add_suffix(self.start_time)
        ends = Adw.ActionRow(title="Ends")
        ends.add_suffix(self.end_date)
        ends.add_suffix(self.end_time)
        timing.add(starts)
        timing.add(ends)
        self.zones = zone_names()
        if self.tzid not in self.zones:
            self.zones.insert(0, self.tzid)
        self.zone_row = _combo("Time zone", [zone_title(name) for name in self.zones],
                               self.zones.index(self.tzid), search=True)
        timing.add(self.zone_row)
        self.problem = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.problem.add_css_class("error")
        self.problem.add_css_class("caption")

        repeat_keys = [key for key, _label, _rule in REPEATS]
        repeat_labels = [label for _key, label, _rule in REPEATS]
        current_repeat = event.repeat if event else "none"
        if current_repeat == REPEAT_CUSTOM:
            repeat_keys.insert(0, REPEAT_CUSTOM)
            repeat_labels.insert(0, event.repeat_label or "Custom (kept as is)")
        self.repeat_keys = repeat_keys
        self.repeat_row = _combo("Repeat", repeat_labels, repeat_keys.index(current_repeat) if current_repeat in repeat_keys else 0)
        timing.add(self.repeat_row)

        alert_values = [value for value, _label in ALERTS]
        alert_labels = [label for _value, label in ALERTS]
        current_alerts = tuple(event.alerts) if event else ()
        self.kept_alerts: tuple[int, ...] | None = None
        if len(current_alerts) > 1 or (len(current_alerts) == 1 and current_alerts[0] not in alert_values):
            self.kept_alerts = current_alerts
            alert_values.insert(0, "kept")
            alert_labels.insert(0, f"{len(current_alerts)} alerts (kept as is)")
            selected_alert = 0
        else:
            selected_alert = alert_values.index(current_alerts[0]) if current_alerts else 0
        self.alert_values = alert_values
        self.alert_row = _combo("Alert", alert_labels, selected_alert)
        timing.add(self.alert_row)
        page.append(timing)
        page.append(self.problem)

        placement = Adw.PreferencesGroup()
        self.source_uids = [source.uid for source in sources]
        wanted = event.source_uid if event else default_source
        self.source_row = _combo("Calendar", [source.name for source in sources],
                                 self.source_uids.index(wanted) if wanted in self.source_uids else 0)
        if event is not None and event.recurring:
            # Moving one occurrence of a series to another calendar is not a
            # thing iCalendar can express; the series moves as a whole.
            self.source_row.set_sensitive(False)
        placement.add(self.source_row)
        self.url_row = Adw.EntryRow(title="URL", text=event.url if event else "")
        self.url_row.set_input_purpose(Gtk.InputPurpose.URL)
        placement.add(self.url_row)
        page.append(placement)

        notes = Adw.PreferencesGroup(title="Notes")
        frame = Gtk.Frame()
        self.notes = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=10, bottom_margin=10,
                                  left_margin=12, right_margin=12, accepts_tab=False)
        self.notes.set_size_request(-1, 110)
        self.notes.get_buffer().set_text(event.description if event else "")
        self.notes.update_property([Gtk.AccessibleProperty.LABEL], ["Notes"])
        frame.set_child(self.notes)
        notes.add(frame)
        page.append(notes)

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(page)
        view.set_content(scroller)
        self.set_child(view)
        self._all_day_changed()
        self._validate()
        self.set_focus(self.title_row)

    # -- the rules between fields -------------------------------------------
    def _moments(self) -> tuple[datetime, datetime]:
        if self.all_day_row.get_active():
            return (datetime.combine(self.start_date.value, clock_time()),
                    datetime.combine(self.end_date.value, clock_time()))
        zone = ZoneInfo(self.zones[self.zone_row.get_selected()])
        return (datetime.combine(self.start_date.value, self.start_time.value, zone),
                datetime.combine(self.end_date.value, self.end_time.value, zone))

    def _start_changed(self, _field) -> None:
        start, _end = self._moments()
        end = start + self._length
        self.end_date.set_value(end.date())
        self.end_time.set_value(end.time())
        self._validate()

    def _end_changed(self, _field) -> None:
        start, end = self._moments()
        if end >= start:
            self._length = end - start
        self._validate()

    def _all_day_changed(self) -> None:
        all_day = self.all_day_row.get_active()
        for widget in (self.start_time, self.end_time, self.zone_row):
            widget.set_visible(not all_day)
        self._validate()

    def _validate(self) -> bool:
        start, end = self._moments()
        problem = ""
        if end < start:
            problem = "The event ends before it starts."
        elif not self.all_day_row.get_active() and end == start and self._length:
            problem = ""
        self.problem.set_label(problem)
        self.problem.set_visible(bool(problem))
        ok = bool(self.title_row.get_text().strip()) and not problem
        self.save_button.set_sensitive(ok)
        return ok

    def _save(self) -> None:
        if not self._validate():
            return
        start, end = self._moments()
        buffer = self.notes.get_buffer()
        alert = self.alert_values[self.alert_row.get_selected()]
        alerts = self.kept_alerts if alert == "kept" else (() if alert is None else (int(alert),))
        event = self.event
        draft = EventDraft(
            source_uid=self.source_uids[self.source_row.get_selected()],
            summary=self.title_row.get_text().strip(),
            start=start, end=end, all_day=self.all_day_row.get_active(),
            tzid=self.zones[self.zone_row.get_selected()],
            location=self.location_row.get_text().strip(),
            description=buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).strip(),
            url=self.url_row.get_text().strip(),
            repeat=self.repeat_keys[self.repeat_row.get_selected()],
            alerts=tuple(alerts or ()),
            uid=event.uid if event else "",
            rid=event.rid if event else "",
            instance_start=event.instance_start if event else None,
        )
        if self._on_save(draft, event) is not False:
            self.close()


def ask_scope(parent: Gtk.Widget, *, deleting: bool, on_choice) -> None:
    """This event, this and following, or all — asked the way every calendar asks."""
    verb = "Delete" if deleting else "Change"
    dialog = Adw.AlertDialog(
        heading="Repeating Event",
        body=f"{verb} only this event, this and all following events, or every event in the series?",
    )
    dialog.add_response("cancel", "Cancel")
    dialog.add_response(SCOPE_THIS, "Only This Event")
    dialog.add_response(SCOPE_FUTURE, "This and Following")
    dialog.add_response(SCOPE_ALL, "All Events")
    dialog.set_response_appearance(SCOPE_THIS, Adw.ResponseAppearance.DESTRUCTIVE if deleting else Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response(SCOPE_THIS)
    dialog.set_close_response("cancel")
    dialog.set_prefer_wide_layout(True)
    dialog.connect("response", lambda _d, response: response != "cancel" and on_choice(response))
    dialog.present(parent)


# ── Adding calendars ──────────────────────────────────────────────────────

def _labelled_entry(title: str, text: str = "", *, password: bool = False) -> Adw.EntryRow:
    row = Adw.PasswordEntryRow(title=title) if password else Adw.EntryRow(title=title)
    row.set_text(text)
    return row


def calendar_form(parent: Gtk.Widget, *, heading: str, body: str, rows: list[Gtk.Widget],
                  action: str, on_submit) -> None:
    dialog = Adw.AlertDialog(heading=heading, body=body)
    group = Adw.PreferencesGroup()
    for row in rows:
        group.add(row)
    dialog.set_extra_child(group)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("go", action)
    dialog.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("go")
    dialog.set_close_response("cancel")
    dialog.connect("response", lambda _d, response: response == "go" and on_submit())
    dialog.present(parent)


def tone_row(selected: str = "blue") -> Adw.ComboRow:
    return _combo("Colour", [tone.title() for tone in TONES], TONES.index(selected) if selected in TONES else 0)


def probe_ics(url: str, timeout: float = 15.0) -> str:
    """Fetch a published calendar far enough to know it is one; returns its name."""
    address = url.strip()
    if address.lower().startswith("webcal://"):
        address = "https://" + address[len("webcal://"):]
    if not re.match(r"^https?://", address, re.I):
        raise ValueError("Enter a web address that starts with https:// or webcal://.")
    request = urllib.request.Request(address, headers={"User-Agent": "ProjectLuma-Calendar/1", "Accept": "text/calendar"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        head = response.read(65536).decode("utf-8", "replace")
    if "BEGIN:VCALENDAR" not in head:
        raise ValueError("That address did not return a calendar (.ics).")
    match = re.search(r"^X-WR-CALNAME:(.+)$", head, re.M)
    return match.group(1).strip() if match else ""


def run_in_thread(work, done) -> None:
    def runner() -> None:
        try:
            result, error = work(), None
        except Exception as problem:  # reported to the person, not swallowed
            result, error = None, problem
        GLib.idle_add(lambda: (done(result, error), False)[1])
    threading.Thread(target=runner, daemon=True).start()


def open_online_accounts() -> bool:
    """GNOME Online Accounts: where Google, Microsoft and Nextcloud accounts live."""
    for command in (["gnome-control-center", "online-accounts"], ["gnome-online-accounts-gtk"]):
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True
        except FileNotFoundError:
            continue
    return False
