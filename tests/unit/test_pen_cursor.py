# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

path = Path(__file__).resolve().parents[2] / 'src/luma-platform/appkit/luma_appkit/pen_cursor.py'
spec = importlib.util.spec_from_file_location('pen_cursor', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PenCursorTests(unittest.TestCase):
    def cursor(self):
        state = SimpleNamespace(cursor=None)
        widget = SimpleNamespace(get_cursor=lambda: state.cursor,
                                 set_cursor=lambda value: setattr(state, 'cursor', value))
        cursor = module.PenCursor.__new__(module.PenCursor)
        cursor.widget, cursor.override, cursor.cursor = widget, False, None
        return cursor, state

    def test_pen_uses_shared_static_dot_and_mouse_restores_default(self):
        cursor, state = self.cursor()
        cursor.update(True)
        self.assertIs(state.cursor, module.pen_dot())
        self.assertEqual((state.cursor.get_hotspot_x(), state.cursor.get_hotspot_y()), (8, 8))
        cursor.update(False)
        self.assertIsNone(state.cursor)

    def test_explicit_brush_cursor_is_preserved(self):
        cursor, state = self.cursor()
        brush = state.cursor = object()
        cursor.update(True); cursor.leave()
        self.assertIs(state.cursor, brush)

    def test_application_cursor_change_during_hover_is_preserved(self):
        cursor, state = self.cursor()
        cursor.update(True)
        brush = state.cursor = object()
        cursor.update(True); cursor.update(False)
        self.assertIs(state.cursor, brush)


if __name__ == '__main__':
    unittest.main()
