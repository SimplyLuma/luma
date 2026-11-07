# SPDX-License-Identifier: MPL-2.0
"""The sheet: "Unlock your saved passwords", and the place to change it later.

One window, one flow, two uses, in each posture's own LumaUI form:

* desktop -- a small dialog window: title, line, the password field, the
  switch row, Cancel and Unlock (TextField, Switch, TextButton, TypeLabel);
* phone -- the same parts in a full-width drawer from the bottom edge
  (LayerHost.present_modal): the 48 touch field, the actions full width,
  primary on top; the on-screen keyboard lifts it with the window;
* TV -- a TV page (sheet_tv.KeyringTV, LumaUI's TV layer): the room, a
  centred sheet at TV scale, a TvField that takes the TV keyboard and rises
  above it, the switch as a TV row, Cancel and Unlock as TV buttons.

Uses:

* ``Use.FIRST`` -- at the start of a session that nothing could unlock (or
  by hand on an automatic sign-in that does not unlock by itself yet). A
  wrong password stays with the error under the field; Cancel leaves the
  keyring locked.
* ``Use.MANAGE`` -- opened by hand: the state and the same switch, which
  asks for the password once to turn automatic unlock on or off.

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

__all__ = ("UnlockSheet", "Use", "Posture", "posture", "FIRST_TITLE", "FIRST_LINE")

FIRST_TITLE = "Unlock your saved passwords"
FIRST_LINE = "Wi-Fi, accounts and apps keep their passwords here."
MANAGE_TITLE = "Saved passwords"


class Use(enum.Enum):
    FIRST = "first"
    MANAGE = "manage"
    PROMPT = "prompt"     # gnome-keyring's own question (another keyring, a key), in this sheet


class Posture(enum.Enum):
    DESKTOP = "desktop"
    PHONE = "phone"
    TV = "tv"


def posture() -> Posture:
    """TV where LumaUI's TV layer says so (and has its parts), phone on a phone."""
    try:
        from luma_appkit import lumaui
    except ImportError:
        return Posture.DESKTOP
    tv = getattr(lumaui, "tv_posture", None)
    if tv is not None and tv():
        try:
            from luma_appkit.tv_parts import TvSwitchRow  # noqa: F401 - the TV page needs it
            return Posture.TV
        except ImportError:
            pass
    if lumaui.mobile_form_factor():
        return Posture.PHONE
    return Posture.DESKTOP


def automatic_label(noun: str) -> str:
    return f"Unlock automatically on {noun}"


