"""Real native scrollable cells remain above a two-row phone action bar."""
import time
import unittest
import subprocess
import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Vte', '3.91')
from gi.repository import GLib, Gtk, Vte
from luma_appkit import ActionCenter, BarAction, ToastHost, install_appkit, install_lumaui


def settle():
    end = time.monotonic() + .4
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


class NativeScrollableRoom(unittest.TestCase):
    def test_two_rows_and_resize_restore_desktop_cells(self):
        # Fontconfig and GTK style providers are process globals. Exercise this
        # native integration with a fresh map instead of inheriting other UI fixtures.
        if __name__ != '__main__':
            result = subprocess.run([sys.executable, __file__], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return
        Gtk.init(); install_appkit(); install_lumaui()
        terminal = Vte.Terminal(hexpand=True, vexpand=True)
        scroll = Gtk.ScrolledWindow(child=terminal, hexpand=True, vexpand=True,
                                   hscrollbar_policy=Gtk.PolicyType.NEVER,
                                   vscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        host = ToastHost(scroll)
        center = ActionCenter().attach(host)
        center.attach_scroller(scroll)
        footer = Gtk.Box(); footer.append(Gtk.Entry(hexpand=True))
        center.show_bar([BarAction('plus', 'New session')], entry=footer)
        window = Gtk.Window(child=host, default_width=402, default_height=874)
        window.add_css_class('luma-app-window'); window.present(); settle()
        for width in (402, 360, 500, 720, 402):
            window.set_default_size(width, 874); settle()
            if width < 560:
                ok, bounds = terminal.compute_bounds(host)
                self.assertTrue(ok)
                # VTE's border box includes decorative CSS padding. Its rendered
                # terminal rows are the viewport that must clear the bar.
                padding = terminal.get_style_context().get_padding()
                cells_bottom = (bounds.get_y() + padding.top
                                + terminal.get_row_count() * terminal.get_char_height())
                self.assertLessEqual(cells_bottom, center._row_top(host) - 19)
                self.assertLessEqual(bounds.get_y() + bounds.get_height(), center._row_top(host))
                room = terminal.get_margin_bottom()
                self.assertGreater(room, 100)
                settle()
                self.assertEqual(terminal.get_margin_bottom(), room, 'safe area must not oscillate')
            else:
                self.assertEqual(terminal.get_margin_bottom(), 0)
        window.close()

if __name__ == '__main__': unittest.main()
