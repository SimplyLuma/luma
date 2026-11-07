# SPDX-License-Identifier: Apache-2.0
"""The bar's menu buttons: a LumaUI pop menu anchored to its button, updated in place (2.4, 3.5).

Picking an item updates the page body and the button's words in place; the
bar and the open menu are never redrawn.
"""
from __future__ import annotations

from typing import Callable, Sequence

from luma_appkit import BarWidget, Button, CheckMenuItem, RadioMenuItem
from luma_appkit.menus import bar_menu

__all__ = ["choice_button", "filters_button"]


def choice_button(label: Callable[[], str], icon: str, heading: str, choices: Callable[[], Sequence[tuple[str, str]]],
                  current: Callable[[], str], on_pick: Callable[[str], None], *, name: str) -> BarWidget:
    """"All apps ⌄": a radio menu headed `heading`; the check sits on the current choice."""
    button = Button(label(), icon=icon, menu=True)
    button.set_name(name)

    def pick(key: str) -> None:
        on_pick(key)
        button.set_label_text(label())

    def open_menu(_button) -> None:
        bar_menu(button, [heading] + [RadioMenuItem(text, group=name, checked=key == current(),
                                                    on_activate=lambda k=key: pick(k))
                                      for key, text in choices()], label=heading)

    button.connect("clicked", open_menu)
    return BarWidget(button)


def filters_button(groups: Callable[[], Sequence[tuple[str, Sequence[tuple[str, str, str | None, bool]]]]],
                   on_toggle: Callable[[str, str, bool], None], count: Callable[[], int], *, name: str) -> BarWidget:
    """"Filters ⌄" ("Filters · 2" when any are on): headed checkbox groups that stay open while ticked."""
    words = lambda: f"Filters · {count()}" if count() else "Filters"  # noqa: E731
    button = Button(words(), icon="list-filter", menu=True)
    button.set_name(name)
    button.set_chosen(bool(count()))

    def toggled(group: str, key: str, on: bool) -> None:
        on_toggle(group, key, on)
        button.set_label_text(words())
        button.set_chosen(bool(count()))

    def open_menu(_button) -> None:
        rows: list = []
        for index, (heading, entries) in enumerate(groups()):
            if index:
                rows.append(None)
            rows.append(heading)
            rows += [CheckMenuItem(text, icon=glyph, checked=on,
                                   on_toggle=lambda state, g=heading, k=key: toggled(g, k, state))
                     for key, text, glyph, on in entries]
        bar_menu(button, rows, label="Filters")

    button.connect("clicked", open_menu)
    return BarWidget(button)
