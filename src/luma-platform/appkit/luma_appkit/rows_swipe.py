# SPDX-License-Identifier: Apache-2.0
"""LumaUI rows: the swipe row (v71 "LumaUI swipe rows", phone).

Slide a row to act on it without opening it. Sliding toward the end (right,
left-to-right) reveals the gentle action on the start edge; sliding toward
the start reveals the other on the end edge. Past 90 px it commits: the row
slides away, then the action runs (the app's own toast confirms, with Undo
where the app has one). Short of 90 px it springs back. A vertical drag stays
a scroll, and a swipe never also opens the row: once the drag is sideways the
row claims it, so the list's click and the row's own buttons never see it.
A side with no action gives a 12 px rubber band and does nothing.

On by default only at phone width (the window under 560, v71's `.win.phone`);
`phone_only=False` keeps it on everywhere. The same actions belong in the
row's menu too: a swipe is a shortcut, never the only way.

v71's rows (changelog 2026-09-30, "LumaUI: swipe rows"):

    Mail      start = SwipeAction("archive", "green", archive, label="Archive")
              end   = SwipeAction("star", "orange", flag, label="Flag")
    Messages  start = SwipeAction("pin", "yellow", pin, label="Pin")
              end   = SwipeAction("bell-off", "blue", mute, label="Mute")
    Tasks     start = SwipeAction("check", "green", done, label="Done")
              end   = SwipeAction("sunrise", "orange", tomorrow, label="Tomorrow")
    Memos     start = SwipeAction("star", "yellow", favourite, label="Favorite")
              end   = SwipeAction("trash-2", "red", delete, label="Delete")
    Phone     end   = SwipeAction("trash-2", "red", remove, label="Remove")   # recents, with Undo

    row = SwipeRow(conversation_row, start=SwipeAction("pin", "yellow", pin, label="Pin"),
                   end=SwipeAction("bell-off", "blue", mute, label="Mute"))
    listbox.append(row)            # the callback gets the SwipeRow; row.child is what you passed
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk  # noqa: E402

from . import icons, lumaui  # noqa: E402

__all__ = ["SWIPE_COLORS", "SwipeAction", "SwipeRow"]

SWIPE_COLORS = ("green", "orange", "blue", "red", "gray", "yellow")
COMMIT = 90          # px past which a release commits (v71 `w > 90`)
SLOP = 12            # px before a drag is read as sideways or vertical
SIDEWAYS = 1.4       # sideways only when |dx| > |dy| * 1.4
RUBBER = 24          # a side with no action follows the finger at half, up to 12 px
SETTLE_MS = 220      # spring back or slide away (v71 .22s var(--ease))
FACE_MIN = 76        # the icon and name's column (v71 `.lswu span { min-width: 76px }`)
PHONE_WIDTH = 560    # the phone tier: under this


@dataclass(frozen=True)
class SwipeAction:
    """What a swipe does: a Lucide icon, one of SWIPE_COLORS, the callback (given the SwipeRow), its name."""

    icon: str
    color: str
    activate: Callable[["SwipeRow"], None]
    label: str = ""

    def __post_init__(self) -> None:
        if self.color not in SWIPE_COLORS:
            raise ValueError(f"swipe colour {self.color!r} is not one of {SWIPE_COLORS}")


def _settle_ms() -> int:
    """220 ms, or none when animations are off, a short fade's worth when motion is reduced."""
    settings = Gtk.Settings.get_default()
    if settings is not None and not settings.get_property("gtk-enable-animations"):
        return 0
    return 120 if lumaui.reduced_motion() else SETTLE_MS


def _ease(t: float) -> float:
    """v71 --ease, near enough: cubic-bezier(.2, .8, .2, 1) as an ease-out."""
    return 1 - (1 - t) ** 3


