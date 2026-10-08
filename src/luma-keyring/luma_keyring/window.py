# SPDX-License-Identifier: MPL-2.0
"""The window that puts it right.

One window, one question at a time, and no promises the repair cannot keep.
It says what is wrong in the words a person would use, asks for the two
passwords it needs and why, and keeps the second choice -- starting a fresh
keyring -- visible but not the obvious one, with what it costs said before
it is taken rather than after.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .repair import repair_in_place, start_again  # noqa: E402
from .secrets import Keyring  # noqa: E402
from .state import Reading, State  # noqa: E402

__all__ = ("KeyringWindow",)

TITLE = "Saved passwords"


class KeyringWindow(Adw.ApplicationWindow):
    def __init__(self, application, reading: Reading, keyring: Keyring | None = None):
        super().__init__(application=application, title=TITLE,
                         default_width=520, default_height=420)
        self._keyring = keyring or Keyring()
        self._reading = reading
        self._busy = False

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.set_content(toolbar)

        page = Adw.PreferencesPage()
        toolbar.set_content(page)

        explain = Adw.PreferencesGroup(
            title="Your saved passwords are locked with an older password",
            description=(
                "Wi-Fi passwords, website logins and app sign-ins are kept in a "
                "keyring that is unlocked with your login password. Yours was "
                "locked with a different one, so it cannot be opened for you and "
                "you are asked every time you log in.\n\n"
                "Type the password it was locked with and your login password, "
                "and Luma will change the keyring over to your login password. "
                "Everything in it is kept."))
        page.add(explain)

        fields = Adw.PreferencesGroup()
        self._old = Adw.PasswordEntryRow(title="Older password")
        self._login = Adw.PasswordEntryRow(title="Your login password")
        fields.add(self._old)
        fields.add(self._login)
        page.add(fields)

        actions = Adw.PreferencesGroup()
        self._unlock = Gtk.Button(label="Unlock and keep them",
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
        page.add(forgotten)

        if reading.state is not State.LOCKED or not reading.repairable:
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
        old = self._old.get_text()
        login = self._login.get_text()
        if not old or not login:
            self._say("Both passwords are needed to change the keyring over.")
            return
        self._run(lambda: repair_in_place(old, login, self._keyring))

    def _on_start_again(self, _button) -> None:
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
            if response == "start":
                self._run(lambda: start_again(login, self._keyring))

        dialog.connect("response", answered)
        dialog.present(self)
