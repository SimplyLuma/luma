# SPDX-License-Identifier: GPL-3.0-only
"""Workspace identity, shared by native sidebar, picker and theme commands."""
from gi.repository import Gtk

THEMES = (('aurora', 'Aurora', 'violet'), ('sky', 'Sky', 'blue'),
          ('mint', 'Mint', 'green'), ('peach', 'Peach', 'amber'),
          ('rose', 'Rose', 'red'), ('lavender', 'Lavender', 'violet'),
          ('sand', 'Sand', 'amber'), ('slate', 'Slate', 'neutral'),
          ('midnight', 'Midnight', 'blue'), ('graphite', 'Graphite', 'neutral'))


def set_theme(widget, theme):
    tone = next((tone for key, _, tone in THEMES if key == theme), 'blue')
    for candidate in ('blue', 'green', 'amber', 'red', 'violet', 'neutral'):
        (widget.add_css_class if candidate == tone else widget.remove_css_class)(candidate)


def dot(theme):
    widget = Gtk.Box(width_request=8, height_request=8, valign=Gtk.Align.CENTER)
    widget.add_css_class('viola-workspace-dot')
    set_theme(widget, theme)
    return widget
