# SPDX-License-Identifier: Apache-2.0
"""The PermissionList widget (content_permissions keeps the vocabulary free of GTK)."""
from __future__ import annotations

from typing import Sequence

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from . import icons  # noqa: E402
from .content_permissions import CHANGES, Grant  # noqa: E402


class PermissionListWidget(Gtk.Box):
    """Depot `.dq-perms`: 30 px glyph wells (9 round), 600 12.5 px words, 11.5 px reasons in ink-3."""

    __gtype_name__ = "LumaUIPermissionList"

    def __init__(self, grants: Sequence[Grant]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.LIST)
        self.add_css_class("lumaui-permissions")
        self.grants = list(grants)
        for grant in self.grants:
            self.append(self._row(grant))

    def _row(self, grant: Grant) -> Gtk.Widget:
        row = Gtk.Box(accessible_role=Gtk.AccessibleRole.LIST_ITEM)
        row.add_css_class("lumaui-permission")
        if grant.change == "removed":
            row.add_css_class("removed")
        well = Gtk.Box(valign=Gtk.Align.CENTER, hexpand=False)
        well.add_css_class("lumaui-permission-glyph")
        glyph = icons.image(grant.glyph)
        glyph.set_hexpand(True)
        glyph.set_halign(Gtk.Align.CENTER)
        well.append(glyph)
        row.append(well)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        text.add_css_class("lumaui-permission-text")
        head = Gtk.Box()
        word = Gtk.Label(label=grant.word, xalign=0)
        word.add_css_class("lumaui-permission-word")
        head.append(word)
        if grant.change:
            tag = Gtk.Label(label=CHANGES[grant.change])
            tag.add_css_class("lumaui-permission-change")
            tag.add_css_class(grant.change)
            head.append(tag)
        text.append(head)
        if grant.reason:
            reason = Gtk.Label(label=grant.reason, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
            reason.add_css_class("lumaui-permission-reason")
            text.append(reason)
        row.append(text)
        row.update_property([Gtk.AccessibleProperty.LABEL], [grant.word])
        if grant.reason:
            row.update_property([Gtk.AccessibleProperty.DESCRIPTION], [grant.reason])
        return row
