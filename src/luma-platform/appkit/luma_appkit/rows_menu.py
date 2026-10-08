# SPDX-License-Identifier: Apache-2.0
"""LumaUI rows: rich menu rows and custom menu sections (MN1).

    FloatingMenu([
        RichMenuItem("Grid", icon="grid-3x3", toggle=True, on_toggle=show_grid),          # a trailing switch
        RichMenuItem("Timer", icon="timer", value="3 s", on_activate=pick_timer),         # a value and chevron
        RichMenuItem("zsh", icon="terminal", subtitle="~/Projects/luma", selected=True,
                     trail=[RowAction("x", "Close session", close)], rename=True, on_rename=rename),
        RichMenuItem("Importing…", icon="download", busy=True),
        MenuSection(import_card),                                                         # any widget
    ]).popup(anchor)

The same rows present in `MenuDrawer.present_items` at phone width, so every
menu keeps its rows on a phone. v70: `.crmi` (36 min, a 11 px subtitle),
`#cm-pop .cmsw` (a switch at 85%) and `.cmval` (a 12 px value and a 14 px
chevron), `.tsesspop` (trailing actions shown on hover or focus, an inline
rename), `#lmenu .pimp/.pnewal` (custom content).

Behaviour: a toggle row flips its switch and stays open; a value row, a
plain row and a trailing action close the menu, then run. A rename row's
pencil (or F2) swaps the name for an entry: Enter keeps it, Esc puts it
back. A busy row shows a spinner where its icon was and does nothing.
"""
from __future__ import annotations

from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, GObject, Gtk, Pango  # noqa: E402

from . import icons, lumaui  # noqa: E402
from .action_bubble import MenuItem  # noqa: E402

__all__ = ["MenuSection", "RichMenuItem"]


class RichMenuItem(MenuItem):
    """A menu row with more to say: a subtitle, a switch, a value, a check, trailing actions, a rename, busy."""

    def __init__(self, label: str, *, icon: str | None = None, subtitle: str | None = None,
                 toggle: bool | None = None, on_toggle: Callable[[bool], None] | None = None,
                 value: str | None = None, busy: bool = False, trail: Sequence[object] = (),
                 rename: bool = False, on_rename: Callable[[str], None] | None = None,
                 on_activate: Callable[[], None] | None = None, selected: bool = False,
                 danger: bool = False, gicon: object | None = None, checked: bool = False) -> None:
        if toggle is not None and value is not None:
            raise ValueError("a menu row is a switch or a value, not both")
        if rename and on_rename is None:
            raise ValueError("a row that renames needs on_rename")
        super().__init__(label, icon=icon, gicon=gicon, note=None, on_activate=on_activate, selected=selected)
        self.subtitle, self.toggle, self.on_toggle = subtitle, toggle, on_toggle
        self.value, self.busy, self.trail = value, busy, tuple(trail)
        self.rename, self.on_rename, self.danger = rename, on_rename, danger
        self.checked = checked

    def menu_widget(self, close: Callable[[], None]) -> Gtk.Widget:
        """The row, for FloatingMenu and MenuDrawer; `close` dismisses the menu it is in."""
        return _RichRow(self, close)


class MenuSection:
    """Custom content in a menu (an import card, a new-album field): the widget as it is."""

    def __init__(self, widget: Gtk.Widget, *, label: str | None = None) -> None:
        self.widget, self.label = widget, label

    def menu_widget(self, close: Callable[[], None]) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class("lumaui-menu-section")
        if self.label:
            heading = Gtk.Label(label=self.label, xalign=0)
            heading.add_css_class("lumaui-menu-heading")
            box.append(heading)
        # One widget, two presentations (the card on a computer, the drawer on a phone): move it.
        parent = self.widget.get_parent()
        if parent is not None:
            parent.remove(self.widget)
        box.append(self.widget)
        box.menu_buttons = []
        return box


