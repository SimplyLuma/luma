# SPDX-License-Identifier: GPL-3.0-only
"""Present retained Chromium shell menu models with the native GTK menu.

GMenu sections preserve separators at every submenu depth. All actions route
back to the original retained Chromium model, with no duplicate command body.
No application menu stylesheet or geometry is defined here.
"""
import re
from workspace_colors import THEMES, dot
import menu_interaction
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gio, GLib, Gtk
from native_zoom_row import NativeZoomRow
from luma_appkit import Command
from luma_appkit.menus import Menu as AppKitMenu, accelerator, _descendants, VARIANTS


def native_label(label, mnemonic=False):
    # Chromium uses ampersand mnemonics; GTK uses underscores. Preserve
    # literal ampersands and underscores rather than displaying markers.
    if mnemonic:
        label = label.replace('_', '__')
    return re.sub(r'&(.)', lambda match: '&' if match[1] == '&'
                  else ('_' if mnemonic else '') + match[1], label)


def native_accelerator(label):
    if label.endswith('++'):
        keys = tuple(label[:-2].split('+')) + ('plus',)
    else:
        keys = tuple(label.split('+'))
    # GTK accelerator syntax requires named punctuation keysyms. A literal
    # comma yields an empty accelerator label in the identity menu.
    if keys and len(keys[-1]) == 1 and not keys[-1].isalnum():
        keys = keys[:-1] + (Gdk.keyval_name(Gdk.unicode_to_keyval(ord(keys[-1]))),)
    return accelerator(keys)


