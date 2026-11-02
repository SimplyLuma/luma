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
prompter): it is the session's keyring prompter from before the Shell starts, so an app's prompt is answered by the same sheet. It
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
    def __init__(self, ask_first: bool = False, prompter=None) -> None:
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._keyring = Keyring()
        self._settings: Gio.Settings | None = None
        self._window = None
        self._checked = False
        self._mode = None
        self._ask_first = ask_first
        self._prompter = prompter
        self._gate_tries = 0
        self._draining: str | None = None   # the sheet's password, only while answering held prompts
        self._parked: list = []             # prompts the sheet's password did not open (another keyring)
        self._prompt_sheet = None
        self._first_pending = ask_first      # the first-unlock sheet is still to come or up
        if prompter is not None:
            self._adopt(prompter)

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
    def _adopt(self, prompter) -> None:
        prompter.on_request = self._on_prompt
        prompter.on_closed = self._on_prompt_closed

    def _first_unlock(self, by_hand: bool = False) -> bool:
        """The sheet, as the session's keyring prompter while the keyring is locked.

        At session start only when should_ask(); by hand (the keyring page
        opened on an automatic sign-in that does not unlock by itself yet)
        also when it is already unlocked, to turn automatic unlock on.
        """
        from .first_unlock import is_automatic, should_ask

        locked = read(self._keyring).state is State.LOCKED
        if locked and self._prompter is None:
            from .prompter import LumaPrompter

            prompter = LumaPrompter()
            if prompter.take(replace=True):
                self._prompter = prompter
                self._adopt(prompter)
        wanted = should_ask(self._keyring) or (
            by_hand and autounlock.is_autologin() and not is_automatic()
            and read(self._keyring).state in (State.LOCKED, State.UNLOCKED))
        if not wanted:
            self._first_unlock_done(None)
            return GLib.SOURCE_REMOVE
        from .sheet import UnlockSheet, Use

        self._first_pending = True
        self._window = UnlockSheet(self, Use.FIRST, self._keyring, work=self._unlock_work,
                                   on_done=self._first_unlock_done)
        self._window.connect("close-request", self._closed)
        self._window.present()
        notify_ready()
        return GLib.SOURCE_REMOVE

    # -- prompts held while the sheet asks -----------------------------------
    def _unlock_work(self, password: str, automatic: bool):
        """The sheet's Unlock (on its thread): open the keyring, answer what waits, enrol."""
        import threading
        import time

        from . import first_unlock, repair
        from .first_unlock import Kind, Result

        opened = first_unlock.unlock(password, False, self._keyring)
        if opened.kind is not Kind.UNLOCKED or self._prompter is None:
            if opened.kind is Kind.UNLOCKED and automatic:
                return first_unlock.unlock(password, True, self._keyring)
            return opened
        # Every prompt that waits (an app that asked meanwhile) is answered with the same
        # password, through the secret exchange, before the keyring moves to its device key.
        done = threading.Event()
        GLib.idle_add(self._drain_start, password)
        quiet_since = time.monotonic()
        deadline = quiet_since + 15
        while time.monotonic() < deadline:
            busy = any(r.waiting and r not in self._parked for r in self._prompter.pending())
            if busy:
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since > 0.8:
                break
            time.sleep(0.1)
        GLib.idle_add(lambda: (self._drain_stop(), done.set(), False)[2])
        done.wait(5)
        if not automatic:
            return Result(Kind.UNLOCKED)
        outcome = repair.enable_auto_unlock(password, self._keyring)
        if outcome.ok:
            return Result(Kind.AUTOMATIC, repair.password_quality_note(password).strip())
        return Result(Kind.UNLOCKED_ONLY, "They're unlocked for now, but can't unlock "
                                          "automatically yet. " + outcome.message)

    def _drain_start(self, password: str) -> bool:
        self._draining = password
        for request in self._prompter.pending():
            self._on_prompt(request)
        return GLib.SOURCE_REMOVE

    def _drain_stop(self) -> None:
        self._draining = None

    def _on_prompt(self, request) -> None:
        """A prompt from gnome-keyring is waiting for its answer."""
        if not request.waiting or request in self._parked:
            if request is getattr(self._prompt_sheet, "_request", None) and request.waiting:
                self._prompt_sheet.prompt_again(request)
            return
        if self._draining is not None and request.kind == "password" and request.answers == 0:
            request.answer(self._draining)
            return
        if self._draining is not None or request.answers > 0 or (self._window is None and not self._first_pending):
            # Not opened by the sheet's password (another keyring, a key), or asked
            # when no sheet is up: it gets a sheet of its own, once the first is done.
            self._parked.append(request)
            if self._window is None:
                self._show_parked()
        # Otherwise it waits: the first-unlock sheet is up, and its Unlock answers it.

    def _on_prompt_closed(self, request) -> None:
        if request in self._parked:
            self._parked.remove(request)
        if self._prompt_sheet is not None and self._prompt_sheet._request is request:
            sheet, self._prompt_sheet = self._prompt_sheet, None
            sheet._finish(None)
        self._show_parked()

    def _show_parked(self) -> None:
        if self._prompt_sheet is not None or self._window is not None:
            return
        waiting = [r for r in self._parked if r.waiting and not r.closed]
        if not waiting:
            self._maybe_release()
            return
        from .sheet import UnlockSheet, Use

        self.hold()
        self._prompt_sheet = UnlockSheet(self, Use.PROMPT, self._keyring, request=waiting[0],
                                         on_done=lambda _r: self.release())
        self._prompt_sheet.present()

    def _maybe_release(self) -> None:
        """Back to the Shell once nothing here waits for an answer."""
        if self._prompter is None or self._window is not None or self._prompt_sheet is not None:
            return
        if any(r.waiting for r in self._prompter.pending()):
            return
        self._prompter.release()
        self._prompter = None
        log.info("keyring prompts go to the Shell again")

    def _first_unlock_done(self, result) -> None:
        from .first_unlock import Kind

        log.info("first unlock: %s", result.kind.value if result else "cancelled")
        if self._prompter is not None:
            if result is None or not result.unlocked:
                # Cancelled: what waits for the login keyring is cancelled with it, by the
                # person's choice; anything else still gets its own sheet.
                for request in self._prompter.pending():
                    if request.waiting and request not in self._parked:
                        request.answer(None)
            self.hold()

            def after() -> bool:
                self._show_parked()
                self.release()
                return GLib.SOURCE_REMOVE
            GLib.idle_add(after)
        self._first_pending = False
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
            from .first_unlock import is_automatic

            if autounlock.is_autologin() and not is_automatic():
                # An automatic sign-in that does not unlock by itself yet: the
                # same sheet as at session start, switch on.
                self._first_unlock(by_hand=True)
                return
            # Otherwise the keyring's own page: its state and the switch.
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
        GLib.idle_add(lambda: (self._show_parked(), False)[1])
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


def main(argv: list[str] | None = None, ask_first: bool | None = None, prompter=None) -> int:
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
        ask_first, prompter = decide(argv)
    return KeyringService(ask_first=ask_first, prompter=prompter).run(argv)
