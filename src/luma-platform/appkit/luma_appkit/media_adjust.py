# SPDX-License-Identifier: Apache-2.0
"""LumaUI image editing: ValueSlider, AdjustmentGroup and AdjustmentPanel (backlog IM1).

One adjustment is a label, a range and its value. The app says what it
adjusts and its range; the kit owns the layout, the fill from zero, the
signed readout, reset and the panel that holds them.

    exposure = ValueSlider("Exposure", 0, -100, 100, on_change=lambda v: photo.set("exposure", v))
    light = AdjustmentGroup("Light", [exposure, ValueSlider("Contrast"), …], open=True)       # Darkroom
    panel = AdjustmentPanel([("Light", [exposure, …]), ("Colour", […]), (None, [straighten])],
                            on_reset=photo.revert)                                          # Viewer
    panel.attach(image_overlay); panel.set_shown(True)

- `ValueSlider(label, value=0, minimum=-100, maximum=100, unit="", default=None, step=1,
  layout="row"|"stacked", on_change=, on_reset=)`. `row` is v70 `.drsl`
  (Darkroom, Reel): the name 92 px, the range, the value 40 px on one 30 px
  line; `stacked` is `.vwsl` (Viewer, Photos): the name and value over the
  range. A range that spans zero fills from zero; a value above zero reads
  "+12"; the unit follows ("−3 dB", "80%", "4°"). The name and value turn
  from muted to full ink once moved. Double-click (or Delete) puts the
  default back.
- `AdjustmentGroup(title, sliders, open=False, on_toggle=)`: `.drgrp`, a
  heading that opens and closes the group, with an amber dot while any of its
  sliders is moved.
- `AdjustmentPanel(sections, title="Adjust", on_reset=)`: `.vwadj`, a
  floating 240 px card (heading, Reset, section labels, the sliders) that
  slides in from the right; at phone width it is a sheet at the bottom edge,
  so the picture stays in view above it. `sections` holds
  `(heading or None, [ValueSlider])` pairs or AdjustmentGroups. Reset is
  enabled only while something is moved.

Keyboard: each range is a slider (arrows by a step, Page Up/Down by ten,
Home/End); group headings are buttons; Esc on the panel calls `on_close`.
"""
from __future__ import annotations

import math
from typing import Callable, Iterable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GObject, Gtk  # noqa: E402

from . import lumaui_tokens as tokens  # noqa: E402
from . import media_style as style  # noqa: E402

__all__ = ["ValueRange", "ValueSlider", "AdjustmentGroup", "AdjustmentPanel", "value_text", "SLIDER_LAYOUTS"]

A = tokens.MEDIA["adjust"]

#: How a slider sets itself out: Darkroom's one-line row, or Viewer's name-over-range.
SLIDER_LAYOUTS = ("row", "stacked", "editor")

MINUS = "−"


def value_text(value: float, minimum: float, unit: str = "", step: float = 1) -> str:
    """A slider's readout: "+12" above zero on a range that spans it, "−3 dB", "80%", "0"."""
    decimals = 0 if float(step).is_integer() else max(0, -int(math.floor(math.log10(abs(step)))))
    number = round(float(value), decimals)
    if number == 0:
        number = 0.0
    text = f"{abs(number):.{decimals}f}"
    sign = "+" if number > 0 and minimum < 0 else MINUS if number < 0 else ""
    joiner = "" if unit in ("%", "°", "×") or not unit else " "
    return f"{sign}{text}{joiner}{unit}"


def fill_span(value: float, minimum: float, maximum: float) -> tuple[float, float]:
    """The filled part of a range as fractions (v70 `rngPaint`): from zero when it spans zero, else from the start."""
    span = (maximum - minimum) or 1.0

    def at(x: float) -> float:
        return min(1.0, max(0.0, (x - minimum) / span))

    origin = at(0.0) if minimum < 0 < maximum else 0.0
    point = at(value)
    return min(origin, point), max(origin, point)


# ── The range ──────────────────────────────────────────────────────────────

class _Thumb(Gtk.Widget):
    """The range's key: a CSS node (`thumb`) so its gradient and edge are the key tokens."""

    __gtype_name__ = "LumaUIValueRangeThumb"


