# SPDX-License-Identifier: GPL-3.0-only
"""Window-local keyboard zoom through Chromium's retained menu commands."""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, Gtk


def zoom_command(key, state):
    meaningful = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK |
                         Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK |
                         Gdk.ModifierType.META_MASK)
    ctrl = Gdk.ModifierType.CONTROL_MASK
    if meaningful in (ctrl, ctrl | Gdk.ModifierType.SHIFT_MASK) and key in (
            Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
        return 'Zoom in'
    if meaningful == ctrl:
        if key in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            return 'Zoom out'
        if key in (Gdk.KEY_0, Gdk.KEY_KP_0):
            return 'Reset to 100%'
    return None


def activate_zoom(services, label, tab_id):
    if (services.state or {}).get('activeTabId') != tab_id:
        return
    menu = services.open_menu('browser:menu',
        {'anchorRect': {'x': 0, 'y': 0, 'width': 28, 'height': 28}})
    for row in menu['items']:
        if row.get('role') != 'zoom' or not row['enabled'] or not row['visible']:
            continue
        for child in row.get('children', []):
            if child['label'] == label and child['enabled'] and child['visible']:
                if (services.state or {}).get('activeTabId') == tab_id:
                    services.activate_menu(menu['nonce'], [row['index'], child['index']])
                    return
    services.dismiss_menu(menu['nonce'])


def install_zoom_shortcuts(window):
    keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
    def pressed(_controller, key, _code, state):
        label = zoom_command(key, state)
        if label is None:
            return False
        services = window.services
        if services and services.state:
            tab = services.state.get('activeTabId')
            if window.open_popover:
                window.open_popover.popdown()
            window.submit(lambda: activate_zoom(services, label, tab))
        return True
    keys.connect('key-pressed', pressed)
    window.add_controller(keys)
    return keys
