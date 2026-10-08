# SPDX-License-Identifier: Apache-2.0
"""LumaUI bar: what goes in a grown bar's panel (v71).

The bar grows instead of opening drawers, popovers or dialogs: ⋯, Details,
Share, Add, a context menu and a destructive confirm all open as a panel that
rises above the bar's own row, inside the same glass (v71 `.fbar.phgrow`,
`.fexp`). These are the panel's parts, so every app's panel reads the same:

- **PanelRow** (`.fexr2`): one action as a 48 row at 16, its icon at 19, the
  words at 15; red when `danger`. A row closes the panel after it acts.
- **panel_list(rows)**: a column of rows from `MenuItem`, `BarAction`, a
  heading string or None (a hairline); what ⋯ and a context menu show.
- **PanelHeading** (`.fexh`): a quiet 12 heading over a group.
- **PanelField** (`.cfield`): the one field style in a panel, a 48 well at 16
  with a leading icon (Contacts' Add, Messages' New message, Photos' New
  album). A field goes last in its panel, closest to the thumb.
- **PanelConfirm** (`.cconf`): a title, one line, Cancel and the red action
  side by side; what `DestructiveDialog.in_bar` grows the bar into.
- **BarTiles([BarTile…], columns=)** (`.fsha .fvt`, global dropdown tiles): an
  icon over a word, 72 tall at 18, a well at rest and the raised chip when
  on; 2–5 across, 6 apart. `size="compact"` is 64 with 11.5 words (Messages'
  held message); `chip=True` is the raised chip at rest, 8 apart at 16
  (Messages' and Notes' title-island options). A tile closes the panel.

    center.grow("more", panel_list([MenuItem("Rename", icon="pencil", on_activate=rename), None,
                                    BarAction("trash-2", "Delete", danger=True, on_activate=delete)]))
    field = PanelField("folder-plus", "Album name", on_submit=create)
    center.grow("album", PanelHeading("New album"), entry=field)

CSS: luma-appkit-bar.css, `/* LumaUI: Grown bar (v71) */`.
"""
from __future__ import annotations

from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk, Pango  # noqa: E402

from . import icons  # noqa: E402

__all__ = ["PanelRow", "PanelHeading", "PanelField", "PanelConfirm", "panel_list", "BarTile", "BarTiles",
           "PanelSwitch", "PanelChoices", "PanelKey"]


def _fold_from(widget: Gtk.Widget) -> None:
    """Fold the action center (or bar frame) `widget` sits in, after an action in its panel."""
    node = widget.get_parent()
    while node is not None:
        fold = getattr(node, "fold_panel", None)
        if callable(fold):
            fold()
            return
        node = node.get_parent()


