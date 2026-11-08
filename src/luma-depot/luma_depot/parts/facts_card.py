# SPDX-License-Identifier: Apache-2.0
"""The facts card (8.4): Rating (or Used by), Made by with its website and seal, Runs on, Size."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from luma_appkit import Inset, Tooltip, icons

from ..model.rules import fmt_count
from ..model.world import FORMS, Item, World
from ..pages.base import label

__all__ = ["facts_card", "runs_on", "website_of"]


def website_of(item: Item) -> str:
    """The developer's site; Luma's own apps link to simplyluma.com (8.4)."""
    site = item.app.homepage if item.app is not None else ""
    return site or ("simplyluma.com" if item.seal == "luma" else "")


def runs_on(item: Item) -> Gtk.Widget:
    """Each form it runs on, as its own 24 px target with a tooltip (3.8)."""
    row = Gtk.Box(halign=Gtk.Align.END)
    row.add_css_class("dq-runs")
    names = [f.name for f in FORMS if f.id in item.runs]
    row.update_property([Gtk.AccessibleProperty.LABEL], ["Runs on " + ", ".join(names)])
    for form in FORMS:
        if form.id not in item.runs:
            continue
        target = Gtk.Box(focusable=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        target.add_css_class("dq-ri")
        glyph = icons.image(form.icon)
        glyph.set_hexpand(True)
        target.append(glyph)
        Tooltip.attach(target, form.name)
        row.append(target)
    return row


def _row(name: str, value: Gtk.Widget) -> Gtk.Widget:
    line = Gtk.Box()
    line.add_css_class("dq-fact")
    line.append(label(name, "dq-fact-name"))
    value.set_hexpand(True)
    value.set_halign(Gtk.Align.END)
    line.append(value)
    return line


def facts_card(world: World, item: Item, on_site=None) -> Gtk.Widget:
    card = Inset(padding="card")
    card.add_css_class("dq-facts")
    if item.app is not None and item.app.rating_count:
        value = Gtk.Box(halign=Gtk.Align.END)
        value.append(label(f"{item.app.rating:g} \u2605", "dq-fact-value"))
        value.append(label(fmt_count(item.app.rating_count), "dq-fact-count"))
        row = _row("Rating", value)
        row.add_css_class("stars")
        card.append(row)
    elif item.users:
        card.append(_row("Used by", label(fmt_count(item.users), "dq-fact-value")))
    else:
        card.append(_row("Rating", label("Not rated yet", "dq-fact-count")))
    made = Gtk.Box(halign=Gtk.Align.END)
    made.append(label(item.by, "dq-fact-value"))
    site = website_of(item)
    if site:
        link = Gtk.Button()
        link.add_css_class("dq-bysite")
        link.set_child(icons.image("arrow-up-right"))
        Tooltip.attach(link, site, label=False)
        link.update_property([Gtk.AccessibleProperty.LABEL], [f"{item.by}'s website, {site}"])
        if on_site is not None:
            link.connect("clicked", lambda _b: on_site(site))
        made.append(link)
    # The seal, unless it only repeats the maker's name (Luma's own): Verified, or Community.
    seal_words = {"luma": ("badge-check", "Luma"), "verified": ("shield-check", "Verified"),
                  "community": ("users", "Community")}[item.seal]
    if seal_words[1] != item.by:
        seal = Gtk.Box()
        seal.add_css_class("dq-seal")
        seal.add_css_class(item.seal)
        seal.append(icons.image(seal_words[0]))
        seal.append(Gtk.Label(label=seal_words[1]))
        made.append(seal)
    card.append(_row("Made by", made))
    card.append(_row("Runs on", runs_on(item)))
    if item.app is not None and item.app.size_label:
        card.append(_row("Size", label(item.app.size_label, "dq-fact-value")))
    return card
