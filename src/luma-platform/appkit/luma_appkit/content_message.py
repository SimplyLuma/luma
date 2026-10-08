# SPDX-License-Identifier: Apache-2.0
"""LumaUI content: the message bubble.

Messages owns the look and every thread uses it (Messages, Charlie):
theirs is the raised chip, yours the accent gradient in white; 18 round,
6 where one sender's run joins; 9/13/10 inside; 14/1.45 text (v70
`.msg.theirs/.mine > .mcol > .bub`, tokens `--lbub-*` and `--mbub*`, here
`--lumaui-msg-*` and `@luma_bubble_mine*`). The joins are the kit's to
draw: the application says whose it is and whether it continues a run.

    MessageBubble("Freeze strings on the 10th?", joined_below=True)
    MessageBubble("Works for me.", mine=True)
    MessageBubble(child=photo, mine=True)          # a picture or a card in a bubble

v71 ("Messages: a run of messages is one block", `mShape`): consecutive
messages from one sender read as one shape. `MessageRun` holds them, 2 px
apart, and after every layout measures their real widths: corners stay 18
where a bubble is free and tighten to 5 only where two touch, always on the
sender's side, and on the far side where the neighbour above or below is at
least as wide (less 3 px). Cards (events, places, files, songs) and photos
take part like text bubbles; pass the card itself.

    run = MessageRun(mine=False)
    run.append(MessageBubble("Are we still on for the walkthrough at 2?"))
    run.append(EventCard("Launch walkthrough", start, where="Studio"))   # a card takes part as it is
    thread.append(run)                                                   # one run per sender's turn
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from . import lumaui  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402


class _BubbleLayout(Gtk.BoxLayout):
    """As wide as its text, never wider than `cap` (v70 .mcol max-width): longer text wraps."""

    def __init__(self, cap: int) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.cap = cap
        self.relative = False
        self.explicit_cap = False

    def do_measure(self, widget, orientation, for_size):
        # Horizontal requests must not widen wrapped text to fit a prior height.
        measure_size = (-1 if orientation == Gtk.Orientation.HORIZONTAL
                        else min(for_size, self.cap) if for_size >= 0 else self.cap)
        minimum, natural, min_base, nat_base = Gtk.BoxLayout.do_measure(self, widget, orientation, measure_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            natural = max(minimum, min(natural, self.cap))
        return minimum, natural, min_base, nat_base

__all__ = ["MessageBubble", "MessageRun", "run_corners"]

RUN_RADIUS = 18     # a free corner (v71 mShape R)
RUN_JOIN = 5        # where two bubbles touch (v71 mShape J)
RUN_SLACK = 3       # a neighbour within 3 px of as wide still counts as wider
_CORNERS = ("tl", "tr", "br", "bl")


def run_corners(widths: list[float], *, mine: bool, rtl: bool = False) -> list[tuple[bool, bool, bool, bool]]:
    """Which corners join (5 px) for each bubble of a run, top to bottom: (tl, tr, br, bl).

    v71 mShape: the sender's side joins wherever there is a neighbour; the far side joins
    where the neighbour is at least as wide (less 3 px)."""
    out = []
    last = len(widths) - 1
    for k, width in enumerate(widths):
        up, down = k > 0, k < last
        side_top, side_bottom = up, down
        far_top = up and widths[k - 1] >= width - RUN_SLACK
        far_bottom = down and widths[k + 1] >= width - RUN_SLACK
        sender_right = mine != rtl          # yours sit at the end edge: your side is the right in LTR
        if sender_right:
            out.append((far_top, side_top, side_bottom, far_bottom))
        else:
            out.append((side_top, far_top, far_bottom, side_bottom))
    return out


class MessageBubble(Gtk.Box):
    """One message. `joined_above` / `joined_below` when the same sender's run continues."""

    __gtype_name__ = "LumaUIMessageBubble"

    def __init__(self, text: str | None = None, *, mine: bool = False, joined_above: bool = False,
                 joined_below: bool = False, selected: bool = False, child: Gtk.Widget | None = None,
                 hue: float | None = None, padded: bool = False, max_width: int | None = None,
                 max_width_ratio: float | None = None) -> None:
        """`hue` (0-360) is the conversation's (v70 Messages v26, lit by the people in it): your
        own bubbles take it. A bubble is as wide as its text, up to v70's 520 (.mcol), or `max_width`.
        A `child` sits in the 3 px media padding (a photo, a card); `padded=True` gives it the text
        padding instead (Ari's reply: paragraphs and a footer over a hairline).
        `max_width_ratio` also caps the outer bubble to a fraction of its parent column.
        The C twin currently retains its older character-based width model."""
        if (text is None) == (child is None):
            raise ValueError("a message bubble holds text or a child, not both")
        super().__init__(halign=Gtk.Align.END if mine else Gtk.Align.START)
        self.add_css_class("lumaui-message")
        self.add_css_class("mine" if mine else "theirs")
        self.mine = mine
        self.label: Gtk.Label | None = None
        if text is not None:
            self.label = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True, hexpand=True,
                                   wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=200)
            self.label.add_css_class("lumaui-message-text")
            self.append(self.label)
        else:
            self.add_css_class("padded" if padded else "media")
            self.append(child)
        self.set_joins(joined_above, joined_below)
        self.set_selected(selected)
        self._max_width = max_width or tokens.MESSAGE_BUBBLE["max_width"]
        self._max_width_ratio = None
        self._width_clock = None
        self._width_handler = 0
        self.set_layout_manager(_BubbleLayout(self._max_width))
        self.connect("map", self._watch_width)
        self.connect("unmap", self._unwatch_width)
        self.set_max_width_ratio(max_width_ratio)
        if hue is not None:
            lumaui.hue_class(self, hue)

    def set_max_width(self, width: int) -> None:
        """Set the text-width cap; relative caps additionally constrain the whole bubble."""
        if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
            raise ValueError("max_width must be a positive integer")
        self._max_width = width
        self.get_layout_manager().explicit_cap = True
        self._sync_width()

    def set_max_width_ratio(self, ratio: float | None) -> None:
        """Limit the bubble's outer width to this fraction of its immediate column."""
        if ratio is not None and (isinstance(ratio, bool) or not 0 < ratio <= 1):
            raise ValueError("max_width_ratio must be in (0, 1], or None")
        self._max_width_ratio = ratio
        self.get_layout_manager().relative = ratio is not None
        self.get_layout_manager().layout_changed()
        self._sync_width()

    def _watch_width(self, _widget) -> None:
        if self._width_clock is None:
            self._width_clock = self.get_frame_clock()
            self._width_handler = self._width_clock.connect_after("after-paint", self._sync_width)
        self._sync_width()

    def _unwatch_width(self, _widget) -> None:
        if self._width_clock is not None:
            self._width_clock.disconnect(self._width_handler)
        self._width_clock = None
        self._width_handler = 0

    def _sync_width(self, *_args) -> None:
        cap = self._max_width
        parent = self.get_parent()
        if self._max_width_ratio is not None and parent is not None and parent.get_width() > 0:
            context = self.get_style_context()
            padding, border = context.get_padding(), context.get_border()
            inset = padding.left + padding.right + border.left + border.right
            cap = min(cap, max(1, int(parent.get_width() * self._max_width_ratio) - inset))
        layout = self.get_layout_manager()
        if layout.cap != cap:
            layout.cap = cap
            layout.layout_changed()

    def set_joins(self, above: bool, below: bool) -> None:
        """Which corners meet the same sender's neighbours (6 there, 18 elsewhere)."""
        for name, on in (("join-above", above), ("join-below", below)):
            (self.add_css_class if on else self.remove_css_class)(name)

    def set_selected(self, selected: bool) -> None:
        (self.add_css_class if selected else self.remove_css_class)("selected")


