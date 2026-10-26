# SPDX-License-Identifier: Apache-2.0
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk

ROOT = Path(__file__).resolve().parents[2] / 'src/prairie-core/prairie_apps'


def method(filename, name):
    tree = ast.parse((ROOT / filename).read_text())
    node = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)
    scope = {'Gtk': Gtk}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), filename, 'exec'), scope)
    return scope[name]


class MediaPinchTests(unittest.TestCase):
    def test_tablet_grid_resizes_from_gesture_start_and_updates_slider(self):
        grid = SimpleNamespace(_phone=False, state=SimpleNamespace(size=168, zoom='days'),
                               _pinch_from=168, _size_changed=Mock(), size_control=Mock())
        method('photos.py', '_pinched')(grid, None, 1.5)
        grid._size_changed.assert_called_once_with(252)
        grid.size_control.set_value.assert_called_once()

    def test_grid_claims_pinch_instead_of_activating_tile_on_release(self):
        grid = SimpleNamespace(state=SimpleNamespace(size=200))
        gesture = Mock()
        method('photos.py', '_pinch_begun')(grid, gesture, None)
        gesture.set_state.assert_called_once_with(Gtk.EventSequenceState.CLAIMED)
        self.assertEqual(grid._pinch_from, 200)

    def test_camera_zoom_uses_existing_zoom_transaction_and_limits(self):
        surface = SimpleNamespace(window=SimpleNamespace(state=SimpleNamespace(zoom=2)), zoom=Mock())
        gesture = Mock()
        method('camera_surface.py', '_zoom_begin')(surface, gesture, None)
        gesture.set_state.assert_called_once_with(Gtk.EventSequenceState.CLAIMED)
        changed = method('camera_surface.py', '_zoom_changed')
        for scale, expected in ((1.5, 3), (.1, 1), (10, 4)):
            changed(surface, gesture, scale)
            self.assertEqual(surface.zoom.call_args.args, (expected,))


if __name__ == '__main__':
    unittest.main()