class _RichRow(Gtk.Button):
    __gtype_name__ = "LumaUIRichMenuRow"

    def __init__(self, item: RichMenuItem, close: Callable[[], None]) -> None:
        checkbox = item.toggle is not None
        super().__init__(accessible_role=Gtk.AccessibleRole.MENU_ITEM_CHECKBOX if checkbox
                         else Gtk.AccessibleRole.MENU_ITEM)
        self.item, self._close = item, close
        for name in ("lumaui-menu-item", "lumaui-menu-row", "lumaui-menu-rich"):
            self.add_css_class(name)
        lumaui.set_css_class(self, "on", item.selected)
        lumaui.set_css_class(self, "danger", item.danger)
        self.menu_buttons = [self]
        line = Gtk.Box()  # the gap is the menu row token's (CSS border-spacing), not a second one here
        line.add_css_class("lumaui-menu-line")
        if item.busy:
            spinner = Gtk.Spinner(spinning=True)
            spinner.add_css_class("lumaui-menu-busy")
            line.append(spinner)
            self.set_sensitive(False)
        elif item.gicon is not None:
            image = Gtk.Image.new_from_gicon(item.gicon)
            image.add_css_class("lumaui-app-icon")
            line.append(image)
        elif item.icon:
            line.append(icons.image(item.icon))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        # Rows wrap rather than cut (v70 .crmi: "Save where photos are taken" takes two lines).
        self.title = Gtk.Label(label=item.label, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        text.append(self.title)
        self.entry: Gtk.Entry | None = None
        if item.subtitle:
            sub = Gtk.Label(label=item.subtitle, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
            sub.add_css_class("lumaui-menu-subtitle")
            text.append(sub)
        self._text = text
        line.append(text)
        self.switch: Gtk.Switch | None = None
        if item.value is not None:
            value = Gtk.Label(label=item.value)
            value.add_css_class("lumaui-menu-value")
            line.append(value)
            chevron = icons.image("chevron-right")
            chevron.add_css_class("lumaui-menu-chevron")
            line.append(chevron)
        if item.checked:
            check = icons.image("check")
            check.add_css_class("lumaui-menu-check")
            line.append(check)
        if checkbox:
            # The row is the control: the switch shows its state and never takes a click of its own.
            self.switch = Gtk.Switch(active=bool(item.toggle), can_focus=False, can_target=False,
                                     valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.PRESENTATION)
            self.switch.add_css_class("lumaui-menu-switch")
            line.append(self.switch)
        self.trail_buttons: list[Gtk.Button] = []
        actions = list(item.trail)
        if item.rename:
            from .rows_navigation import RowAction
            actions.insert(0, RowAction("pencil", f"Rename {item.label}", self.start_rename))
        # The row gap separates the label from its actions, not actions from each other.
        # v70 .tsesspop .tx places the 22 px pencil and close controls edge to edge.
        action_box = Gtk.Box()
        if actions:
            action_box.add_css_class("lumaui-menu-actions")
            line.append(action_box)
        for action in actions:
            button = Gtk.Button(tooltip_text=action.label, valign=Gtk.Align.CENTER)
            button.add_css_class("lumaui-menu-trail")
            button.set_child(icons.image(action.icon))
            button.update_property([Gtk.AccessibleProperty.LABEL], [action.label])
            renaming = item.rename and action is actions[0]
            button.connect("clicked", lambda _b, a=action, r=renaming: a.on_activate() if r else self._run(a.on_activate))
            action_box.append(button)
            self.trail_buttons.append(button)
        self.set_child(line)
        self._speak()
        self.connect("clicked", lambda _b: self._activated())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    def _speak(self) -> None:
        item = self.item
        # The row is named by its label; what it says beside that is its description.
        self.update_property([Gtk.AccessibleProperty.LABEL], [item.label])
        more = [part for part in (item.subtitle, item.value, "busy" if item.busy else None,
                                  "chosen" if item.checked else None) if part]
        self.update_property([Gtk.AccessibleProperty.DESCRIPTION], [", ".join(more)])
        if self.switch is not None:
            self.update_state([Gtk.AccessibleState.CHECKED], [GObject.Value(GObject.TYPE_INT, int(self.switch.get_active()))])

    def _run(self, callback: Callable[[], None] | None) -> None:
        self._close()
        if callback is not None:
            GLib.idle_add(lambda: (callback(), False)[1])

    def _activated(self) -> None:
        if self.entry is not None or self.item.busy:
            return
        if self.switch is not None:
            active = not self.switch.get_active()
            self.switch.set_active(active)
            self.item.toggle = active
            self._speak()
            if self.item.on_toggle is not None:
                self.item.on_toggle(active)
            return
        self._run(self.item.on_activate)

    # ── rename ─────────────────────────────────────────────────────────────

    def start_rename(self) -> None:
        if not self.item.rename or self.entry is not None:
            return
        entry = Gtk.Entry(text=self.item.label, hexpand=True)
        entry.add_css_class("lumaui-menu-rename")
        entry.update_property([Gtk.AccessibleProperty.LABEL], [f"Rename {self.item.label}"])
        entry.connect("activate", lambda e: self._finish(e.get_text()))
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, keyval, *_a: (self._finish(None), True)[1]
                     if keyval == Gdk.KEY_Escape else False)
        entry.add_controller(keys)
        self._text.insert_child_after(entry, self.title)
        self.title.set_visible(False)
        self.entry = entry
        for button in self.trail_buttons:
            button.set_visible(False)
        entry.grab_focus()
        entry.select_region(0, -1)

    def _finish(self, text: str | None) -> None:
        entry, self.entry = self.entry, None
        if entry is None:
            return
        self._text.remove(entry)
        self.title.set_visible(True)
        for button in self.trail_buttons:
            button.set_visible(True)
        name = (text or "").strip()
        if text is not None and name and name != self.item.label:
            self.item.label = name
            self.title.set_label(name)
            self._speak()
            self.item.on_rename(name)
        self.grab_focus()

    def _key(self, _controller, keyval: int, _code: int, _state: object) -> bool:
        if keyval == Gdk.KEY_F2 and self.item.rename:
            self.start_rename()
            return True
        return False
