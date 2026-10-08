# SPDX-License-Identifier: Apache-2.0
"""LumaUI structure: the navigation trail.

NavigationTrailBar — where you came from sits inline with where you are:
"‹ Albums · 12 albums", never a framed Back button on a row of its own
(v70 `lTrail`, `.lnt`; Tide's pages and its Sources pane). It is the widget
over the existing history model (`NavigationTrail`/`Place`), so the back
chevron, the mouse's back button and Alt+Left (bind_navigation_input) all
move the same trail, and the bar follows every change by itself.

- With a title (a page): the chevron stands alone before the title (its
  name says where it goes: "Back to Albums"), the title is title-1, and the
  meta line follows after a dot.
- Without a title (a pane's own trail): the chevron carries the name,
  "‹ Sources".
- Nowhere to go back to: no chevron.

    bar = NavigationTrailBar(trail, meta=["12 albums", "214 songs"])
    bar.set_meta(["12 albums"])
    NavigationTrailBar(pane_trail, title=False)

PageHeader — v71's page header (`.lnt`): the same trail, which stacks on a
phone (under 560): a 30/750 title, and its meta on a line of its own at 13.
Without a trail it is a plain title and meta.

    header = PageHeader(trail, meta=["12 albums", "214 songs"])
    header = PageHeader(title="Appearance", meta=["Dark", "Blue accent"])

Rules every part follows: docs/developer/kit/lumaui-principles.md and
behaviour.md. CSS lives in luma-appkit-base.css under `/* LumaUI:
Navigation trail */`.
"""
from __future__ import annotations

from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from . import icons  # noqa: E402
from .navigation import NavigationTrail, Place  # noqa: E402
from .structure_adapt import children  # noqa: E402

__all__ = ["NavigationTrailBar", "PageHeader"]

#: A meta item: plain text, or (text, callback) for one that goes somewhere.
Meta = str | tuple[str, Callable[[], None]]


class NavigationTrailBar(Gtk.Box):
    """The trail, inline: back chevron, title, meta."""

    __gtype_name__ = "LumaUINavigationTrailBar"

    def __init__(self, trail: NavigationTrail, *, meta: Sequence[Meta] = (), title: bool = True) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, accessible_role=Gtk.AccessibleRole.NAVIGATION)
        self.add_css_class("lumaui-trail")
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Navigation"])
        self.trail = trail
        self.with_title = title
        self._meta: list[Meta] = list(meta)

        self.back = Gtk.Button(valign=Gtk.Align.CENTER)
        self.back.add_css_class("lumaui-trail-back")
        back_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.back_glyph = icons.image("chevron-left")
        back_line.append(self.back_glyph)
        self.back_label = Gtk.Label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=24)
        back_line.append(self.back_label)
        self.back.set_child(back_line)
        self.back.connect("clicked", lambda _b: self.trail.back())
        self.append(self.back)
        if title:
            self.back.add_css_class("icon-only")
            self.back_label.set_visible(False)

        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, accessible_role=Gtk.AccessibleRole.HEADING)
        self.title.add_css_class("lumaui-trail-title")
        self.title.set_visible(title)
        self.append(self.title)

        self.meta_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, valign=Gtk.Align.CENTER)
        self.meta_box.add_css_class("lumaui-trail-meta")
        self.append(self.meta_box)

        self._stop = trail.add_listener(lambda _place, _direction: self.refresh())
        self.connect("destroy", lambda *_a: self._stop())
        self.connect("direction-changed", lambda *_a: self.refresh())
        self.refresh()

    # ── public API ────────────────────────────────────────────────────────

    def set_meta(self, meta: Sequence[Meta]) -> None:
        """What the page holds, after the title: "12 albums", "214 songs"."""
        self._meta = list(meta)
        self._fill_meta()

    def refresh(self) -> None:
        places = self.trail.places
        current: Place = places[-1]
        previous: Place | None = places[-2] if len(places) > 1 else None
        rtl = self.get_direction() == Gtk.TextDirection.RTL
        self.back_glyph.set_from_icon_name(icons.icon_name("chevron-right" if rtl else "chevron-left"))
        self.back.set_visible(previous is not None)
        if previous is not None:
            where = f"Back to {previous.title}"
            self.back.set_tooltip_text(where)
            self.back.update_property([Gtk.AccessibleProperty.LABEL], [where])
            self.back_label.set_label(previous.title)
            # GTK rounds a tracked 11.5 px label's natural width down and then
            # ellipsizes it at exactly that width; asking for its characters avoids it.
            self.back_label.set_width_chars(min(len(previous.title), 24))
        self.title.set_label(current.title)
        self._fill_meta()

    # ── internals ─────────────────────────────────────────────────────────

    def _fill_meta(self) -> None:
        for child in children(self.meta_box):
            self.meta_box.remove(child)
        items = [m for m in self._meta if m]
        for index, item in enumerate(items):
            if self.with_title or index:
                dot = Gtk.Box(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.PRESENTATION)
                dot.add_css_class("lumaui-trail-dot")
                self.meta_box.append(dot)
            if isinstance(item, tuple):
                text, callback = item
                button = Gtk.Button(label=text, valign=Gtk.Align.CENTER)
                button.add_css_class("lumaui-trail-link")
                button.connect("clicked", lambda _b, cb=callback: cb())
                self.meta_box.append(button)
            else:
                label = Gtk.Label(label=item, ellipsize=Pango.EllipsizeMode.END)
                self.meta_box.append(label)
        self.meta_box.set_visible(bool(items))


class PageHeader(Gtk.Box):
    """v71 `.lnt`: back, title and meta on one line; on a phone the meta takes its own line."""

    __gtype_name__ = "LumaUIPageHeader"

    def __init__(self, trail: NavigationTrail | None = None, *, title: str = "",
                 meta: Sequence[Meta] = ()) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("lumaui-page-header")
        if trail is None:
            trail = NavigationTrail(Place("page", title))
        self.bar = NavigationTrailBar(trail, meta=meta)
        self.append(self.bar)
        self.phone = False
        from . import lumaui_tokens as tokens
        from .structure_adapt import WidthWatch
        self._watch = WidthWatch(self, lambda width: self._stack(width <= tokens.PHONE_MAX_WIDTH),
                                 threshold=tokens.PHONE_MAX_WIDTH)
        stop = trail.add_listener(lambda _place, _direction: self._first_dot())
        self.connect("destroy", lambda *_a: stop())

    @property
    def trail(self) -> NavigationTrail:
        return self.bar.trail

    @property
    def title(self) -> Gtk.Label:
        return self.bar.title

    @property
    def meta_box(self) -> Gtk.Box:
        return self.bar.meta_box

    def set_title(self, title: str) -> None:
        """Rename the current page (no step is added)."""
        current = self.trail.current
        self.trail.replace(Place(current.view, title, current.subject))
        self.bar.refresh()

    def set_meta(self, meta: Sequence[Meta]) -> None:
        self.bar.set_meta(meta)
        self._first_dot()

    def _stack(self, phone: bool) -> None:
        if phone == self.phone:
            return
        self.phone = phone
        meta = self.bar.meta_box
        meta.get_parent().remove(meta)
        if phone:
            self.append(meta)
        else:
            self.bar.append(meta)
        (self.add_css_class if phone else self.remove_css_class)("phone")
        self._first_dot()

    def _first_dot(self) -> None:
        # On its own line the meta starts with its text, not a dot.
        first = self.bar.meta_box.get_first_child()
        if first is not None and first.has_css_class("lumaui-trail-dot"):
            first.set_visible(not self.phone)
