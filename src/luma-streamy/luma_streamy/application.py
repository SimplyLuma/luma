# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Adw, Gio, GLib, Gst  # noqa: E402

APP_ID = "org.projectluma.Streamy"


class StreamyApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        GLib.set_application_name("Streamy")

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            from .window import StreamyWindow

            portals = None
            if not int(os.environ.get("STREAMY_FAKE_SOURCES", "0")):
                from .portals import Portals

                try:
                    portals = Portals()
                except GLib.Error:
                    portals = None
            window = StreamyWindow(self, portals=portals)
            shot = os.environ.get("STREAMY_SHOT")
            if shot:
                from .shots import schedule

                schedule(window, shot)
        window.present()


def main(argv: list[str] | None = None) -> int:
    Gst.init(None)
    return StreamyApplication().run(argv if argv is not None else sys.argv)
