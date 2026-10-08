"""Regression for GTK's repeating-gradient nodes in the conform capture walker."""
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import patch


HARNESS = pathlib.Path(__file__).resolve().parents[2] / "tools/lumaui-conform/harness/lumaui_conform_harness.py"
spec = importlib.util.spec_from_file_location("lumaui_conform_harness_test", HARNESS)
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class RGBA:
    def parse(self, value):
        if not value.startswith(("rgb(", "rgba(")) or not value.endswith(")"):
            return False
        fields = [float(v.strip()) for v in value[value.index("(") + 1:-1].split(",")]
        if len(fields) not in (3, 4):
            return False
        self.red, self.green, self.blue = (v / 255 for v in fields[:3])
        self.alpha = fields[3] if len(fields) == 4 else 1
        return True


class RepeatingNode:
    def __init__(self, kind, serialized):
        self.kind, self.serialized = kind, serialized

    def get_node_type(self):
        return types.SimpleNamespace(value_nick=self.kind + "-node")

    def get_bounds(self):
        return types.SimpleNamespace(origin=types.SimpleNamespace(x=0, y=0),
                                     size=types.SimpleNamespace(width=20, height=20))

    def serialize(self):
        return types.SimpleNamespace(get_data=lambda: self.serialized.encode("utf-8"))


class RepeatingGradientCaptureTest(unittest.TestCase):
    def setUp(self):
        repository = types.SimpleNamespace(Gdk=types.SimpleNamespace(RGBA=RGBA),
                                           Graphene=types.SimpleNamespace(), Gsk=types.SimpleNamespace())
        self.modules = patch.dict(sys.modules, {"gi": types.SimpleNamespace(repository=repository),
                                              "gi.repository": repository})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_repeating_linear_node_emits_exact_stops_and_period(self):
        # The pinned host produces this GSK serialization for Calendar's hatch.
        node = RepeatingNode("repeating-linear-gradient", """linear-gradient {
  bounds: 0 0 20 20;
  start: 0 0;
  end: 8 8;
  stops: 0 rgba(255,255,255,0), 0.875 rgba(255,255,255,0), 0.875 rgba(255,255,255,0.04), 1 rgba(255,255,255,0.04);
  repeat: repeat;
} """)
        ops = []
        harness.Capture(None).paint(node, lambda rect: rect, 1, None, ops)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["type"], "repeating-linear-gradient")
        self.assertEqual(ops[0]["stops"], [[0, [255, 255, 255, 0]],
                                          [0.875, [255, 255, 255, 0]],
                                          [0.875, [255, 255, 255, 0.04]],
                                          [1, [255, 255, 255, 0.04]]])
        self.assertEqual(ops[0]["period"], [8, 8])
        self.assertEqual(ops[0]["box"], [0, 0, 20, 20])

    def test_repeating_radial_node_uses_same_safe_stop_path(self):
        node = RepeatingNode("repeating-radial-gradient", """radial-gradient {
  bounds: 0 0 20 20;
  start: 10 10 0;
  end: 10 10 10;
  stops: 0 rgb(0,0,0), 1 rgba(255,0,0,0.5);
  repeat: repeat;
} """)
        ops = []
        harness.Capture(None).paint(node, lambda rect: rect, 0.5, None, ops)
        self.assertEqual(ops[0]["stops"], [[0, [0, 0, 0, 1]], [1, [255, 0, 0, 0.5]]])
        self.assertEqual(ops[0]["period"], [0, 0, 10])
        self.assertEqual(ops[0]["op"], 0.5)

    def test_unrecognized_serialization_fails_instead_of_dropping_paint(self):
        node = RepeatingNode("repeating-linear-gradient", "linear-gradient { stops: unknown; }")
        with self.assertRaisesRegex(ValueError, "no stops"):
            harness.Capture(None).paint(node, lambda rect: rect, 1, None, [])


if __name__ == "__main__":
    unittest.main()
