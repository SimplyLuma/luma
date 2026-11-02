# SPDX-License-Identifier: MPL-2.0
"""The sheet: "Unlock your saved passwords", and the place to change it later.

One window, two uses, LumaUI parts throughout (TextField, Switch,
TextButton, TypeLabel); the TV posture comes from the shared TV layer, and
the field takes the TV keyboard like any app field.

* ``Use.FIRST`` -- at the start of a session that nothing could unlock: a
  title, one line, the password, "Unlock automatically on this TV" (on by
  default), Cancel and Unlock. A wrong password stays here with the error
  under the field; Cancel leaves the keyring locked.
* ``Use.MANAGE`` -- opened by hand: what state the saved passwords are in
  and the same switch, which asks for the password once to turn automatic
  unlock on or off.

The work runs on a thread so the window keeps drawing; the password lives
in the field until the answer comes back and is cleared then.
"""

from __future__ import annotations

import enum
import threading
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from luma_appkit import Switch, TextButton, TextField, TypeLabel, install_lumaui  # noqa: E402

from . import first_unlock  # noqa: E402
from .first_unlock import Kind, Result  # noqa: E402
from .secrets import Keyring  # noqa: E402
from .state import State, read  # noqa: E402

__all__ = ("UnlockSheet", "Use", "FIRST_TITLE", "FIRST_LINE")

FIRST_TITLE = "Unlock your saved passwords"
FIRST_LINE = "Wi-Fi, accounts and apps keep their passwords here."
MANAGE_TITLE = "Saved passwords"


class Use(enum.Enum):
    FIRST = "first"
    MANAGE = "manage"


def _automatic_label(noun: str) -> str:
    return f"Unlock automatically on {noun}"