class SwipeRow(Gtk.Widget):
    """A row that acts when slid sideways. `start` is revealed by sliding toward the end edge."""

    __gtype_name__ = "LumaUISwipeRow"

    def __init__(self, child: Gtk.Widget, *, start: SwipeAction | None = None, end: SwipeAction | None = None,
                 phone_only: bool = True) -> None:
        super().__init__()
        self.add_css_class("lumaui-swipe-row")
        self.start, self.end, self.phone_only = start, end, phone_only
        self._dx = 0.0                  # the row's offset
        self._reveal: tuple[float, float] | None = (0.0, 0.0)   # the reveal's x and width; None: follows the row
        self._side: SwipeAction | None = None
        self._on = False
        self._vertical = False
        self._tick = 0
        self.reveal = Gtk.Box(css_classes=["lumaui-swipe-reveal"], can_target=False)
        self.face = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["lumaui-swipe-face"],
                            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, can_target=False)
        self.face_icon = Gtk.Image(css_classes=["lumaui-swipe-icon"])
        self.face_label = Gtk.Label(css_classes=["lumaui-swipe-label"])
        self.face.append(self.face_icon)
        self.face.append(self.face_label)
        self.reveal.set_parent(self)
        self.face.set_parent(self)
        self.child = child
        child.set_parent(self)
        self.reveal.set_child_visible(False)
        self.face.set_child_visible(False)

        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", lambda g, x, y: self.drag_to(x, y, g))
        drag.connect("drag-end", lambda _g, _x, _y: self.release())
        drag.connect("cancel", lambda *_a: self.release(cancelled=True))
        self.add_controller(drag)
        self._gesture = drag

    # ── state ──────────────────────────────────────────────────────────────
    @property
    def offset(self) -> float:
        return self._dx

    @property
    def committing(self) -> bool:
        """Whether letting go now would act (an action on this side, past 90 px)."""
        return self._side is not None and abs(self._dx) > COMMIT

    def enabled(self) -> bool:
        if not (self.start or self.end):
            return False
        if not self.phone_only:
            return True
        root = self.get_root()
        return isinstance(root, Gtk.Widget) and 0 < root.get_width() < PHONE_WIDTH

    def _action_for(self, toward_end: bool) -> SwipeAction | None:
        return self.start if toward_end else self.end

    def _rtl(self) -> bool:
        return self.get_direction() == Gtk.TextDirection.RTL

    # ── the drag ───────────────────────────────────────────────────────────
    def _begin(self, gesture: Gtk.GestureDrag, _x: float, _y: float) -> None:
        self._on = self._vertical = False
        self._stop()
        if not self.enabled():
            gesture.set_state(Gtk.EventSequenceState.DENIED)

    def drag_to(self, mx: float, my: float, gesture: Gtk.Gesture | None = None) -> None:
        """Follow the finger, `mx`/`my` from where it went down (also how the gallery shows a swipe mid-way)."""
        if self._vertical:
            return
        if not self._on:
            if abs(mx) > SLOP and abs(mx) > abs(my) * SIDEWAYS:
                self._on = True
                if gesture is not None:
                    gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            elif abs(my) > SLOP:
                self._vertical = True
                if gesture is not None:
                    gesture.set_state(Gtk.EventSequenceState.DENIED)
                return
            else:
                return
        toward_end = (mx > 0) != self._rtl()
        action = self._action_for(toward_end)
        self._side = action
        dx = mx if action else (1 if mx > 0 else -1) * min(RUBBER, abs(mx)) * 0.5
        self._set(dx, None)

    def release(self, cancelled: bool = False) -> None:
        """Let go: past 90 px with an action, slide away and act; otherwise spring back."""
        self._vertical = False
        if not self._on:
            return
        self._on = False
        action = None if cancelled else self._side
        go = action is not None and abs(self._dx) > COMMIT
        hx, _hy, hw, _hh = self.host_box()
        if go:   # the row slides out of sight and the reveal takes the whole row
            target = (hw * 1.05 if self._dx > 0 else -hw * 1.05, (hx, hw))
        else:    # the row springs back and the reveal shuts toward its edge
            target = (0.0, (hx if self._dx > 0 else hx + hw, 0.0))
        self._animate(target, lambda: self._settled(action if go else None))

    def _settled(self, action: SwipeAction | None) -> None:
        self._side = None
        self._set(0.0, (0.0, 0.0))
        if action is not None:
            action.activate(self)

    def host(self) -> Gtk.Widget:
        """The row this sits in (v71 slides `.crow` itself): the nearest Gtk.ListBoxRow, or this widget."""
        row = self.get_ancestor(Gtk.ListBoxRow)
        return row if row is not None else self

    def host_box(self) -> tuple[float, float, float, float]:
        """The host row's border box in this widget's coordinates (the reveal covers its padding too)."""
        host = self.host()
        if host is not self:
            ok, bounds = host.compute_bounds(self)
            if ok:
                return bounds.get_x(), bounds.get_y(), bounds.get_width(), bounds.get_height()
        return 0.0, 0.0, float(self.get_width()), float(self.get_height())

    def reveal_rect(self) -> tuple[float, float]:
        """The coloured reveal's x and width: the gap the row leaves at its edge, unless it is settling."""
        if self._reveal is not None:
            return self._reveal
        hx, _hy, hw, _hh = self.host_box()
        return (hx, self._dx) if self._dx > 0 else (hx + hw + self._dx, -self._dx)

    def _set(self, dx: float, reveal: tuple[float, float] | None) -> None:
        self._dx, self._reveal = dx, reveal
        side = self._side
        visible = (abs(dx) if reveal is None else reveal[1]) > 0.5
        self.reveal.set_child_visible(visible)
        self.face.set_child_visible(visible and side is not None)
        for colour in SWIPE_COLORS:
            self.reveal.remove_css_class(colour)
        if side is not None:
            self.reveal.add_css_class(side.color)
            self.face_icon.set_from_icon_name(icons.icon_name(side.icon))
            self.face_label.set_label(side.label)
            self.face_label.set_visible(bool(side.label))
        else:
            self.reveal.add_css_class("none")
        if visible and side is not None:
            self.reveal.remove_css_class("none")
        (self.add_css_class if self.committing else self.remove_css_class)("go")
        (self.add_css_class if visible else self.remove_css_class)("swiping")
        host = self.host()
        if host is not self:
            (host.add_css_class if visible else host.remove_css_class)("lumaui-swiping")
        self.queue_allocate()

    # ── motion ─────────────────────────────────────────────────────────────
    def _stop(self) -> None:
        if self._tick:
            self.remove_tick_callback(self._tick)
            self._tick = 0

    def _animate(self, target: tuple[float, tuple[float, float]], done: Callable[[], None]) -> None:
        self._stop()
        duration = _settle_ms()
        clock = self.get_frame_clock()
        if not duration or clock is None or not self.get_mapped():
            self._set(target[0], target[1])
            done()
            return
        start = (self._dx, self.reveal_rect())
        began = clock.get_frame_time()

        def tick(_widget: Gtk.Widget, frame_clock: Gdk.FrameClock) -> bool:
            t = min(1.0, (frame_clock.get_frame_time() - began) / 1000 / duration)
            k = _ease(t)
            dx = start[0] + (target[0] - start[0]) * k
            reveal = tuple(a + (b - a) * k for a, b in zip(start[1], target[1]))
            self._set(dx, reveal)  # type: ignore[arg-type]
            if t >= 1:
                self._tick = 0
                done()
                return False
            return True

        self._tick = self.add_tick_callback(tick)

    # ── layout ─────────────────────────────────────────────────────────────
    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return self.child.get_request_mode()

    def do_measure(self, orientation: Gtk.Orientation, for_size: int):
        return self.child.measure(orientation, for_size)

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        moved = Gsk.Transform().translate(Graphene.Point().init(self._dx, 0)) if self._dx else None
        self.child.allocate(width, height, baseline, moved)
        x, w = self.reveal_rect()
        if w <= 0.5:
            return
        hx, hy, hw, hh = self.host_box()
        self.reveal.allocate(max(1, round(w)), round(hh), -1,
                             Gsk.Transform().translate(Graphene.Point().init(round(x), round(hy))))
        face_w = max(FACE_MIN, self.face.measure(Gtk.Orientation.HORIZONTAL, -1)[1])
        face_h = min(round(hh), self.face.measure(Gtk.Orientation.VERTICAL, face_w)[1])
        # the face sits at the edge being revealed: the start edge when the row moves toward the end
        fx = hx if self._dx > 0 else hx + hw - face_w
        self.face.allocate(face_w, face_h, -1,
                           Gsk.Transform().translate(Graphene.Point().init(round(fx), round(hy + (hh - face_h) / 2))))

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        x, w = self.reveal_rect()
        if w > 0.5:
            _hx, hy, _hw, hh = self.host_box()
            snapshot.push_clip(Graphene.Rect().init(x, hy, w, hh))
            self.snapshot_child(self.reveal, snapshot)
            if self.face.get_child_visible():
                self.snapshot_child(self.face, snapshot)
            snapshot.pop()
        self.snapshot_child(self.child, snapshot)

    def do_dispose(self) -> None:
        self._stop()
        for part in (self.reveal, self.face, self.child):
            if part is not None and part.get_parent() is self:
                part.unparent()
        super().do_dispose()
