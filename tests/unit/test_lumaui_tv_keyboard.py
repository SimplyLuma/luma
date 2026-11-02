#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""LumaUI and the system's TV keyboard: the action label, the height, the stage docking."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/luma-platform/appkit"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, GObject, Gtk  # noqa: E402

from luma_appkit import lumaui_tokens, tv_keyboard  # noqa: E402

HAS_DISPLAY = Gtk.init_check() if hasattr(Gtk, "init_check") else Gdk.Display.get_default() is not None
SHELL = REPO_ROOT / "patches/gnome-shell/0309-tv-keyboard.patch"


class FakeProxy:
    def __init__(self, visible=False, height=0, owner=":1.2"):
        self.props = {"Visible": GLib.Variant("b", visible), "Height": GLib.Variant("u", height)}
        self.owner = owner
        self.calls = []

    def get_name_owner(self):
        return self.owner

    def get_cached_property(self, name):
        return self.props.get(name)

    def call(self, method, params, *_rest):
        self.calls.append((method, params.unpack()))


def fake_shell(proxy):
    shell = tv_keyboard._Shell.__new__(tv_keyboard._Shell)
    shell.visible, shell.height, shell._watchers, shell._sent = False, 0.0, [], None
    shell._proxy = proxy
    return shell


class Stage(GObject.Object):
    docked = GObject.Property(type=str, default="")


class State(unittest.TestCase):
    def test_height_follows_the_shell_and_is_zero_while_hidden(self):
        proxy = FakeProxy()
        shell = fake_shell(proxy)
        seen = []
        stop = shell.watch(lambda visible, height: seen.append((visible, height)))
        proxy.props.update(Visible=GLib.Variant("b", True), Height=GLib.Variant("u", 428))
        shell._read()
        shell._read()
        proxy.props.update(Visible=GLib.Variant("b", False))
        shell._read()
        stop()
        proxy.props.update(Visible=GLib.Variant("b", True))
        shell._read()
        self.assertEqual(seen, [(False, 0.0), (True, 428.0), (False, 0.0)])

    def test_no_shell_means_no_keyboard(self):
        shell = fake_shell(FakeProxy(visible=True, height=428, owner=None))
        shell._read()
        self.assertEqual((shell.visible, shell.height), (False, 0.0))

    def test_labels_are_sent_once_per_change(self):
        proxy = FakeProxy()
        shell = fake_shell(proxy)
        for label in ("Search", "Search", "", "Add"):
            shell.send(label)
        self.assertEqual(proxy.calls, [("SetActionLabel", ("Search",)), ("SetActionLabel", ("",)),
                                       ("SetActionLabel", ("Add",))])

    def test_stage_docks_while_the_keyboard_is_up(self):
        proxy = FakeProxy()
        shell = fake_shell(proxy)
        stage = Stage(docked="action-bar")
        with mock.patch.object(tv_keyboard, "_shell", shell):
            stop = tv_keyboard.dock_stage(stage)
            proxy.props.update(Visible=GLib.Variant("b", True), Height=GLib.Variant("u", 428))
            shell._read()
            self.assertEqual(stage.docked, "keyboard")
            proxy.props.update(Visible=GLib.Variant("b", False))
            shell._read()
            self.assertEqual(stage.docked, "action-bar")
            stop()

    def test_tokens_match_the_mockup(self):
        k = lumaui_tokens.TV["keyboard"]
        height = k["padding_top"] + 5 * k["key_height"] + 4 * k["row_gap"] + k["padding_bottom"]
        self.assertEqual(height, 428)
        self.assertEqual(k["above"], 470)
        self.assertEqual(lumaui_tokens.TV["toast"]["above_keyboard"], 586)

    def test_the_shell_publishes_the_same_contract(self):
        text = SHELL.read_text()
        for name in (tv_keyboard.BUS_NAME, tv_keyboard.OBJECT_PATH, tv_keyboard.INTERFACE,
                     '<method name="SetActionLabel">', '<property name="Height" type="u"',
                     '<property name="Visible" type="b"'):
            self.assertIn(name, text)


@unittest.skipUnless(HAS_DISPLAY, "needs a display")
class Widgets(unittest.TestCase):
    def test_the_label_comes_from_the_field_or_an_ancestor(self):
        box = Gtk.Box()
        text = Gtk.Text()
        box.append(text)
        self.assertEqual(tv_keyboard.keyboard_action(text), "")
        tv_keyboard.set_keyboard_action(box, "Search")
        self.assertEqual(tv_keyboard.keyboard_action(text), "Search")
        tv_keyboard.set_keyboard_action(text, "Add")
        self.assertEqual(tv_keyboard.keyboard_action(text), "Add")

    def test_focus_sends_only_in_tv_posture(self):
        proxy = FakeProxy()
        shell = fake_shell(proxy)
        text = Gtk.Text()
        tv_keyboard.set_keyboard_action(text, "Search")
        with mock.patch.object(tv_keyboard, "_shell", shell), \
                mock.patch("luma_appkit.lumaui.tv_posture", return_value=False):
            tv_keyboard._focus(text, True)
        self.assertEqual(proxy.calls, [])
        with mock.patch.object(tv_keyboard, "_shell", shell), \
                mock.patch("luma_appkit.lumaui.tv_posture", return_value=True):
            tv_keyboard._focus(text, True)
            tv_keyboard._focus(text, False)
        self.assertEqual(proxy.calls, [("SetActionLabel", ("Search",)), ("SetActionLabel", ("",))])

    def test_tv_field_takes_an_action(self):
        from luma_appkit.tv_parts import TvField
        field = TvField(placeholder="Add a place", action="Add")
        self.assertEqual(tv_keyboard.keyboard_action(field.text), "Add")


if __name__ == "__main__":
    unittest.main()