class UnlockSheet(Adw.ApplicationWindow):
    """The first-unlock sheet (Use.FIRST) or the keyring's own page (Use.MANAGE)."""

    def __init__(self, application, use: Use = Use.FIRST, keyring: Keyring | None = None,
                 noun: str | None = None, autologin: bool | None = None,
                 work: Callable[..., Result] | None = None,
                 on_done: Callable[[Result | None], None] | None = None) -> None:
        install_lumaui()  # the kit's sheets and icons, once per display
        super().__init__(application=application,
                         title=FIRST_TITLE if use is Use.FIRST else MANAGE_TITLE,
                         default_width=460, default_height=-1, resizable=False)
        self.add_css_class("luma-keyring-sheet")
        self._use = use
        self._keyring = keyring or Keyring()
        self._noun = noun or first_unlock.device_noun()
        self._autologin = autologin
        self._work = work
        self._on_done = on_done
        self._busy = False
        self._result: Result | None = None
        self._finished = False

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_title=False)
        header.add_css_class("flat")
        toolbar.add_top_bar(header)
        self.set_content(toolbar)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                       margin_start=28, margin_end=28, margin_bottom=24)
        clamp = Adw.Clamp(maximum_size=440, child=body)
        toolbar.set_content(clamp)

        self.title_label = TypeLabel(self.get_title(), role="form_title", wrap=True)
        body.append(self.title_label)
        self.line = TypeLabel("", role="body", wrap=True)
        body.append(self.line)

        self.field = TextField("Password", purpose="password",
                               on_activate=lambda _text: self._on_primary())
        body.append(self.field)

        # Under the field it is about: a wrong password, or why it is not automatic.
        self.message = TypeLabel("", role="caption", wrap=True)
        self.message.set_visible(False)
        body.append(self.message)

        row = Gtk.Box(spacing=12)
        self.switch_label = TypeLabel(_automatic_label(self._noun), role="row_title", wrap=True)
        self.switch_label.set_hexpand(True)
        row.append(self.switch_label)
        self.switch = Switch(active=True, label=_automatic_label(self._noun))
        row.append(self.switch)
        self.switch_row = row
        body.append(row)

        buttons = Gtk.Box(spacing=10, homogeneous=True, halign=Gtk.Align.END, margin_top=6)
        self.cancel = TextButton("Cancel", on_click=self._on_cancel)
        self.primary = TextButton("Unlock", style="key", on_click=self._on_primary)
        buttons.append(self.cancel)
        buttons.append(self.primary)
        self.buttons = buttons
        body.append(buttons)

        self.set_default_widget(self.primary)
        self.connect("close-request", self._on_close_request)

        if use is Use.FIRST:
            self.line.label.set_label(FIRST_LINE)
            self.field.grab_focus()
        else:
            self._show_state()
            self._switch_handler = self.switch.connect("notify::active", self._on_switch_toggled)

    # -- manage -----------------------------------------------------------
    def _show_state(self) -> None:
        automatic = first_unlock.is_automatic()
        reading = read(self._keyring)
        if automatic:
            sentence = f"They unlock by themselves when {self._noun} starts."
        elif reading.state is State.LOCKED:
            sentence = "They're locked. Apps ask for your password when they need one."
        elif reading.state is State.UNLOCKED:
            sentence = "They're unlocked."
        else:
            sentence = reading.sentence
        self.line.label.set_label(FIRST_LINE + " " + sentence)
        autologin = self._autologin
        if autologin is None:
            from .autounlock import is_autologin
            autologin = is_autologin()
        # Without automatic sign-in the keyring opens with the password typed
        # at sign-in; the switch only means something where nothing is typed.
        self.switch_row.set_visible(autologin or automatic)
        self.switch.set_active(automatic)
        self._editing(False)

    def _editing(self, on: bool) -> None:
        self.field.set_visible(on)
        self.buttons.set_visible(on)
        if on:
            self.primary.label_widget.set_label(
                "Turn On" if self.switch.get_active() else "Turn Off")
            self.field.grab_focus()
        else:
            self.field.text = ""

    def _on_switch_toggled(self, *_args) -> None:
        if self._busy:
            return
        changing = self.switch.get_active() != first_unlock.is_automatic()
        self._say("")
        self._editing(changing)

    # -- doing it ---------------------------------------------------------
    def _say(self, text: str, error: bool = False) -> None:
        self.message.label.set_label(text)
        self.message.set_visible(bool(text))
        (self.message.label.add_css_class if error else self.message.label.remove_css_class)("error")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget in (self.field, self.switch, self.primary, self.cancel):
            widget.set_sensitive(not busy)
        if self._use is Use.FIRST:
            self.primary.label_widget.set_label("Unlocking…" if busy else "Unlock")

    def _on_primary(self) -> None:
        if self._busy:
            return
        if self._result is not None and self._result.kind is Kind.UNLOCKED_ONLY:
            self._finish(self._result)
            return
        password = self.field.text
        if not password:
            self._say("Type your password.", error=True)
            return
        automatic = self.switch.get_active()
        if self._work is not None:
            work = lambda: self._work(password, automatic)  # noqa: E731 - tests stand in here
        elif self._use is Use.FIRST:
            work = lambda: first_unlock.unlock(password, automatic, self._keyring)  # noqa: E731
        else:
            work = lambda: first_unlock.set_automatic(automatic, password, self._keyring)  # noqa: E731
        self._say("")
        self._set_busy(True)

        def run() -> None:
            try:
                result = work()
            except Exception as error:  # noqa: BLE001 - shown, never a crash with a password in hand
                result = Result(Kind.UNAVAILABLE, f"Something went wrong: {error}")
            GLib.idle_add(self._answered, result)

        threading.Thread(target=run, name="luma-keyring-unlock", daemon=True).start()

    def _answered(self, result: Result) -> bool:
        self._set_busy(False)
        self.field.text = ""
        self._result = result
        if result.kind in (Kind.WRONG_PASSWORD, Kind.UNAVAILABLE):
            self._say(result.message, error=True)
            self.field.grab_focus()
        elif self._use is Use.MANAGE:
            self._say(result.message)
            self._show_state()
        elif result.kind is Kind.UNLOCKED_ONLY:
            # Unlocked for this session; say why it is not automatic, once.
            self._say(result.message)
            self.field.set_visible(False)
            self.switch_row.set_visible(False)
            self.cancel.set_visible(False)
            self.primary.label_widget.set_label("Done")
        else:
            self._finish(result)
        return GLib.SOURCE_REMOVE

    def _on_cancel(self) -> None:
        if self._use is Use.MANAGE and self.field.get_visible():
            self.switch.handler_block(self._switch_handler)
            self.switch.set_active(first_unlock.is_automatic())
            self.switch.handler_unblock(self._switch_handler)
            self._editing(False)
            self._say("")
            return
        self._finish(None)

    def _on_close_request(self, _window) -> bool:
        if self._busy:
            return True  # the answer is on its way; closing now would drop it
        self._finish(self._result if self._result and self._result.unlocked else None,
                     destroy=False)
        return False

    def _finish(self, result: Result | None, destroy: bool = True) -> None:
        if self._finished:
            return
        self._finished = True
        self.field.text = ""
        if self._on_done is not None:
            self._on_done(result)
        if destroy:
            self.close()
