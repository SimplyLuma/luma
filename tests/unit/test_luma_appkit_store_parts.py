"""LumaUI's November parts: the permission vocabulary, the changelog format and the frame geometry.

The pure rules run without GTK; the widget checks (checkbox and radio menu
rows that update in place, the two-column inset, the non-destructive
confirmation) run where a display is available.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / "src/luma-platform/appkit"
sys.path.insert(0, str(KIT))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"store_parts_{name}", KIT / "luma_appkit" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


permissions = _load("content_permissions")
changelog = _load("content_changelog")


def words(grants) -> list[str]:
    return [grant.word for grant in grants]


class Vocabulary(unittest.TestCase):
    def test_eleven_words_in_order(self) -> None:
        self.assertEqual([word for _k, word, _i in permissions.PERMISSIONS],
                         ["Camera", "Microphone", "Location", "Files", "Internet", "Notifications", "Screen",
                          "Other apps", "Passwords", "Devices", "System"])
        self.assertEqual([glyph for _k, _w, glyph in permissions.PERMISSIONS],
                         ["camera", "mic", "map-pin", "folder", "globe", "bell", "monitor", "layers", "key-round",
                          "usb", "terminal"])

    def test_folders_become_files_with_the_folder_in_the_reason(self) -> None:
        grants = permissions.from_reach([
            ("music-2", "Your Music folder", "Reads it to build your library"),
            ("image", "Your Pictures folder", "Shows your photos"),
            ("download", "Downloads", "Saves what you export"),
        ])
        self.assertEqual(words(grants), ["Files"])
        self.assertEqual(grants[0].reason, "Music folder: reads it to build your library. "
                                           "Pictures folder: shows your photos. Downloads: saves what you export")

    def test_microphone_and_camera_become_two(self) -> None:
        grants = permissions.from_reach([("mic", "Microphone and camera", "Only during a call you start or answer")])
        self.assertEqual(words(grants), ["Camera", "Microphone"])
        self.assertEqual(grants[1].reason, "Only during a call you start or answer")

    def test_graphics_card_and_drawing_tablets_become_devices(self) -> None:
        grants = permissions.from_reach([("cpu", "Graphics card", "Renders on your GPU"),
                                         ("monitor", "Your drawing tablets", "Pressure for brushes")])
        self.assertEqual(words(grants), ["Devices"])
        self.assertEqual(grants[0].reason, "Graphics card: renders on your GPU. Drawing tablets: pressure for brushes")

    def test_terminal_and_system_install_become_system(self) -> None:
        grants = permissions.from_reach([("terminal", "A terminal", "Runs the tools you ask for"),
                                         ("package", "Installed into the system", "Adds a printer driver")])
        self.assertEqual(words(grants), ["System"])

    def test_a_music_server_is_internet(self) -> None:
        self.assertEqual(words(permissions.from_reach([("headphones", "Your music server", "Plays from it")])),
                         ["Internet"])

    def test_not_permissions_are_never_listed(self) -> None:
        self.assertEqual(permissions.from_reach([("user-x", "No account", "Nothing to sign in to"),
                                                 ("check", "Nothing else", ""),
                                                 ("volume-2", "Sound", "Plays music")]), [])

    def test_order_is_fixed_whatever_the_publisher_sends(self) -> None:
        grants = permissions.from_reach([("terminal", "A terminal", "x"), ("bell", "Notifications", "y"),
                                         ("camera", "Camera", "z")])
        self.assertEqual(words(grants), ["Camera", "Notifications", "System"])

    def test_technical_keys_use_the_same_words(self) -> None:
        class Item:
            def __init__(self, key: str, detail: str = "", change: str = "") -> None:
                self.key, self.detail, self.change = key, detail, change
        grants = permissions.from_keys([Item("files.home", "Your home folder"), Item("network", change="added"),
                                        Item("devices.camera"), Item("audio"), Item("sandbox.escape")])
        self.assertEqual(words(grants), ["Camera", "Files", "Internet", "System"])
        self.assertEqual(grants[2].change, "added")
        self.assertEqual(grants[1].reason, "Your home folder")

    def test_a_grant_must_be_one_of_the_eleven(self) -> None:
        with self.assertRaises(ValueError):
            permissions.Grant("sound")


class ChangelogFormat(unittest.TestCase):
    def test_plain_text_is_one_list(self) -> None:
        groups = changelog.group_changelog(["Faster search", "Fixes a crash"])
        self.assertEqual([(heading, [line.text for line in lines]) for heading, lines in groups],
                         [("", ["Faster search", "Fixes a crash"])])

    def test_typed_lines_group_in_the_fixed_order(self) -> None:
        groups = changelog.group_changelog([
            {"type": "Fix", "text": "The dock no longer flickers"},
            {"type": "Security", "text": "Image decoders"},
            {"type": "Feature", "title": "Adjustable scroll speed", "text": "Set how fast mice scroll."},
            {"type": "Improvement", "text": "Windows open where you left them"},
        ])
        self.assertEqual([heading for heading, _lines in groups], ["New", "Better", "Fixed", "Security"])
        self.assertEqual(groups[0][1][0].title, "Adjustable scroll speed")

    def test_luma_system_rows_and_loose_lines(self) -> None:
        groups = changelog.group_changelog([["Fix", "bug", "Wake", "Wakes from sleep"], "Thanks to testers"])
        self.assertEqual([heading for heading, _lines in groups], ["", "Fixed"])
        self.assertEqual(groups[1][1][0].title, "Wake")

    def test_empty_log_draws_nothing(self) -> None:
        self.assertEqual(changelog.group_changelog(["", "  "]), [])


class FrameGeometry(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            from luma_appkit import media_framed
        except (ImportError, ValueError) as error:  # no GTK here
            raise unittest.SkipTest(f"GTK unavailable: {error}")
        cls.framed = media_framed

    def test_window_is_64_percent_from_9_percent_down(self) -> None:
        x, y, w, h = self.framed.window_rect(1600, 900, 1180 / 698)
        self.assertAlmostEqual(w, 1024)
        self.assertAlmostEqual(x, 288)
        self.assertAlmostEqual(y, 81)
        self.assertAlmostEqual(h, 1024 * 698 / 1180)

    def test_phone_is_82_percent_of_the_height_and_upright(self) -> None:
        x, y, w, h, s = self.framed.phone_rect(1600, 900)
        self.assertAlmostEqual(h, 738)
        self.assertAlmostEqual(w / h, 414 / 868)
        self.assertAlmostEqual(x + w / 2, 800)

    def test_same_picture_at_every_size(self) -> None:
        big = self.framed.window_rect(1600, 900, 1.6)
        small = self.framed.window_rect(96, 54, 1.6)
        self.assertAlmostEqual(big[2] / 1600, small[2] / 96)
        self.assertAlmostEqual(big[1] / 900, small[1] / 54)


@unittest.skipUnless(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY") or sys.platform == "darwin",
                     "needs a display")
class Widgets(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        if not Gtk.init_check():
            raise unittest.SkipTest("GTK could not open a display")
        cls.Gtk = Gtk

    def test_checkbox_row_ticks_in_place_and_stays_open(self) -> None:
        from luma_appkit.rows_menu import CheckMenuItem, RadioMenuItem
        closed, ticks = [], []
        row = CheckMenuItem("Phone", checked=False, on_toggle=ticks.append).menu_widget(lambda: closed.append(1))
        row.emit("clicked")
        self.assertEqual(ticks, [True])
        self.assertTrue(row.box.checked)
        self.assertEqual(closed, [])
        self.assertEqual(row.get_accessible_role(), self.Gtk.AccessibleRole.MENU_ITEM_CHECKBOX)
        del RadioMenuItem

    def test_radio_row_moves_the_check_in_place(self) -> None:
        from luma_appkit.rows_menu import RadioMenuItem
        box = self.Gtk.Box()
        rows = [RadioMenuItem(name, group="cat", checked=name == "All apps", stay_open=True).menu_widget(lambda: None)
                for name in ("All apps", "Create", "Work")]
        for row in rows:
            box.append(row)
        rows[1].emit("clicked")
        self.assertEqual([row.item.checked for row in rows], [False, True, False])
        self.assertEqual([row.check.get_visible() for row in rows], [False, True, False])
        self.assertEqual(rows[0].get_accessible_role(), self.Gtk.AccessibleRole.MENU_ITEM_RADIO)

    def test_two_column_inset_is_row_major(self) -> None:
        from luma_appkit.content_inset import TwoColumnInset
        inset = TwoColumnInset()
        labels = [self.Gtk.Label(label=str(n)) for n in range(1, 6)]
        for label in labels:
            inset.append(label)
        self.assertEqual([[w.get_label() for w in row] for row in inset.halves._rows()],
                         [["1", "2"], ["3", "4"], ["5"]])
        inset.set_columns(1)
        self.assertEqual(len(inset.halves._rows()), 5)

    def test_confirm_key_form(self) -> None:
        from luma_appkit.action_dialog import Confirm
        dialog = Confirm(title="Join Beta?", body="x", action="Join Beta", destructive=False)
        self.assertTrue(dialog.has_css_class("key"))
        self.assertEqual(dialog.get_accessible_role(), self.Gtk.AccessibleRole.ALERT_DIALOG)
        red = Confirm(title="Delete it?", body="x", action="Delete")
        self.assertFalse(red.has_css_class("key"))

    def test_button_never_a_pill_and_names_its_menu(self) -> None:
        from luma_appkit.content_controls import Button
        button = Button("All apps", icon="layout-grid", menu=True)
        self.assertTrue(button.has_css_class("bar"))
        with self.assertRaises(ValueError):
            Button("Open", variant="pill")
        button.set_label_text("Create")
        self.assertEqual(button.label_widget.get_label(), "Create")


if __name__ == "__main__":
    unittest.main()
