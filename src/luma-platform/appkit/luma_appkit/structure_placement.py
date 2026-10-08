# SPDX-License-Identifier: Apache-2.0
"""LumaUI structure: the corner pill and the mode switch.

The placement rule (v70 `.flo.lcorner`, Viewer the reference): the top right
of the island holds the view and the thing itself; the bottom bar holds what
you do in this mode right now. CornerPill puts what an app has in the one
order every app uses, so an app says *what* it has, never *where*:

    mode switch · who's here · Open in · Share · object actions (Edit) ·
    on/off states (Favourite) · Information · ···

    corner = CornerPill(modes=ModeSwitch([("view", "View", "eye"), ("markup", "Mark up", "pen-line"),
                                          ("adjust", "Adjust", "sliders-horizontal")], on_change=switch),
                        open_in=show_open_in, share=share, info=details_pane, more=registry)
    island_overlay.add_overlay(corner)          # it floats 16 from the top-right corner

    # Contacts: words on a computer, Edit the key, and Cancel · Done while editing.
    corner = CornerPill(share=share, actions=[("pencil", "Edit", start_editing)], primary="Edit",
                        labelled=True, more=registry)
    corner.edit(on_cancel=discard, on_done=save)

ModeSwitch — icon plus label segments on a recessed well; the current mode
is the raised chip, which slides to the new mode (v70 `lModes`). In a narrow
window (820 px or less) only the current mode keeps its label. Arrows move
between modes and choose them.

Rules every part follows: docs/developer/kit/lumaui-principles.md and
behaviour.md. CSS lives in luma-appkit-base.css under `/* LumaUI: Corner
pill and mode switch */`.
"""
from __future__ import annotations

from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from . import icons, lumaui  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402
from .commands import CommandRegistry  # noqa: E402
from .structure_adapt import WidthWatch, arrow_keys, icon_button, is_phone  # noqa: E402

__all__ = ["ModeSwitch", "CornerPill"]

Mode = tuple[str, str, str]


def _bezier(points: Sequence[float]) -> Callable[[float], float]:
    """A CSS cubic-bezier as a function of progress, for motion drawn by hand."""
    x1, y1, x2, y2 = points

    def coord(t: float, a: float, b: float) -> float:
        return 3 * a * t * (1 - t) ** 2 + 3 * b * t * t * (1 - t) + t ** 3

    def ease(x: float) -> float:
        low, high = 0.0, 1.0
        for _ in range(24):
            mid = (low + high) / 2
            if coord(mid, x1, x2) < x:
                low = mid
            else:
                high = mid
        return coord((low + high) / 2, y1, y2)

    return ease


