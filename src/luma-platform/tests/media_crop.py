# SPDX-License-Identifier: Apache-2.0
"""Real pointer crop movement/resizing on generated pixels at four native widths."""
import ctypes
import ctypes.util
import math
import os
from pathlib import Path
import time
import unittest
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('GdkX11', '4.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gdk, GdkPixbuf, GLib, Graphene, Gtk
from luma_appkit import ImageViewport, install_lumaui
from luma_appkit.media_viewport import normalise_adjustments


def settle(seconds=.15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        time.sleep(.002)


def point(viewport, u, v):
    x, y, w, h = viewport.get_content_bounds()
    dx, dy = (u-.5)*w, (v-.5)*h
    if viewport._adjustments['flip']: dx = -dx
    angle = math.radians(viewport._adjustments['rot'])
    return x+w/2+dx*math.cos(angle)-dy*math.sin(angle), y+h/2+dx*math.sin(angle)+dy*math.cos(angle)


class CropTests(unittest.TestCase):
    def drag(self, window, start, end):
        x11, xtst = (ctypes.CDLL(ctypes.util.find_library(lib)) for lib in ('X11', 'Xtst'))
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XTranslateCoordinates.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong)]
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        display = x11.XOpenDisplay(None)
        self.assertTrue(display)
        try:
            x, y, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
            self.assertTrue(x11.XTranslateCoordinates(display, window.get_surface().get_xid(), x11.XDefaultRootWindow(display),
                0, 0, ctypes.byref(x), ctypes.byref(y), ctypes.byref(child)))
            def move(p):
                self.assertTrue(xtst.XTestFakeMotionEvent(display, -1, round(x.value+p[0]), round(y.value+p[1]), 0))
                x11.XFlush(display); settle(.04)
            move(start)
            xtst.XTestFakeButtonEvent(display, 1, True, 0); x11.XFlush(display); settle(.06)
            for step in range(1, 7):
                move(tuple(a+(b-a)*step/6 for a, b in zip(start, end)))
            xtst.XTestFakeButtonEvent(display, 1, False, 0); x11.XFlush(display); settle()
        finally:
            x11.XCloseDisplay(display)

    def test_native_move_resize_rotation_flip_and_read_only_preview(self):
        self.assertTrue(Gtk.init_check(), 'native display required')
        install_lumaui()
        pixels = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 900, 600)
        pixels.fill(0x359463ff)
        for width in (360, 500, 1024, 1440):
            for rotation, flip in ((0, False), (90, False), (270, True)):
                with self.subTest(width=width, rotation=rotation, flip=flip):
                    changes = []
                    view = ImageViewport()
                    view.set_paintable(Gdk.Texture.new_for_pixbuf(pixels))
                    view.set_fit_insets(40, 24, 40, 24)
                    view.set_adjustments({'rot': rotation, 'flip': flip, 'crop_rect': [.2, .2, .5, .5]})
                    view.set_crop_editing(True, on_change=changes.append)
                    window = Gtk.Window(decorated=False, child=view, default_width=width, default_height=740)
                    window.present(); settle(.3)
                    self.assertEqual(window.get_width(), width)
                    try:
                        self.drag(window, point(view, .45, .45), point(view, .55, .5))
                        self.assertTrue(changes, 'real pointer did not move the crop')
                        x, y, w, h = changes[-1]
                        self.assertAlmostEqual(x, .3, delta=.015)
                        self.assertAlmostEqual(y, .25, delta=.015)
                        self.assertAlmostEqual(w, .5, delta=.015)
                        self.drag(window, point(view, x+w, y+h), point(view, x+w-.1, y+h-.08))
                        result = changes[-1]
                        self.assertLess(result[2], w-.06)
                        self.assertLess(result[3], h-.04)
                        self.assertEqual(normalise_adjustments({'crop_rect': result})['crop_rect'], result)
                        self.assertTrue(view.get_focusable())
                        before = view._crop_region()
                        self.assertTrue(view._crop_key(None, Gdk.KEY_Right, 0, Gdk.ModifierType.SHIFT_MASK))
                        self.assertNotEqual(view._crop_region(), before)
                        view.set_direction(Gtk.TextDirection.RTL)
                        self.assertFalse(view._crop_key(None, Gdk.KEY_Tab, 0, 0), 'crop must not trap Tab focus')
                        output = os.environ.get('LUMA_CROP_OUTPUT')
                        if output and rotation == 0:
                            Path(output).mkdir(parents=True, exist_ok=True)
                            snapshot = Gtk.Snapshot()
                            Gtk.WidgetPaintable.new(window).snapshot(snapshot, window.get_width(), window.get_height())
                            window.get_renderer().render_texture(snapshot.to_node(), Graphene.Rect().init(0, 0, width, 740)).save_to_png(str(Path(output)/f'crop-{width}.png'))
                        view.set_crop_editing(False)
                        after = view._crop_region()
                        self.assertFalse(view._crop_key(None, Gdk.KEY_Left, 0, 0))
                        self.drag(window, point(view, .4, .4), point(view, .6, .6))
                        self.assertEqual(view._crop_region(), after, 'viewing must not change the crop')
                    finally:
                        window.destroy(); settle()


if __name__ == '__main__':
    unittest.main(verbosity=2)
