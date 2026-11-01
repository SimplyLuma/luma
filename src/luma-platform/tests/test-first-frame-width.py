"""Responsive shapes must be settled before the first content snapshot."""
import time
import unittest

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk
from luma_appkit.structure_adapt import WidthWatch, tier_for
from luma_appkit.structure_layers import LayerHost
from luma_appkit.structure_listfirst import ListFirst


def settle():
    until = time.monotonic() + 0.25
    while time.monotonic() < until:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.002)


class Probe(Gtk.Box):
    def __init__(self, read):
        super().__init__(hexpand=True, vexpand=True)
        self.read, self.frames = read, []

    def do_snapshot(self, snapshot):
        self.frames.append(self.read())
        Gtk.Box.do_snapshot(self, snapshot)


class FirstFrameWidth(unittest.TestCase):
    def make_window(self, width, *, embedded=None):
        window = Gtk.Window(default_width=width, default_height=600)
        root = LayerHost(name="window")
        window.layer_host = root
        window.set_child(root)
        host = root
        if embedded is not None:
            host = LayerHost(name="window")
            host.set_size_request(embedded, -1)
            host.set_halign(Gtk.Align.START)
            root.set_child(host)
        hidden = Gtk.Box(visible=False)
        listing = Gtk.Box()
        listing.append(hidden)
        stack = ListFirst(listing, Gtk.Box(), "Test")
        probe = Probe(lambda: (watch.tier, hidden_watch.tier, stack.phone,
                               host.get_width(), window_watch.tier))
        probe.append(stack)
        host.set_child(probe)
        watch, hidden_watch = WidthWatch(probe), WidthWatch(hidden)
        window_watch = WidthWatch(window)
        window.present()
        settle()
        return window, probe, watch, hidden_watch

    def assert_frames(self, frames, root_tier=None):
        self.assertTrue(frames)
        for actual, hidden, phone, width, root in frames:
            expected = tier_for(width)
            self.assertEqual((actual, hidden, phone), (expected, expected, expected == "phone"))
            if root_tier is not None:
                self.assertEqual(root, root_tier)

    def test_first_snapshot_and_resize(self):
        for width in (390, 540, 800, 1100):
            with self.subTest(width=width):
                window, probe, watch, hidden = self.make_window(width)
                try:
                    self.assert_frames(probe.frames, tier_for(width))
                    probe.frames.clear()
                    window.set_default_size(1100 if width < 900 else 390, 600)
                    settle()
                    self.assert_frames(probe.frames)
                    self.assertEqual(watch._frame_handler, 0)
                    self.assertEqual(hidden._frame_handler, 0)
                    count = len(probe.frames)
                    settle()
                    self.assertEqual(len(probe.frames), count, "idle layout must not request continuous frames")
                finally:
                    window.destroy()
                    settle()

    def test_embedded_phone_uses_its_own_width(self):
        window, probe, *_ = self.make_window(1100, embedded=390)
        try:
            self.assert_frames(probe.frames, "regular")
            self.assertEqual(probe.frames[0][:3], ("phone", "phone", True))
        finally:
            window.destroy()
            settle()


if __name__ == "__main__":
    unittest.main()
