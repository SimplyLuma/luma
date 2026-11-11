"""Terminal on a TV: the shell's size and colours, the keys a remote reaches, and the presentation itself.

GTK-free, except the last class, which needs GTK 4, VTE and a display and skips without them.
"""

import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "luma-platform" / "appkit"))

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("Vte", "3.91")
    from gi.repository import Gdk, GLib, Gtk, Vte  # noqa: F401
    HAS_GTK = Gtk.init_check() if hasattr(Gtk, "init_check") else True
except (ImportError, ValueError):
    HAS_GTK = False

from luma_appkit import lumaui_tokens  # noqa: E402

TERMINAL = lumaui_tokens.TV["terminal"]


class Scale(unittest.TestCase):
    def test_the_text_is_tv_reading_size(self):
        from luma_terminal.tv import DESKTOP_MONO, tv_font_scale
        self.assertAlmostEqual(tv_font_scale(1080), TERMINAL["text_size"] / DESKTOP_MONO)
        self.assertAlmostEqual(tv_font_scale(2160), 2 * TERMINAL["text_size"] / DESKTOP_MONO)
        self.assertEqual(tv_font_scale(200), 1.0)          # never smaller than the desktop's

    def test_the_remote_reaches_the_phones_keys_not_its_symbols(self):
        from luma_terminal.model import PHONE_KEYS
        from luma_terminal.tv import TV_KEYS
        self.assertEqual([k.label or k.name for k in TV_KEYS], ["esc", "tab", "⌃C", "ArrowUp", "ArrowDown"])
        self.assertTrue(all(k in PHONE_KEYS for k in TV_KEYS))

    def test_the_palette_is_the_tv_terminals(self):
        self.assertEqual(len(TERMINAL["ansi"]), 16)
        self.assertEqual(len(TERMINAL["shade"]), 2)
        self.assertLess(TERMINAL["text_top"], 1080 - TERMINAL["text_bottom"])


@unittest.skipUnless(HAS_GTK, "needs GTK 4, VTE and a display")
class Presentation(unittest.TestCase):
    """The same window on a TV: a stage, the shell as a console, the action bar; back again on leaving."""

    def window(self):
        from gi.repository import GObject
        from luma_terminal.model import TerminalModel

        class Window(Gtk.Window):
            tv_posture = GObject.Property(type=bool, default=False)

            def __init__(self):
                super().__init__(default_width=960, default_height=540)
                self.model = TerminalModel("/home/nick", home="/home/nick")
                self.display_home = "/home/nick"
                self.shell = Gtk.Box()
                self.host = Gtk.Box()
                self.host.append(self.shell)
                self.center = Gtk.Box()
                self.fed, self.changes = [], 0
                self.set_body(self.host)

            def set_body(self, body):
                self.set_child(body)

            def _active_terminal(self):
                return self.shell

            def _session_icon(self):
                return "house"

            def _feed(self, text):
                self.fed.append(text)

            def new_session(self):
                self.model.new_session("/tmp")

            def activate_session(self, sid):
                self.model.activate_session(sid) if hasattr(self.model, "activate_session") else None

            def tv_presentation_changed(self):
                self.changes += 1

        return Window()

    def test_entering_and_leaving_tv(self):
        from luma_appkit.tv_parts import TvActionBar
        from luma_appkit.tv_stage import TvStage
        from luma_terminal.tv import TerminalOnTv
        window = self.window()
        tv = TerminalOnTv(window)
        self.assertFalse(tv.active)
        window.tv_posture = True
        self.assertTrue(tv.active)
        self.assertIsInstance(window.get_child(), TvStage)
        self.assertIs(window.host.get_parent(), window.get_child())
        self.assertFalse(window.center.get_visible())
        self.assertIsInstance(tv.bar, TvActionBar)
        labels = [i.label.get_label() for i in tv.bar.items()]
        self.assertEqual(labels[:2], ["Home", "New session"])
        self.assertEqual(len(tv.bar.items()), 2 + 5)                  # and esc tab ⌃C ↑ ↓
        self.assertIs(tv.focus.focused, window.shell)                  # the shell has the remote
        self.assertFalse(tv.focus.handle(Gdk.KEY_Up))                  # history, not the bar
        self.assertTrue(tv.focus.handle(Gdk.KEY_Escape))               # Back: down to the bar
        self.assertIs(tv.focus.focused, tv.bar.items()[0])
        tv.focus.focus(tv.bar.items()[2])                              # esc
        tv.focus.handle(Gdk.KEY_Return)
        self.assertEqual(window.fed, ["\x1b"])
        self.assertFalse(tv.focus.handle(Gdk.KEY_Escape))              # Back from the bar: the TV home
        window.tv_posture = False
        self.assertFalse(tv.active)
        self.assertIs(window.get_child(), window.host)
        self.assertTrue(window.center.get_visible())
        self.assertEqual(window.changes, 2)


if __name__ == "__main__":
    unittest.main()
