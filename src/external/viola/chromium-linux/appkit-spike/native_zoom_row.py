# SPDX-License-Identifier: GPL-3.0-only
"""Luma kit candidate: inline browser zoom, composed from installed widgets.

This structural composition awaits upstream AppKit adoption; menu surfaces,
colors, typography, focus and button rendering remain toolkit-owned.
"""
from pathlib import Path
from gi.repository import Gdk, Gtk
from luma_appkit import IconButton, add_style_sheet


class NativeZoomRow(Gtk.Box):
    _provider = None

    def __init__(self, row, path, repeat, invoke, context):
        if NativeZoomRow._provider is None:
            NativeZoomRow._provider = add_style_sheet(
                str(Path(__file__).parent / 'kit_candidates/inline_zoom.css'),
                priority=Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        super().__init__(spacing=4, height_request=34)
        self.add_css_class('luma-menu-row')
        self.add_css_class('luma-inline-zoom')
        self.append(Gtk.Label(label='Zoom', xalign=0, hexpand=True))
        children = [child for child in row['children'] if child['kind'] != 'separator']
        expected = ['Zoom out', 'Reset to 100%', 'Zoom in', 'Full screen']
        if [child['label'] for child in children] != expected:
            raise ValueError('Unrecognized retained zoom commands')
        self.buttons = []
        for index, child in enumerate(children):
            command_path = path + (child['index'],)
            if index == 1:
                button = Gtk.Button(label=str(row['value']) + '%', width_request=44, height_request=26)
                button.add_css_class('flat')
                button.add_css_class('luma-zoom-value')
                button.set_tooltip_text(child['label'])
            else:
                if index == 3:
                    self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL,
                        height_request=16, valign=Gtk.Align.CENTER))
                icon = {0: 'list-remove-symbolic', 2: 'list-add-symbolic',
                        3: 'view-fullscreen-symbolic'}[index]
                button = IconButton(icon, child['label'], context=context, quiet=True)
                button.set_size_request(28, 26)
            button.set_valign(Gtk.Align.CENTER)
            button.set_sensitive(row['enabled'] and child['enabled'] and child['visible'])
            button.connect('clicked', lambda _, p=command_path, close=index == 3:
                           invoke(p) if close else repeat(p))
            button.retained_path = command_path
            self.buttons.append(button)
            self.append(button)
