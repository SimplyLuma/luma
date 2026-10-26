# SPDX-License-Identifier: Apache-2.0
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

source = Path(__file__).resolve().parents[2] / 'src/luma-viewer/luma_viewer/application.py'
tree = ast.parse(source.read_text())
methods = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in ('_mark_begin', '_cancel_drag')]
scope = {'Gdk': NS(DeviceToolType=NS(MOUSE='mouse')),
         'Gtk': NS(EventSequenceState=NS(CLAIMED='claimed')), 'Mark': Mock()}
exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])), str(source), 'exec'), scope)

class PenStrokeTests(unittest.TestCase):
    def begin(self, tool, valid=True):
        state = NS(cursor=None)
        area = Mock()
        area.get_cursor.side_effect = lambda: state.cursor
        area.set_cursor.side_effect = lambda value: setattr(state, 'cursor', value)
        area.set_cursor_from_name.side_effect = lambda name: setattr(state, 'cursor', name)
        app = NS(cropping=False, compare=False, mode='markup', loaded=True, saving=False,
                 marks_area=area, document=None, tool='ink', stroke_width=2, ink='#000', page=0,
                 _viewport=lambda: NS(to_page=lambda value: value if valid else None), _page_size=lambda: (900, 600),
                 _refresh_actions=Mock())
        device = NS(get_tool_type=lambda: tool) if tool is not None else None
        gesture = NS(get_current_event=lambda: NS(get_device_tool=lambda: device), set_state=Mock())
        scope['_mark_begin'](app, gesture, 10, 20)
        return app, state, gesture

    def test_pen_hides_only_while_inking_and_cancel_restores_hover(self):
        app, state, gesture = self.begin('pen')
        self.assertEqual(state.cursor, 'none')
        scope['_cancel_drag'](app)
        self.assertIsNone(state.cursor)
        self.assertIsNone(app._inking_cursor)

    def test_mouse_and_touch_keep_their_cursor(self):
        for tool in ('mouse', None):
            app, state, gesture = self.begin(tool)
            self.assertIsNone(state.cursor)
            self.assertFalse(hasattr(app, '_inking_cursor'))

    def test_accepted_strokes_own_touch_mouse_and_pen_sequences(self):
        for tool in ('pen', 'mouse', None):
            _app, _state, gesture = self.begin(tool)
            gesture.set_state.assert_called_once_with('claimed')

    def test_outside_document_does_not_claim_the_scroll_sequence(self):
        _app, _state, gesture = self.begin(None, valid=False)
        gesture.set_state.assert_not_called()

    def test_cancel_respects_an_independent_cursor_change(self):
        app, state, gesture = self.begin('pen')
        state.cursor = 'custom'
        scope['_cancel_drag'](app)
        self.assertEqual(state.cursor, 'custom')

if __name__ == '__main__':
    unittest.main()
