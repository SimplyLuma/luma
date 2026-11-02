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

The one case nothing else opens -- automatic sign-in, no device seal, the
keyring locked -- does not wait: the unit is ordered before the graphical
session is reached, decides before GTK is even loaded, and shows Luma's own
sheet (see first_unlock and sheet) while it holds the system prompter (see
prompt_gate), so no app can raise a second password prompt meanwhile. It
tells systemd it is ready once the sheet is up, or straight away when there
is nothing to ask, and never later than READY_BOUND_SECONDS.
"""

from __future__ import annotations

import logging
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib  # noqa: E402

from . import autounlock  # noqa: E402
from .ready import READY_BOUND_SECONDS, decide, notify_ready  # noqa: E402
from .secrets import Keyring  # noqa: E402
from .state import SETTLE_SECONDS, State, read  # noqa: E402

__all__ = ("KeyringService", "main")

log = logging.getLogger("luma-keyring")

APP_ID = "org.projectluma.Keyring"
SCHEMA_ID = "org.projectluma.keyring"
NOTIFICATION_ID = "login-keyring-locked"
# How long the sheet waits for a prompter that cannot be replaced (a prompt
# gcr-prompter is showing) before it shows anyway.
GATE_WAIT_SECONDS = 5

class KeyringService(Adw.Application):
    def __init__(self, ask_first: bool = False) -> None:
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._keyring = Keyring()
        self._settings: Gio.Settings | None = None
        self._window = None
        self._checked = False
        self._mode = None
        self._ask_first = ask_first
        self._gate = None
        self._gate_tries = 0

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
        GLib.timeout_add_seconds(READY_BOUND_SECONDS, notify_ready)
        if self._ask_first:
            self._checked = True
            GLib.idle_add(self._first_unlock)
        else:
            notify_ready()
            GLib.timeout_add_seconds(SETTLE_SECONDS, self._check)

    def do_activate(self) -> None:
        # Activated by hand (or from the notification): show the window.
        self._open_window()

    # -- the first unlock ---------------------------------------------------
    def _first_unlock(self) -> bool:
        from .prompt_gate import PromptGate

        if self._gate is None:
            self._gate = PromptGate()
        if not self._gate.take():
            self._gate_tries += 1
            if self._gate_tries <= GATE_WAIT_SECONDS:
                GLib.timeout_add_seconds(1, self._first_unlock)
                return GLib.SOURCE_REMOVE
            log.info("showing the sheet without holding the prompter")
        # Still locked? Something may have unlocked it while we waited.
        from .first_unlock import should_ask
        if not should_ask(self._keyring):
            self._first_unlock_done(None)
            return GLib.SOURCE_REMOVE
        from .sheet import UnlockSheet, Use

        self._window = UnlockSheet(self, Use.FIRST, self._keyring,
                                   on_done=self._first_unlock_done)
        self._window.connect("close-request", self._closed)
        self._window.present()
        notify_ready()
        return GLib.SOURCE_REMOVE

    def _first_unlock_done(self, result) -> None:
        from .first_unlock import Kind

        if self._gate is not None:
            self._gate.release()
        log.info("first unlock: %s", result.kind.value if result else "cancelled")
        if result is not None and result.kind is Kind.AUTOMATIC and result.message:
            notification = Gio.Notification.new("Your saved passwords unlock by themselves now")
            notification.set_body(result.message)
            self.send_notification("login-keyring-weak-password", notification)
        notify_ready()
        if self._window is None:
            self._done()

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
        if self._mode is None:
            # Opened by hand with nothing to put right: the keyring's own page.
            from .sheet import UnlockSheet, Use

            self._window = UnlockSheet(self, Use.MANAGE, self._keyring)
            self._window.connect("close-request", self._closed)
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
        from .window import Mode
        # A keyring that still opens without a password on an account that
        # now signs in with one is worth asking about again next session.
        if self._settings is not None and self._mode != Mode.LOGIN:
            self._settings.set_boolean("ask-about-login-keyring", False)
        self.withdraw_notification(NOTIFICATION_ID)
        self._done()


def main(argv: list[str] | None = None, ask_first: bool | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="luma-keyring: %(message)s")
    argv = list(argv if argv is not None else sys.argv)
    from .cli import dispatch

    code = dispatch(argv)
    if code is not None:
        return code
    # The window takes passwords: no core dumps, no tracing.
    from .autounlock import no_core_dumps

    no_core_dumps()
    if ask_first is None:
        ask_first = decide(argv)
    return KeyringService(ask_first=ask_first).run(argv)
