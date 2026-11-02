# SPDX-License-Identifier: Apache-2.0
"""LumaUI on TV: an item's menu, from the same command model as its desktop context menu.

An app gives a focusable item a menu with `TvFocus.add(item, menu=registry)`,
the `CommandRegistry` it already passes to `attach_context_menu` on the
desktop (one model, so desktop and TV offer the same commands). Holding OK
on the item, or pressing Menu, opens `TvMenu`: a floating panel in the bar
material anchored under the item (above it when there is no room), one row per
visible command, the focus on the first. OK runs a command and closes the
menu; Back closes it; either way the focus returns to the item.

Commands with children are not shown; a TV menu is one level.

A destructive command confirms in the same panel, dropped from the same item:

    TvMenu.confirm(focus, row, "Remove Navidrome?", "Its songs leave your library.", "Remove", remove)

a question, a quiet line saying what happens, then Cancel (focused) and the action.
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import icons  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402
from .commands import CommandRegistry  # noqa: E402
from .tv_stage import TvStage, add_tv_style, tv_material  # noqa: E402

__all__ = ["TvMenu", "menu_commands"]

TV = tokens.TV


def _sheet(opaque: bool) -> str:
    m = lambda name: tv_material(name, opaque=opaque)  # noqa: E731
    t = TV["menu"]
    return f"""
.tv-menu {{ padding: {t['padding']}tv; border-radius: {t['radius']}tv; min-width: {t['min_width']}tv; }}
.tv-menu.tv-bar {{ background-color: {m('sheet')}; }}
.tv-menu-row {{ min-height: {t['row_height']}tv; padding: 0 {t['row_padding_x']}tv; border-radius: {t['row_radius']}tv;
  font-size: {t['row_size']}tv; font-weight: {t['row_weight']}; color: {m('ink')}; }}
.tv-menu-row > box {{ border-spacing: {t['icon_gap']}tv; }}
.tv-menu-row image {{ -gtk-icon-size: {t['icon']}tv; }}
.tv-menu-row.tv-key {{ color: {m('key_ink')}; }}
.tv-menu-row.tv-danger {{ color: {m('danger')}; }}
.tv-menu-row.tv-key.tv-danger {{ color: {m('danger_on_key')}; }}
.tv-menu-question {{ padding: 14tv {t['row_padding_x']}tv 12tv; border-spacing: 6tv; }}
.tv-menu-question .tv-menu-title {{ font-size: 25tv; font-weight: 700; color: {m('ink')}; }}
.tv-menu-question .tv-menu-body {{ font-size: 20tv; color: {m('ink_3')}; }}
"""


def menu_commands(registry: CommandRegistry) -> list:
    """The commands a TV menu lists: visible, one level, in the model's order."""
    found = []
    for group in registry.groups:
        if not group.in_menu:
            continue
        for command in group.commands:
            if command.children or not command.visible():
                continue
            found.append(command)
    return found


class TvMenu(Gtk.Box):
    """The TV menu for one item (see the module docstring); open it with `TvMenu.open`."""

    __gtype_name__ = "LumaUITvMenu"

    def __init__(self, registry: CommandRegistry, question: tuple[str, str] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.MENU)
        add_tv_style(_sheet)
        self.add_css_class("tv-menu")
        self.add_css_class("tv-bar")
        if question is not None:
            head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            head.add_css_class("tv-menu-question")
            for text, css in zip(question, ("tv-menu-title", "tv-menu-body")):
                if text:
                    label = Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=36)
                    label.add_css_class(css)
                    head.append(label)
            self.append(head)
        self.rows: list[Gtk.Widget] = []
        self.commands = menu_commands(registry)
        for command in self.commands:
            row = Gtk.Box(accessible_role=Gtk.AccessibleRole.MENU_ITEM)
            row.add_css_class("tv-menu-row")
            if command.destructive:
                row.add_css_class("tv-danger")
            inner = Gtk.Box(valign=Gtk.Align.CENTER)
            if command.icon:
                inner.append(icons.image(command.icon))
            inner.append(Gtk.Label(label=command.label, xalign=0))
            if command.checked is not None and command.checked():
                inner.append(icons.image("check"))
            row.append(inner)
            row.set_sensitive(command.enabled())
            row.update_property([Gtk.AccessibleProperty.LABEL], [command.label])
            row.set_tv_focus = (lambda on, r=row: (r.add_css_class if on else r.remove_css_class)("tv-key"))
            self.append(row)
            self.rows.append(row)

    @classmethod
    def confirm(cls, focus, item: Gtk.Widget, title: str, body: str, action: str,
                on_confirm: Callable[[], None], *, cancel: str = "Cancel") -> "TvMenu | None":
        """Ask before a destructive command: `title`, `body`, then Cancel (focused) and `action`."""
        from .commands import Command, CommandGroup
        registry = CommandRegistry((CommandGroup(None, (
            Command("tv.cancel", cancel, lambda: None),
            Command("tv.confirm", action, on_confirm, destructive=True),
        )),))
        return cls.open(focus, item, registry, question=(title, body))

    @classmethod
    def open(cls, focus, item: Gtk.Widget, registry: CommandRegistry,
             on_closed: Callable[[], None] | None = None,
             question: tuple[str, str] | None = None) -> "TvMenu | None":
        """Open the menu for `item` on `focus`'s stage; None when it has no command to show."""
        stage = focus.root
        menu = cls(registry, question)
        if not menu.rows or not isinstance(stage, TvStage):
            return None
        unit = stage.unit or 1.0
        ok, bounds = item.compute_bounds(stage)
        x = bounds.get_x() / unit if ok else TV["layout"]["safe_x"]
        top = (bounds.get_y() + bounds.get_height()) / unit + TV["menu"]["gap"] if ok else 200
        stage.place(menu, left=x, top=top)
        stage.raise_child(menu)
        menu._focus, menu._stage, menu._closed = focus, stage, on_closed

        def place_above_if_needed() -> bool:
            if menu.get_parent() is None or not ok:
                return False
            height = stage.get_height() / unit
            needed = menu.get_height() / unit
            if top + needed > height - TV["layout"]["safe_y"]:
                stage.place(menu, left=x, top=max(TV["layout"]["safe_y"], bounds.get_y() / unit - TV["menu"]["gap"] - needed))
            return False
        GLib.idle_add(place_above_if_needed)
        live = [r for r in menu.rows if r.get_sensitive()]
        for row, command in zip(menu.rows, menu.commands):
            focus.add(row, group="tv-menu", on_activate=lambda c=command: menu._run(c),
                      on_key=lambda direction, r=row: menu._step(r, direction, live))
        focus.push_scope(live, on_back=menu._back)
        return menu

    def _step(self, row: Gtk.Widget, direction: str, live: list) -> bool:
        """Up and Down walk the rows in order (a list, not a map); Left and Right stay."""
        from .tv_focus import DOWN, UP
        if direction in (UP, DOWN) and row in live:
            index = live.index(row) + (1 if direction == DOWN else -1)
            if 0 <= index < len(live):
                self._focus.focus(live[index])
        return True

    def _back(self) -> bool:
        self._dismiss()
        return True

    def close(self) -> None:
        """Close the menu and return the focus to its item."""
        if self.get_parent() is None:
            return
        self._dismiss()
        self._focus.pop_scope()

    def _dismiss(self) -> None:
        for row in self.rows:
            self._focus.remove(row)
        self._stage.remove(self)
        if self._closed is not None:
            self._closed()

    def _run(self, command) -> None:
        self.close()
        if command.enabled():
            command.execute()
