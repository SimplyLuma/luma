# SPDX-License-Identifier: GPL-3.0-only
"""Inspect actual AppKit ownership without presenting an unfinished browser.

This is a structural integration probe, not an application launcher. In
particular it intentionally does not put a screenshot stream into a GtkPicture
and call that an embedded Chromium browser.
"""
import argparse
import json
import gi

gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Adw, Gio, Gtk
from luma_appkit import AppWindow, Command, CommandGroup, CommandRegistry, EmptyState, Island, Toolbar, install_appkit

APP_ID = 'org.projectluma.Viola.NativeHostProbe'

class NativeHost(AppWindow):
    def __init__(self, application):
        commands = CommandRegistry((CommandGroup(None, (
            Command('probe.close', 'Close integration probe', self.close, 'window-close-symbolic'),
        )),))
        super().__init__(application=application, app_id=APP_ID,
                         title='Viola native host integration probe',
                         icon_name='com.rhyme.viola', commands=commands,
                         default_width=1180, default_height=820,
                         minimum_width=640, minimum_height=460)
        content = Island()
        self.toolbar = Toolbar()
        content.append(self.toolbar)
        # No placeholder browser data, buttons without behavior, custom
        # caption controls, copied palette, or pretended page embedding.
        content.append(EmptyState(
            'Chromium embedding is not integrated',
            'Internal architecture probe. This is not a browser preview.',
            'dialog-information-symbolic'))
        self.set_body(content)

    def inspect(self):
        counts = {}
        def visit(widget):
            name = widget.__gtype__.name
            counts[name] = counts.get(name, 0) + 1
            child = widget.get_first_child()
            while child:
                visit(child)
                child = child.get_next_sibling()
        visit(self)
        return {
            'classification': 'unpresented structural probe; not runtime visual acceptance',
            'window_base': AppWindow.__module__ + '.' + AppWindow.__name__,
            'toolkit_window': isinstance(self, Adw.ApplicationWindow),
            'decorated': self.get_decorated(),
            'title_row_measure': list(self.title_bar.measure(Gtk.Orientation.VERTICAL, 1180)),
            'toolbar_measure': list(self.toolbar.measure(Gtk.Orientation.VERTICAL, 900)),
            'window_minimum_request': list(self.get_size_request()),
            'real_gtk_window_controls': counts.get('GtkWindowControls', 0),
            'real_gtk_menu_buttons': counts.get('GtkMenuButton', 0),
            'widget_types': counts,
            'application_stylesheets': [],
            'page_host': 'unimplemented; screenshot transport rejected',
            'presented_to_user': False,
        }

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--inspect-only', action='store_true', required=True)
    parser.parse_args()
    application = Adw.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
    application.register(None)
    install_appkit()
    host = NativeHost(application)
    report = host.inspect()
    if report['real_gtk_window_controls'] < 1:
        raise RuntimeError('AppKit must own native GTK window controls')
    # AdwHeaderBar creates leading/trailing controls internally, including
    # empty/hidden groups. Raw object count does not prove visible duplication.
    print(json.dumps(report, indent=2))
    host.destroy()