class ValueRange(Gtk.Widget):
    """A 16 px range: a 5 px well filled from zero to the value, and a key thumb. Emits `value-changed`."""

    __gtype_name__ = "LumaUIValueRange"
    __gsignals__ = {"value-changed": (GObject.SignalFlags.RUN_LAST, None, (float,))}

    def __init__(self, value: float = 0.0, minimum: float = -100.0, maximum: float = 100.0, step: float = 1.0,
                 label: str = "Value") -> None:
        super().__init__(css_name="lumaui-value-range", focusable=True, hexpand=True, overflow=Gtk.Overflow.VISIBLE)
        self.set_accessible_role(Gtk.AccessibleRole.SLIDER)
        self.minimum, self.maximum = float(minimum), float(maximum)
        if self.maximum <= self.minimum:
            raise ValueError("ValueRange needs maximum > minimum")
        self.step = float(step) or 1.0
        self.value = self.minimum
        self._thumb = _Thumb(css_name="thumb", can_target=False)
        self._thumb.set_parent(self)
        self.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.VALUE_MIN,
                              Gtk.AccessibleProperty.VALUE_MAX], [label, self.minimum, self.maximum])
        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", lambda _g, x, _y: self._pointer(x))
        drag.connect("drag-update", lambda g, dx, _dy: self._pointer(g.get_start_point()[1] + dx))
        self.add_controller(drag)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.set_value(value)

    def set_value(self, value: float, *, notify: bool = False) -> None:
        value = min(self.maximum, max(self.minimum, float(value) if math.isfinite(float(value)) else 0.0))
        value = round(value / self.step) * self.step
        changed = value != self.value
        self.value = value
        self.update_property([Gtk.AccessibleProperty.VALUE_NOW], [value])
        self.queue_allocate()
        self.queue_draw()
        if changed and notify:
            self.emit("value-changed", value)

    def set_value_text(self, text: str) -> None:
        self.update_property([Gtk.AccessibleProperty.VALUE_TEXT], [text])

    def _pointer(self, x: float) -> None:
        thumb = A["thumb"]
        width = max(1.0, self.get_width() - thumb)
        fraction = min(1.0, max(0.0, (x - thumb / 2) / width))
        self.grab_focus()
        self.set_value(self.minimum + fraction * (self.maximum - self.minimum), notify=True)

    def _key(self, _controller, keyval: int, _code: int, _state: Gdk.ModifierType) -> bool:
        steps = {Gdk.KEY_Left: -1, Gdk.KEY_Down: -1, Gdk.KEY_Right: 1, Gdk.KEY_Up: 1,
                 Gdk.KEY_Page_Down: -10, Gdk.KEY_Page_Up: 10}
        if keyval in steps:
            direction = -1 if self.get_direction() == Gtk.TextDirection.RTL and keyval in (Gdk.KEY_Left, Gdk.KEY_Right) else 1
            self.set_value(self.value + steps[keyval] * self.step * direction, notify=True)
            return True
        if keyval in (Gdk.KEY_Home, Gdk.KEY_End):
            self.set_value(self.minimum if keyval == Gdk.KEY_Home else self.maximum, notify=True)
            return True
        return False

    def do_measure(self, orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        if orientation == Gtk.Orientation.HORIZONTAL:
            return int(A["range_min_width"]), int(A["range_min_width"]) * 2, -1, -1
        return int(A["range_height"]), int(A["range_height"]), -1, -1

    def _fraction(self) -> float:
        return (self.value - self.minimum) / (self.maximum - self.minimum)

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        thumb = int(A["thumb"])
        fraction = self._fraction()
        if self.get_direction() == Gtk.TextDirection.RTL:
            fraction = 1 - fraction
        x = (width - thumb) * fraction
        rectangle = Gdk.Rectangle()
        rectangle.x, rectangle.y = int(round(x)), int(round((height - thumb) / 2))
        rectangle.width = rectangle.height = thumb
        self._thumb.size_allocate(rectangle, -1)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        if width <= 0:
            return
        track, thumb = float(A["track"]), float(A["thumb"])
        inner = width - thumb
        box = style.rect(0, (height - track) / 2, width, track)
        shape = style.rounded(box, A["track_radius"])
        snapshot.push_rounded_clip(shape)
        snapshot.append_color(style.colour(self, "luma_well"), box)
        a, b = fill_span(self.value, self.minimum, self.maximum)
        if self.get_direction() == Gtk.TextDirection.RTL:
            a, b = 1 - b, 1 - a
        if b > a:
            snapshot.append_color(style.colour(self, "luma_media_progress"),
                                  style.rect(thumb / 2 + a * inner, box.get_y(), (b - a) * inner, track))
        snapshot.append_inset_shadow(shape, style.colour(self, "luma_well_shade"), 0, 1, 0, 1)
        snapshot.pop()
        snapshot.append_border(shape, [1] * 4, [style.colour(self, "luma_well_ring")] * 4)
        self.snapshot_child(self._thumb, snapshot)

    def do_dispose(self) -> None:
        if self._thumb.get_parent() is self:
            self._thumb.unparent()


# ── The slider ─────────────────────────────────────────────────────────────

class ValueSlider(Gtk.Box):
    """`ValueSlider("Exposure", 0, -100, 100, on_change=fn)`: a name, a range and its signed value."""

    __gtype_name__ = "LumaUIValueSlider"

    def __init__(self, label: str, value: float = 0.0, minimum: float = -100.0, maximum: float = 100.0, *,
                 unit: str = "", default: float | None = None, step: float = 1.0, layout: str = "row",
                 on_change: Callable[[float], None] | None = None,
                 on_reset: Callable[[], None] | None = None) -> None:
        if layout not in SLIDER_LAYOUTS:
            raise ValueError(f"ValueSlider layout must be one of {SLIDER_LAYOUTS}, not {layout!r}")
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL if layout == "row" else Gtk.Orientation.VERTICAL)
        self.add_css_class("lumaui-value-slider")
        self.add_css_class(layout)
        if layout == "editor":
            self.add_css_class("stacked")
        self.label, self.unit, self.layout = label, unit, layout
        self.minimum, self.maximum, self.step = float(minimum), float(maximum), float(step)
        self.default = float(default) if default is not None else min(self.maximum, max(self.minimum, 0.0))
        self.on_change, self.on_reset = on_change, on_reset
        self._listeners: list[Callable[["ValueSlider"], None]] = []
        self.name_label = Gtk.Label(label=label, xalign=0, ellipsize=3)
        self.name_label.add_css_class("name")
        self.value_label = Gtk.Label(xalign=1)
        self.value_label.add_css_class("value")
        self.range = ValueRange(value, self.minimum, self.maximum, self.step, label=label)
        self.range.connect("value-changed", self._moved)
        if layout == "row":
            self.append(self.name_label)
            self.append(self.range)
            self.append(self.value_label)
        else:
            head = Gtk.Box()
            head.add_css_class("head")
            self.name_label.set_hexpand(True)
            head.append(self.name_label)
            head.append(self.value_label)
            self.append(head)
            self.append(self.range)
        twice = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        twice.connect("pressed", lambda _g, n, _x, _y: self.reset() if n == 2 else None)
        self.add_controller(twice)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, k, _code, _s: (self.reset(), True)[1]
                     if k in (Gdk.KEY_Delete, Gdk.KEY_BackSpace) else False)
        self.range.add_controller(keys)
        self._show()

    @property
    def value(self) -> float:
        return self.range.value

    @property
    def changed(self) -> bool:
        """Moved away from its default."""
        return abs(self.range.value - self.default) > 1e-9

    def set_value(self, value: float) -> None:
        """The app's value (a photo was opened, an undo); never calls on_change."""
        self.range.set_value(value)
        self._show()

    def reset(self) -> None:
        """Back to the default, as a double-click does; reports it."""
        was = self.changed
        self.range.set_value(self.default)
        self._show()
        if was:
            if self.on_change is not None:
                self.on_change(self.value)
            if self.on_reset is not None:
                self.on_reset()

    def _moved(self, _range: ValueRange, value: float) -> None:
        self._show()
        if self.on_change is not None:
            self.on_change(value)

    def _show(self) -> None:
        text = value_text(self.value, self.minimum, self.unit, self.step)
        self.value_label.set_label(text)
        self.range.set_value_text(text)
        (self.add_css_class if self.changed else self.remove_css_class)("set")
        for listener in list(self._listeners):
            listener(self)

    def _listen(self, callback: Callable[["ValueSlider"], None]) -> None:
        self._listeners.append(callback)


