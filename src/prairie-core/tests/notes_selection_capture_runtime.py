# SPDX-License-Identifier: Apache-2.0
"""Conform selection is a real editor operation after focus settles."""
import unittest
from notes_v71_phone_runtime import PhoneTests, pump

class SelectionCaptureTests(PhoneTests):
    width,height=1180,740
    def test_settled_selection_capture_opens_menu_repeatedly(self):
        import importlib.util
        from pathlib import Path
        from gi.repository import GLib
        path=Path(__file__).resolve().parents[3]/'tools/lumaui-conform/harness/lumaui_conform_harness.py'
        spec=importlib.util.spec_from_file_location('capture_selection_test',path)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        w=self.window
        capture=module.Capture(GLib)
        capture.window=w
        for _ in range(3):
            w.buffer.place_cursor(w.buffer.get_start_iter())
            capture.pending_actions=[{'focus':'nt-doc'},{'wait':150},
                {'select_text':{'widget':'nt-doc','start':0,'end':10}},{'wait':350},
                {'activate':'nt-selection-more'}]
            while not capture.act(): pump(.1)
            pump()
            self.assertFalse(capture.notes)
            self.assertEqual(tuple(i.get_offset() for i in w.buffer.get_selection_bounds()),(0,10))
            menu=capture.find(w,'nt-format-menu')
            self.assertIsNotNone(menu)
            self.assertTrue(menu.get_mapped())
            self.assertEqual(len(menu.buttons),5)
            menu.close()
            pump()

if __name__=='__main__':unittest.main()
