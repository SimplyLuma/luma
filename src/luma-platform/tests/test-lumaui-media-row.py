#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""MediaRow from Python: the builder's keywords, the person's moves only, a
row without a player, and the trailing picker."""
import time

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

Gtk.init()

from luma_appkit import MEDIA_ROW_DENSITIES, MediaRow, MediaRowChip  # noqa: E402
from luma_appkit.lumaui_tokens import MEDIA_ROW  # noqa: E402


def settle(seconds=0.3):
    end = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def key(widget, keyval):
    for controller in widget.observe_controllers():
        if isinstance(controller, Gtk.EventControllerKey):
            return controller.emit("key-pressed", keyval, 0, Gdk.ModifierType(0))
    return False


def press(widget):
    for controller in widget.observe_controllers():
        if isinstance(controller, Gtk.GestureClick):
            controller.emit("pressed", 1, 1.0, 1.0)


def builder():
    heard = []
    icon = Gio.ThemedIcon.new("lumaui-volume-2-symbolic")
    row = MediaRow("God Only Knows", "Tide · The Beach Boys", app="Tide", icon=icon,
                   transport=("previous", "play", "next"), playing=True, sounding=True, volume=80, meter=28,
                   on_play=lambda: heard.append("play"), on_previous=lambda: heard.append("previous"),
                   on_next=lambda: heard.append("next"), on_raise=lambda: heard.append("raise"),
                   on_volume=lambda v: heard.append(("volume", v)), on_mute=lambda m: heard.append(("mute", m)))
    assert row.get_title() == "God Only Knows" and row.get_line() == "Tide · The Beach Boys"
    assert row.get_playing() and row.get_sounding() and row.get_volume() == 80 and not row.get_muted()
    assert row.get_meter().get_segments() == 28 == MEDIA_ROW["regular"]["meter_segments"]
    assert heard == [], "the builder's state is not the person's"
    row.set_volume(40)
    row.set_muted(True)
    row.set_playing(False)
    assert heard == [], "setters are silent"
    chip = MediaRowChip("volume-2", "Speakers")
    row.set_trailing(chip)
    window = Gtk.Window(default_width=720, default_height=300, child=row)
    window.present()
    settle()
    assert key(row.get_slider(), Gdk.KEY_Right) and row.get_volume() == 45
    assert key(row, Gdk.KEY_space) and key(row, Gdk.KEY_M)
    press(row.get_art_widget())
    press(row.get_art_widget())
    press(row.get_badge_widget())
    row.get_play_button().emit("clicked")
    assert heard == [("volume", 45.0), "play", ("mute", False), "raise", ("mute", False), ("mute", False), "play"], heard
    assert row.get_art_widget().get_width() == MEDIA_ROW["regular"]["art"]
    assert chip.get_height() == MEDIA_ROW["chip"]["height"], chip.get_height()
    assert not chip.has_css_class("raised")
    chip.set("headphones", "WH-1000XM5", True)
    assert chip.has_css_class("raised")
    window.destroy()


def densities_and_errors():
    assert MEDIA_ROW_DENSITIES == ("regular", "compact", "touch")
    compact = MediaRow("Luma launch film", "Viola", app="Viola", density="compact", transport=("pause",))
    assert compact.has_css_class("compact") and compact.get_slider().get_compact()
    touch = MediaRow("Luma launch film", density="touch")
    assert touch.has_css_class("touch") and not touch.get_play_button().get_visible()
    window = Gtk.Window(default_width=340, default_height=300, child=Gtk.Box(orientation=Gtk.Orientation.VERTICAL))
    window.get_child().append(compact)
    window.get_child().append(touch)
    window.present()
    settle()
    assert compact.get_play_button().get_width() == MEDIA_ROW["compact"]["play"]
    assert touch.get_height() >= MEDIA_ROW["touch"]["row"], touch.get_height()
    window.destroy()
    for bad in (dict(density="huge"), dict(transport=("rewind",)), dict(meter=-1)):
        try:
            MediaRow("x", **bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} was accepted")


builder()
densities_and_errors()
print("ok")
