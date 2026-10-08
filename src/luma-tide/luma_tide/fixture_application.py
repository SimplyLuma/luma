# SPDX-License-Identifier: Apache-2.0
"""Isolated fixture launcher; no import of Tide's real application startup."""
from __future__ import annotations
import os
import tempfile
from pathlib import Path


def main(argv=None):
    import gi
    gi.require_version('Adw', '1')
    from gi.repository import Adw, Gio, GLib, Gtk, Gdk
    from luma_appkit import add_style_sheet, install_appkit, install_lumaui
    from .fixture import FixtureLibrary
    from .ui import TideWindow
    path = Path(os.environ['LUMA_TIDE_FIXTURE']).resolve()
    # Window geometry and toolkit caches also belong to the fixture, never Nick.
    with tempfile.TemporaryDirectory(prefix='tide-fixture-') as sandbox:
        previous = {key: os.environ.get(key) for key in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME', 'GSETTINGS_BACKEND')}
        for key, folder in (('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'), ('XDG_CACHE_HOME', 'cache'), ('XDG_STATE_HOME', 'state')):
            os.environ[key] = str(Path(sandbox) / folder)
        os.environ['GSETTINGS_BACKEND'] = 'memory'
        source = FixtureLibrary(path)
        clock = GLib.timeout_add(1000, source.advance) if os.environ.get('LUMA_TIDE_FIXTURE_CLOCK', '1') != '0' else 0
        class FixtureApplication(Adw.Application):
            def __init__(self):
                super().__init__(application_id='org.projectluma.Tide.LumaUIFixture', flags=Gio.ApplicationFlags.NON_UNIQUE)
                self.window = None
            def do_startup(self):
                Adw.Application.do_startup(self)
                install_appkit()
                install_lumaui()
                Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(
                    str(Path(__file__).resolve().parents[1] / 'data'))
                add_style_sheet(str(Path(__file__).resolve().parents[1] / 'data/tide.css'))
            def do_activate(self):
                if self.window is None:
                    self.window = TideWindow(self, source)
                self.window.present()
            def do_shutdown(self):
                if self.window:
                    self.window.close_resources()
                Adw.Application.do_shutdown(self)
        try:
            return FixtureApplication().run(argv or [])
        finally:
            if clock:
                GLib.source_remove(clock)
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
