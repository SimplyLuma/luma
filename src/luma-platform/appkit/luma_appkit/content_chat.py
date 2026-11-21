# SPDX-License-Identifier: Apache-2.0
"""LumaUI: a live chat, line by line (a stream's chat, not a 1:1 conversation).

    feed = ChatFeed(max_lines=300)
    feed.append(ChatLine("lumafan", "hello!", lead=platform_logo, color="#9146FF", badges=("subscriber",)))
    feed.append(ChatLine("lumafan", "waves", kind="action"))
    feed.append(ChatLine("", "lumafan raided with 12 viewers", kind="notice"))

A line is one compact block: a 16 px lead (a platform's logo), quiet badge
glyphs, the author in their own colour (made readable on the surface in light,
dark and high contrast; without a colour, a hue from the name), then the text,
selectable, wrapping under the author. "action" lines are italic in the
author's colour; "notice" lines are a centred quiet sentence. `set_dimmed()`
marks a deleted message.

`ChatFeed` keeps the newest `max_lines`, stays at the bottom while you are
there, and offers "Jump to newest" when you have scrolled up.
"""
from __future__ import annotations

import colorsys
import zlib
from typing import Sequence

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import icons, lumaui  # noqa: E402

__all__ = ["ChatLine", "ChatFeed", "CHAT_BADGES", "CHAT_KINDS", "readable_color"]

CHAT_KINDS = ("message", "action", "notice")
#: The badges a line shows, as glyphs; others are dropped.
CHAT_BADGES = {"broadcaster": ("radio-tower", "Broadcaster"), "moderator": ("shield", "Moderator"),
               "vip": ("gem", "VIP"), "subscriber": ("star", "Subscriber")}
_SURFACES = {"light": (1.0, 1.0, 1.0), "dark": (0.118, 0.129, 0.141)}


def _luminance(rgb: tuple[float, float, float]) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def readable_color(color: str, name: str = "", *, dark: bool = False, contrast: float = 4.5) -> str:
    """`color` ("#RRGGBB"; empty: a hue from `name`) moved in lightness until it reads at `contrast`
    against the light or dark surface. Returns "#rrggbb"."""
    text = color.strip().lstrip("#") if color else ""
    if len(text) == 6 and all(c in "0123456789abcdefABCDEF" for c in text):
        rgb = tuple(int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))
        h, lightness, s = colorsys.rgb_to_hls(*rgb)
    else:
        h, lightness, s = (zlib.crc32(name.encode()) % 360) / 360, 0.5, 0.6
    surface = _SURFACES["dark" if dark else "light"]
    step = 0.03 if dark else -0.03
    for _ in range(40):
        rgb = colorsys.hls_to_rgb(h, min(1.0, max(0.0, lightness)), s)
        if _contrast(rgb, surface) >= contrast or not 0.0 < lightness < 1.0:
            break
        lightness += step
    return "#" + "".join(f"{round(c * 255):02x}" for c in rgb)


def _dark(widget: Gtk.Widget) -> bool:
    try:
        from gi.repository import Adw
        return Adw.StyleManager.get_default().get_dark()
    except (ImportError, ValueError):
        return False


