# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, Gtk

from .lumaui_window import APP_ID, DarkroomWindow


class DarkroomApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._about)
        self.add_action(about)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", self._quit)
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def do_activate(self) -> None:
        window = self.props.active_window or DarkroomWindow(self)
        window.present()
        window.offer_recovery()

    def do_open(self, files, _n_files, _hint) -> None:
        paths = [Path(file.get_path()) for file in files if file.get_path()]
        window = self.props.active_window or DarkroomWindow(self)
        window.present()
        window.open_paths(paths)

    def _quit(self, *_args) -> None:
        # Close-request preserves every dirty photo; quit() bypasses that hook.
        for window in tuple(self.get_windows()): window.close()


    def _about(self, *_args) -> None:
        dialog = Adw.AboutDialog(
            application_name="Darkroom",
            application_icon=APP_ID,
            developer_name="Project Luma",
            version="0.1.0",
            comments="A native, non-destructive image editor for Luma.",
            website="https://projectluma.org",
            license_type=Gtk.License.APACHE_2_0,
        )
        dialog.present(self.props.active_window)


def main(argv: list[str] | None = None) -> int:
    return DarkroomApplication().run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
