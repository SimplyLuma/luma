"""The sessions list and the phone's keys row (v71).

The first class is GTK-free. The others need GTK 4 and a display, and skip without them
(run them under a headless compositor, as the conform server does).
"""

import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "luma-platform" / "appkit"))

from luma_terminal.model import (  # noqa: E402
    PHONE_KEYS, TerminalModel, fixture_branch, git_branch, session_subtitle,
)

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    import luma_appkit  # noqa: F401  (the kit needs the platform typelibs)

    HAVE_DISPLAY = bool(Gtk.init_check())
except (ImportError, ValueError):
    HAVE_DISPLAY = False


class PhoneKeysAndData(unittest.TestCase):
    def test_keys_row_is_v71s_in_order(self):
        self.assertEqual([key.label or key.icon for key in PHONE_KEYS],
                         ["esc", "tab", "⌃C", "arrow-up", "arrow-down",
                          "|", "~", "/", "-", "*", ">", "&"])

    def test_control_keys_go_to_the_shell_and_symbols_to_the_line(self):
        control = {key.name: key.send for key in PHONE_KEYS if not key.field}
        self.assertEqual(control, {"Escape": "\x1b", "Tab": "\t", "^C": "\x03",
                                   "ArrowUp": "\x1b[A", "ArrowDown": "\x1b[B"})
        self.assertTrue(all(key.field and key.send == key.label for key in PHONE_KEYS[5:]))

    def test_session_subtitle_names_the_folder_and_extra_panes(self):
        model = TerminalModel("/home/nick/Projects/luma", home="/home/nick")
        self.assertEqual(session_subtitle(model.current, "/home/nick"), "~/Projects/luma")
        model.split_active("row")
        self.assertEqual(session_subtitle(model.current, "/home/nick"), "~/Projects/luma · 2 panes")

    def test_branch_is_read_from_head_and_fixture_follows_v71(self):
        self.assertEqual(fixture_branch("/home/nick/Projects/luma"), "main")
        self.assertIsNone(fixture_branch("/home/nick"))
        self.assertIsNone(git_branch("/nonexistent/folder"))


def _labels(widget):
    found = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            found.append(child.get_label())
        found.extend(_labels(child))
        child = child.get_next_sibling()
    if isinstance(widget, Gtk.Label):
        found.append(widget.get_label())
    return found


@unittest.skipUnless(HAVE_DISPLAY, "needs GTK 4 and a display")
class SessionRows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def setUp(self):
        from luma_appkit import MenuSection, RichMenuItem
        from luma_terminal.terminal import session_rows
        self.MenuSection, self.RichMenuItem, self.session_rows = MenuSection, RichMenuItem, session_rows
        self.model = TerminalModel("/home/nick", home="/home/nick")
        self.calls = []

    def rows(self, phone=False):
        return self.session_rows(
            self.model, "/home/nick", phone=phone,
            on_activate=lambda sid: self.calls.append(("open", sid)),
            on_close=lambda sid: self.calls.append(("close", sid)),
            on_rename=lambda sid, name: self.calls.append(("rename", sid, name)),
            on_new=lambda: self.calls.append(("new",)))

    def test_lists_every_open_session_then_new_session(self):
        self.model.new_session("/home/nick/Projects/luma")
        rows = self.rows()
        self.assertEqual(rows[0], "Sessions")
        sessions = [row for row in rows if isinstance(row, self.RichMenuItem)]
        self.assertEqual([row.label for row in sessions], ["Home", "luma"])
        self.assertEqual([row.subtitle for row in sessions], ["~", "~/Projects/luma"])
        self.assertEqual([row.selected for row in sessions], [False, True])
        self.assertIsNone(rows[-2])
        self.assertIsInstance(rows[-1], self.MenuSection)
        self.assertIn("New session", _labels(rows[-1].widget))

    def test_a_phone_panel_has_no_heading_and_new_session_runs_its_callback(self):
        rows = self.rows(phone=True)
        self.assertNotIn("Sessions", rows)
        rows[-1].widget.emit("clicked")
        self.assertEqual(self.calls, [("new",)])

    def test_rows_open_rename_and_close_their_own_session(self):
        first = self.model.current_id
        second = self.model.new_session("/tmp/work").id
        rows = [row for row in self.rows() if isinstance(row, self.RichMenuItem)]
        rows[0].on_activate()
        rows[1].on_rename("Build")
        rows[1].trail[0].on_activate()
        self.assertEqual(self.calls, [("open", first), ("rename", second, "Build"), ("close", second)])
        self.assertTrue(all(row.rename for row in rows))

    def test_the_only_session_cannot_be_closed(self):
        (row,) = [row for row in self.rows() if isinstance(row, self.RichMenuItem)]
        self.assertEqual(row.trail, ())


def _find(widget, name):
    if widget.get_name() == name:
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = _find(child, name)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


@unittest.skipUnless(HAVE_DISPLAY, "needs GTK 4 and a display")
class WindowPanel(unittest.TestCase):
    """The sessions panel and the phone bar as the real window builds them, from the fixture."""

    @classmethod
    def setUpClass(cls):
        Adw.init()
        from luma_appkit import install_appkit, install_lumaui
        install_appkit()
        install_lumaui()
        cls.app = Adw.Application(application_id="org.projectluma.TerminalTest")
        cls.app.register(None)

    def window(self):
        from luma_terminal.terminal import TerminalWindow
        fixture = {"home": "/home/nick", "sessions": [{"name": "Home", "cwd": "/home/nick"}],
                   "history": ["git status"], "blocks": []}
        return TerminalWindow(self.app, fixture)

    def test_panel_lists_the_open_sessions_and_new_session(self):
        window = self.window()
        window.model.new_session("/home/nick/Projects/luma")
        labels = _labels(window._sessions_panel())
        for wanted in ("Home", "luma", "~", "~/Projects/luma", "New session"):
            self.assertIn(wanted, labels)

    def test_phone_bar_is_keys_then_command_line_with_sessions_and_run(self):
        window = self.window()
        window.model.new_session("/home/nick/Projects/luma")
        window._show_phone_prompt()
        bar = window.center
        strip = _find(bar, "tm-keys")
        self.assertIsNotNone(strip)
        keys = []
        node = strip.get_child().get_child().get_first_child()
        while node is not None:
            keys.append(node.get_tooltip_text())
            node = node.get_next_sibling()
        self.assertEqual(keys, [key.name for key in PHONE_KEYS])
        for name in ("tm-sessions", "tm-command", "tm-run"):
            self.assertIsNotNone(_find(bar, name), name)
        self.assertEqual(window._command.prefix, ("~/Projects/luma", "main"))
        self.assertIn("2", _labels(bar), "the sessions count remains visible")

    def test_symbol_keys_type_into_the_line_and_run_sends_it(self):
        window = self.window()
        window._show_phone_prompt()
        symbol = next(key for key in PHONE_KEYS if key.send == "|")
        window._press_key(symbol)
        self.assertEqual(window._command.text, "|")
        window._command.set_text("ls")
        window._run_command("ls")
        self.assertEqual(window.history[-1], "ls")
        self.assertEqual(window._command.text, "")


if __name__ == "__main__":
    unittest.main()
