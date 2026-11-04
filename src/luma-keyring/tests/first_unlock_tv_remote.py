#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
"""The TV sheet driven by the remote: no keyring daemon, LumaUI's TV layer, LUMA_POSTURE=tv.

    LUMA_POSTURE=tv xvfb-run -a dbus-run-session -- python3 tests/first_unlock_tv_remote.py

Down leaves the field for the switch row and the buttons; the TV keyboard going away
puts the focus on Unlock; the second step (an older keyring password) asks for the
sign-in password in place; Done, and every other shown button, acts.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("LUMA_POSTURE", "tv")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib  # noqa: E402

from luma_keyring.first_unlock import Kind, Result  # noqa: E402
from luma_keyring.secrets import Collection  # noqa: E402
from luma_keyring.sheet import Posture, UnlockSheet, Use  # noqa: E402

failures: list[str] = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'} {name}{f' -- {detail}' if detail else ''}", flush=True)
    if not ok:
        failures.append(name)


def spin(until, seconds=5.0):
    context, end = GLib.MainContext.default(), time.monotonic() + seconds
    while time.monotonic() < end and not until():
        context.iteration(False)
        time.sleep(0.01)
    return until()


class _Keyring:
    def running(self):
        return True

    def collection(self, path=None):
        return Collection("/login", locked=True, label="Login")

    def supports_repair(self):
        return True


def main() -> int:
    from luma_appkit import tv_keyboard

    app = Adw.Application(application_id="org.projectluma.KeyringTvRemote", flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.register(None)
    app.hold()
    answers = [Result(Kind.NEEDS_LOGIN_PASSWORD, ""), Result(Kind.UNLOCKED_ONLY, "Can't seal here.")]
    done = []
    sheet = UnlockSheet(app, Use.FIRST, _Keyring(), noun="this TV",
                        work=lambda _p, _a: answers.pop(0), on_done=done.append,
                        second_work=lambda _login, _older: answers.pop(0))
    check("TV presentation", sheet.presentation is Posture.TV)
    sheet.set_default_size(1920, 1080)
    sheet.present()
    view = sheet.view
    spin(lambda: view.sheet.get_width() > 0)
    focus = view.focus
    check("the field has the remote first", focus.focused is view.field.text)
    focus.handle(Gdk.KEY_Down)
    check("Down leaves the field for the switch row", focus.focused is view.switch_row)
    focus.handle(Gdk.KEY_Down)
    check("Down again reaches a button", focus.focused in (view.cancel, view.primary))
    focus.focus(view.field.text)
    shell = tv_keyboard._get()
    shell._read = lambda: None
    for visible in (True, False):
        shell.visible, shell.height = visible, 428.0 if visible else 0.0
        for watcher in list(shell._watchers):
            watcher(shell.visible, shell.height)
    spin(lambda: focus.focused is view.primary, 2)
    check("the keyboard put away leaves the focus on Unlock", focus.focused is view.primary)

    sheet.password = "older-keyring-password"
    focus.handle(Gdk.KEY_Return)
    spin(lambda: view.texts()[0] == "One more step", 5)
    spin(lambda: False, 0.3)
    check("an older keyring password asks for the sign-in password, in place",
          view.texts()[0] == "One more step" and view.shown()["field"] and not view.shown()["switch"],
          repr(view.texts()[:2]))
    spin(lambda: view.field.text.has_focus(), 3)
    check("step two: the field has the remote and the GTK focus (so the TV keyboard comes up)",
          focus.focused is view.field.text and view.field.text.has_focus())
    check("step two says what the password is for", "backup key" in view.texts()[1], view.texts()[1])
    focus.handle(Gdk.KEY_Down)
    check("step two: Down reaches Not Now / Continue", focus.focused in (view.cancel, view.primary),
          repr(focus.focused))
    focus.focus(view.field.text)
    sheet.password = "sign-in-password"
    # The TV keyboard's action key: the field's Enter continues.
    view.field.text.emit("activate")
    spin(lambda: view.shown()["primary"] == "Done", 5)
    spin(lambda: focus.focused is view.primary, 2)
    check("when it can't unlock automatically, Done has the focus", focus.focused is view.primary
          and view.shown()["primary"] == "Done")
    time.sleep(0.9)  # a new press, not the repeat of the last one
    focus.handle(Gdk.KEY_Return)
    spin(lambda: bool(done), 3)
    check("Done closes the sheet", bool(done) and not sheet.get_visible(), repr(done))
    app.release()
    print(f"{len(failures)} failed" if failures else "all passed", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
