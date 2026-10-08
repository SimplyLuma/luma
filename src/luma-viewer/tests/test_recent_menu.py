# SPDX-License-Identifier: Apache-2.0
"""Real right-click Recents menu: admitted icons and unchanged file actions."""
import ctypes
import ctypes.util
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkX11', '4.0')
from gi.repository import Adw, Gdk, GdkX11, Gio, GLib, Gtk
from luma_appkit import install_appkit, command_popover
from luma_appkit.structure_drawer import MenuDrawer
from luma_viewer.application import ViewerWindow
from luma_viewer.recents import Recents


def descendants(widget):
    child = widget.get_first_child()
    while child:
        yield child
        yield from descendants(child)
        child = child.get_next_sibling()


class RecentMenuTests(unittest.TestCase):
    def settle(self, condition):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            if condition():
                return
            time.sleep(.01)
        self.fail('Actual Recents menu or command did not settle')

    def right_click(self, window, row):
        # Feed a real XTest pointer press through GTK's production GestureClick.
        self.assertIsInstance(window.get_display(), GdkX11.X11Display)
        x11 = ctypes.CDLL(ctypes.util.find_library('X11'))
        xtst = ctypes.CDLL(ctypes.util.find_library('Xtst'))
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XTranslateCoordinates.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                                            ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int),
                                            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong)]
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        display = x11.XOpenDisplay(None)
        self.assertTrue(display, 'Private X display must accept real input')
        try:
            # A narrow sidebar animates into its shared modal. Wait until its
            # actual hit rectangle stops moving before injecting the pointer.
            last = None
            stable = 0
            deadline = time.monotonic()+3
            while time.monotonic() < deadline:
                while GLib.MainContext.default().pending():
                    GLib.MainContext.default().iteration(False)
                ok, bounds = row.compute_bounds(window)
                current = (round(bounds.get_x(), 2), round(bounds.get_y(), 2),
                           round(bounds.get_width(), 2), round(bounds.get_height(), 2)) if ok else None
                stable = stable+1 if current == last and current else 0
                last = current
                if stable >= 4: break
                time.sleep(.05)
            self.assertGreaterEqual(stable, 4, 'Recents hit rectangle must settle')
            ok, bounds = row.compute_bounds(window)
            self.assertTrue(ok)
            x, y, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
            self.assertTrue(x11.XTranslateCoordinates(display, window.get_surface().get_xid(),
                x11.XDefaultRootWindow(display), int(bounds.get_x()+bounds.get_width()/2),
                int(bounds.get_y()+bounds.get_height()/2), ctypes.byref(x), ctypes.byref(y), ctypes.byref(child)))
            self.assertTrue(xtst.XTestFakeMotionEvent(display, -1, x.value, y.value, 0))
            self.assertTrue(xtst.XTestFakeButtonEvent(display, 3, True, 0))
            self.assertTrue(xtst.XTestFakeButtonEvent(display, 3, False, 0))
            x11.XFlush(display)
        finally:
            x11.XCloseDisplay(display)

    def test_actual_recent_context_menu_icons_and_routes(self):
        self.assertIsNotNone(Gdk.Display.get_default(), 'Native display is required')
        app = Adw.Application(application_id='org.projectluma.ViewerRecentMenuTest',
                              flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.assertTrue(app.register(None))
        install_appkit()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / 'Original.txt'
            original = b'Original document must remain unchanged.\n'
            document.write_bytes(original)
            store = Recents(root / 'history', root / 'cache')
            store.record(str(document))
            captured, drawers = [], []
            present_drawer = MenuDrawer.present
            def capture_drawer(*args, **kwargs):
                drawer = present_drawer(*args, **kwargs)
                drawers.append(drawer)
                return drawer
            def capture(registry):
                menu = command_popover(registry)
                captured.append(menu)
                return menu
            with patch('luma_viewer.composition.Recents', return_value=store):
                window = ViewerWindow(app)
            try:
                window.present()
                self.settle(lambda: window.get_mapped())
                theme = Gtk.IconTheme.get_for_display(window.get_display())
                with patch('luma_viewer.application.command_popover', side_effect=capture), patch.object(MenuDrawer, 'present', side_effect=capture_drawer):
                    for width in (1180, 1024, 500, 360):
                        window.set_default_size(width, 740)
                        self.settle(lambda: window.get_surface().get_width() == width)
                        window.commands.invoke('viewer.recents')
                        row = window.file_rows[str(document)]
                        self.settle(lambda: row.get_mapped())
                        before = len(captured)
                        self.right_click(window, row)
                        self.settle(lambda: len(captured) == before+1)
                        menu = captured[-1]
                        shown = drawers[-1] if width < 560 else menu
                        self.settle(lambda: shown.get_mapped())
                        commands = [c for g in menu.registry.visible_groups(menu=True) for c in g.commands]
                        self.assertEqual([c.id for c in commands], ['viewer.recent-open',
                            'viewer.recent-copy-path', 'viewer.recent-remove'])
                        for command in commands:
                            self.assertTrue(command.icon and command.icon.startswith('lumaui-'), command.id)
                            self.assertTrue(theme.has_icon(command.icon), command.icon)
                        actual_rows = [w for w in descendants(shown) if isinstance(w, Gtk.Button) and (w.has_css_class('luma-menu-row') or w.has_css_class('lumaui-menu-row'))]
                        self.assertEqual(len(actual_rows), 3)
                        actual_images = [image for row in actual_rows for image in descendants(row) if isinstance(image, Gtk.Image)]
                        self.assertEqual(len(actual_images), 3)
                        self.assertEqual({w.get_icon_name() for w in actual_images}, {c.icon for c in commands})
                        self.assertEqual(document.read_bytes(), original)
                        print(f'PASS: actual Recents pointer menu and three icons at {width}px')
                        if isinstance(shown, MenuDrawer):
                            shown.close()
                        else:
                            menu.popdown()
                        self.settle(lambda: menu.get_parent() is None)
                        if width < 721:
                            window.sidebar_toggle._drawer.cancel()
                            self.settle(lambda: not window.sidebar_toggle.shown)
                    # Exercise the actual shared command registry without launching external apps.
                    opened = []
                    with patch.object(window, 'open_path', side_effect=opened.append):
                        self.assertTrue(menu.registry.invoke(commands[0].id))
                    self.assertEqual(opened, [str(document)])
                    self.assertTrue(menu.registry.invoke(commands[1].id))
                    clipboard = []
                    window.get_display().get_clipboard().read_text_async(None,
                        lambda source, result: clipboard.append(source.read_text_finish(result)))
                    self.settle(lambda: bool(clipboard))
                    self.assertEqual(clipboard, [str(document)])
                    self.assertTrue(menu.registry.invoke(commands[2].id))
                    self.assertEqual(store.entries, [])
                    self.assertNotIn(str(document), window.file_rows)
                    self.assertEqual(document.read_bytes(), original)
                    self.assertTrue((root / 'history' / 'recents.json.before-lumaui').is_file())
            finally:
                window.destroy()
                app.quit()
