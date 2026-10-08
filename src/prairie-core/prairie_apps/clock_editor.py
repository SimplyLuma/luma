# SPDX-License-Identifier: Apache-2.0
"""Clock's alarm editor: what the bar grows into for New alarm and for an alarm you tap (v71 `.ckal2`).

Three wheels (hour, minutes in fives, AM/PM) over an inset band, the repeat
as seven round day toggles, a Snooze switch, Delete alarm when editing, and
the Label field last, closest to the thumb. The wheels are Clock’s own
instrument; every row and field around them is a LumaUI part.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from luma_appkit import PanelField, PanelHeading, PanelRow, RichMenuItem, TypeLabel  # noqa: E402

from luma_appkit.structure_adapt import WidthWatch  # noqa: E402

from .clock_backend import Alarm  # noqa: E402

DAY_LETTERS = ("M", "T", "W", "T", "F", "S", "S")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
#: The wheel's rows: two above the chosen one, two below (v71: 5 × 40 = 200 tall).
WHEEL_ROWS = 5
WHEEL_STEP = 40
SNOOZE_MINUTES = 9


class TimeWheel(Gtk.Box):
    """One scrolling wheel: the chosen value in the band, two neighbours either side fading out.

    Scroll, drag (a step per 40 px) or the arrow keys turn it. It reads to a
    screen reader as a spin button with its value.
    """

    __gtype_name__ = "ClockTimeWheel"

    def __init__(self, name: str, values: Sequence[str], current: str,
                 on_change: Callable[[str], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, focusable=True,
                         accessible_role=Gtk.AccessibleRole.SPIN_BUTTON)
        self.add_css_class("ck-wheel")
        self.set_name(f"ck-wheel-{name}")
        self.values = list(values)
        self.index = self.values.index(current) if current in self.values else 0
        self.on_change = on_change
        self._drag_rows = 0
        self.rows: list[TypeLabel] = []
        for _ in range(WHEEL_ROWS):
            row = TypeLabel("", role="numeric")
            row.set_size_request(-1, WHEEL_STEP)
            row.set_valign(Gtk.Align.CENTER)
            row.set_halign(Gtk.Align.CENTER)
            row.add_css_class("ck-wheel-item")
            self.rows.append(row)
            self.append(row)
        self.update_property([Gtk.AccessibleProperty.LABEL], [name.capitalize()])
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL
                                           | Gtk.EventControllerScrollFlags.DISCRETE)
        scroll.connect("scroll", lambda _c, _dx, dy: self._turn(1 if dy > 0 else -1) or True)
        self.add_controller(scroll)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda *_a: setattr(self, "_drag_rows", 0))
        drag.connect("drag-update", self._dragged)
        self.add_controller(drag)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda *_a: self.grab_focus())
        self.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self._show()

    @property
    def value(self) -> str:
        return self.values[self.index]

    def _dragged(self, _gesture, _dx: float, dy: float) -> None:
        rows = int(-dy / WHEEL_STEP)
        if rows != self._drag_rows:
            self._turn(rows - self._drag_rows)
            self._drag_rows = rows

    def _key(self, _controller, keyval: int, _code: int, _state) -> bool:
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            self._turn(-1)
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            self._turn(1)
            return True
        return False

    def _turn(self, steps: int) -> None:
        index = max(0, min(len(self.values) - 1, self.index + steps))
        if index != self.index:
            self.index = index
            self._show()
            self.on_change(self.value)

    def _show(self) -> None:
        middle = WHEEL_ROWS // 2
        for offset, row in enumerate(self.rows):
            at = self.index + offset - middle
            row.set_text(self.values[at] if 0 <= at < len(self.values) else "")
            for css, on in (("on", offset == middle), ("near", abs(offset - middle) == 1),
                            ("far", abs(offset - middle) == 2)):
                (row.add_css_class if on else row.remove_css_class)(css)
        self.update_property([Gtk.AccessibleProperty.VALUE_TEXT], [self.value])


class AlarmForm(Gtk.Box):
    """The editor's content. `alarm` is None for a new alarm; `result()` is the alarm to save."""

    __gtype_name__ = "ClockAlarmForm"

    def __init__(self, alarm: Alarm | None, *, uid: str, hour24: bool,
                 on_delete: Callable[[], None] | None, on_submit: Callable[[], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.set_name("ck-alarm-editor")
        self.append(PanelHeading("Edit alarm" if alarm is not None else "New alarm"))
        self.base = alarm or Alarm(uid, "Alarm", 7, 0, (), True)
        self.hour24 = hour24
        self.hour, self.minute = self.base.hour, self.base.minute
        self.days = set(self.base.days)
        self.snooze = self.base.snooze_minutes > 0

        # The wheels over the band.
        band_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        band_holder.set_size_request(-1, WHEEL_ROWS * WHEEL_STEP)
        band = Gtk.Box(valign=Gtk.Align.CENTER, vexpand=True, can_target=False)
        band.set_name("ck-wheel-band")
        band.set_size_request(-1, WHEEL_STEP)
        band_holder.append(band)
        wheels = Gtk.Box(halign=Gtk.Align.CENTER, spacing=4)
        wheels.set_name("ck-wheels")
        if hour24:
            hours = [f"{h:02d}" for h in range(24)]
            self.hour_wheel = TimeWheel("hour", hours, f"{self.hour:02d}", self._hour)
        else:
            hours = [str(h) for h in range(1, 13)]
            self.hour_wheel = TimeWheel("hour", hours, str(self.hour % 12 or 12), self._hour)
        minutes = sorted({m for m in range(0, 60, 5)} | {self.minute})
        self.minute_wheel = TimeWheel("minutes", [f"{m:02d}" for m in minutes], f"{self.minute:02d}", self._minute)
        colon = TypeLabel(":", role="numeric")
        colon.add_css_class("ck-wheel-colon")
        wheels.append(self.hour_wheel)
        wheels.append(colon)
        wheels.append(self.minute_wheel)
        self.period_wheel = None
        if not hour24:
            self.period_wheel = TimeWheel("period", ["AM", "PM"], "AM" if self.hour < 12 else "PM", self._period)
            self.period_wheel.add_css_class("period")
            wheels.append(self.period_wheel)
        stage = Gtk.Overlay(child=band_holder)
        stage.add_overlay(wheels)
        stage.set_measure_overlay(wheels, True)
        self.append(stage)

        # Repeat: seven round toggles.
        days = Gtk.Box(homogeneous=True, spacing=6)
        days.set_name("ck-days")
        days.update_property([Gtk.AccessibleProperty.LABEL], ["Repeat"])
        self.day_buttons: list[Gtk.ToggleButton] = []
        for index, letter in enumerate(DAY_LETTERS):
            button = Gtk.ToggleButton(label=letter, active=index in self.days, halign=Gtk.Align.CENTER)
            button.add_css_class("ck-day")
            button.set_name(f"ck-day-{index}")
            button.set_tooltip_text(DAY_NAMES[index])
            button.update_property([Gtk.AccessibleProperty.LABEL], [DAY_NAMES[index]])
            button.connect("toggled", self._day, index)
            self.day_buttons.append(button)
            days.append(button)
        self.append(days)
        # Seven 40px targets need 316px at the regular 6px gap. On the
        # smallest phones, reduce only the gaps so the editor keeps its gutter.
        self._day_width = WidthWatch(self, lambda width: days.set_spacing(4 if width < 368 else 6),
                                     threshold=367)

        # Snooze, then Delete alarm when editing (v71 .fexr2 rows: LumaUI menu rows).
        snooze = RichMenuItem("Snooze", icon="alarm-clock", toggle=self.snooze,
                              on_toggle=lambda on: setattr(self, "snooze", on))
        self.snooze_row = snooze.menu_widget(lambda: None)
        self.snooze_row.set_name("ck-alarm-snooze")
        self.append(self.snooze_row)
        self.delete_row = None
        if on_delete is not None:
            self.delete_row = PanelRow("Delete alarm", icon="trash-2", danger=True, closes=False,
                                       on_activate=on_delete)
            self.delete_row.set_name("ck-alarm-delete")
            self.append(self.delete_row)

        # The Label field is last: text entry is the bottom-most thing (v71).
        self.label_entry = PanelField("tag", "Label", text=self.base.label, on_submit=lambda _text: on_submit())
        self.label_entry.set_name("ck-alarm-label")
        self.append(self.label_entry)

    # ── the wheels ────────────────────────────────────────────────────────

    def _hour(self, value: str) -> None:
        if self.hour24:
            self.hour = int(value)
        else:
            self.hour = int(value) % 12 + (12 if self.hour >= 12 else 0)

    def _minute(self, value: str) -> None:
        self.minute = int(value)

    def _period(self, value: str) -> None:
        self.hour = self.hour % 12 + (12 if value == "PM" else 0)

    def _day(self, button: Gtk.ToggleButton, index: int) -> None:
        (self.days.add if button.get_active() else self.days.discard)(index)

    # ── the result ────────────────────────────────────────────────────────

    def result(self) -> Alarm:
        label = self.label_entry.text.strip() or "Alarm"
        snooze = (self.base.snooze_minutes or SNOOZE_MINUTES) if self.snooze else 0
        return replace(self.base, label=label, hour=self.hour, minute=self.minute,
                       days=tuple(sorted(self.days)), enabled=True, snooze_minutes=snooze)
