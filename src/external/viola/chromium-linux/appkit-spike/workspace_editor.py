# SPDX-License-Identifier: GPL-3.0-only
"""Native workspace settings; all mutations use existing organization commands."""
from gi.repository import Adw, GLib, Gtk
from workspace_colors import THEMES, dot


class WorkspaceEditor(Adw.Dialog):
    def __init__(self, host):
        super().__init__(title='Edit workspaces', content_width=480, content_height=560)
        self.host = host
        self.rows = {}
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.page = Adw.PreferencesPage()
        self.group = Adw.PreferencesGroup(title='Workspaces')
        self.page.add(self.group)
        create = Adw.PreferencesGroup(title='New workspace')
        entry = Adw.EntryRow(title='Workspace name', show_apply_button=True)
        entry.connect('apply', lambda *_: self.create(entry))
        create.add(entry)
        self.page.add(create)
        view.set_content(self.page)
        self.set_child(view)
        self.refresh(host.state or {})

    def create(self, entry):
        name = entry.get_text().strip()
        if name:
            self.host.send('space:create', {'name': name, 'icon': '◫'})
            entry.set_text('')

    def refresh(self, state):
        spaces = state.get('spaces', [])
        ids = {space['id'] for space in spaces}
        for key in list(self.rows):
            if key not in ids:
                self.group.remove(self.rows.pop(key))
        for space in spaces:
            key = space['id']
            row = self.rows.get(key)
            if row is None:
                row = Adw.EntryRow(title='Workspace name', show_apply_button=True)
                row.set_text(space.get('name', ''))
                row.connect('apply', lambda entry, k=key: self.rename(k, entry))
                row.color = Gtk.Button(tooltip_text='Workspace color', valign=Gtk.Align.CENTER)
                row.color.add_css_class('flat')
                row.color.connect('clicked', lambda button, k=key: self.colors(button, k))
                row.add_prefix(row.color)
                row.delete = Gtk.Button(icon_name='user-trash-symbolic', tooltip_text='Delete workspace', valign=Gtk.Align.CENTER)
                row.delete.add_css_class('flat')
                row.delete.connect('clicked', lambda _, k=key: self.delete(k))
                row.add_suffix(row.delete)
                self.group.add(row)
                self.rows[key] = row
            row.color.set_child(dot(space.get('theme')))
            row.delete.set_sensitive(len(spaces) > 1)

    def rename(self, key, entry):
        name = entry.get_text().strip()
        if name:
            self.host.send('space:update', {'spaceId': key, 'patch': {'name': name}})

    def colors(self, button, key):
        from native_menu import NativeMenu
        space = next((s for s in (self.host.state or {}).get('spaces', []) if s['id'] == key), {})
        items = [dict(index=i, kind='check', label=label, theme=theme, visible=True,
                      enabled=True, checked=theme == space.get('theme'))
                 for i, (theme, label, _) in enumerate(THEMES)]
        menu = NativeMenu({'nonce': 'workspace-colors', 'items': items},
            lambda _, path: self.host.send('space:update',
                {'spaceId': key, 'patch': {'theme': THEMES[path[0]][0]}}))
        menu.set_parent(button)
        menu.connect('closed', lambda popup: GLib.idle_add(lambda: popup.unparent() or False))
        menu.popup()

    def delete(self, key):
        confirm_delete(self.host, key)


def confirm_delete(host, key):
    dialog = Adw.AlertDialog(heading='Delete workspace?', body='Its tabs will be closed. This cannot be undone.')
    dialog.add_response('cancel', 'Cancel')
    dialog.add_response('delete', 'Delete workspace')
    dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response('cancel')
    dialog.set_close_response('cancel')
    dialog.connect('response', lambda _, response: host.send('space:delete', {'spaceId': key})
                   if response == 'delete' else None)
    dialog.present(host)
    return dialog
