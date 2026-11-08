# SPDX-License-Identifier: Apache-2.0
"""LumaUI structure: the disclosure (a fold).

A summary row with an icon, a label and a chevron that turns 180° when open
(0.2 s; instant with reduced motion). Two forms (Depot 3.9):

- **line**: a quiet line (600 12.5 px, ink-3) that sits in line with the text
  of the cards above it and opens into an inset card under it (Technical info).
- **card**: the whole fold is an inset card with a header row (650 15 px, an
  icon in ink-2), its contents under the header (Updates' Automatic updates and
  Recently updated).

The open state is the widget's own; an app that rebuilds its page keeps it by
reading `expanded` first and passing it back (`expanded=`).

    tech = Disclosure("Technical info", content=facts)                         # line form
    auto = Disclosure("Automatic updates", icon="settings-2", content=rows, form="card")
    tech.connect_toggled(lambda open_: remember(open_))
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk  # noqa: E402

from . import icons, lumaui  # noqa: E402

__all__ = ["Disclosure", "DISCLOSURE_FORMS"]

DISCLOSURE_FORMS = ("line", "card")


class Disclosure(Gtk.Box):
    """A summary button and the content it shows."""

    __gtype_name__ = "LumaUIDisclosure"

    def __init__(self, label: str, *, content: Gtk.Widget, icon: str | None = None, form: str = "line",
                 expanded: bool = False, on_toggled: Callable[[bool], None] | None = None) -> None:
        if form not in DISCLOSURE_FORMS:
            raise ValueError(f"a disclosure is {' or '.join(DISCLOSURE_FORMS)}, not {form!r}")
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("lumaui-disclosure")
        self.add_css_class(form)
        if form == "card":
            self.add_css_class("lumaui-inset")
        self.form = form
        self._handlers: list[Callable[[bool], None]] = [on_toggled] if on_toggled else []

        self.summary = Gtk.Button(halign=Gtk.Align.FILL if form == "card" else Gtk.Align.START,
                                  accessible_role=Gtk.AccessibleRole.BUTTON)
        self.summary.add_css_class("lumaui-disclosure-summary")
        line = Gtk.Box()
        line.add_css_class("lumaui-disclosure-line")
        if icon:
            lead = icons.image(icon)
            lead.add_css_class("lumaui-disclosure-icon")
            line.append(lead)
        self.title = Gtk.Label(label=label, xalign=0, hexpand=form == "card")
        line.append(self.title)
        self.chevron = icons.image("chevron-down")
        self.chevron.add_css_class("lumaui-disclosure-chevron")
        line.append(self.chevron)
        self.summary.set_child(line)
        self.summary.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self.summary.connect("clicked", lambda _b: self.set_expanded(not self.expanded))
        self.append(self.summary)

        self.revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                                     transition_duration=lumaui.duration("disclosure"))
        body = content
        if form == "line":  # opens into an inset card
            from .content_inset import Inset
            body = Inset(padding="card")
            body.append(content)
        body.add_css_class("lumaui-disclosure-body")
        self.content = content
        self.revealer.set_child(body)
        self.append(self.revealer)
        self._expanded = False
        self.set_expanded(expanded, notify=False)

    @property
    def expanded(self) -> bool:
        return self._expanded

    def connect_toggled(self, callback: Callable[[bool], None]) -> None:
        self._handlers.append(callback)

    def set_expanded(self, expanded: bool, *, notify: bool = True) -> None:
        if expanded == self._expanded and self.revealer.get_reveal_child() == expanded:
            return
        self._expanded = expanded
        self.revealer.set_reveal_child(expanded)
        lumaui.set_css_class(self, "open", expanded)
        self.summary.update_state([Gtk.AccessibleState.EXPANDED], [GObject.Value(GObject.TYPE_INT, int(expanded))])
        if notify:
            for handler in self._handlers:
                handler(expanded)
