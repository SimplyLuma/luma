#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

"""Content-fixed focused-field probe for the Luma mobile input bridge."""

import hashlib

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402


EXPECTED_LENGTH = 10


class Probe(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.project_luma.KeyboardAcceptance")

    def do_activate(self) -> None:
        window = Gtk.ApplicationWindow(application=self, title="Luma input acceptance")
        entry = Gtk.Entry(placeholder_text="Synthetic input probe")
        window.set_child(entry)
        window.set_default_size(420, 180)
        window.present()
        GLib.idle_add(entry.grab_focus)

        def changed(widget: Gtk.Entry) -> None:
            value = widget.get_text()
            if len(value) < EXPECTED_LENGTH:
                return
            print(f"length={len(value)}", flush=True)
            print(
                f"sha256={hashlib.sha256(value.encode()).hexdigest()}",
                flush=True,
            )
            self.quit()

        entry.connect("changed", changed)
        GLib.timeout_add_seconds(12, self._timeout)

    def _timeout(self) -> bool:
        print("timeout=true", flush=True)
        self.quit()
        return GLib.SOURCE_REMOVE


raise SystemExit(Probe().run())
