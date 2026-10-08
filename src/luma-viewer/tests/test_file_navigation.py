# SPDX-License-Identifier: Apache-2.0
"""v70 arrow navigation keeps a stable list while history becomes most recent."""
from pathlib import Path
from types import SimpleNamespace
import unittest
import gi
gi.require_version('Gdk','4.0')
gi.require_version('Gtk','4.0')
from gi.repository import Gdk,Gtk
from luma_viewer.composition import ViewerUI


class FileNavigationTests(unittest.TestCase):
    def window(self, mode='view'):
        entries=[SimpleNamespace(path=p,exists=p!='/gone')
            for p in ('/first','/second','/gone','/third')]
        window=SimpleNamespace(saving=False,guard_open=False,loaded=True,mode=mode,
            facts=SimpleNamespace(path=Path('/first')),get_focus=lambda:None)
        window.recents=SimpleNamespace(grouped=lambda:[('',entries)])
        visited=[]
        def opening(path,**_kwargs):
            visited.append(path)
            window.facts.path=Path(path)
            entry=next(e for e in entries if e.path==path)
            entries.remove(entry);entries.insert(0,entry)
        window.open_path=opening
        return window,visited

    def key(self,window,key,state=Gdk.ModifierType(0)):
        return ViewerUI._file_key(window,None,key,0,state)

    def test_down_up_skip_missing_and_do_not_wrap_or_reload_endpoints(self):
        window,visited=self.window()
        for key in (Gdk.KEY_Down,Gdk.KEY_Down,Gdk.KEY_Down,
                    Gdk.KEY_Up,Gdk.KEY_Up,Gdk.KEY_Up):
            self.assertTrue(self.key(window,key))
        self.assertEqual(visited,['/second','/third','/second','/first'])

    def test_horizontal_arrows_only_navigate_in_view_mode(self):
        window,visited=self.window('markup')
        self.assertFalse(self.key(window,Gdk.KEY_Right))
        self.assertTrue(self.key(window,Gdk.KEY_Down))
        self.assertFalse(self.key(window,Gdk.KEY_Left))
        self.assertEqual(visited,['/second'])

    def test_save_guard_dialog_and_loading_leave_navigation_unhandled(self):
        for attr,value in (('saving',True),('guard_open',True),('loaded',False)):
            window,visited=self.window()
            setattr(window,attr,value)
            self.assertFalse(self.key(window,Gdk.KEY_Down))
            self.assertEqual(visited,[])

    def test_a_single_existing_file_never_reloads(self):
        window,visited=self.window()
        window.recents.grouped=lambda:[('',[SimpleNamespace(path='/first',exists=True)])]
        self.assertTrue(self.key(window,Gdk.KEY_Right))
        self.assertEqual(visited,[])

    def test_keypad_arrows_use_the_same_stable_order(self):
        window,visited=self.window()
        self.assertTrue(self.key(window,Gdk.KEY_KP_Down))
        self.assertTrue(self.key(window,Gdk.KEY_KP_Right))
        self.assertTrue(self.key(window,Gdk.KEY_KP_Left))
        self.assertEqual(visited,['/second','/third','/second'])

    def test_annotation_slider_and_modified_arrows_keep_their_keys(self):
        window,visited=self.window('markup')
        window.marks_area=Gtk.Box()
        window.selected=0
        window.get_focus=lambda:window.marks_area
        self.assertFalse(self.key(window,Gdk.KEY_Down))
        window.get_focus=lambda:Gtk.Scale()
        self.assertFalse(self.key(window,Gdk.KEY_Up))
        window.get_focus=lambda:None
        self.assertFalse(self.key(window,Gdk.KEY_Down,Gdk.ModifierType.CONTROL_MASK))
        self.assertEqual(visited,[])


if __name__=='__main__':unittest.main()