class PanelRow(Gtk.Button):
    """One action in a grown panel: icon, words, an optional quiet note, a trailing chevron for a submenu."""

    __gtype_name__ = "LumaUIPanelRow"

    def __init__(self, label: str, *, icon: str | None = None, gicon: object | None = None,
                 note: str | None = None, danger: bool = False, selected: bool = False,
                 submenu: bool = False, on_activate: Callable[[], None] | None = None,
                 closes: bool = True, sensitive: bool = True, lead: Gtk.Widget | None = None,
                 subtitle: str | None = None, count: int | str | None = None, current: bool = False,
                 detail: str | None = None, toggle: bool | None = None,
                 on_toggle: Callable[[bool], None] | None = None,
                 appearance: str = "regular", live: bool = False) -> None:
        """`lead` is a face before the words (a list's colour dot, a 32 app icon) in place of `icon`;
        `subtitle` a quiet second line; `count` sits at the end; `current` is the raised chip (the
        place you're in), where `selected` is a choice with a check. `detail` is a trailing value with
        no chevron (a place's "66°"). `toggle` (True/False) ends the row in a switch; tapping the row
        flips it, calls `on_toggle(active)` and leaves the panel open."""
        if appearance not in ("regular", "person"):
            raise ValueError("panel row appearance must be regular or person")
        role = Gtk.AccessibleRole.SWITCH if toggle is not None else Gtk.AccessibleRole.MENU_ITEM
        super().__init__(accessible_role=role, sensitive=sensitive)
        self.add_css_class("lumaui-panel-row")
        if appearance == "person":
            self.add_css_class("person")
        if danger:
            self.add_css_class("danger")
        if selected:
            self.add_css_class("on")
        if current:
            self.add_css_class("current")
        if subtitle:
            self.add_css_class("two-line")
        line = Gtk.Box()
        line.add_css_class("lumaui-panel-row-line")
        if lead is not None:
            lead.set_valign(Gtk.Align.CENTER)
            line.append(lead)
        elif gicon is not None:
            image = Gtk.Image.new_from_gicon(gicon)
            image.add_css_class("lumaui-app-icon")
            line.append(image)
        elif icon:
            glyph = icons.image(icon)
            if appearance == "person":
                well = Gtk.Box(valign=Gtk.Align.CENTER, hexpand=False)
                well.add_css_class("lumaui-panel-person-well")
                glyph.set_halign(Gtk.Align.CENTER)
                glyph.set_valign(Gtk.Align.CENTER)
                glyph.set_hexpand(True)
                well.append(glyph)
                line.append(well)
            else:
                line.append(glyph)
        text = Gtk.Label(label=label, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        text.add_css_class("lumaui-panel-row-title")
        if subtitle:
            words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
            words.append(text)
            second = Gtk.Label(label=subtitle, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            second.add_css_class("lumaui-panel-row-subtitle")
            if live:
                status = Gtk.Box()
                status.add_css_class("lumaui-panel-person-status")
                dot = Gtk.Box(valign=Gtk.Align.CENTER)
                dot.add_css_class("lumaui-panel-person-presence")
                status.append(dot)
                status.append(second)
                words.append(status)
            else:
                words.append(second)
            line.append(words)
        else:
            line.append(text)
        if note:
            small = Gtk.Label(label=note)
            small.add_css_class("lumaui-panel-row-note")
            line.append(small)
        if count is not None and count != 0:
            number = Gtk.Label(label=str(count))
            number.add_css_class("lumaui-panel-row-count")
            line.append(number)
        if detail:
            value = Gtk.Label(label=detail)
            value.add_css_class("lumaui-panel-row-detail")
            line.append(value)
        self.switch: Gtk.Switch | None = None
        if toggle is not None:
            self.switch = Gtk.Switch(active=bool(toggle), valign=Gtk.Align.CENTER, can_focus=False,
                                     can_target=False)
            self.switch.add_css_class("lumaui-panel-row-switch")
            line.append(self.switch)
            self.add_css_class("switch")
            self.update_state([Gtk.AccessibleState.CHECKED], [bool(toggle)])
            closes = False
        self.on_toggle = on_toggle
        if selected and not note:
            line.append(icons.image("check"))
        if submenu:
            line.append(icons.image("chevron-right"))
        self.set_child(line)
        self.label, self.on_activate, self.closes = label, on_activate, closes and not submenu
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"{label}, {note}" if note else label])
        self.connect("clicked", self._clicked)

    def _clicked(self, _button: Gtk.Button) -> None:
        if self.switch is not None:
            active = not self.switch.get_active()
            self.switch.set_active(active)
            self.update_state([Gtk.AccessibleState.CHECKED], [active])
            if self.on_toggle is not None:
                self.on_toggle(active)
            return
        if self.closes:
            _fold_from(self)
        if self.on_activate is not None:
            self.on_activate()


