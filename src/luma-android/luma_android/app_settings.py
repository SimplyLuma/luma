# SPDX-License-Identifier: Apache-2.0
"""Luma's small per-app compatibility panel; normal OS controls stay native."""
from __future__ import annotations

import argparse
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
from luma_appkit import AppWindow, Card, CommandRegistry, Switch, apply_type

from .activation import request_existing_restore
from .app_preferences import AppPreferences
from .config import load_runtime_config
from .engine import WaydroidEngine, _validate_package_name
from .errors import LumaAndroidError


class AppSettingsWindow(AppWindow):
    def __init__(self, application, package):
        _validate_package_name(package)
        self.package = package
        self.engine = WaydroidEngine(load_runtime_config().engine)
        self.preferences = AppPreferences(self.engine, package)
        info = Gio.DesktopAppInfo.new(f"waydroid.{package}.desktop")
        name = info.get_display_name() if info else package
        super().__init__(application=application,
            app_id=f"waydroid.{package}", title=name, subtitle="Settings",
            icon_name="org.projectluma.AndroidSettings", commands=CommandRegistry(()),
            geometry_scope="app-compatibility", default_width=500, default_height=260,
            minimum_width=340, minimum_height=230)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(18)
        box.set_margin_end(18)
        row = Gtk.Box(spacing=16)
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        title = Gtk.Label(label="Use mouse clicks as touch", xalign=0)
        apply_type(title, "body", weight=600)
        detail = Gtk.Label(label="For games and apps designed for a touchscreen.",
                           xalign=0, wrap=True)
        apply_type(detail, "small")
        words.append(title)
        words.append(detail)
        row.append(words)
        self.touch = Switch(label="Use mouse clicks as touch")
        self.touch.set_sensitive(False)
        self.touch.connect("state-set", self.change_touch)
        row.append(self.touch)
        box.append(Card(row))
        self.message = Gtk.Label(label="Reading application preferences…", xalign=0, wrap=True)
        apply_type(self.message, "small")
        box.append(self.message)
        self.restart = Gtk.Button(label="Reopen app", halign=Gtk.Align.END, sensitive=False)
        self.restart.connect("clicked", self.reopen)
        box.append(self.restart)
        self.set_body(box)
        self.saved_touch = False
        self.busy = False
        self.work(self.preferences.touch, self.loaded)

    def work(self, action, success):
        self.busy = True
        self.touch.set_sensitive(False)
        self.restart.set_sensitive(False)

        def run():
            try:
                result = action()
            except (LumaAndroidError, OSError) as error:
                GLib.idle_add(self.failed, str(error))
            else:
                GLib.idle_add(success, result)
        threading.Thread(target=run, daemon=True).start()

    def loaded(self, value):
        self.saved_touch = value
        self.touch.set_state(value)
        self.touch.set_active(value)
        self.busy = False
        self.touch.set_sensitive(True)
        self.message.set_label("Changes apply the next time this app opens.")
        return GLib.SOURCE_REMOVE

    def change_touch(self, _switch, enabled):
        if self.busy:
            return True
        self.work(lambda: self.preferences.set_touch(enabled),
                  lambda _result: self.saved(enabled))
        return True

    def saved(self, enabled):
        self.loaded(enabled)
        self.message.set_label("Saved. Reopen this app to apply the change.")
        self.restart.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    def failed(self, message):
        # Never show an unconfirmed switch value as a saved preference.
        self.touch.set_state(self.saved_touch)
        self.touch.set_active(self.saved_touch)
        self.busy = False
        self.touch.set_sensitive(True)
        self.message.set_label(message)
        return GLib.SOURCE_REMOVE

    def reopen(self, _button):
        def action():
            if not request_existing_restore(self.package, close=True):
                raise LumaAndroidError("The app could not be closed. Close it and reopen it normally.")
            self.engine.launch(self.package)
        self.work(action, lambda _result: self.reopened())

    def reopened(self):
        self.busy = False
        self.touch.set_sensitive(True)
        self.message.set_label("Application reopened.")
        return GLib.SOURCE_REMOVE


class AppSettingsApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.projectluma.AndroidAppSettings",
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.windows = {}

    def do_command_line(self, command_line):
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--app", required=True)
        try:
            arguments = parser.parse_args(command_line.get_arguments()[1:])
            _validate_package_name(arguments.app)
        except (SystemExit, LumaAndroidError):
            return 2
        window = self.windows.get(arguments.app)
        if window is None:
            if len(self.windows) >= 16:
                return 1
            window = AppSettingsWindow(self, arguments.app)
            self.windows[arguments.app] = window
            window.connect("close-request", lambda *_: self.windows.pop(arguments.app, None) and False)
        window.present()
        return 0


def main(package):
    _validate_package_name(package)
    return AppSettingsApplication().run(["luma-android-settings", "--app", package])
