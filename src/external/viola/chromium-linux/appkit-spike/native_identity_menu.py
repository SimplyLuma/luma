# SPDX-License-Identifier: GPL-3.0-only
"""Expose retained application commands through the toolkit identity menu."""
from gi.repository import Gio
from native_menu import NativeMenu


def command_at(description, path):
    items = description['items']
    for depth, index in enumerate(path):
        row = next((item for item in items if item['index'] == index), None)
        if not row or not row['visible'] or not row['enabled']:
            return None
        if depth + 1 == len(path):
            return row if row['kind'] in ('item', 'check', 'radio') else None
        if row['kind'] != 'submenu':
            return None
        items = row['children']
    return None


class NativeIdentityMenu:
    def __init__(self, window, description):
        self.description = description
        # AppWindow's identity consumes the application's GMenuModel. Reuse
        # the same retained-menu adapter with application-scoped actions.
        self.menu = NativeMenu(description, self.activate, action_prefix='app')
        application = window.get_application()
        self.application = application
        for name in self.menu.actions.list_actions():
            application.add_action(self.menu.actions.lookup_action(name))
        model = self.menu.get_menu_model()
        existing = application.get_menubar()
        if isinstance(existing, Gio.Menu):
            # Native headerbars retain the initial model object. Updating its
            # sections notifies those real popovers; swapping the application
            # property can leave them showing the bootstrap command registry.
            existing.remove_all()
            for index in range(model.get_n_items()):
                existing.append_item(Gio.MenuItem.new_from_model(model, index))
        else:
            application.set_menubar(model)
        window.submit(lambda: window.services.dismiss_menu(description['nonce']))

    def activate(self, _old_nonce, path):
        original = command_at(self.description, path)
        if not original:
            return
        window = self.application.get_active_window()
        if window is None or not getattr(window, 'services', None):
            return
        def invoke():
            # Opening any other menu consumes the old retained model. Obtain
            # a fresh original model, and verify the command before invoking.
            services = window.services
            fresh = services.open_menu('app:menu', {'anchorRect': {'x': 0, 'y': 0, 'width': 28, 'height': 28}})
            # Model indices can change after a tab/window closes. Match each
            # original semantic ancestor in the fresh, nonce-owned model.
            old_items, new_items, resolved = self.description['items'], fresh['items'], []
            for index in path:
                old = next((row for row in old_items if row['index'] == index), None)
                matches = [row for row in new_items if old and
                           (row['kind'], row['label']) == (old['kind'], old['label'])
                           and row['visible'] and row['enabled']]
                if len(matches) != 1:
                    services.dismiss_menu(fresh['nonce'])
                    return
                current = matches[0]
                resolved.append(current['index'])
                old_items, new_items = old.get('children', []), current.get('children', [])
            services.activate_menu(fresh['nonce'], resolved)
        window.submit(invoke)
