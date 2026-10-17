#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Small real LumaUI app for the tester's source-sync/restart workflow."""
from pathlib import Path
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk
from luma_appkit import Command, CommandGroup, CommandRegistry
from luma_appkit.content_type import apply_type
from luma_appkit.widgets import AppWindow, Island


class Hello(Adw.Application):
    def __init__(self):
        super().__init__(application_id='io.luma.Tester.Hello')

    def do_activate(self):
        if self.get_active_window():
            self.get_active_window().present()
            return
        commands = CommandRegistry((CommandGroup(None, (
            Command('hello.quit', 'Quit', self.quit, 'log-out', shortcut=('Ctrl', 'Q')),
        )),))
        window = AppWindow(application=self, app_id='io.luma.Tester.Hello',
                           title='Hello Luma', icon_name='applications-development-symbolic',
                           commands=commands, default_width=640, default_height=480,
                           minimum_width=280, minimum_height=320)
        island = Island()
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                       halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                       margin_start=24, margin_end=24, margin_top=24, margin_bottom=24)
        title = Gtk.Label(label=Path(__file__).with_name('message.txt').read_text().strip(), wrap=True)
        apply_type(title, 'hero')
        page.append(title)
        description = Gtk.Label(label='Edit message.txt on your computer.\nLuma Tester syncs your changes and restarts this app.', wrap=True)
        apply_type(description, 'body', muted=True)
        page.append(description)
        page.set_vexpand(True)
        page.set_hexpand(True)
        island.append(page)
        window.set_body(island)
        window.present()


raise SystemExit(Hello().run([]))