# ── Group and panel ────────────────────────────────────────────────────────

class AdjustmentGroup(Gtk.Box):
    """A heading that opens and closes a set of sliders, dotted while any is moved (v70 `.drgrp`)."""

    __gtype_name__ = "LumaUIAdjustmentGroup"

    def __init__(self, title: str, sliders: Sequence[ValueSlider], *, open: bool = False,
                 on_toggle: Callable[[bool], None] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("lumaui-adjust-group")
        self.title = title
        self.sliders = list(sliders)
        self.on_toggle = on_toggle
        from .media_transport import MediaGlyph

        self.header = Gtk.Button()
        self.header.add_css_class("lumaui-adjust-heading")
        row = Gtk.Box()
        name = Gtk.Label(label=title, xalign=0)
        name.add_css_class("title")
        row.append(name)
        self.dot = Gtk.Box(valign=Gtk.Align.CENTER, visible=False)
        self.dot.add_css_class("dot")
        row.append(self.dot)
        spacer = Gtk.Box(hexpand=True)
        row.append(spacer)
        self.chevron = MediaGlyph("chevron-down", A["group_chevron"])
        self.chevron.add_css_class("chevron")
        row.append(self.chevron)
        self.header.set_child(row)
        self.header.connect("clicked", lambda _b: self.set_open(not self.open, notify=True))
        self.append(self.header)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.add_css_class("lumaui-adjust-sliders")
        for slider in self.sliders:
            body.append(slider)
            slider._listen(lambda _s: self._refresh())
        self.revealer = Gtk.Revealer(child=body, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.append(self.revealer)
        self.open = False
        self.set_open(open)
        self._refresh()

    def set_open(self, open: bool, *, notify: bool = False) -> None:
        self.open = bool(open)
        from .lumaui import duration

        self.revealer.set_transition_duration(duration("morph"))
        self.revealer.set_reveal_child(self.open)
        (self.add_css_class if self.open else self.remove_css_class)("open")
        self.header.update_state([Gtk.AccessibleState.EXPANDED], [int(self.open)])
        if notify and self.on_toggle is not None:
            self.on_toggle(self.open)

    @property
    def changed(self) -> bool:
        return any(slider.changed for slider in self.sliders)

    def _refresh(self) -> None:
        self.dot.set_visible(self.changed)
        label = f"{self.title}, changed" if self.changed else self.title
        self.header.update_property([Gtk.AccessibleProperty.LABEL], [label])


class AdjustmentPanel(Gtk.Box):
    """The floating Adjust card (v70 `#vw-adj`); a bottom sheet at phone width."""

    __gtype_name__ = "LumaUIAdjustmentPanel"

    def __init__(self, sections: Sequence[tuple[str | None, Sequence[ValueSlider]] | AdjustmentGroup], *,
                 title: str = "Adjust", on_reset: Callable[[], None] | None = None,
                 on_close: Callable[[], None] | None = None, phone_layout: str = "sheet") -> None:
        if phone_layout not in ("sheet", "floating"):
            raise ValueError("phone_layout must be sheet or floating")
        self.phone_layout = phone_layout
        super().__init__(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.GROUP)
        self.add_css_class("lumaui-adjust-panel")
        self.update_property([Gtk.AccessibleProperty.LABEL], [title])
        self.on_reset, self.on_close = on_reset, on_close
        self.sliders: list[ValueSlider] = []
        head = Gtk.Box()
        head.add_css_class("lumaui-adjust-panel-head")
        heading = Gtk.Label(label=title, xalign=0, hexpand=True)
        heading.add_css_class("title")
        head.append(heading)
        self.reset_button = Gtk.Button(label="Reset", valign=Gtk.Align.CENTER)
        self.reset_button.add_css_class("lumaui-adjust-reset")
        self.reset_button.connect("clicked", lambda _b: self.reset())
        head.append(self.reset_button)
        self.append(head)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.add_css_class("lumaui-adjust-panel-body")
        first = True
        for section in sections:
            if isinstance(section, AdjustmentGroup):
                body.append(section)
                sliders = section.sliders
            else:
                heading_text, sliders = section
                if not first:
                    rule = Gtk.Box()
                    rule.add_css_class("rule")
                    body.append(rule)
                if heading_text:
                    label = Gtk.Label(label=heading_text, xalign=0)
                    label.add_css_class("section")
                    body.append(label)
                for slider in sliders:
                    body.append(slider)
            for slider in sliders:
                self.sliders.append(slider)
                slider._listen(lambda _s: self._refresh())
            first = False
        self.scroller = Gtk.ScrolledWindow(child=body, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                           propagate_natural_height=True, vexpand=True)
        self.append(self.scroller)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self._overlay: Gtk.Overlay | None = None
        self._shown = False
        self.set_visible(False)
        self._refresh()

    def _key(self, _controller, keyval: int, _code: int, _state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_Escape and self.on_close is not None:
            self.on_close()
            return True
        return False

    @property
    def changed(self) -> bool:
        return any(slider.changed for slider in self.sliders)

    def reset(self) -> None:
        """Every slider back to its default, then `on_reset` once."""
        for slider in self.sliders:
            slider.range.set_value(slider.default)
            slider._show()
        self._refresh()
        if self.on_reset is not None:
            self.on_reset()

    def _refresh(self) -> None:
        self.reset_button.set_sensitive(self.changed)

    # ── Placement ──
    def attach(self, overlay: Gtk.Overlay) -> "AdjustmentPanel":
        """Float over `overlay` (the picture): top right on a computer, a bottom sheet on a phone."""
        self._overlay = overlay
        overlay.add_overlay(self)
        overlay.connect("get-child-position", self._position)
        return self

    def set_shown(self, shown: bool) -> None:
        from .lumaui import on_next_frame

        self._shown = bool(shown)
        if self._shown:
            self.set_visible(True)
            on_next_frame(self, lambda: self.add_css_class("shown"))
        else:
            self.remove_css_class("shown")
            self.set_visible(False)

    @property
    def shown(self) -> bool:
        return self._shown

    def _position(self, overlay: Gtk.Overlay, child: Gtk.Widget, allocation: Gdk.Rectangle) -> bool:
        if child is not self:
            return False
        width, height = overlay.get_width(), overlay.get_height()
        phone = 0 < width <= tokens.PHONE_MAX_WIDTH
        sheet = phone and self.phone_layout == "sheet"
        if sheet != self.has_css_class("sheet"):
            from gi.repository import GLib

            # A style change waits until this layout is done.
            GLib.idle_add(lambda: ((self.add_css_class if sheet else self.remove_css_class)("sheet"), False)[1])
        if phone:
            floating = self.phone_layout == "floating"
            inset = int(A["panel_inset"]) if floating else 0
            bottom = int(A["floating_phone_bottom"]) if floating else 0
            panel_width = max(0, width - 2 * inset)
            _m, natural, _mb, _nb = self.measure(Gtk.Orientation.VERTICAL, panel_width)
            fraction = A["floating_phone_max_pct"] if floating else A["sheet_max_pct"]
            h = min(natural, int(height * fraction / 100), max(0, height - bottom))
            allocation.x, allocation.y = inset, height - bottom - h
            allocation.width, allocation.height = panel_width, h
        else:
            inset, panel = int(A["panel_inset"]), int(A["panel_width"])
            _m, natural, _mb, _nb = self.measure(Gtk.Orientation.VERTICAL, panel)
            h = max(0, min(natural, height - int(A["panel_bottom_room"])))
            allocation.x, allocation.y = width - inset - panel, inset
            allocation.width, allocation.height = panel, h
        return True
