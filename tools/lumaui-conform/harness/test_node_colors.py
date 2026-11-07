#!/usr/bin/env python3
"""Render-node colours in a non-sRGB colour state (python3 -m unittest discover)."""
import importlib.util
from pathlib import Path
import unittest


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("lumaui_conform_harness", HERE / "lumaui_conform_harness.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class Serialized:
    def __init__(self, text):
        self.text = text

    def serialize(self):
        text = self.text

        class Bytes:
            def get_data(self):
                return text.encode()
        return Bytes()


class Raw:
    red, green, blue, alpha = 0.48, 0.12, 250.547, 1.0


class NodeColorTest(unittest.TestCase):
    def test_oklch_is_converted_not_read_as_rgb(self):
        # GTK draws the light accent ink, oklch(0.48 0.12 250.5), as #1c609e.
        node = Serialized("text {\n  color: color(oklch 0.48 0.12 250.547);\n}\n")
        self.assertEqual(harness._node_rgba(node, Raw()), [28.2, 95.9, 158.1, 1.0])

    def test_alpha_and_other_states(self):
        self.assertEqual(harness._color_fn_rgba("color(srgb 0.2 0.4 0.6 / 0.5)"), [51.0, 102.0, 153.0, 0.5])
        self.assertEqual(harness._color_fn_rgba("color(srgb-linear 1 1 1)"), [255.0, 255.0, 255.0, 1.0])
        self.assertIsNone(harness._color_fn_rgba("color(rec2100-pq 0.5 0.5 0.5)"))

    def test_srgb_nodes_keep_the_getter(self):
        class Rgb:
            red, green, blue, alpha = 0.2, 0.4, 0.6, 1.0
        node = Serialized("text {\n  color: rgb(51,102,153);\n}\n")
        self.assertEqual(harness._node_rgba(node, Rgb()), [51.0, 102.0, 153.0, 1.0])


if __name__ == "__main__":
    unittest.main()