class ModeSwitch(Gtk.Box):
    """Icon and label modes on a well; the chip slides to the current one.

    `labels_only=True` (or modes given as (key, label) pairs) is the text-only
    segment (v70 `.seg`: aspect, zoom level, priority, theme): every label
    shows at every width. `fill=True` shares the width it is given equally
    (the share sheet's Work together · Send a copy; v71 Settings' phone rows).
    `size="small"` uses 12px labels and 6px side padding for constrained property rows.
    `size="large"` is v71 Calendar's answer (44 tall, 14/600).
    `stop_width=34` keeps numerical stops equal and fixed (Camera zoom).
    Fixed stops override fill and ellipsize long labels without losing their accessible names.
    `ellipsize=True` lets text-only modes shrink to fit a constrained corner.
    """

    __gtype_name__ = "LumaUIModeSwitch"

    def __init__(self, modes: Sequence[Mode | tuple[str, str]], *, current: str | None = None,
                 on_change: Callable[[str], None] | None = None, label: str = "Mode",
                 labels_only: bool = False, fill: bool = False, compact: bool = False,
                 size: str = "regular", stop_width: int | None = None, ellipsize: bool = False) -> None:
        if stop_width is not None and (isinstance(stop_width, bool) or not isinstance(stop_width, int) or stop_width <= 0):
            raise ValueError("stop_width is a positive integer or None")
        if size not in ("regular", "small", "large"):
            raise ValueError("size is 'regular', 'small' or 'large'")
        if len(modes) < 2:
            raise ValueError("a mode switch offers at least two modes")
        if any(len(m) == 2 for m in modes):
            labels_only = True
        # A mode may carry a count (v70 Clock's Alarms · 2): (key, label, icon, count).
        counts = {m[0]: m[3] for m in modes if len(m) > 3 and m[3] is not None}
        modes = [(m[0], m[1], None if labels_only else m[2]) for m in modes]
        keys = [m[0] for m in modes]
        if len(keys) != len(set(keys)):
            raise ValueError("mode keys are unique")
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, valign=Gtk.Align.CENTER,
                         accessible_role=Gtk.AccessibleRole.RADIO_GROUP)
        self.add_css_class("lumaui-modes")
        self.labels_only = labels_only
        self.ellipsize = ellipsize
        self.bar_flexible = ellipsize
        self.stop_width = stop_width
        lumaui.set_css_class(self, "fixed-stops", stop_width is not None)
        lumaui.set_css_class(self, "labels-only", labels_only)
        # v71 Calendar's answer: a 44 control, 14 frame, an 11 indicator 3 in, 14/600 words.
        lumaui.set_css_class(self, "large", size == "large")
        lumaui.set_css_class(self, "small", size == "small")
        # compact: a choice inside a settings row (v70 .cfrow .seg: 32 tall, 26 buttons, 12.5).
        lumaui.set_css_class(self, "compact", compact)
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self.on_change = on_change
        self._current = current if current is not None else keys[0]
        if self._current not in keys:
            raise ValueError(f"unknown mode {self._current!r}")

        # The chip is the row's first child, placed by the row's own layout over the current
        # button's real allocation in the same pass: right at first paint, after labels fold
        # or the width changes, and during the slide (Nick, 26 Sep: a half pill behind the icon).
        self.row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.row.add_css_class("lumaui-modes-row")
        self.indicator = Gtk.Box(can_target=False, accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.indicator.add_css_class("lumaui-modes-indicator")
        self.row.append(self.indicator)
        self._layout = _ModesLayout(self, fill)
        self.row.set_layout_manager(self._layout)
        if fill and stop_width is None:
            self.set_hexpand(True)
            self.row.set_hexpand(True)
        self.append(self.row)
        self._overlay = self.row

        self.buttons: dict[str, Gtk.ToggleButton] = {}
        group: Gtk.ToggleButton | None = None
        for key, text, icon in modes:
            button = Gtk.ToggleButton(group=group, accessible_role=Gtk.AccessibleRole.RADIO)
            group = group or button
            button.add_css_class("lumaui-mode")
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.CENTER)
            line.add_css_class("lumaui-mode-line")
            if icon:
                line.append(icons.image(icon))
            # Words alone keep their width (v70 .seg: the row around it wraps instead).
            name = Gtk.Label(label=text, ellipsize=Pango.EllipsizeMode.NONE if labels_only and size != "small" and not ellipsize else Pango.EllipsizeMode.END)
            name.add_css_class("lumaui-mode-label")
            if stop_width is not None:
                name.set_ellipsize(Pango.EllipsizeMode.END)
                name.set_max_width_chars(1)
                name.set_hexpand(True)
                name.set_xalign(.5)
                line.set_halign(Gtk.Align.FILL)
            line.append(name)
            button.label_widget = name
            button.count_badge = None
            if key in counts:
                from .content_badges import CountBadge
                button.count_badge = CountBadge(counts[key])
                button.count_badge.add_css_class("lumaui-mode-count")
                line.append(button.count_badge)
            # What the place is doing (v71 Clock .ckplaces): the timer's time left (.ckem) and a running dot (.ckrun).
            button.status_text = Gtk.Label(visible=False)
            button.status_text.add_css_class("lumaui-mode-status")
            button.status_dot = Gtk.Box(valign=Gtk.Align.CENTER, visible=False, can_target=False)
            button.status_dot.add_css_class("lumaui-mode-dot")
            line.append(button.status_text)
            line.append(button.status_dot)
            button.set_child(line)
            button.set_tooltip_text(text)
            button.update_property([Gtk.AccessibleProperty.LABEL], [text])
            button.set_active(key == self._current)
            button.connect("toggled", self._toggled, key)
            self.buttons[key] = button
            self.row.append(button)
        arrow_keys(self.row, activate=True)
        self._narrow = False
        self._anim = 0
        self._watch = WidthWatch(self, self._width_changed)

    # ── public API ────────────────────────────────────────────────────────

    @property
    def current(self) -> str:
        return self._current

    def set_count(self, key: str, count: int) -> None:
        """The count a mode carries (0 hides it)."""
        badge = self.buttons[key].count_badge
        if badge is None:
            raise ValueError(f"mode {key!r} was made without a count")
        badge.set_count(count)
        badge.set_visible(count > 0)

    def set_status(self, key: str, status: str | None) -> None:
        """What a mode is doing: "running" is a green dot beside its words (a corner dot on an icon-only tab on
        a phone); any other text (the timer's "4:12") is shown after the words on a computer (v71 Clock's
        .ckplaces .ckem, .ckrun); None clears it."""
        if key not in self.buttons:
            raise ValueError(f"unknown mode {key!r}")
        button = self.buttons[key]
        button.update_property([Gtk.AccessibleProperty.DESCRIPTION], ["Running" if status == "running" else status or ""])
        button.status_text.set_label("" if status in (None, "running") else status)
        button.status_dot.set_visible(status == "running")
        self._labels()

    def set_current(self, key: str) -> None:
        """Choose a mode (without calling `on_change`)."""
        if key not in self.buttons:
            raise ValueError(f"unknown mode {key!r}")
        self._select(key, notify=False)

    # ── internals ─────────────────────────────────────────────────────────

    def _toggled(self, button: Gtk.ToggleButton, key: str) -> None:
        if button.get_active() and key != self._current:
            self._select(key, notify=True)

    def _select(self, key: str, *, notify: bool) -> None:
        changed = key != self._current
        self._current = key
        for name, button in self.buttons.items():
            if button.get_active() != (name == key):
                button.set_active(name == key)
            lumaui.set_css_class(button, "on", name == key)
        self._labels()
        if changed:
            self._place(True)
        if notify and changed and self.on_change is not None:
            self.on_change(key)

    def _width_changed(self, width: int) -> None:
        # In the action center's bar (v70 .ckplaces, .pnplaces) only a phone folds the labels.
        limit = tokens.PHONE_MAX_WIDTH if self.has_css_class("in-bar") else tokens.STRUCTURE["modes"]["narrow_max_width"]
        narrow = 0 < width <= limit and not self.labels_only
        if narrow != self._narrow:
            self._narrow = narrow
            lumaui.set_css_class(self, "narrow", narrow)
            self._labels()
            self.row.queue_allocate()

    def _labels(self) -> None:
        for name, button in self.buttons.items():
            button.label_widget.set_visible(not self._narrow or name == self._current)
            lumaui.set_css_class(button, "on", name == self._current)
            # The time left needs room: not on an icon-only tab, nor beside a folded label.
            button.status_text.set_visible(bool(button.status_text.get_label()) and button.label_widget.get_visible()
                                           and not (self._narrow and self.has_css_class("in-bar")))

    def _place(self, animate: bool) -> None:
        """Slide the chip from where it is to the current button (the layout knows both)."""
        layout = self._layout
        if self._anim:
            self.row.remove_tick_callback(self._anim)
            self._anim = 0
        duration = lumaui.duration("morph") if animate else 0
        if duration <= 0 or lumaui.reduced_motion() or layout.shown is None:
            layout.start, layout.progress = None, 1.0
            self.row.queue_allocate()
            return
        layout.start, layout.progress = layout.shown, 0.0
        ease = _bezier(tokens.MOTION["ease"])
        began: list[int] = []

        def tick(widget: Gtk.Widget, clock) -> bool:
            now = clock.get_frame_time()
            if not began:
                began.append(now)
            raw = min(1.0, (now - began[0]) / (duration * 1000))
            layout.progress = ease(raw)
            widget.queue_allocate()
            if raw >= 1.0:
                self._anim = 0
                layout.start = None
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        self._anim = self.row.add_tick_callback(tick)


