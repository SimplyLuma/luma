# SPDX-License-Identifier: Apache-2.0
"""The sheet in fixture mode, driven page by page: one press, one step."""
import os
import time
import unittest

os.environ.setdefault("LUMA_SETUP_FIXTURE", "1")
os.environ.setdefault("LUMA_SETUP_WINDOWED", "1440x900")
# On Wi-Fi, so Get online is a page too.
os.environ.setdefault("LUMA_SETUP_FIXTURE_STATE", '{"connected": "Home", "networks": [["Home", 3, true]]}')

try:
    import gi
    gi.require_version("Gdk", "4.0")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, GLib, Gtk
    DISPLAY = Gtk.init_check()
    from luma_setup import app, sheet
except (ImportError, ValueError):  # no GTK 4 on this machine
    DISPLAY = False


def spin(until, seconds: float = 5.0) -> bool:
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if until():
            return True
        context.iteration(False)
        time.sleep(0.005)
    return until()


@unittest.skipUnless(DISPLAY, "needs GTK 4 and a display")
class Steps(unittest.TestCase):
    def setUp(self):
        self.window = sheet.SetupWindow(None, app.initial_setup(), sheet.Posture.DESKTOP)
        self.window.set_default_size(1440, 900)
        self.window.present()
        self.addCleanup(self.window.destroy)
        self.finished = 0
        self.window.finish = self._finished
        self.assertTrue(self.settle(), "the first page never settled")

    def settle(self) -> bool:
        """Wait as a person would before the next press: the page drawn and settled."""
        gate = getattr(self.window, "_gate", None)
        if gate is None:
            start = time.monotonic()
            return spin(lambda: time.monotonic() - start > 0.5)
        return spin(gate.open)

    def _finished(self):
        self.finished += 1

    def press(self, keyval=None, release: bool = True) -> None:
        """A key as GTK delivers it: the window's capture handler first, then the focus."""
        keyval = keyval or Gdk.KEY_Return
        window = self.window
        if not window._key(None, keyval, 0, Gdk.ModifierType(0)):
            focus = window.get_focus()
            if isinstance(focus, Gtk.Text):
                focus.emit("activate")
            elif isinstance(focus, Gtk.Button):
                focus.emit("clicked")
        if release and hasattr(window, "_key_released"):
            window._key_released(None, keyval, 0, Gdk.ModifierType(0))

    def fill(self) -> None:
        parts = self.window.page_parts
        if self.window.page == "you":
            parts["name"].text.set_text("Robin Test")
            parts["password"].text.set_text("a long test password")
            parts["again"].text.set_text("a long test password")
            parts["name"].text.grab_focus()   # the page's main field
        elif self.window.page == "wifi":
            self.window.key.grab_focus()   # already on Wi-Fi: Return on a row would open it
        self.assertTrue(self.window.key.get_sensitive(), f"{self.window.page}: Continue stays off")

    def test_every_page_once_in_order(self):
        window = self.window
        visited = [window.page]
        while True:
            self.fill()
            page = window.page
            # Return in the page's main field, held long enough to repeat, then released.
            self.press(release=False)
            self.press(release=False)
            self.press()
            if self.finished:
                break
            self.assertNotEqual(window.page, page, f"Return on {page} went nowhere")
            visited.append(window.page)
            # The rest of a double click lands on the new page's key at once: no step.
            window.key.emit("clicked")
            self.assertEqual(window.page, visited[-1], f"a double click on {page} skipped {visited[-1]}")
            self.assertTrue(self.settle(), f"{window.page} never settled")
        self.assertEqual(visited, window.pages)
        self.assertEqual(self.finished, 1)


if __name__ == "__main__":
    unittest.main()
