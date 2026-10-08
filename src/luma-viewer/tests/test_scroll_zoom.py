# SPDX-License-Identifier: Apache-2.0
"""Ctrl+scroll follows each device's current natural-scroll preference."""
import unittest

from types import SimpleNamespace
from unittest.mock import Mock, patch
import gi
gi.require_version('Gdk','4.0')
from gi.repository import Gdk
from luma_viewer.application import ViewerWindow, scroll_zoom_multiplier, scroll_zoom_schema


class ScrollZoomTests(unittest.TestCase):
    def test_natural_mouse_and_touchpad_scroll(self):
        for device in ('mouse', 'touchpad'):
            with self.subTest(device=device):
                self.assertGreater(scroll_zoom_multiplier(+1, True), 1)
                self.assertLess(scroll_zoom_multiplier(-1, True), 1)

    def test_conventional_scroll_and_zero_delta(self):
        self.assertGreater(scroll_zoom_multiplier(-1, False), 1)
        self.assertLess(scroll_zoom_multiplier(+1, False), 1)
        self.assertEqual(scroll_zoom_multiplier(0, True), 1)

    def test_event_device_selects_its_own_setting(self):
        mouse=SimpleNamespace(get_source=lambda:Gdk.InputSource.MOUSE)
        touchpad=SimpleNamespace(get_source=lambda:Gdk.InputSource.TOUCHPAD)
        self.assertTrue(scroll_zoom_schema(mouse).endswith('.mouse'))
        self.assertTrue(scroll_zoom_schema(touchpad).endswith('.touchpad'))

    def test_native_controller_routes_mouse_and_touchpad_settings(self):
        requested=[]
        def settings(schema):
            requested.append(schema)
            return SimpleNamespace(get_boolean=lambda _key: schema.endswith('.touchpad'))
        zoom=Mock()
        window=SimpleNamespace(pixbuf=object(),compare=None,zoom=1.,_set_zoom=zoom)
        with patch('luma_viewer.application.Gio.Settings.new',side_effect=settings):
            for source,dy in ((Gdk.InputSource.MOUSE,-1),(Gdk.InputSource.TOUCHPAD,+1)):
                controller=SimpleNamespace(
                    get_current_event_state=lambda:Gdk.ModifierType.CONTROL_MASK,
                    get_current_event_device=lambda:SimpleNamespace(get_source=lambda:source))
                self.assertTrue(ViewerWindow._wheel_zoom(window,controller,dy))
        self.assertEqual([name.rsplit('.',1)[-1] for name in requested],['mouse','touchpad'])
        self.assertEqual(zoom.call_count,2)
        for call in zoom.call_args_list:self.assertGreater(call.args[0],1)
