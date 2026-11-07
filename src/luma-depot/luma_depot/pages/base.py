# SPDX-License-Identifier: Apache-2.0
"""What every Depot page is: a body the shell scrolls, a bar it hosts, state it can reset.

A page builds its body from the world and its own state. The shell keeps one
bar per page and never redraws it while it is in use (handoff 2.4, 3.11): a
page that changes its body after a menu pick calls `shell.refresh_body()`,
which rebuilds only the body and keeps the bar, its open menu and the search
field's focus.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover
    from ..shell import DepotShell

__all__ = ["Page", "intro", "kicker_title", "label", "section"]


def label(text: str, css: str | tuple[str, ...] = (), *, wrap: bool = False, xalign: float = 0,
          lines: int = 0, ellipsize: bool = False) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=xalign, wrap=wrap)
    if wrap:
        widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if lines:
        widget.set_lines(lines)
        widget.set_ellipsize(Pango.EllipsizeMode.END)
    elif ellipsize:
        widget.set_ellipsize(Pango.EllipsizeMode.END)
    for name in (css,) if isinstance(css, str) else css:
        if name:
            widget.add_css_class(name)
    return widget


def intro(title: str, line: str | None = None) -> Gtk.Widget:
    """A top-level page's head: the large title (34/750) and one short line under it (`.dq-intro`)."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.add_css_class("dq-intro")
    box.append(label(title, "dq-title"))
    if line:
        box.append(label(line, "dq-intro-line", wrap=True))
    return box


def kicker_title(kicker: str, title: str) -> Gtk.Widget:
    """Today's head: the date as a small kicker over the title (`.dq-hello`)."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.add_css_class("dq-hello")
    box.append(label(kicker.upper(), "dq-kicker"))
    box.append(label(title, "dq-title"))
    return box


def section(title: str, body: Gtk.Widget, *, kicker: str = "", more: tuple[str, object] | None = None,
            note: str = "") -> Gtk.Widget:
    """A section: an optional kicker over the 21/700 title, "See all ›" on the title's line (`.dq-sec`)."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.add_css_class("dq-sec")
    head = Gtk.Box(valign=Gtk.Align.END)
    head.add_css_class("dq-sec-head")
    words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.END)
    if kicker:
        words.append(label(kicker.upper(), "dq-sec-kicker"))
    words.append(label(title, "dq-sec-title"))
    head.append(words)
    if note:
        head.append(label(note, "dq-sec-note"))
    if more is not None:
        from luma_appkit import icons
        words_text, on_click = more
        link = Gtk.Button(valign=Gtk.Align.END)
        link.add_css_class("dq-link")
        line = Gtk.Box()
        line.append(Gtk.Label(label=words_text))
        line.append(icons.image("chevron-right"))
        link.set_child(line)
        link.connect("clicked", lambda _b: on_click())
        head.append(link)
    box.append(head)
    box.append(body)
    return box


class Page:
    """One top-level place (Today, Apps, Looks, Tweaks, Library, Updates) or the item or search page."""

    #: The sidebar place it belongs to.
    place = ""
    #: Whether the bar holds the search field (every top-level page does).
    searchable = True

    def __init__(self, shell: "DepotShell") -> None:
        self.shell = shell

    @property
    def world(self):
        return self.shell.world

    def reset(self) -> None:
        """Back to defaults: moving to another sidebar page resets its filters (2.3)."""

    def snapshot(self) -> dict:
        """What Back restores (filters, open folds); the shell restores scroll itself."""
        return {}

    def restore(self, state: dict) -> None:
        pass

    def build(self) -> Gtk.Widget:
        raise NotImplementedError

    def bar_items(self) -> list:
        """The bar after the search field, left to right (2.4)."""
        return []
