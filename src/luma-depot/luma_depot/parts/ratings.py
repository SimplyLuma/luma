# SPDX-License-Identifier: Apache-2.0
"""Ratings and reviews (8.6): the score and its spread on the left, every review in a sideways strip."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from luma_appkit import EdgeFadeScroller, lumaui

from ..model.rules import fmt_count
from ..model.world import Item, _hash
from ..pages.base import label

__all__ = ["ratings"]


def _bars(distribution: tuple[int, ...]) -> Gtk.Widget:
    total = sum(distribution) or 1
    bars = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    bars.add_css_class("dq-rbars")
    for index, count in enumerate(distribution[:5]):
        line = Gtk.Box()
        line.append(label(str(5 - index), "dq-rbar-digit", xalign=1))
        track = Gtk.ProgressBar(fraction=count / total, hexpand=True, valign=Gtk.Align.CENTER)
        track.set_size_request(150, -1)
        track.add_css_class("dq-rbar")
        track.update_property([Gtk.AccessibleProperty.LABEL], [f"{5 - index} stars: {count}"])
        line.append(track)
        bars.append(line)
    return bars


def _review(initials: str, name: str, stars: int, date: str, text: str) -> Gtk.Widget:
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    card.add_css_class("dq-rev")
    card.set_size_request(236, -1)
    head = Gtk.Box()
    head.add_css_class("dq-rev-head")
    avatar = Gtk.Label(label=initials, valign=Gtk.Align.CENTER)
    avatar.add_css_class("dq-rav")
    from .face import hue_paint
    hue_paint(avatar, 0.6, 0.12, _hash(name))
    head.append(avatar)
    who = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
    who.append(label(name, "dq-rev-name"))
    star_line = Gtk.Label(xalign=0)
    star_line.set_markup("★" * stars + f"<span alpha='45%'>{'★' * (5 - stars)}</span>")
    star_line.add_css_class("dq-stars")
    star_line.update_property([Gtk.AccessibleProperty.LABEL], [f"{stars} of 5"])
    who.append(star_line)
    head.append(who)
    stamp = label(date, "dq-rev-date")
    stamp.set_valign(Gtk.Align.START)
    head.append(stamp)
    card.append(head)
    words = label(text, "dq-rev-text", wrap=True, lines=5)
    words.set_max_width_chars(1)  # wrap at the card's width, never widen it
    card.append(words)
    return card


def ratings(item: Item, *, arrows: bool = True) -> Gtk.Widget | None:
    app = item.app
    if app is None or not app.rating_count or not app.rating_distribution:
        return None
    grid = Gtk.Box()
    grid.add_css_class("dq-rgrid")
    score = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=False, valign=Gtk.Align.START)
    score.add_css_class("dq-rate")
    score.set_size_request(180, -1)
    average = Gtk.Box()
    average.add_css_class("dq-ravg")
    figure = label(f"{app.rating:g}", "dq-ravg-score")
    figure.set_valign(Gtk.Align.BASELINE_FILL)  # the count sits on the figure's baseline
    average.append(figure)
    count = label(f"{fmt_count(app.rating_count)} ratings", "dq-ravg-count")
    count.set_valign(Gtk.Align.BASELINE_FILL)
    average.append(count)
    score.append(average)
    score.append(_bars(app.rating_distribution))
    grid.append(score)
    if app.reviews:
        strip = Gtk.Box(homogeneous=False, halign=Gtk.Align.START)
        strip.add_css_class("dq-reviews")
        for review in app.reviews:
            from gi.repository import Adw
            strip.append(Adw.Clamp(maximum_size=236, tightening_threshold=236, child=_review(*review),
                                   halign=Gtk.Align.START))
        scroller = EdgeFadeScroller(strip, fade=56, arrows=arrows)
        scroller.set_hexpand(True)
        grid.append(scroller)
    return grid