class ChatLine(Gtk.Box):
    """One chat message (or action, or notice)."""

    __gtype_name__ = "LumaUIChatLine"

    def __init__(self, author: str, text: str, *, lead: Gtk.Widget | None = None, color: str = "",
                 badges: Sequence[str] = (), kind: str = "message", time: float | None = None) -> None:
        if kind not in CHAT_KINDS:
            raise ValueError(f"a chat line is one of {', '.join(CHAT_KINDS)}, not {kind!r}")
        super().__init__(spacing=6, accessible_role=Gtk.AccessibleRole.LIST_ITEM)
        self.add_css_class("lumaui-chat-line")
        self.add_css_class(kind)
        self.author, self.text, self.kind, self.color, self.time = author, text, kind, color, time
        self.badges = [b for b in badges if b in CHAT_BADGES]
        self.label = Gtk.Label(wrap=True, wrap_mode=2, xalign=0, selectable=True, hexpand=True,
                               natural_wrap_mode=Gtk.NaturalWrapMode.WORD)
        self.label.add_css_class("lumaui-chat-text")
        if kind == "notice":
            self.label.set_xalign(0.5)
            self.label.set_justify(Gtk.Justification.CENTER)
            self.append(self.label)
        else:
            if lead is not None:
                slot = Gtk.Box(valign=Gtk.Align.START)
                slot.add_css_class("lumaui-chat-lead")
                slot.set_size_request(16, 16)
                slot.append(lead)
                self.append(slot)
            for badge in self.badges:
                glyph = icons.image(CHAT_BADGES[badge][0])
                glyph.add_css_class("lumaui-chat-badge")
                glyph.set_valign(Gtk.Align.START)
                glyph.set_tooltip_text(CHAT_BADGES[badge][1])
                self.append(glyph)
            self.append(self.label)
        self._fill()
        # The author's colour follows light and dark while the line shows (no handler outlives it).
        self._style_id = 0
        self.connect("map", self._watch_style)
        self.connect("unmap", self._unwatch_style)
        words = text if kind == "notice" else f"{author}: {text}"
        self.update_property([Gtk.AccessibleProperty.LABEL], [words])

    def _watch_style(self, *_args) -> None:
        try:
            from gi.repository import Adw
            self._style_id = Adw.StyleManager.get_default().connect("notify::dark", lambda *_a: self._fill())
        except (ImportError, ValueError):
            return
        self._fill()

    def _unwatch_style(self, *_args) -> None:
        if self._style_id:
            from gi.repository import Adw
            Adw.StyleManager.get_default().disconnect(self._style_id)
            self._style_id = 0

    def _fill(self) -> None:
        text = GLib.markup_escape_text(self.text)
        if self.kind == "notice":
            self.label.set_markup(text)
            return
        color = readable_color(self.color, self.author, dark=_dark(self))
        author = GLib.markup_escape_text(self.author)
        if self.kind == "action":
            self.label.set_markup(f'<span foreground="{color}"><b>{author}</b> <i>{text}</i></span>')
        else:
            self.label.set_markup(f'<span foreground="{color}"><b>{author}</b></span>  {text}')

    def set_dimmed(self, dimmed: bool) -> None:
        """A deleted message: dimmed, still there."""
        lumaui.set_css_class(self, "dimmed", dimmed)


class ChatFeed(Gtk.Overlay):
    """A scrolling column of ChatLines that keeps the newest `max_lines` and follows the bottom."""

    __gtype_name__ = "LumaUIChatFeed"

    def __init__(self, *, max_lines: int = 300) -> None:
        super().__init__(hexpand=True, vexpand=True)
        self.add_css_class("lumaui-chat-feed")
        self.max_lines = max(1, max_lines)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True, vexpand=True)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("lumaui-chat-list")
        self.list.update_property([Gtk.AccessibleProperty.LABEL], ["Chat"])
        self.scroller.set_child(self.list)
        self.set_child(self.scroller)
        from .content_controls import TextButton
        self.jump = TextButton("Jump to newest", icon="arrow-down", size="small", style="raised",
                               on_click=self.scroll_to_newest)
        self.jump.set_halign(Gtk.Align.CENTER)
        self.jump.set_valign(Gtk.Align.END)
        self.jump.add_css_class("lumaui-chat-jump")
        self.jump.set_visible(False)
        self.add_overlay(self.jump)
        self._following = True
        self._count = 0
        adjustment = self.scroller.get_vadjustment()
        adjustment.connect("value-changed", self._scrolled)
        adjustment.connect("notify::upper", self._grew)

    @property
    def lines(self) -> int:
        return self._count

    def append(self, line: ChatLine) -> None:
        self.list.append(line)
        self._count += 1
        while self._count > self.max_lines:
            first = self.list.get_row_at_index(0)
            if first is None:
                break
            self.list.remove(first)
            self._count -= 1

    def clear(self) -> None:
        self.list.remove_all()
        self._count = 0

    def scroll_to_newest(self) -> None:
        self._following = True
        adjustment = self.scroller.get_vadjustment()
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        self.jump.set_visible(False)

    def _at_bottom(self) -> bool:
        a = self.scroller.get_vadjustment()
        return a.get_value() >= a.get_upper() - a.get_page_size() - 4

    def _scrolled(self, _adjustment) -> None:
        self._following = self._at_bottom()
        self.jump.set_visible(not self._following)

    def _grew(self, *_args) -> None:
        if self._following:
            GLib.idle_add(lambda: (self.scroll_to_newest(), False)[1])