class PanelHeading(Gtk.Box):
    """A quiet heading over a group in a panel ("Send to", "New album").

    `action` (a `BarAction`: icon and/or label, `on_activate`) is the small trailing key at the end of the
    heading (v71 `.ppnew`, Photos' "+ New album"); like a row it folds the panel, then acts.
    """

    __gtype_name__ = "LumaUIPanelHeading"

    def __init__(self, text: str, *, action: object | None = None) -> None:
        super().__init__(valign=Gtk.Align.CENTER)
        self.label = Gtk.Label(label=text, xalign=0, hexpand=True)
        self.label.add_css_class("lumaui-panel-heading")
        self.append(self.label)
        self.action_button: Gtk.Button | None = None
        if action is not None:
            key = Gtk.Button(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.BUTTON)
            key.add_css_class("lumaui-panel-heading-action")
            line = Gtk.Box()
            if action.icon:
                line.append(icons.image(action.icon))
            if action.label:
                line.append(Gtk.Label(label=action.label))
            key.set_child(line)
            key.update_property([Gtk.AccessibleProperty.LABEL], [action.label or action.tooltip or action.icon])
            key.set_sensitive(action.sensitive)
            key.connect("clicked", lambda _b: (_fold_from(self), action.on_activate and action.on_activate()))
            self.append(key)
            self.action_button = key


class PanelField(Gtk.Box):
    """The panel's field: a 48 well with a leading icon. Return calls `on_submit(text)`."""

    __gtype_name__ = "LumaUIPanelField"

    def __init__(self, icon: str, placeholder: str, *, text: str = "",
                 on_change: Callable[[str], None] | None = None,
                 on_submit: Callable[[str], None] | None = None) -> None:
        super().__init__(valign=Gtk.Align.CENTER, hexpand=True)
        self.add_css_class("lumaui-panel-field")
        glyph = icons.image(icon)
        glyph.add_css_class("lumaui-panel-field-icon")
        self.append(glyph)
        self.entry = Gtk.Text(hexpand=True, placeholder_text=placeholder, text=text,
                              accessible_role=Gtk.AccessibleRole.TEXT_BOX)
        self.entry.update_property([Gtk.AccessibleProperty.LABEL], [placeholder])
        self.append(self.entry)
        self.on_change, self.on_submit = on_change, on_submit
        self.entry.connect("changed", lambda e: self.on_change(e.get_text()) if self.on_change else None)
        self.entry.connect("activate", lambda e: self.on_submit(e.get_text()) if self.on_submit else None)

    @property
    def text(self) -> str:
        return self.entry.get_text()

    def set_text(self, text: str) -> None:
        self.entry.set_text(text)

    def grab_focus(self) -> bool:
        return self.entry.grab_focus()


