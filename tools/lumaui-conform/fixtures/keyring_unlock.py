# SPDX-License-Identifier: MPL-2.0
"""lumaui-conform fixture: the saved-passwords sheet (src/luma-keyring), no keyring daemon.

Run by the harness as `bin/run --module keyring_unlock` (scenarios keyring-unlock and
tv-keyring-unlock). LUMA_CONFORM_KEYRING_STATE picks what the Design Center's mockup
(mockups/luma-keyring-unlock.html) shows:

  sheet      the first unlock, the field focused (default)
  wrong      after a wrong password: the error under the field
  keyboard   TV: the field typing, the TV keyboard up (the keyboard is the Shell's and
             never in the app's window; its state is set here as the Shell would report it)

The posture is the harness's: LUMA_POSTURE=tv for the TV scenario, the phone form factor
for --phone, the desktop otherwise. Nothing here reads or writes a keyring.
"""
from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib  # noqa: E402

from luma_keyring import first_unlock  # noqa: E402
from luma_keyring.first_unlock import Kind, Result  # noqa: E402
from luma_keyring.secrets import Collection  # noqa: E402
from luma_keyring.sheet import UnlockSheet, Use  # noqa: E402


class _Keyring:
    """A locked login keyring, as far as the sheet asks."""

    def running(self):
        return True

    def collection(self, path=None):
        return Collection("/org/freedesktop/secrets/collection/login", locked=True, label="Login")

    def supports_repair(self):
        return True


def build(app: Adw.Application) -> None:
    from pathlib import Path
    from gi.repository import Gdk, Gtk

    # The Prairie theme's LumaUI glyphs (the field's lock), as on a Luma system.
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    theme.add_search_path(str(Path(__file__).resolve().parents[3] / "assets/icon-theme/Prairie/symbolic/actions"))
    state = os.environ.get("LUMA_CONFORM_KEYRING_STATE", "sheet")
    first_unlock.is_automatic = lambda: False
    sheet = UnlockSheet(app, Use.FIRST, _Keyring(), noun=first_unlock.device_noun(),
                        work=lambda _password, _automatic: Result(Kind.WRONG_PASSWORD, ""))
    if state == "wrong":
        sheet._answered(Result(Kind.WRONG_PASSWORD, "That password didn't unlock them. Try again."))
    elif state == "keyboard":
        from luma_appkit import tv_keyboard

        shell = tv_keyboard._get()
        shell.visible, shell.height = True, float(tokens_keyboard_height())
        for watcher in list(shell._watchers):
            watcher(shell.visible, shell.height)
    size = os.environ.get("LUMA_CONFORM_WINDOW") or ("1920x1080" if sheet.presentation.value == "tv" else "")
    if size:
        width, height = (int(v) for v in size.split("x"))
        sheet.set_default_size(width, height)
    sheet.present()


def tokens_keyboard_height() -> int:
    from luma_appkit import lumaui_tokens

    k = lumaui_tokens.TV["keyboard"]
    return k["padding_top"] + 5 * k["key_height"] + 4 * k["row_gap"] + k["padding_bottom"]


def main() -> int:
    app = Adw.Application(application_id="org.projectluma.Keyring", flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.connect("activate", build)
    return app.run(sys.argv[:1])


if __name__ == "__main__":
    raise SystemExit(main())
