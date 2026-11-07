# SPDX-License-Identifier: Apache-2.0
"""Technical info (8.10): a quiet line in line with the cards, opening into an inset card with Format,
Version, Updated, Licence, Website and Permissions in the one vocabulary. Rows with nothing are left out."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from luma_appkit import Disclosure, PermissionList, from_keys, from_reach, icons

from ..model.world import PACKAGE_WORDS, Item
from ..pages.base import label
from .facts_card import website_of

__all__ = ["technical_info", "technical_rows"]


def technical_rows(item: Item) -> list[tuple[str, str]]:
    app = item.app
    if app is None:
        return []
    rows = [("Format", PACKAGE_WORDS.get(app.package_kind, "")), ("Version", app.version),
            ("Updated", app.updated), ("Licence", app.licence), ("Website", website_of(item))]
    return [(key, value) for key, value in rows if value]


def technical_info(item: Item, *, expanded: bool, on_toggled, on_site=None, changes: tuple = ()) -> Gtk.Widget | None:
    """`changes` are what a waiting update asks for (tagged New, Wider…); they are shown with the rest."""
    rows = technical_rows(item)
    grants = []
    if item.app is not None:
        grants = from_reach(item.app.reach) or from_keys(list(item.app.permissions) + list(changes))
        if item.app.reach and changes:
            grants = from_keys(list(changes)) + [g for g in grants if g.key not in {c.key for c in from_keys(changes)}]
    if not rows and not grants:
        return None
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    body.add_css_class("dq-techb")
    for key, value in rows:
        line = Gtk.Box()
        line.add_css_class("dq-fact")
        line.append(label(key, "dq-fact-name"))
        if key == "Website":
            link = Gtk.Button(halign=Gtk.Align.END, hexpand=True)
            link.add_css_class("dq-link")
            inner = Gtk.Box()
            inner.append(Gtk.Label(label=value))
            inner.append(icons.image("arrow-up-right"))
            link.set_child(inner)
            if on_site is not None:
                link.connect("clicked", lambda _b: on_site(value))
            line.append(link)
        else:
            line.append(label(value, "dq-fact-value", xalign=1))
            line.get_last_child().set_hexpand(True)
        body.append(line)
    if grants:
        body.append(label("Permissions", "dq-tech-heading"))
        body.append(PermissionList(grants))
    fold = Disclosure("Technical info", content=body, expanded=expanded, on_toggled=on_toggled)
    fold.add_css_class("dq-tech")
    return fold