# -- the desktop and phone views ---------------------------------------------
class _KitView:
    """Desktop dialog and phone drawer: the same LumaUI parts, laid out per posture."""

    def __init__(self, phone: bool, noun: str, on_primary, on_cancel, on_toggle) -> None:
        self.phone = phone
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.body.add_css_class("luma-keyring-body")
        self.title_label = TypeLabel("", role="form_title", wrap=True)
        self.line = TypeLabel("", role="body", wrap=True)
        self.field = TextField("Password", purpose="password", size="panel" if phone else "regular",
                               on_activate=lambda _text: on_primary())
        self.message = TypeLabel("", role="caption", wrap=True)
        self.message.set_visible(False)
        # Wrapped words take the sheet's whole width, not a narrower natural width.
        for label in (self.title_label, self.line, self.message):
            label.label.set_hexpand(True)
        self.switch_row = Gtk.Box(spacing=12)
        self.switch_label = TypeLabel(automatic_label(noun), role="row_title", wrap=True)
        self.switch_label.set_hexpand(True)
        self.switch_label.label.set_hexpand(True)
        self.switch = Switch(active=True, label=automatic_label(noun))
        self.switch_row.append(self.switch_label)
        self.switch_row.append(self.switch)
        self._toggled = self.switch.connect("notify::active", lambda *_a: on_toggle(self.switch.get_active()))
        size = "touch" if phone else "regular"
        self.primary = TextButton("Unlock", style="key", size=size, on_click=on_primary)
        self.cancel = TextButton("Cancel", size=size, on_click=on_cancel)
        if phone:
            # Phone: actions full width, the primary on top, within the thumb's reach.
            self.buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=6)
            for button in (self.primary, self.cancel):
                button.set_hexpand(True)
                self.buttons.append(button)
        else:
            self.buttons = Gtk.Box(spacing=10, homogeneous=True, halign=Gtk.Align.END, margin_top=6)
            self.buttons.append(self.cancel)
            self.buttons.append(self.primary)
        for part in (self.title_label, self.line, self.field, self.message, self.switch_row, self.buttons):
            self.body.append(part)

    # The sheet speaks to every view through these.
    @property
    def password(self) -> str:
        return self.field.text

    @password.setter
    def password(self, value: str) -> None:
        self.field.text = value

    @property
    def automatic(self) -> bool:
        return self.switch.get_active()

    def set_automatic(self, on: bool) -> None:
        self.switch.handler_block(self._toggled)
        self.switch.set_active(on)
        self.switch.handler_unblock(self._toggled)

    def set_texts(self, title: str, line: str) -> None:
        self.title_label.label.set_label(title)
        self.line.label.set_label(line)

    def say(self, text: str, error: bool) -> None:
        self.message.label.set_label(text)
        self.message.set_visible(bool(text))
        (self.message.label.add_css_class if error else self.message.label.remove_css_class)("error")

    def message_text(self) -> str:
        return self.message.label.get_label() if self.message.get_visible() else ""

    def message_is_error(self) -> bool:
        return self.message.label.has_css_class("error")

    def set_busy(self, busy: bool, label: str) -> None:
        for widget in (self.field, self.switch, self.primary, self.cancel):
            widget.set_sensitive(not busy)
        self.primary.label_widget.set_label(label)

    def set_shown(self, *, field: bool, switch: bool, buttons: bool, cancel: bool, primary: str) -> None:
        self.field.set_visible(field)
        self.switch_row.set_visible(switch)
        self.buttons.set_visible(buttons)
        self.cancel.set_visible(cancel)
        self.primary.label_widget.set_label(primary)

    def shown(self) -> dict:
        return {"field": self.field.get_visible(), "switch": self.switch_row.get_visible(),
                "buttons": self.buttons.get_visible(), "primary": self.primary.label_widget.get_label()}

    def focus_field(self) -> None:
        self.field.grab_focus()

    def set_labels(self, switch: str, primary: str, cancel: str) -> None:
        self.switch_label.label.set_label(switch)
        self.switch.update_property([Gtk.AccessibleProperty.LABEL], [switch])
        self.primary.label_widget.set_label(primary)
        self.cancel.label_widget.set_label(cancel)

    def toggle(self) -> None:
        """The switch flipped by the person (what a click or OK does)."""
        self.switch.set_active(not self.switch.get_active())

    def texts(self) -> tuple[str, str, str]:
        return (self.title_label.label.get_label(), self.line.label.get_label(),
                self.switch_label.label.get_label())


def _prompt_title(request) -> str:
    props = request.properties if request is not None else {}
    return str(props.get("message") or props.get("title") or "Authentication required")


_STYLE = """
.luma-keyring-body label.error { color: @error_color; }
"""
_styled = False


def _install_style() -> None:
    """The one rule LumaUI's parts leave to the app: the error line's ink."""
    global _styled
    from gi.repository import Gdk

    display = Gdk.Display.get_default()
    if _styled or display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_string(_STYLE)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _styled = True


