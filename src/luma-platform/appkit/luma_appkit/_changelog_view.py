# SPDX-License-Identifier: Apache-2.0
"""The Changelog widget (content_changelog keeps the grouping free of GTK)."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from .content_changelog import ChangeLine  # noqa: E402

#: Depot `.dq-clog ul { max-width: 68ch }`.
_LINE_CHARS = 68


class ChangelogView(Gtk.Box):
    """Depot `.dq-clog`: 650 12 px ink-3 headings, 13/1.5 ink-2 lines 6 apart, a bold lead in ink."""

    __gtype_name__ = "LumaUIChangelog"

    def __init__(self, groups: list[tuple[str, list[ChangeLine]]]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("lumaui-changelog")
        self.groups = groups
        self._texts: list[Gtk.Label] = []
        for heading, lines in groups:
            if heading:
                label = Gtk.Label(label=heading, xalign=0)
                label.add_css_class("lumaui-changelog-heading")
                label.update_property([Gtk.AccessibleProperty.LABEL], [heading])
                self.append(label)
            items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.LIST)
            items.add_css_class("lumaui-changelog-lines")
            for line in lines:
                items.append(self._line(line))
            self.append(items)

    def _line(self, line: ChangeLine) -> Gtk.Widget:
        row = Gtk.Box(accessible_role=Gtk.AccessibleRole.LIST_ITEM)
        row.add_css_class("lumaui-changelog-line")
        bullet = Gtk.Label(label="\u2022", valign=Gtk.Align.START)
        bullet.add_css_class("lumaui-changelog-bullet")
        row.append(bullet)
        text = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=_LINE_CHARS,
                         hexpand=True)
        text.add_css_class("lumaui-changelog-text")
        text._line = line
        self._texts.append(text)
        self._mark(text)
        row.append(text)
        return row

    def _mark(self, text: Gtk.Label) -> None:
        """The lead title is 600 in the full ink; the line itself is ink-2 (the label's CSS colour)."""
        line: ChangeLine = text._line
        title = line.title.strip().rstrip(".")
        body = GLib.markup_escape_text(line.text.strip())
        if not title:
            text.set_markup(body)
            return
        ink = self.get_color()
        colour = "#%02x%02x%02x" % tuple(round(c * 255) for c in (ink.red, ink.green, ink.blue))
        text.set_markup(f"<span weight='600' foreground='{colour}'>{GLib.markup_escape_text(title)}.</span> {body}")

    def do_css_changed(self, change) -> None:
        Gtk.Box.do_css_changed(self, change)
        for text in self._texts:
            self._mark(text)
