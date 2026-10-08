# SPDX-License-Identifier: Apache-2.0

"""Calendar application entry, reminder routing and legacy date helpers."""

from __future__ import annotations

import ctypes
from datetime import date, datetime, timedelta
import locale as locale_module
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from luma_appkit import add_style_sheet, install_appkit, install_lumaui
from .calendar_backend import ALERTS, Event
from .calendar_editor import format_short_time, format_time, short_date


APP_ID = "org.projectluma.Calendar"


def week_start() -> int:
    """The first day of the week, as a Python weekday (0 = Monday).

    The locale is the default because that is what the person's system already
    says; `LUMA_WEEK_START` overrides it for anyone whose habit differs from
    their locale. Every surface in this application reads this one function.
    """
    if os.environ.get("LUMA_CALENDAR_FIXTURE"):
        return 0  # v70 always starts on Monday, regardless of host locale
    override = os.environ.get("LUMA_WEEK_START", "").strip().lower()
    names = {"monday": 0, "sunday": 6, "saturday": 5}
    if override in names:
        return names[override]
    if override.isdigit() and 0 <= int(override) <= 6:
        return int(override)
    try:
        # glibc states it as 1 = Sunday … 7 = Saturday, but only once the
        # process has adopted the environment's locale: the default C locale
        # answers Monday for everybody.
        try:
            locale_module.setlocale(locale_module.LC_TIME, "")
        except locale_module.Error:
            pass
        libc = ctypes.cdll.LoadLibrary("libc.so.6")
        libc.nl_langinfo.restype = ctypes.c_char_p
        value = libc.nl_langinfo(0x20017)
        if not isinstance(value, bytes) or not value:
            return 6
        # The answer arrives as one byte that is either the number itself or
        # the ASCII digit for it, depending on the C library. en_US answers
        # b"1" — reading that as 49 is how a calendar ends up starting the
        # week on the wrong day.
        first = value[0]
        if 0x31 <= first <= 0x37:
            first -= 0x30
        if not 1 <= first <= 7:
            return 6
        return (first + 5) % 7
    except Exception:
        return 6


def week_days() -> tuple[int, ...]:
    """The seven weekdays in the order this application shows them."""
    start = week_start()
    return tuple((start + offset) % 7 for offset in range(7))


def start_of_week(day: date) -> date:
    return day - timedelta(days=(day.weekday() - week_start()) % 7)


def month_grid(anchor: date) -> tuple[date, ...]:
    """Six rows of seven, the way the design draws a month."""
    first = anchor.replace(day=1)
    origin = start_of_week(first)
    return tuple(origin + timedelta(days=offset) for offset in range(42))


def _time_text(moment: datetime) -> str:
    """Follows the system's 12- or 24-hour clock."""
    return format_short_time(moment)


def _range_text(event: Event) -> str:
    if event.all_day:
        return "All day"
    if event.end.date() != event.start.date():
        return f"{format_time(event.start)} – {short_date(event.end)}, {format_time(event.end)}"
    return f"{format_time(event.start)} – {format_time(event.end)}"


def _alert_text(minutes: tuple[int, ...]) -> str:
    labels = dict(ALERTS)
    return ", ".join(labels.get(value, f"{value} minutes before") for value in minutes)


from .calendar_window import CalendarWindow


class CalendarApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        install_appkit()
        install_lumaui()
        fixture=os.environ.get("LUMA_CALENDAR_FIXTURE")
        if fixture:
            from pathlib import Path
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(Path(fixture).parent/"calendar-v70-data/icons"))
        _install_calendar_style()

    def do_activate(self) -> None:
        (self.props.active_window or CalendarWindow(self)).present()

    def do_command_line(self, command_line) -> int:
        # A reminder from the Calendar agent opens its event:
        # --event-uid=<uid> --event-day=<YYYY-MM-DD>
        options = dict(item[2:].split("=", 1) for item in command_line.get_arguments()[1:]
                       if item.startswith("--event-") and "=" in item)
        self.activate()
        window = self.props.active_window
        if options.get("event-uid") and options.get("event-day") and isinstance(window, CalendarWindow):
            try:
                window.show_event(options["event-uid"], date.fromisoformat(options["event-day"]))
            except ValueError:
                pass
        return 0


def _install_calendar_style() -> None:
    if Gdk.Display.get_default() is not None:
        add_style_sheet(os.environ.get("LUMA_CALENDAR_STYLE_PATH", "/usr/share/prairie-core/calendar.css"))


def main() -> int:
    # Only the reminder options are passed on; GApplication's own are not offered.
    return CalendarApplication().run(sys.argv[:1] + [item for item in sys.argv[1:] if item.startswith("--event-")])


if __name__ == "__main__":
    raise SystemExit(main())
