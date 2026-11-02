# SPDX-License-Identifier: MPL-2.0
"""The sheet on a TV: a TV page built from LumaUI's TV layer, nothing of its own.

The room (the wallpaper, blurred, as behind every TV page), and in the
middle of the safe area a TV sheet (bar material, radius 44, 860 wide):
the title, the line, a TvField for the password, the switch as a TV row,
then Cancel and Unlock as TV action buttons. The remote moves between them
(TvFocus); the field takes the TV keyboard when it has the focus, and while
the keyboard is up the sheet stands on it instead of in the middle. Back is
Cancel. Sizes are TV units throughout (one pixel of 1920x1080, scaled).

Only loaded in TV posture (sheet.posture()), where the TV layer exists.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from luma_appkit import lumaui_tokens as tokens  # noqa: E402
from luma_appkit.tv_focus import TvFocus  # noqa: E402
from luma_appkit.tv_keyboard import watch_tv_keyboard  # noqa: E402
from luma_appkit.tv_media import TvRoom  # noqa: E402
from luma_appkit.tv_parts import TvButton, TvField, TvSwitchRow  # noqa: E402
from luma_appkit.tv_stage import TvStage, add_tv_style, tv_material  # noqa: E402

__all__ = ("KeyringTV",)

TV = tokens.TV
SHEET = TV["sheet"]
# The sheet's bottom while the TV keyboard is up: where the keyboard leaves room.
ABOVE_KEYBOARD = TV["keyboard"]["above"]


def _style(opaque: bool) -> str:
    return f"""
