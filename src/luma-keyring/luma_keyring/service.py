# SPDX-License-Identifier: MPL-2.0
"""The session service: look once, say so once, and then be quiet.

At the start of a session the keyring is still settling -- PAM unlocks it a
moment after the session starts -- so the check waits for that to finish
before deciding anything. If the login keyring is still locked, the person
is told once, in a notification with a way to put it right, and never again
this session. If they say never, they are not asked again at all.

What they are told depends on the account (see autounlock): one that signs
in automatically and is not enrolled yet, or whose sealed key stopped
opening, is asked for its password once; one that stopped signing in
automatically is offered its login password back.
"""

from __future__ import annotations

import logging
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib  # noqa: E402

from . import autounlock  # noqa: E402
from .secrets import Keyring  # noqa: E402
from .state import SETTLE_SECONDS, State, read  # noqa: E402

__all__ = ("KeyringService", "main")

log = logging.getLogger("luma-keyring")

APP_ID = "org.projectluma.Keyring"
SCHEMA_ID = "org.projectluma.keyring"
NOTIFICATION_ID = "login-keyring-locked"


class KeyringService(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._keyring = Keyring()
        self._settings: Gio.Settings | None = None
        self._window = None
        self._checked = False
        self._mode = None

        self.add_action_entries([
            ("open", lambda *_: self._open_window()),
            ("never", lambda *_: self._never_again()),
        ])

    # -- lifetime ---------------------------------------------------------
    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        try:
            self._settings = Gio.Settings(schema_id=SCHEMA_ID)
        except GLib.Error:
            log.info("no settings schema; asking once per session only")
        self.hold()
        GLib.timeout_add_seconds(SETTLE_SECONDS, self._check)

    def do_activate(self) -> None:
        # Activated by hand (or from the notification): show the window.
        self._open_window()

    # -- the check --------------------------------------------------------
    def _check(self) -> bool:
        self._checked = True
        reading = read(self._keyring)
        log.info("login keyring: %s", reading.sentence)
        autologin = autounlock.is_autologin()
        sealed = autounlock.credential_path().exists()

        from .window import Mode
        if autologin and (reading.state in (State.LOCKED, State.NO_KEYRING) or
                          (not sealed and autounlock.stored_unencrypted())):
            self._mode = Mode.RECOVER if sealed else Mode.DEVICE
        elif reading.state is State.LOCKED:
            self._mode = Mode.RECOVER if sealed else Mode.MISMATCH
        elif reading.state is State.UNLOCKED and sealed and not autologin:
            self._mode = Mode.LOGIN
        elif reading.state is State.UNLOCKED and sealed and autounlock.wrap_is_stale():
            self._mode = Mode.REWRAP
        else:
            if reading.state is State.UNLOCKED:
                self.withdraw_notification(NOTIFICATION_ID)
            self._done()
            return GLib.SOURCE_REMOVE

        if self._settings is not None and not self._settings.get_boolean("ask-about-login-keyring"):
            log.info("the person asked not to be told about this again")
            self._done()
            return GLib.SOURCE_REMOVE

        self._tell(reading.repairable)
        return GLib.SOURCE_REMOVE

    def _tell(self, repairable: bool) -> None:
        from .window import Mode
        if self._mode == Mode.REWRAP:
            notification = Gio.Notification.new("Type your new password once")
            notification.set_body(
                "You changed your password. Type it once so it can still recover "
                "your saved passwords on other hardware.")
            notification.add_button("Type it", "app.open")
        elif self._mode == Mode.LOGIN:
            notification = Gio.Notification.new("Your saved passwords unlock without your password")
            notification.set_body(
                "This device no longer signs in automatically. Lock your saved "
                "passwords with your login password instead?")
            notification.add_button("Use my password", "app.open")
        elif self._mode in (Mode.DEVICE, Mode.RECOVER):
            notification = Gio.Notification.new("Let your saved passwords unlock by themselves")
            notification.set_body(
                "This device signs in automatically, but your saved passwords "
                "need your password to unlock. Type it once and they will "
                "unlock by themselves from now on.")
            notification.add_button("Set up", "app.open")
        else:
            notification = Gio.Notification.new("Your saved passwords are locked")
            if repairable:
                notification.set_body(
                    "They are locked with an older password, so Wi-Fi passwords and "
                    "website logins cannot be filled in for you. Luma can change "
                    "them over to your login password.")
                notification.add_button("Unlock them", "app.open")
            else:
                notification.set_body(
                    "They are locked with an older password and this computer's "
                    "keyring cannot be changed over, so you will be asked for it "
                    "when something needs a saved password.")
        notification.add_button("Never mind", "app.never")
        notification.set_priority(Gio.NotificationPriority.NORMAL)
        self.send_notification(NOTIFICATION_ID, notification)
        # The notification is the whole conversation unless they open the
        # window; nothing keeps the service alive after it is sent.
        GLib.timeout_add_seconds(30, self._done_if_idle)

    def _done_if_idle(self) -> bool:
        if self._window is None:
            self._done()
        return GLib.SOURCE_REMOVE

    def _done(self) -> None:
        if self._window is None:
            self.release()

    # -- the window -------------------------------------------------------
    def _open_window(self) -> None:
        if self._window is not None:
            self._window.present()
            return
        from .window import KeyringWindow, Mode

        reading = read(self._keyring)
        self._window = KeyringWindow(self, reading, self._keyring,
                                     mode=self._mode or Mode.MISMATCH)
        self._window.connect("close-request", self._closed)
        self._window.present()
        self.withdraw_notification(NOTIFICATION_ID)

    def _closed(self, _window) -> bool:
        self._window = None
        if self._checked:
            self.release()
        return False

    def _never_again(self) -> None:
        if self._settings is not None:
            self._settings.set_boolean("ask-about-login-keyring", False)
        self.withdraw_notification(NOTIFICATION_ID)
        self._done()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="luma-keyring: %(message)s")
    argv = list(argv if argv is not None else sys.argv)
    if "--check" in argv:
        from .cli import check

        return check()
    if "--autologin" in argv:
        from .cli import autologin

        return autologin()
    if "--enable-auto-unlock" in argv:
        from .cli import enable_auto_unlock

        return enable_auto_unlock()
    if "--recover" in argv:
        from .cli import recover

        return recover()
    if "--rewrap" in argv:
        from .cli import rewrap

        return rewrap()
    if "--enrol" in argv:
        from .cli import enrol

        return enrol(argv[argv.index("--enrol") + 1:])
    return KeyringService().run(argv)