class _ModesLayout(Gtk.LayoutManager):
    """Buttons side by side (equal when `fill`), and the chip over the current one."""

    def __init__(self, switch: "ModeSwitch", fill: bool) -> None:
        super().__init__()
        self.switch, self.fill = switch, fill
        self.start: tuple[float, float] | None = None   # where a slide began
        self.progress = 1.0
        self.shown: tuple[float, float] | None = None   # where the chip is now

    def _buttons(self, widget):
        child = widget.get_first_child()
        while child is not None:
            if child is not self.switch.indicator and child.get_visible():
                yield child
            child = child.get_next_sibling()

    def do_get_request_mode(self, widget):
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_measure(self, widget, orientation, for_size):
        buttons = list(self._buttons(widget))
        sizes = [b.measure(orientation, -1)[:2] for b in buttons]
        if orientation == Gtk.Orientation.HORIZONTAL:
            if self.switch.stop_width is not None:
                total = self.switch.stop_width * len(buttons) + max(0, len(buttons) - 1) * tokens.STRUCTURE["modes"]["stop_gap"]
                return total, total, -1, -1
            if self.fill and sizes:
                widest = max(n for _m, n in sizes)
                return max(m for m, _n in sizes) * len(sizes), widest * len(sizes), -1, -1
            return sum(m for m, _n in sizes), sum(n for _m, n in sizes), -1, -1
        return max((m for m, _n in sizes), default=0), max((n for _m, n in sizes), default=0), -1, -1

    def do_allocate(self, widget, width, height, baseline):
        buttons = list(self._buttons(widget))
        naturals = [b.measure(Gtk.Orientation.HORIZONTAL, -1)[1] for b in buttons]
        minimums = [b.measure(Gtk.Orientation.HORIZONTAL, -1)[0] for b in buttons]
        gap = tokens.STRUCTURE["modes"]["stop_gap"] if self.switch.stop_width is not None else 0
        if self.switch.stop_width is not None:
            widths = [self.switch.stop_width] * len(buttons)
        elif self.fill and buttons:
            widths = [width // len(buttons)] * len(buttons)
            widths[-1] += width - sum(widths)
        else:
            total = sum(naturals)
            if total <= width:
                widths = list(naturals)
            else:  # narrower than natural: give each its minimum, then share what is left
                spare = max(0, width - sum(minimums))
                extra = [n - m for n, m in zip(naturals, minimums)]
                room = sum(extra) or 1
                widths = [m + int(spare * e / room) for m, e in zip(minimums, extra)]
        x, target = 0, None
        current = self.switch.buttons.get(self.switch._current)
        for button, w in zip(buttons, widths):
            button.allocate(w, height, baseline, Gsk.Transform().translate(Graphene.Point().init(x, 0)))
            if button is current:
                target = (float(x), float(w))
            x += w + gap
        if target is None:
            self.switch.indicator.set_child_visible(False)
            return
        if self.start is not None and self.progress < 1.0:
            p = self.progress
            chip = (self.start[0] + (target[0] - self.start[0]) * p, self.start[1] + (target[1] - self.start[1]) * p)
        else:
            chip = target
        self.shown = chip
        indicator = self.switch.indicator
        indicator.set_child_visible(True)
        indicator.measure(Gtk.Orientation.HORIZONTAL, -1)
        indicator.allocate(max(0, round(chip[1])), height, -1,
                           Gsk.Transform().translate(Graphene.Point().init(round(chip[0]), 0)))


def _fill_when_on(button: Gtk.ToggleButton, icon: str) -> None:
    """v70 draws an on state filled (the Favourite heart, .ib.loved): swap to the
    glyph's -filled twin when there is one, and a heart takes the love colour."""
    filled = f"{icon}-filled"
    if icon == "star":
        button.add_css_class("favourite")  # v71 Memos' Favorite: the star itself yellow, no chip
    if not any((path / f"{icons.icon_name(filled)}.svg").is_file() for path in icons.search_paths()):
        return
    if icon == "heart":
        button.add_css_class("love")

    def sync(b: Gtk.ToggleButton) -> None:
        image = b.get_child()
        if isinstance(image, Gtk.Image):
            image.set_from_icon_name(icons.icon_name(filled if b.get_active() else icon))
    button.connect("toggled", sync)
    button.connect("realize", sync)


#: v71 .pview .psize folds away in a window this wide or narrower.
_ZOOM_FOLD = 720


class CornerPill(Gtk.Box):
    """The top-right pill: modes, then the thing itself, in the one placement order.

    `order=("people", "actions", "states", "info")` changes the placement order (a name may be one
    control of a group: `("actions.0", "states", "actions.1", "more")`)
    for a header that reads differently (v71 Messages: Call, Video, Pin, Details);
    groups it leaves out keep ORDER after the ones it names.

    `search=BarSearch(...)` embeds the shared 36px search well. In a constrained
    island it wraps below the other controls instead of clipping them; text and
    focus survive that move. Its on_close handles the close key and Escape.

    `labelled=True` gives Share and the actions their words on a computer
    (icon only at phone width); `primary="Edit"` makes that one action the
    raised key, which keeps its word everywhere. `edit(on_cancel, on_done)`
    turns the pill into Cancel · Done (Done is the key) until either is
    pressed; `stop_editing()` turns it back. v70 Contacts' corner.
    """

    __gtype_name__ = "LumaUICornerPill"

    # v70 pCorner, "the LumaUI order": Share · Favourite (states) · Information · Edit (actions) · ···
    ORDER = ("modes", "search", "zoom", "people", "open_in", "share", "states", "info", "actions", "more")

    def __init__(self, *, modes: ModeSwitch | None = None, search: object = None, zoom: Gtk.Widget | None = None,
                 people: Gtk.Widget | None = None,
                 open_in: Callable[[Gtk.Widget], None] | None = None,
                 share: Callable[[Gtk.Widget], None] | None = None,
                 actions: Sequence[tuple[str, str, Callable[[], None]]] = (),
                 states: Sequence[tuple[str, str, bool, Callable[[bool], None]]] = (),
                 info: object = None, more: CommandRegistry | None = None,
                 labelled: bool = False, primary: str | None = None, keep_labels: bool = False,
                 order: Sequence[str] | None = None, orientation: str = "horizontal",
                 quiet: bool = False, primary_icon_only: bool = False) -> None:
        if orientation not in ("horizontal", "vertical"):
            raise ValueError("corner orientation is horizontal or vertical")
        axis = Gtk.Orientation.VERTICAL if orientation == "vertical" else Gtk.Orientation.HORIZONTAL
        super().__init__(orientation=axis, halign=Gtk.Align.END, valign=Gtk.Align.START,
                         accessible_role=Gtk.AccessibleRole.TOOLBAR)
        # A group, or one control of it ("actions.1": Charlie's Delete after the Flag, v71's Details slot).
        if order is not None and ({g.split(".")[0] for g in order} - set(self.ORDER) or len(set(order)) != len(order)):
            raise ValueError(f"order names each of {self.ORDER} (or one of its controls, 'actions.1') at most once")
        self._primary_icon_only = primary_icon_only
        self.add_css_class("lumaui-corner")
        if quiet:   # v71 .ib: glyphs in the second ink (Charlie's .crright), a lit state its own
            self.add_css_class("quiet")
        self.update_property([Gtk.AccessibleProperty.LABEL], ["View and item"])
        if primary is not None and primary not in [label for _i, label, _c in actions]:
            raise ValueError(f"the primary action {primary!r} is not one of the actions")
        self.normal = Gtk.Box(orientation=axis)
        self.normal.add_css_class("lumaui-corner-group")
        self.editing = Gtk.Box(orientation=axis, visible=False)
        self.editing.add_css_class("lumaui-corner-group")
        self.append(self.normal)
        self.append(self.editing)
        self.controls: dict[str, Gtk.Widget] = {}
        self._labelled: list[Gtk.Button] = []
        self._keep_labels = keep_labels
        self._edit_callbacks: tuple[Callable[[], None] | None, Callable[[], None] | None] = (None, None)
        if modes is not None:
            if not isinstance(modes, ModeSwitch):
                raise TypeError("modes is a ModeSwitch")
            self._add("modes", modes)
        self._search_widget = None
        self._search_tick = 0
        self._search_wrapped = False
        if search is not None:
            from .bar_items import BarSearch
            from .action_center import make_control
            if not isinstance(search, BarSearch):
                raise TypeError("corner search is a BarSearch")
            if orientation != "horizontal":
                raise ValueError("search belongs in a horizontal corner")
            widget = make_control(search)
            widget.add_css_class("in-corner")
            widget.set_hexpand(False)
            widget.set_margin_end(0)
            widget.get_layout_manager().width = (tokens.STRUCTURE["corner"]["search_width"]
                - tokens.BAR["search"]["padding_start"] - tokens.BAR["search"]["padding_end"])
            self._search_widget = widget
            self._add("search", widget)
            if search.on_close is not None:
                search.clear_button.set_tooltip_text("Close search")
                search.clear_button.update_property([Gtk.AccessibleProperty.LABEL], ["Close search"])
                search.clear_button.connect("clicked", lambda *_: search.on_close())
                keys = Gtk.EventControllerKey()
                keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
                def escape(_controller, keyval, *_args):
                    if keyval == Gdk.KEY_Escape:
                        search.on_close()
                        return True
                    return False
                keys.connect("key-pressed", escape)
                widget.add_controller(keys)
            self.connect("map", self._search_mapped)
            self.connect("unmap", self._search_unmapped)
        if zoom is not None:
            # v71 Photos' view island: the size slider after the modes; it folds away in a
            # window of 720 or less (@container win (max-width: 720px) .pview .psize).
            self._add("zoom", zoom)
            self._zoom_wanted, self._zoom_room = True, True

            def room(width: int) -> None:
                self._zoom_room = width > _ZOOM_FOLD
                zoom.set_visible(self._zoom_wanted and self._zoom_room)

            self._zoom_watch = WidthWatch(self, room, threshold=_ZOOM_FOLD)
        if people is not None:
            people.add_css_class("lumaui-corner-people")
            self._add("people", people)
        if open_in is not None:
            button = icon_button("square-arrow-out-up-right", "Open in", "lumaui-corner-button")
            button.update_property([Gtk.AccessibleProperty.HAS_POPUP], [True])
            button.connect("clicked", lambda b: open_in(b))
            self._add("open_in", button)
        if share is not None:
            button = self._button(icons.SHARE, "Share", labelled)
            button.connect("clicked", lambda b: share(b))
            self._add("share", button)
        for index, (icon, label, active, callback) in enumerate(states):
            button = icon_button(icon, label, "lumaui-corner-button", toggle=True)
            _fill_when_on(button, icon)
            button.set_active(active)
            button.connect("toggled", lambda b, cb=callback: cb(b.get_active()))
            self._add(f"states.{index}", button)
        if info is not None:
            if hasattr(info, "info_button"):
                button = info.info_button()
            elif callable(info):
                button = icon_button("info", "Information", "lumaui-corner-button", toggle=True)
                button.connect("toggled", lambda b: info(b.get_active()))
            else:
                raise TypeError("info is a DetailsPane or a callable(shown)")
            self._add("info", button)
        for index, (icon, label, callback) in enumerate(actions):
            button = self._button(icon, label, labelled or label == primary, primary=label == primary)
            button.connect("clicked", lambda _b, cb=callback: cb())
            self._add(f"actions.{index}", button)
        if more is not None:
            button = icon_button("ellipsis", "More", "lumaui-corner-button")
            button.add_css_class("more")
            button.update_property([Gtk.AccessibleProperty.HAS_POPUP], [True])
            button.connect("clicked", lambda b: self._more(b, more))
            self._add("more", button)
        if not self.controls:
            raise ValueError("a corner pill holds at least one control")
        if order is not None:
            # v71 Messages' header (msgHead): Call, Video, Pin, then Details last.
            groups = list(order) + [g for g in self.ORDER if g not in order]
            previous, placed = None, set()
            for group in groups:
                for name, widget in self.controls.items():
                    if name in placed:
                        continue
                    if name == group or name.startswith(group + "."):
                        self.normal.reorder_child_after(widget, previous)
                        previous = widget
                        placed.add(name)

        self.cancel_button = self._button(None, "Cancel", True)
        self.cancel_button.connect("clicked", lambda _b: self._finish(0))
        self.done_button = self._button(None, "Done", True, primary=True)  # v70: words only
        self.done_button.connect("clicked", lambda _b: self._finish(1))
        self.editing.append(self.cancel_button)
        self.editing.append(self.done_button)
        if orientation == "vertical":
            self.add_css_class("vertical-stack")
            for group in (self.normal, self.editing):
                controls = []
                child = group.get_first_child()
                while child is not None:
                    controls.append(child)
                    child = child.get_next_sibling()
                for child in controls[:-1]:
                    separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                    separator.add_css_class("lumaui-corner-separator")
                    group.insert_child_after(separator, child)
        arrow_keys(self.normal)
        arrow_keys(self.editing)
        self._watch = WidthWatch(self, lambda _w: self._collapse(), threshold=tokens.PHONE_MAX_WIDTH)

    def _search_mapped(self, *_args) -> None:
        if not self._search_tick:
            self._search_tick = self.add_tick_callback(self._fit_search)

    def _search_unmapped(self, *_args) -> None:
        if self._search_tick:
            self.remove_tick_callback(self._search_tick)
            self._search_tick = 0

    def _fit_search(self, *_args) -> bool:
        """Keep every mode/action reachable when an open pane constrains the corner."""
        parent = self.get_parent()
        search = self._search_widget
        if parent is None or search is None or parent.get_width() <= 0:
            return True
        metrics = tokens.STRUCTURE["corner"]
        available = parent.get_width() - 2 * (metrics["inset"] + metrics["padding"])
        children = [widget for widget in self.controls.values() if widget.get_visible()]
        minimum = sum(widget.measure(Gtk.Orientation.HORIZONTAL, -1)[0] for widget in children)
        minimum += metrics["gap"] * max(0, len(children) - 1)
        wrap = search.get_visible() and minimum > available
        if wrap != self._search_wrapped:
            self._search_wrapped = wrap
            root = self.get_root()
            focus = root.get_focus() if root is not None and hasattr(root, "get_focus") else None
            restore_focus = focus is not None and (focus is search or focus.is_ancestor(search))
            search.get_parent().remove(search)
            if wrap:
                self.set_orientation(Gtk.Orientation.VERTICAL)
                self.append(search)
            else:
                self.set_orientation(Gtk.Orientation.HORIZONTAL)
                self.normal.insert_child_after(search, self.controls.get("modes"))
            if restore_focus:
                focus.grab_focus()
        return True

    # ── editing ───────────────────────────────────────────────────────────

    @property
    def is_editing(self) -> bool:
        return self.editing.get_visible()

    def edit(self, on_cancel: Callable[[], None] | None = None, on_done: Callable[[], None] | None = None, *,
             done: str = "Done") -> None:
        """Show Cancel · Done (the key) in place of the pill's controls."""
        self._edit_callbacks = (on_cancel, on_done)
        self.done_button.label_widget.set_label(done)
        self.done_button.update_property([Gtk.AccessibleProperty.LABEL], [done])
        self.normal.set_visible(False)
        if self._search_widget is not None:
            self._search_widget.set_child_visible(False)
        self.editing.set_visible(True)
        self.done_button.grab_focus()

    def stop_editing(self) -> None:
        self.editing.set_visible(False)
        self.normal.set_visible(True)
        if self._search_widget is not None:
            self._search_widget.set_child_visible(True)
        self._edit_callbacks = (None, None)

    def _finish(self, which: int) -> None:
        callback = self._edit_callbacks[which]
        self.stop_editing()
        if callback is not None:
            callback()

    # ── internals ─────────────────────────────────────────────────────────

    def set_zoom_shown(self, shown: bool) -> None:
        """Whether the zoom slot is wanted (Photos hides it at Years); a narrow window still folds it."""
        zoom = self.controls.get("zoom")
        if zoom is None:
            return
        self._zoom_wanted = shown
        zoom.set_visible(shown and self._zoom_room)

    def _add(self, name: str, widget: Gtk.Widget) -> None:
        self.controls[name] = widget
        self.normal.append(widget)

    def _button(self, icon: str | None, label: str, labelled: bool, *, primary: bool = False) -> Gtk.Button:
        if not labelled or (primary and self._primary_icon_only and icon is not None):
            button = icon_button(icon, label, "lumaui-corner-button")
            if primary:
                button.add_css_class("primary")
            return button
        button = Gtk.Button(valign=Gtk.Align.CENTER)
        button.add_css_class("lumaui-corner-button")
        button.add_css_class("labelled")
        if primary:
            button.add_css_class("primary")
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.CENTER)
        line.add_css_class("lumaui-corner-line")
        if icon is not None:
            line.append(icons.image(icon))
        text = Gtk.Label(label=label)
        line.append(text)
        button.set_child(line)
        button.label_widget = text
        button.primary = primary
        button.has_icon = icon is not None
        button.set_tooltip_text(label)
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self._labelled.append(button)
        return button

    def _collapse(self) -> None:
        """Phone words fold unless the key, icon-less, or explicitly kept navigation labels."""
        phone = is_phone(self)
        for button in self._labelled:
            keep = self._keep_labels or button.primary or not button.has_icon
            button.label_widget.set_visible(keep or not phone)
            lumaui.set_css_class(button, "labelled", keep or not phone)

    @staticmethod
    def _more(button: Gtk.Widget, registry: CommandRegistry) -> None:
        from .menus import command_popover

        menu = command_popover(registry)
        menu.set_parent(button)
        button.add_css_class("menu-open")
        menu.connect("closed", lambda *_a: button.remove_css_class("menu-open"))
        if hasattr(menu, "popup_below"):
            menu.popup_below(button)
        else:
            menu.set_position(Gtk.PositionType.BOTTOM)
            menu.popup()
