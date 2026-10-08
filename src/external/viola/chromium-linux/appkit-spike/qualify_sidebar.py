# SPDX-License-Identifier: GPL-3.0-only
"""Native widget invariants for long titles and retained organization rows."""
import copy
import json
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
from luma_appkit import install_appkit
from native_sidebar import NativeSidebar


class Host:
    favicons = None
    def __init__(self):
        self.commands = []
    def send(self, channel, payload=None):
        self.commands.append((channel, payload))
    def open_retained_menu(self, *_args, **_kwargs):
        pass
    def button(self, icon, label, action):
        button = Gtk.Button(icon_name=icon, tooltip_text=label)
        button.connect('clicked', lambda *_: action())
        return button
    def _new_tab(self):
        self.commands.append(('new-tab-presentation', None))


def main():
    Gtk.init()
    install_appkit()
    host = Host()
    sidebar = NativeSidebar(host)
    state = {'activeTabId': 'a', 'activeSpaceId': 'one', 'workspaceLensId': 'all',
             'isAllWorkspaces': True, 'collapsedWorkspaceSections': [],
             'spaces': [{'id': 'one', 'name': 'Personal ' * 20}, {'id': 'two', 'name': 'Work'}],
             'favorites': [{'id': 'f', 'title': 'Favorite', 'url': 'https://example.invalid/'}],
             'pinned': [], 'today': [
                 {'id': 'a', 'title': 'Long title ' * 40, 'sourceSpaceId': 'one'},
                 {'id': 'b', 'title': 'Second', 'sourceSpaceId': 'one'}],
             'tabGroups': [{'id': 'group', 'spaceId': 'one', 'title': 'Group ' * 40,
                            'section': 'today', 'tabIds': ['a', 'b'], 'anchorTabId': 'a'}]}
    sidebar.render(state)
    retained = sidebar.rows['a']
    row_height = retained.measure(Gtk.Orientation.VERTICAL, 252)[0]
    width = sidebar.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
    assert width == 252, ('Long titles force excessive width', width)
    assert row_height == 34, ('Tab row differs from 34px contract', row_height)
    assert sidebar.groups['group'].content.get_visible()
    sidebar.toggle_group('group')
    assert not sidebar.groups['group'].content.get_visible()
    assert host.commands[-1] == ('sidebar:collapsedGroups', {'groupIds': ['group']})
    moved = copy.deepcopy(state)
    moved['tabGroups'] = []
    moved['today'][0]['sourceSpaceId'] = 'two'
    sidebar.render(moved)
    assert sidebar.rows['a'] is retained
    assert retained.get_parent() is sidebar.sections['two'].content
    retained.activate.emit('clicked')
    assert host.commands[-1] == ('tab:activate', {'tabId': 'a'})
    retained.close.emit('clicked')
    assert host.commands[-1] == ('tab:close', {'tabId': 'a'})
    print(json.dumps({'classification': 'native widget fixture, not visual acceptance',
                      'long_title_minimum_width': width, 'tab_row_minimum_height': row_height,
                      'row_retained_across_group_and_workspace_move': True,
                      'existing_collapse_activate_close_commands': True}, indent=2))


if __name__ == '__main__':
    main()
