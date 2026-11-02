# SPDX-License-Identifier: MPL-2.0
"""The window that puts it right.

One window, one question at a time, and no promises the repair cannot keep.
It says what is wrong in the words a person would use, asks for the two
passwords it needs and why, and keeps the second choice -- starting a fresh
keyring -- visible but not the obvious one, with what it costs said before
it is taken rather than after.

It has four shapes, one per situation (see Mode): a keyring locked with an
older password; an account that signs in automatically and still has a
password-locked keyring, which is switched to the device key with that
password typed once; a device key that no longer opens the keyring; and an
account that stopped signing in automatically, offered its login password
back.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .repair import (repair_in_place, start_again, start_again_with_device_key,  # noqa: E402
                     switch_to_device_key, use_login_password)
from .secrets import Keyring  # noqa: E402
from .state import Reading, State  # noqa: E402

__all__ = ("KeyringWindow", "Mode")

TITLE = "Saved passwords"


class Mode:
    MISMATCH = "mismatch"
    DEVICE = "device"
    DEVICE_KEY_LOST = "device-key-lost"
    LOGIN = "login"


_EXPLAIN = {
    Mode.MISMATCH: (
        "Your saved passwords are locked with an older password",
        "Wi-Fi passwords, website logins and app sign-ins are kept in a "
        "keyring that is unlocked with your login password. Yours was "
        "locked with a different one, so it cannot be opened for you and "
        "you are asked every time you log in.\n\n"
        "Type the password it was locked with and your login password, "
        "and Luma will change the keyring over to your login password. "
        "Everything in it is kept."),
    Mode.DEVICE: (
        "Your saved passwords can unlock by themselves",
        "This device signs in without a password, so nothing can unlock "
        "your saved passwords for you and apps keep asking. Type the "
        "password they are locked with once, and Luma will lock them with "
        "a key this device keeps instead. Everything in them is kept.\n\n"
        "From then on they are protected by this device and its storage, "
        "not by a password: anyone who can use this device can use them."),
    Mode.DEVICE_KEY_LOST: (
        "This device can no longer unlock your saved passwords",
        "They were locked with a key this device kept, and that key no "
        "longer works here -- for example after the security chip was "
        "reset. Nobody can read them without it."),
    Mode.LOGIN: (
        "Your saved passwords unlock without your password",
        "They were set up to unlock by themselves while this device signed "
        "in automatically. It now asks for your password, so you can lock "
        "them with your login password too. Everything in them is kept."),
}


class KeyringWindow(Adw.ApplicationWindow):
    def __init__(self, application, reading: Reading, keyring: Keyring | None = None,
                 mode: str = Mode.MISMATCH):
        super().__init__(application=application, title=TITLE,
                         default_width=520, default_height=420)
        self._keyring = keyring or Keyring()
        self._reading = reading
        self._mode = mode
        self._busy = False

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.set_content(toolbar)

        page = Adw.PreferencesPage()
        toolbar.set_content(page)

        title, description = _EXPLAIN[mode]
        page.add(Adw.PreferencesGroup(title=title, description=description))

        fields = Adw.PreferencesGroup()
        self._old = Adw.PasswordEntryRow(
            title="The password they are locked with" if mode == Mode.DEVICE
            else "Older password")
        self._login = Adw.PasswordEntryRow(title="Your login password")
        if mode in (Mode.MISMATCH, Mode.DEVICE):
            fields.add(self._old)
        if mode in (Mode.MISMATCH, Mode.LOGIN):
            fields.add(self._login)
        if mode != Mode.DEVICE_KEY_LOST:
            page.add(fields)

        actions = Adw.PreferencesGroup()
        label = {Mode.DEVICE: "Unlock by themselves from now on",
                 Mode.LOGIN: "Use my login password"}.get(mode, "Unlock and keep them")
        self._unlock = Gtk.Button(label=label,
                                  css_classes=["suggested-action", "pill"],
                                  halign=Gtk.Align.CENTER)
        self._unlock.connect("clicked", self._on_unlock)
        actions.add(self._unlock)
        if mode != Mode.DEVICE_KEY_LOST:
            page.add(actions)

        self._status = Adw.PreferencesGroup()
        self._message = Gtk.Label(wrap=True, xalign=0)
        self._message.set_visible(False)
        self._status.add(self._message)
        page.add(self._status)

        forgotten = Adw.PreferencesGroup(
            title="If you do not know the older password",
            description=(
                "Nobody can read what is in the keyring without it, Luma "
                "included. You can keep being asked at login, or start a new "
                "keyring: the old one is kept on this computer in case you "
                "remember, and what is in it stays out of reach until you do."))
        start = Gtk.Button(label="Start a new keyring", css_classes=["pill"],
                           halign=Gtk.Align.CENTER)
        start.connect("clicked", self._on_start_again)
        forgotten.add(start)
        if mode == Mode.DEVICE_KEY_LOST:
            forgotten.set_title("Start again")
            forgotten.set_description(
                "Start a new keyring that this device unlocks. The old one is "
                "kept on this computer, out of reach.")
        if mode != Mode.LOGIN:
            page.add(forgotten)

        if mode == Mode.MISMATCH and (reading.state is not State.LOCKED
                                      or not reading.repairable):
            self._say(reading.sentence, ok=reading.state is State.UNLOCKED)
            self._unlock.set_sensitive(False)

    # -- doing it ---------------------------------------------------------
    def _say(self, text: str, ok: bool = False) -> None:
        self._message.set_text(text)
        self._message.set_css_classes(["success"] if ok else ["error"])
        self._message.set_visible(True)

    def _run(self, work) -> None:
        if self._busy:
            return
        self._busy = True
        self._unlock.set_sensitive(False)

        def finish() -> bool:
            outcome = work()
            self._say(outcome.message, ok=outcome.ok)
            self._busy = False
            self._unlock.set_sensitive(not outcome.ok)
            if outcome.ok:
                self._old.set_text("")
                self._login.set_text("")
            return GLib.SOURCE_REMOVE

        GLib.idle_add(finish)

    def _on_unlock(self, _button) -> None:
        if self._mode == Mode.DEVICE:
            old = self._old.get_text()
            if not old:
                self._say("Type the password your saved passwords are locked with.")
                return
            self._run(lambda: switch_to_device_key(old, self._keyring))
            return
        if self._mode == Mode.LOGIN:
            login = self._login.get_text()
            if not login:
                self._say("Type your login password.")
                return
            self._run(lambda: use_login_password(login, self._keyring))
            return
        old = self._old.get_text()
        login = self._login.get_text()
        if not old or not login:
            self._say("Both passwords are needed to change the keyring over.")
            return
        self._run(lambda: repair_in_place(old, login, self._keyring))

    def _on_start_again(self, _button) -> None:
        device = self._mode in (Mode.DEVICE, Mode.DEVICE_KEY_LOST)
        login = self._login.get_text()
        if not device and not login:
            self._say("Type your login password, so the new keyring can be "
                      "locked with it and unlock when you log in.")
            return

        dialog = Adw.AlertDialog(
            heading="Start a new keyring?",
            body=("What is in the old one -- saved Wi-Fi passwords, website "
                  "logins and app sign-ins -- stays locked. The file is kept "
                  "on this computer, so remembering the password later still "
                  "gets it back."))
        dialog.add_response("cancel", "Keep asking me")
        dialog.add_response("start", "Start a new keyring")
        dialog.set_response_appearance("start", Adw.ResponseAppearance.DESTRUCTIVE)

        def answered(_dialog, response: str) -> None:
            if response != "start":
                return
            if device:
                self._run(lambda: start_again_with_device_key(self._keyring))
            else:
                self._run(lambda: start_again(login, self._keyring))

        dialog.connect("response", answered)
        dialog.present(self)