.kr-tv-sheet {{ border-spacing: {SHEET['gap']}tv; }}
.kr-tv-sheet .tv-sheet-title {{ margin-bottom: -8tv; }}
.kr-tv-actions {{ border-spacing: 16tv; margin-top: 6tv; }}
.kr-tv-sheet label.tv-body {{ color: {tv_material('ink_2', opaque=opaque)}; }}
.kr-tv-message {{ font-size: 24tv; font-weight: 600; color: {tv_material('ink_2', opaque=opaque)}; }}
.kr-tv-message.error {{ color: rgba(255, 190, 92, 1); }}
"""


def _wallpaper() -> str | None:
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup("org.gnome.desktop.background", True) if source else None
    if schema is None:
        return None
    settings = Gio.Settings.new("org.gnome.desktop.background")
    key = "picture-uri-dark" if schema.has_key("picture-uri-dark") else "picture-uri"
    uri = settings.get_string(key) or settings.get_string("picture-uri")
    try:
        path = GLib.filename_from_uri(uri)[0] if uri else None
    except GLib.Error:
        return None
    return path if path and os.path.exists(path) else None


def _label(text: str, css: str, *, wrap: bool = False) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0, wrap=wrap)
    label.add_css_class(css)
    return label


class KeyringTV(TvStage):
    """The TV page of the sheet; speaks the same view API as the desktop and phone ones."""

    __gtype_name__ = "LumaKeyringTV"

    def __init__(self, noun: str, on_primary, on_cancel, on_toggle) -> None:
        super().__init__()
        add_tv_style(_style)
        self._on_primary = on_primary
        self._on_cancel = on_cancel
        self._keyboard_up = False

        self.room = TvRoom()
        wallpaper = _wallpaper()
        if wallpaper:
            self.room.set_artwork(wallpaper)
        else:
            self.room.set_artwork(hues=(250, 265, 280))
        self.place(self.room, fill=True)

        self.sheet = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sheet.add_css_class("tv-sheet")
        self.sheet.add_css_class("kr-tv-sheet")
        self.sheet.set_name("kr-tv-sheet")
        self.title = _label("", "tv-sheet-title", wrap=True)
        self.line = _label("", "tv-body", wrap=True)
        self.field = TvField(placeholder="Password", icon="lock", purpose=Gtk.InputPurpose.PASSWORD,
                             action="Unlock")
        self.field.text.set_visibility(False)
        self.message = _label("", "kr-tv-message", wrap=True)
        self.message.set_visible(False)
        self.switch_row = TvSwitchRow(f"Unlock automatically on {noun}", active=True, on_toggle=on_toggle)
        self.actions = Gtk.Box(homogeneous=True)
        self.actions.add_css_class("kr-tv-actions")
        self.cancel = TvButton("Cancel", hexpand=True)
        self.primary = TvButton("Unlock", accent=True, hexpand=True)
        for button in (self.cancel, self.primary):
            button.add_css_class("tv-action")
            self.actions.append(button)
        for part in (self.title, self.line, self.field, self.message, self.switch_row, self.actions):
            self.sheet.append(part)
        self._place_sheet()

        self.focus = TvFocus(self, on_back=self._back)
        self.focus.add(self.field.text, editable=True, on_activate=self._field_ok)
        self.focus.add(self.switch_row, on_activate=self.switch_row.toggle)
        self.focus.add(self.cancel, on_activate=on_cancel)
        self.focus.add(self.primary, on_activate=on_primary)
        self.connect("notify::unit", lambda *_a: self._place_sheet())
        self._stop_keyboard = watch_tv_keyboard(self._keyboard)
        self.connect("unrealize", lambda *_a: self._stop_keyboard and self._stop_keyboard())

    # -- layout ----------------------------------------------------------
    def _place_sheet(self) -> None:
        self.sheet.set_size_request(round(SHEET["width"] * self.unit), -1)
        if self._keyboard_up:
            self.place(self.sheet, bottom=ABOVE_KEYBOARD, center_x=True)
        else:
            self.place(self.sheet, center_x=True)

    def _keyboard(self, visible: bool, _height: float) -> None:
        if visible != self._keyboard_up:
            self._keyboard_up = visible
            self._place_sheet()

    # -- the remote --------------------------------------------------------
    def _field_ok(self) -> None:
        # The keyboard's action key is Unlock: the field's Enter unlocks.
        self._on_primary()

    def _back(self) -> bool:
        self._on_cancel()
        return True

    # -- the view API (see sheet._KitView) ---------------------------------
    @property
    def password(self) -> str:
        return self.field.get_value()

    @password.setter
    def password(self, value: str) -> None:
        self.field.set_value(value)

    @property
    def automatic(self) -> bool:
        return self.switch_row.get_active()

    def set_automatic(self, on: bool) -> None:
        self.switch_row.set_active(on)

    def set_texts(self, title: str, line: str) -> None:
        self.title.set_label(title)
        self.line.set_label(line)

    def say(self, text: str, error: bool) -> None:
        self.message.set_label(text)
        self.message.set_visible(bool(text))
        (self.message.add_css_class if error else self.message.remove_css_class)("error")

    def message_text(self) -> str:
        return self.message.get_label() if self.message.get_visible() else ""

    def message_is_error(self) -> bool:
        return self.message.has_css_class("error")

    def set_busy(self, busy: bool, label: str) -> None:
        for widget in (self.field, self.switch_row, self.cancel, self.primary):
            widget.set_sensitive(not busy)
        self.primary.label.set_label(label)

    def set_shown(self, *, field: bool, switch: bool, buttons: bool, cancel: bool, primary: str) -> None:
        self.field.set_visible(field)
        self.switch_row.set_visible(switch)
        self.actions.set_visible(buttons)
        self.cancel.set_visible(cancel)
        self.primary.label.set_label(primary)
        for widget, shown in ((self.field.text, field), (self.switch_row, switch),
                              (self.cancel, buttons and cancel), (self.primary, buttons)):
            if shown:
                self.focus.add(widget, editable=widget is self.field.text,
                               on_activate={self.field.text: self._field_ok,
                                            self.switch_row: self.switch_row.toggle,
                                            self.cancel: self._on_cancel,
                                            self.primary: self._on_primary}[widget])
            else:
                self.focus.remove(widget)
        if buttons and not field:
            self.focus.focus(self.primary)
        elif not field and switch:
            self.focus.focus(self.switch_row)

    def shown(self) -> dict:
        return {"field": self.field.get_visible(), "switch": self.switch_row.get_visible(),
                "buttons": self.actions.get_visible(), "primary": self.primary.label.get_label()}

    def focus_field(self) -> None:
        if self.field.get_visible():
            self.focus.focus(self.field.text)
