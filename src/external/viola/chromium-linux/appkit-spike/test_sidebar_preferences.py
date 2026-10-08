# SPDX-License-Identifier: GPL-3.0-only
import copy
from types import SimpleNamespace
import unittest
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
from native_sidebar import NativeSidebar
from sidebar_preferences import bounded


class SidebarPreferencesTest(unittest.TestCase):
    def test_settings_are_bounded_before_css_generation(self):
        result = bounded({'tabHeight': float('nan'), 'tabRadius': 'bad',
                          'tabSpacing': -300, 'workspaceContentInset': 500})
        self.assertEqual(result['tabHeight'],34)
        self.assertEqual(result['tabRadius'],9)
        self.assertEqual(result['tabSpacing'],0)
        self.assertEqual(result['workspaceContentInset'],24)

    def test_layout_and_ownership_changes_keep_the_same_rows(self):
        def button(icon, label, callback):
            return Gtk.Button(icon_name=icon)
        host = SimpleNamespace(button=button,send=lambda *args:None,_new_tab=lambda:None,favicons=None)
        sidebar = NativeSidebar(host)
        self.addCleanup(sidebar.reorder.close)
        self.addCleanup(sidebar.preferences.close)
        state = {'settings':{},'spaces':[{'id':'a','name':'Personal'},{'id':'b','name':'Work'}],
                 'activeSpaceId':'a','workspaceLensId':'@all','isAllWorkspaces':True,
                 'activeTabId':'one','today':[{'id':'one','sourceSpaceId':'a','title':'One'},
                 {'id':'two','sourceSpaceId':'a','title':'Two','viewedElsewhere':True},
                 {'id':'three','sourceSpaceId':'b','title':'Three'}]}
        sidebar.render(state)
        row = sidebar.rows['two']
        self.assertIs(row.get_parent(),sidebar.sections['a'].content)
        state = copy.deepcopy(state)
        state['settings'] = {'otherWindowTabsLayout':'nested','allWorkspacesGroupByWorkspace':False,
                             'tabSpacing':7,'workspaceContentInset':18}
        sidebar.render(state)
        self.assertIs(row,sidebar.rows['two'])
        self.assertIs(row.get_parent(),sidebar.sections['a'].elsewhere_content)
        self.assertFalse(sidebar.sections['a'].header.get_visible())
        self.assertEqual(sidebar.sections['a'].content.get_spacing(),7)
        self.assertEqual(sidebar.sections['a'].content.get_margin_start(),0)
        state['today'][1]['viewedElsewhere'] = False
        state['settings']['allWorkspacesGroupByWorkspace'] = True
        sidebar.render(state)
        self.assertIs(row.get_parent(),sidebar.sections['a'].content)
        self.assertIs(row,sidebar.rows['two'])
        self.assertTrue(sidebar.sections['a'].header.get_visible())
        self.assertEqual(sidebar.sections['a'].content.get_margin_start(),18)


if __name__ == '__main__':
    unittest.main()
