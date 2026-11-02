# SPDX-License-Identifier: MPL-2.0
"""The window that puts it right.

One window, one question at a time, and no promises the repair cannot keep.
It says what is wrong in the words a person would use, asks for the two
passwords it needs and why, and keeps the second choice -- starting a fresh
keyring -- visible but not the obvious one, with what it costs said before
it is taken rather than after.

It has three shapes, one per situation (see Mode): a keyring locked with an
older password; an account that signs in automatically whose password the
device does not keep yet, which types it once; and one whose sealed
password stopped working (another board, a changed password), which types
it once again.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .repair import (enable_auto_unlock, repair_in_place, start_again,  # noqa: E402
                     start_again_with_auto_unlock)
from .secrets import Keyring  # noqa: E402
from .state import Reading, State  # noqa: E402

__all__ = ("KeyringWindow", "Mode")

TITLE = "Saved passwords"


class Mode:
    MISMATCH = "mismatch"
    DEVICE = "device"
    RECOVER = "recover"


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
        "Let your saved passwords unlock by themselves",
        "This device signs in without asking for your password, so nothing "
        "unlocks your saved passwords and apps keep asking. Type your "
        "password once and this device will keep it sealed, to unlock them "
        "at every sign-in. They stay locked with your password, which still "
        "opens them anywhere else.\n\n"
        "Anyone who can use this device signed in can use them."),
    Mode.RECOVER: (
        "Your saved passwords need your password once",
        "This device could not unlock them by itself -- the password it "
        "keeps no longer opens them, for example after a password change or "
        "on new hardware. Type your password once and it will keep the new "
        "one."),
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
        self._old = Adw.PasswordEntryRow(title="Older password")
        self._login = Adw.PasswordEntryRow(
            title="Your password" if mode != Mode.MISMATCH else "Your login password")
        if mode == Mode.MISMATCH:
            fields.add(self._old)
        fields.add(self._login)
        page.add(fields)

        actions = Adw.PreferencesGroup()
        label = "Unlock and keep them" if mode == Mode.MISMATCH else "Unlock by themselves"
        self._unlock = Gtk.Button(label=label,
                                  css_classes=["suggested-action", "pill"],
                                  halign=Gtk.Align.CENTER)
        self._unlock.connect("clicked", self._on_unlock)
        actions.add(self._unlock)
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
        if mode != Mode.MISMATCH:
            forgotten.set_title("If they are locked with a password you do not know")
            forgotten.set_description(
                "Type your current password and start a new keyring with it. "
                "The old one is kept on this computer, out of reach until you "
                "remember its password.")
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
        if self._mode != Mode.MISMATCH:
            password = self._login.get_text()
            if not password:
                self._say("Type your password.")
                return
            self._run(lambda: enable_auto_unlock(password, self._keyring))
            return
        old = self._old.get_text()
        login = self._login.get_text()
        if not old or not login:
            self._say("Both passwords are needed to change the keyring over.")
            return
        self._run(lambda: repair_in_place(old, login, self._keyring))

    def _on_start_again(self, _button) -> None:
        device = self._mode != Mode.MISMATCH
        login = self._login.get_text()
        if not login:
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
                self._run(lambda: start_again_with_auto_unlock(login, self._keyring))
            else:
                self._run(lambda: start_again(login, self._keyring))

        dialog.connect("response", answered)
        dialog.present(self)