class PanelConfirm(Gtk.Box):
    """A destructive confirm inside the grown bar: title, one line, Cancel and the red action."""

    __gtype_name__ = "LumaUIPanelConfirm"

    def __init__(self, *, title: str, body: str, action: str = "Delete",
                 on_confirm: Callable[[], None] | None = None, on_cancel: Callable[[], None] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.ALERT_DIALOG)
        self.add_css_class("lumaui-panel-confirm")
        heading = Gtk.Label(label=title, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        heading.add_css_class("lumaui-panel-confirm-title")
        line = Gtk.Label(label=body, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        line.add_css_class("lumaui-panel-confirm-body")
        self.append(heading)
        self.append(line)
        buttons = Gtk.Box(homogeneous=True)
        buttons.add_css_class("lumaui-panel-confirm-buttons")
        self.cancel_button = Gtk.Button(label="Cancel", hexpand=True)
        self.cancel_button.add_css_class("lumaui-panel-confirm-cancel")
        self.action_button = Gtk.Button(label=action, hexpand=True)
        self.action_button.add_css_class("lumaui-panel-confirm-action")
        buttons.append(self.cancel_button)
        buttons.append(self.action_button)
        self.append(buttons)
        self.on_confirm, self.on_cancel = on_confirm, on_cancel
        self.cancel_button.connect("clicked", lambda _b: self._done(self.on_cancel))
        self.action_button.connect("clicked", lambda _b: self._done(self.on_confirm))
        self.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION], [title, body])
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    def _done(self, callback: Callable[[], None] | None) -> None:
        _fold_from(self)
        if callback is not None:
            callback()

    def _key(self, _c, keyval: int, _code: int, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self._done(self.on_cancel)
            return True
        return False

    def grab_focus(self) -> bool:  # Cancel has focus: Enter never deletes by accident
        return self.cancel_button.grab_focus()


class BarTile:
    """One tile: an icon over a word. `on` is the raised chip (the current choice); `danger` is red."""

    def __init__(self, icon: str, label: str, on_activate: Callable[[], None] | None = None, *, on: bool = False,
                 danger: bool = False, name: str | None = None, closes: bool = True,
                 sub: str | None = None, well: bool = False) -> None:
        self.icon, self.label, self.on_activate = icon, label, on_activate
        self.sub, self.well = sub, bool(well)
        self.on, self.danger, self.name, self.closes = on, danger, name or label, closes


class BarTiles(Gtk.Grid):
    """Tiles in a grown panel (`.fsha .fvt`): `columns` across (default: one per tile, up to 5)."""

    __gtype_name__ = "LumaUIBarTiles"

    def __init__(self, items: Sequence[BarTile], *, columns: int | None = None, size: str = "regular",
                 chip: bool = False) -> None:
        if size not in ("regular", "compact"):
            raise ValueError('tiles are "regular" (72) or "compact" (64)')
        super().__init__(column_homogeneous=True, hexpand=True)
        self.add_css_class("lumaui-bar-tiles")
        self.add_css_class(size)
        if chip:
            self.add_css_class("chip")
        gap = 8 if chip else 6
        self.set_row_spacing(gap)
        self.set_column_spacing(gap)
        columns = columns or max(1, min(5, len(items)))
        self.buttons: list[Gtk.Button] = []
        for index, item in enumerate(items):
            button = Gtk.Button(hexpand=True)
            button.add_css_class("lumaui-bar-tile")
            if item.on:
                button.add_css_class("on")
                button.update_state([Gtk.AccessibleState.PRESSED], [int(Gtk.AccessibleTristate.TRUE)])
            if item.danger:
                button.add_css_class("danger")
            stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            stack.add_css_class("lumaui-bar-tile-stack")
            glyph = icons.image(item.icon)
            if item.well:
                glyph.add_css_class("lumaui-favourite-well")
                button.add_css_class("well")
            stack.append(glyph)
            words = Gtk.Label(label=item.label, justify=Gtk.Justification.CENTER, wrap=True,
                              wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=10)
            stack.append(words)
            if item.sub:
                sub = Gtk.Label(label=item.sub, ellipsize=Pango.EllipsizeMode.END)
                sub.add_css_class("lumaui-bar-tile-sub")
                stack.append(sub)
            button.set_child(stack)
            button.update_property([Gtk.AccessibleProperty.LABEL], [item.name + (f", {item.sub}" if item.sub else "")])
            button.connect("clicked", self._clicked, item)
            self.attach(button, index % columns, index // columns, 1, 1)
            self.buttons.append(button)
        if items and len(items) < columns:
            # Gtk.Grid drops empty trailing columns; an explicit column count
            # still reserves those cells in the v71 grid.
            self.attach(Gtk.Box(hexpand=True), columns - 1, 0, 1, 1)

    def _clicked(self, button: Gtk.Button, item: BarTile) -> None:
        if item.closes:
            _fold_from(button)
        if item.on_activate is not None:
            item.on_activate()


def PanelSwitch(icon: str, label: str, *, active: bool = False, on_toggle: Callable[[bool], None] | None = None,
                subtitle: str | None = None) -> PanelRow:
    """A row ending in a switch (`.fexr2` with `.cfsw`): "Reduce background noise". It leaves the panel open."""
    return PanelRow(label, icon=icon, subtitle=subtitle, toggle=active, on_toggle=on_toggle)


class PanelChoices(Gtk.Box):
    """Choice chips in a panel (`.fexs > .fsch`): one of a few, the chosen one raised (°F · °C, a mic, a view).

    `choices` are (key, label) or (key, label, icon). Picking calls `on_choose(key)` and leaves the panel open
    (`closes=True` folds it)."""

    __gtype_name__ = "LumaUIPanelChoices"

    def __init__(self, choices: Sequence[tuple], *, selected: str | None = None,
                 on_choose: Callable[[str], None] | None = None, closes: bool = False,
                 document_style: bool = False) -> None:
        super().__init__(accessible_role=Gtk.AccessibleRole.RADIO_GROUP)
        self.add_css_class("lumaui-panel-choices")
        if document_style:
            self.add_css_class("document-style")
            self.set_homogeneous(True)
        self.on_choose, self.closes = on_choose, closes
        self.buttons: dict[str, Gtk.Button] = {}
        for choice in choices:
            key, words = choice[0], choice[1]
            icon = choice[2] if len(choice) > 2 else None
            button = Gtk.Button(accessible_role=Gtk.AccessibleRole.RADIO)
            button.add_css_class("lumaui-panel-choice")
            if key in ("heading", "quote"):
                button.add_css_class("style-" + key)
            if document_style:
                button.set_hexpand(True)
            line = Gtk.Box(halign=Gtk.Align.CENTER if document_style else Gtk.Align.FILL)
            if icon:
                line.append(icons.image(icon))
            line.append(Gtk.Label(label=words))
            button.set_child(line)
            button.connect("clicked", lambda _b, k=key: self._pick(k))
            self.buttons[key] = button
            self.append(button)
        self.selected = None
        self.set_selected(selected if selected is not None else choices[0][0])

    def set_selected(self, key: str) -> None:
        self.selected = key
        for name, button in self.buttons.items():
            (button.add_css_class if name == key else button.remove_css_class)("on")
            button.update_state([Gtk.AccessibleState.CHECKED], [name == key])

    def _pick(self, key: str) -> None:
        self.set_selected(key)
        if self.closes:
            _fold_from(self)
        if self.on_choose is not None:
            self.on_choose(key)


class PanelKey(Gtk.Button):
    """The panel's one big action, full width, 52 at 20 (Memos' "Start recording": `record=True` is red with a
    white dot; otherwise the primary key). It folds the panel after it acts."""

    __gtype_name__ = "LumaUIPanelKey"

    def __init__(self, label: str, *, icon: str | None = None, record: bool = False,
                 on_activate: Callable[[], None] | None = None) -> None:
        super().__init__(hexpand=True)
        self.add_css_class("lumaui-panel-key")
        if record:
            self.add_css_class("record")
        line = Gtk.Box(halign=Gtk.Align.CENTER)
        if record:
            dot = Gtk.Box(valign=Gtk.Align.CENTER)
            dot.add_css_class("lumaui-record-dot")
            dot.add_css_class("white")
            line.append(dot)
        elif icon:
            line.append(icons.image(icon))
        line.append(Gtk.Label(label=label))
        self.set_child(line)
        self.on_activate = on_activate
        self.connect("clicked", self._clicked)

    def _clicked(self, _button: Gtk.Button) -> None:
        _fold_from(self)
        if self.on_activate is not None:
            self.on_activate()


def panel_list(rows: Sequence[object], *, label: str = "Actions") -> Gtk.Widget:
    """A column of panel rows: MenuItem, BarAction, PanelRow, a heading string, or None for a hairline."""
    from .action_bubble import MenuItem
    from .action_center import BarAction

    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.MENU)
    column.add_css_class("lumaui-panel-list")
    column.update_property([Gtk.AccessibleProperty.LABEL], [label])
    for row in rows:
        if row is None:
            rule = Gtk.Box()
            rule.add_css_class("lumaui-panel-rule")
            column.append(rule)
        elif isinstance(row, str):
            column.append(PanelHeading(row))
        elif isinstance(row, Gtk.Widget):
            column.append(row)
        elif hasattr(row, "menu_widget"):  # rows family (RichMenuItem, MenuSection): switch, subtitle, value kept
            column.append(row.menu_widget(lambda: _fold_from(column)))
        elif isinstance(row, BarAction):
            column.append(PanelRow(row.label or row.tooltip or row.icon, icon=row.icon or None, danger=row.danger,
                                   selected=row.active, on_activate=row.on_activate, sensitive=row.sensitive))
        elif isinstance(row, MenuItem):
            column.append(PanelRow(row.label, icon=row.icon, gicon=row.gicon, note=row.note,
                                   selected=row.selected, danger=getattr(row, "danger", False),
                                   on_activate=row.on_activate))
        else:
            raise TypeError(f"not a panel row: {row!r}")
    return column