class UnlockSheet(Adw.ApplicationWindow):
    """The first-unlock sheet (Use.FIRST) or the keyring's own page (Use.MANAGE)."""

    def __init__(self, application, use: Use = Use.FIRST, keyring: Keyring | None = None,
                 noun: str | None = None, autologin: bool | None = None,
                 work: Callable[..., Result] | None = None,
                 on_done: Callable[[Result | None], None] | None = None,
                 presentation: Posture | None = None, request=None,
                 second_work: Callable[[str, str], Result] | None = None) -> None:
        install_lumaui()  # the kit's sheets and icons, once per display
        _install_style()
        self.presentation = presentation or posture()
        self._request = request
        self._second_work = second_work
        title = {Use.FIRST: FIRST_TITLE, Use.MANAGE: MANAGE_TITLE}.get(use) or _prompt_title(request)
        if self.presentation is Posture.DESKTOP:
            super().__init__(application=application, title=title,
                             default_width=460, default_height=-1, resizable=False)
        else:
            super().__init__(application=application, title=title)
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
        self._modal = None
        self._keyring_password: str | None = None   # step two only: what opened the keyring

        if self.presentation is Posture.TV:
            from .sheet_tv import KeyringTV

            self.view = KeyringTV(self._noun, self._on_primary, self._on_cancel, self._on_switch_toggled)
            self.set_content(self.view)
            self.maximize()
        else:
            phone = self.presentation is Posture.PHONE
            self.view = _KitView(phone, self._noun, self._on_primary, self._on_cancel,
                                 self._on_switch_toggled)
            if phone:
                self._build_phone()
            else:
                self._build_desktop()

        self.connect("close-request", self._on_close_request)
        if use is Use.FIRST:
            self.view.set_texts(title, FIRST_LINE)
            self.view.focus_field()
        elif use is Use.PROMPT:
            self.prompt_again(request)
        else:
            self._show_state()

    def _build_desktop(self) -> None:
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_title=False)
        header.add_css_class("flat")
        toolbar.add_top_bar(header)
        self.view.body.set_margin_start(28)
        self.view.body.set_margin_end(28)
        self.view.body.set_margin_bottom(24)
        toolbar.set_content(Adw.Clamp(maximum_size=440, child=self.view.body))
        self.set_content(toolbar)
        self.set_default_widget(self.view.primary)

    def _build_phone(self) -> None:
        # The phone's sheet is LumaUI's: it rises from the bar's place (BarFrame, kind "sheet":
        # 16 a side, 34 up, 26 round, the title on the frame), over a scrim that cancels it.
        self._screen = Gtk.Box(hexpand=True, vexpand=True)
        self.set_content(self._screen)
        self.view.title_label.set_visible(False)
        self.view.body.set_margin_start(10)
        self.view.body.set_margin_end(10)
        self.connect("map", lambda *_a: GLib.idle_add(self._present_drawer))

    def _present_drawer(self) -> bool:
        from luma_appkit.bar_frame import BarFrame

        if self._modal is None and not self._finished:
            self._modal = BarFrame.present(self._screen, self.view.body, kind="sheet", title=self.get_title(),
                                           on_cancel=self._on_cancel, initial_focus=self.view.field.entry)
            # The frame's height cap follows the window: a phone window can still be taking
            # its size when the sheet rises, and the keyboard shortens it later.
            self._capped = 0

            def follow(widget, _clock) -> bool:
                height = widget.get_height()
                if self._modal is None or self._finished:
                    return GLib.SOURCE_REMOVE
                if height > 0 and height != self._capped:
                    self._capped = height
                    self._modal.cap(height)
                return GLib.SOURCE_CONTINUE
            self._screen.add_tick_callback(follow)
        return GLib.SOURCE_REMOVE

    # -- gnome-keyring's own questions ---------------------------------------
    def prompt_again(self, request) -> None:
        """Show (again) what gnome-keyring asks: its words, its choice, its warning."""
        self._request = request
        props = request.properties
        choice = str(props.get("choice-label") or "")
        password = request.kind != "confirm"
        self.view.set_texts(_prompt_title(request), str(props.get("description") or ""))
        self.view.set_shown(field=password, switch=bool(choice), buttons=True, cancel=True,
                            primary=str(props.get("continue-label") or ("Unlock" if password else "Continue")))
        self.view.set_labels(choice or automatic_label(self._noun),
                             str(props.get("continue-label") or ("Unlock" if password else "Continue")),
                             str(props.get("cancel-label") or "Cancel"))
        self.view.set_automatic(bool(props.get("choice-chosen")))
        self._set_busy(False)
        self.view.say(request.warning, True)
        if password:
            self.view.focus_field()

    def _answer_prompt(self, cancel: bool) -> None:
        request = self._request
        if request is None or request.closed or not request.waiting:
            return
        if cancel:
            request.answer(None) if request.kind != "confirm" else request.confirm(False)
            return
        if request.kind == "confirm":
            request.confirm(True)
        else:
            password = self.view.password
            if not password:
                self.view.say("Type the password.", True)
                return
            request.answer(password, self.view.automatic if request.properties.get("choice-label") else None)
        self.view.password = ""
        self._set_busy(True)

    # -- what tests and the service read -----------------------------------
    @property
    def password(self) -> str:
        return self.view.password

    @password.setter
    def password(self, value: str) -> None:
        self.view.password = value

    @property
    def busy(self) -> bool:
        return self._busy

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
        self.view.set_texts(MANAGE_TITLE, FIRST_LINE + " " + sentence)
        autologin = self._autologin
        if autologin is None:
            from .autounlock import is_autologin
            autologin = is_autologin()
        self.view.set_automatic(automatic)
        # Without automatic sign-in the keyring opens with the password typed
        # at sign-in; the switch only means something where nothing is typed.
        self.view.set_shown(field=False, switch=autologin or automatic, buttons=False, cancel=True,
                            primary="Turn On")
        self.view.password = ""

    def _on_switch_toggled(self, active: bool) -> None:
        if self._busy or self._use is not Use.MANAGE:
            return
        changing = active != first_unlock.is_automatic()
        self.view.say("", False)
        self.view.set_shown(field=changing, switch=True, buttons=changing, cancel=True,
                            primary="Turn On" if active else "Turn Off")
        if changing:
            self.view.focus_field()
        else:
            self.view.password = ""

    # -- doing it ---------------------------------------------------------
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        primary = self.view.shown()["primary"]
        if self._use is Use.FIRST and self._result is None:
            primary = "Unlocking…" if busy else "Unlock"
        self.view.set_busy(busy, primary)

    def _on_primary(self) -> None:
        if self._busy:
            return
        if self._use is Use.PROMPT:
            self._answer_prompt(cancel=False)
            return
        if self._result is not None and self._result.kind is Kind.UNLOCKED_ONLY:
            self._finish(self._result)
            return
        password = self.view.password
        if not password:
            self.view.say("Type your password.", True)
            return
        automatic = self.view.automatic
        if self._keyring_password is not None:
            # Step two: the password this account signs in with, then enrol.
            opened = self._keyring_password
            second = self._second_work or (lambda login, older: first_unlock.enrol_with_login_password(
                login, older, self._keyring))
            work = lambda: second(password, opened)  # noqa: E731
        elif self._work is not None:
            work = lambda: self._work(password, automatic)  # noqa: E731 - tests stand in here
        elif self._use is Use.FIRST:
            work = lambda: first_unlock.unlock(password, automatic, self._keyring)  # noqa: E731
        else:
            work = lambda: first_unlock.set_automatic(automatic, password, self._keyring)  # noqa: E731
        self.view.say("", False)
        self._set_busy(True)

        def run() -> None:
            try:
                result = work()
            except Exception as error:  # noqa: BLE001 - shown, never a crash with a password in hand
                result = Result(Kind.UNAVAILABLE, f"Something went wrong: {error}")
            GLib.idle_add(self._answered, result, password)

        threading.Thread(target=run, name="luma-keyring-unlock", daemon=True).start()

    def _answered(self, result: Result, password: str | None = None) -> bool:
        self._set_busy(False)
        self.view.password = ""
        if result.kind is Kind.NEEDS_LOGIN_PASSWORD:
            # The keyring opened with an older password: one more step, in place.
            self._keyring_password = password
            self.view.set_texts("One more step", f"Enter the password you sign in to {self._noun} with. "
                                "It becomes the backup key for your saved passwords.")
            self.view.set_shown(field=True, switch=False, buttons=True, cancel=True, primary="Continue")
            self.view.set_labels(self.view.texts()[2], "Continue", "Not Now")
            self.view.say("", False)
            self.view.focus_field()
            return GLib.SOURCE_REMOVE
        if result.kind is not Kind.WRONG_PASSWORD:
            self._keyring_password = None
        self._result = result
        if result.kind in (Kind.WRONG_PASSWORD, Kind.UNAVAILABLE, Kind.NOT_CHANGED):
            self.view.say(result.message, True)
            self.view.focus_field()
        elif self._use is Use.MANAGE:
            self._show_state()
            self.view.say(result.message, False)
        elif result.kind is Kind.UNLOCKED_ONLY:
            # Unlocked for this session; say why it is not automatic, once.
            self.view.say(result.message, False)
            self.view.set_shown(field=False, switch=False, buttons=True, cancel=False, primary="Done")
        else:
            self._finish(result)
        return GLib.SOURCE_REMOVE

    def _on_cancel(self) -> None:
        if self._keyring_password is not None:
            # "Not Now" on step two: unlocked for this session, asked again next time.
            self._keyring_password = None
            self._finish(Result(Kind.UNLOCKED))
            return
        if self._use is Use.PROMPT:
            self._answer_prompt(cancel=True)
            return
        if self._use is Use.MANAGE and self.view.shown()["field"]:
            self._show_state()
            self.view.say("", False)
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
        self.view.password = ""
        self._keyring_password = None
        try:
            if self._on_done is not None:
                self._on_done(result)
        finally:
            if destroy:
                self.close()