class NativeMenu(AppKitMenu):
    __gtype_name__ = 'ViolaRetainedNativeMenuProbe'

    def __init__(self, description, activate, action_prefix='viola', repeat=None, context=None, prefer_submenu_left=False, hide_root_title=False):
        Gtk.PopoverMenu.__init__(self, flags=Gtk.PopoverMenuFlags.NESTED)
        self.action_prefix = action_prefix
        self._activate = activate
        self._repeat = repeat
        self.context = context
        self.hide_root_title = hide_root_title
        self.prefer_submenu_left = prefer_submenu_left
        self._placed = []
        self.set_has_arrow(False)
        self.add_css_class('luma-menu-popover')
        self.add_css_class('luma-menu-app')
        self.connect('map', lambda popup: GLib.idle_add(self._settle_layout, popup))
        self.reset(description)
        if description.get('role') == 'select':
            keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
            keys.connect('key-pressed', self._select_boundary_key)
            self.add_controller(keys)

    def _select_boundary_key(self, _controller, key, _code, _state):
        if key not in (Gdk.KEY_Home, Gdk.KEY_End):
            return False
        rows = [widget for _, widget in self._placed
                if isinstance(widget, Gtk.Button)
                and widget.get_mapped() and widget.is_sensitive()]
        if not rows:
            return False
        return (rows[0] if key == Gdk.KEY_Home else rows[-1]).grab_focus()

    def reset(self, description):
        # Replace the entire retained model after a repeat action. A fresh
        # nonce must never be paired with stale dynamic history/menu paths.
        for popover, widget in self._placed:
            popover.remove_child(widget)
        self._placed = []
        self.actions = Gio.SimpleActionGroup()
        self.paths = {}
        self.model_item_limit = 4096 if description.get('role') == 'select' else 128
        self.nonce = description['nonce']
        self.invoked = False
        self.decorations = {}
        self.custom_rows = {}
        self.zoom_rows = {}
        self._checked_rows = []
        self.set_menu_model(self._model(description['items'], ()))
        self.insert_action_group('viola', self.actions)
        self.insert_action_group('menu', self.actions)
        popovers = [self] + [w for w in _descendants(self) if isinstance(w, Gtk.PopoverMenu)]
        menu_interaction.install(popovers)
        for popover in popovers[1:]:
            popover.add_css_class(VARIANTS['submenu'])
            popover.connect('show', self.position_submenu)
            popover.connect('map', lambda popup: GLib.idle_add(self._settle_layout, popup))
        def place(widget, key):
            for popover in popovers:
                if popover.add_child(widget, key):
                    self._placed.append((popover, widget))
                    return
            raise RuntimeError('Missing native menu slot: ' + key)
        for key, (command, label, theme) in self.custom_rows.items():
            button = self._custom_row(command)
            for child in _descendants(button):
                if isinstance(child, Gtk.Label):
                    child.set_text_with_mnemonic(native_label(label, mnemonic=True))
                    child.set_mnemonic_widget(button)
                    break
            if theme:
                button.get_child().prepend(dot(theme))
            button.set_focus_on_click(False)
            place(button, key)
        for key, (title, count) in self.decorations.items():
            place(self._submenu_heading(title, count), key)
        for key, (row, path) in self.zoom_rows.items():
            place(NativeZoomRow(row, path, lambda p: self._repeat(self, p),
                                self._invoke, self.context), key)

    @staticmethod
    def _settle_layout(popup):
        # GTK section separators become visible in an idle after custom slots
        # are populated. Re-present once with their final natural size; the
        # first configure otherwise clips the final command by their margins.
        if popup.get_mapped():
            popup.queue_resize()
            popup.present()
        return GLib.SOURCE_REMOVE

    def position_submenu(self, popover):
        AppKitMenu._position_submenu(popover)
        if self.prefer_submenu_left:
            popover.set_position(Gtk.PositionType.LEFT)

    def _model(self, items, parent, parent_label=None):
        model = Gio.Menu()
        section = Gio.Menu()
        title = None

        def flush():
            nonlocal section, title
            if section.get_n_items():
                model.append_section(title, section)
            section = Gio.Menu()
            title = None

        if len(parent) >= 8 or len(items) > self.model_item_limit:
            raise ValueError('Menu exceeds the retained model bounds')
        for row in items:
            if not row['visible']:
                continue
            kind = row['kind']
            if kind == 'title' and not parent and self.hide_root_title:
                continue
            if kind in ('separator', 'title'):
                flush()
                title = native_label(row['label']) if kind == 'title' else None
                continue
            path = parent + (row['index'],)
            name = 'item-' + '-'.join(str(index) for index in path)
            self.paths[path] = name
            if kind in ('check', 'radio'):
                action = Gio.SimpleAction.new_stateful(name, None,
                    GLib.Variant.new_boolean(row.get('checked', False)))
            else:
                action = Gio.SimpleAction.new(name, None)
            action.set_enabled(row['enabled'])
            action.connect('activate', lambda _action, _parameter, p=path: self._invoke(p))
            self.actions.add_action(action)
            item = Gio.MenuItem.new(native_label(row['label'], mnemonic=True), self.action_prefix + '.' + name)
            if row.get('accelerator'):
                item.set_attribute_value('accel', GLib.Variant.new_string(native_accelerator(row['accelerator'])))
            if row.get('role') == 'zoom' and self._repeat is not None:
                key = 'viola-native-zoom:' + name
                self.zoom_rows[key] = (row, path)
                item = Gio.MenuItem.new(None, None)
                item.set_attribute_value('custom', GLib.Variant.new_string(key))
                section.append_item(item)
                continue
            if kind == 'submenu':
                submenu = self._model(row['children'], path, native_label(row['label']))
                key = 'viola-native-header:' + name
                header = Gio.MenuItem.new(None, None)
                header.set_attribute_value('custom', GLib.Variant.new_string(key))
                submenu.prepend_item(header)
                self.decorations[key] = (native_label(row['label']), sum(
                    child['visible'] and child['kind'] not in ('separator', 'title')
                    for child in row['children']))
                item.set_submenu(submenu)
            if self.action_prefix != 'app' and kind in ('item', 'check', 'radio'):
                shortcut = row.get('accelerator', '')
                keys = (tuple(shortcut[:-2].split('+')) + ('+',) if shortcut.endswith('++')
                        else tuple(shortcut.split('+')) if shortcut else ())
                command = Command(name, native_label(row['label']), lambda p=path: self._invoke(p),
                    shortcut=keys, enabled=lambda r=row: r['enabled'],
                    checked=(lambda r=row: r.get('checked', False)) if kind in ('check', 'radio') else None)
                custom = 'viola-native-row:' + name
                theme = row.get('theme')
                if parent_label in ('Workspace color', 'Theme'):
                    theme = next((key for key, label, _ in THEMES if label == native_label(row['label'])), None)
                self.custom_rows[custom] = (command, row['label'], theme)
                item.set_attribute_value('custom', GLib.Variant.new_string(custom))
            section.append_item(item)
        flush()
        return model

    def _invoke(self, path):
        self.invoked = True
        self.popdown()
        self._activate(self.nonce, list(path))