class _RunLayout(Gtk.BoxLayout):
    """A column, 2 apart; after it places the messages, the run measures them and sets the corners."""

    def __init__(self, run: "MessageRun") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._run = run

    def do_allocate(self, widget, width, height, baseline):
        Gtk.BoxLayout.do_allocate(self, widget, width, height, baseline)
        self._run.shape()


class MessageRun(Gtk.Box):
    """One sender's consecutive messages, drawn as one block (v71 mShape)."""

    __gtype_name__ = "LumaUIMessageRun"

    def __init__(self, messages: tuple[Gtk.Widget, ...] | list[Gtk.Widget] = (), *, mine: bool = False) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("lumaui-message-run")
        self.add_css_class("mine" if mine else "theirs")
        self.mine = mine
        self._shapes: list[Gtk.Widget] = []
        self._last: list[tuple[bool, bool, bool, bool]] = []
        self.set_layout_manager(_RunLayout(self))
        for message in messages:
            self.append(message)

    def append(self, child: Gtk.Widget, shape: Gtk.Widget | None = None) -> None:  # type: ignore[override]
        """Add the next message. `shape` is what takes the corners when it is not `child` itself
        (a bubble inside a row with a face, say); a MessageBubble or a card is its own shape."""
        shape = shape or child
        if isinstance(shape, MessageBubble):
            shape.set_joins(False, False)        # the run draws every corner
        elif shape is child:
            child.set_halign(Gtk.Align.END if self.mine else Gtk.Align.START)
        shape.add_css_class("lumaui-run-shaped")
        self._shapes.append(shape)
        super().append(child)

    def remove(self, child: Gtk.Widget) -> None:  # type: ignore[override]
        self._shapes = [s for s in self._shapes if s is not child and not s.is_ancestor(child)]
        super().remove(child)
        self.queue_allocate()

    @property
    def shapes(self) -> list[Gtk.Widget]:
        return list(self._shapes)

    def corners(self) -> list[tuple[bool, bool, bool, bool]]:
        """The joins as last drawn (tl, tr, br, bl per message), for tests and the gallery."""
        return list(self._last)

    def shape(self) -> None:
        """Measure the messages as laid out and set each one's corners (run after every layout)."""
        shown = [s for s in self._shapes if s.get_visible()]
        widths = []
        for shape in shown:
            ok, bounds = shape.compute_bounds(self)
            widths.append(bounds.get_width() if ok else float(shape.get_width()))
        joins = run_corners(widths, mine=self.mine, rtl=self.get_direction() == Gtk.TextDirection.RTL)
        self._last = joins
        for shape, corner in zip(shown, joins):
            for name, on in zip(_CORNERS, corner):
                css = f"run-{name}"
                if on != shape.has_css_class(css):   # radius only: no relayout
                    (shape.add_css_class if on else shape.remove_css_class)(css)
