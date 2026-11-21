#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""MixerStrip and MixerRow from Python: the builders, the person's moves only,
and a row of strips in a fitted DockTile that clips nothing, narrow or on a phone."""
import time

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

Gtk.init()

from luma_appkit import Dock, DockTile, MixerRow, MixerStrip  # noqa: E402
from luma_appkit.lumaui_tokens import DOCK, MIXER_STRIP  # noqa: E402


def settle(seconds=0.4):
    end = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def key(widget, keyval):
    """A key on @widget's own key controller, as GTK delivers it there."""
    for controller in widget.observe_controllers():
        if isinstance(controller, Gtk.EventControllerKey):
            return controller.emit("key-pressed", keyval, 0, Gdk.ModifierType(0))
    return False


def builders():
    changes, mutes, monitors = [], [], []
    strip = MixerStrip("Mic", "USB microphone", db=-6, min_db=-50, max_db=10, muted=True,
                       on_change=changes.append, on_mute=mutes.append, on_monitor=monitors.append)
    assert strip.get_label() == "Mic" and strip.get_subtitle() == "USB microphone"
    assert strip.get_db() == -6 and strip.get_muted() and not strip.get_monitoring()
    assert strip.get_range() == (-50, 10), strip.get_range()
    assert changes == [] and mutes == [], "the builder's state is not the person's"
    strip.set_db(-3)
    strip.set_muted(False)
    strip.set_monitoring(True)
    assert changes == [] and mutes == [] and monitors == [], "setters are silent"
    fader = strip.get_fader()
    assert key(fader, Gdk.KEY_Up) and strip.get_db() == -2
    assert changes == [-2], changes
    assert not key(fader, Gdk.KEY_m) and key(strip, Gdk.KEY_m), "M is the strip's"
    assert strip.get_muted() and mutes == [True], mutes
    strip.get_monitor_button().emit("clicked")
    assert monitors == [False] and not strip.get_monitoring()
    strip.set_levels(0.5, 0.5)
    assert abs(strip.get_meter().get_level_db(0) + 6.0206) < 1e-3
    strip.set_levels_db(-20, -12)
    assert strip.get_meter().get_peak_db(1) >= -12
    try:
        MixerStrip("Bad", min_db=6, max_db=-6)
    except ValueError:
        pass
    else:
        raise AssertionError("a strip's range runs upward")
    row = MixerRow([strip, MixerStrip("Music")], label="Audio mixer")
    assert row.get_n_strips() == 2 and row.get_strip(0) is strip
    row.remove(strip)
    assert row.get_n_strips() == 1
    row.append(strip)
    assert row.get_strip(1) is strip


def strips(count):
    names = ["Mic", "Desktop audio", "Music", "Game capture with a long name", "Browser", "Alerts", "Chat", "Aux"]
    return [MixerStrip(names[i % len(names)], "Device") for i in range(count)]


def whole(strip, tile):
    """The strip is laid out at its own size: as wide as the token, at least as tall as it asks."""
    minimum = strip.measure(Gtk.Orientation.VERTICAL, -1)[0]
    ok, box = strip.compute_bounds(strip)
    assert ok and box.size.width == MIXER_STRIP["width"], box.size.width
    assert box.size.height >= minimum, (box.size.height, minimum)


def fitted_tile():
    """In a fitted DockTile the row is the strips' height, never past the tile's edge, and scrolls sideways."""
    row = MixerRow(strips(8))
    preview = DockTile("preview", "Preview", "monitor", Gtk.Box(), main=True, bare=True)
    audio = DockTile("audio", "Audio", "volume-2", row, fit=True)
    dock = Dock([preview, audio], layout=Dock.split("v", 0.5, "preview", "audio"))
    window = Gtk.Window(default_width=520, default_height=900)
    window.set_child(dock)
    window.present()
    settle()
    # The tile fits the strips (within the dock's seam; past its cap the tile scrolls): the row has
    # all the height it asks for and is never squeezed.
    natural = audio.measure(Gtk.Orientation.VERTICAL, audio.get_width())[1]
    expected = min(natural, int(dock.fit_max * dock.get_height()))
    assert abs(audio.get_height() - expected) <= DOCK["gap"], (audio.get_height(), natural, dock.get_height())
    _, box = row.compute_bounds(row.get_parent())  # its border box: the row's padding included, as measured
    assert box.get_height() == row.measure(Gtk.Orientation.VERTICAL, int(box.get_width()))[1], "the row is never squeezed"
    ok, bounds = row.compute_bounds(audio)
    assert ok and bounds.origin.x >= 0 and bounds.origin.x + bounds.size.width <= audio.get_width() + 0.5, \
        (bounds.origin.x, bounds.size.width, audio.get_width())
    for i in range(row.get_n_strips()):
        whole(row.get_strip(i), audio)
    scroller = row.get_first_child()
    adjustment = scroller.get_hadjustment()
    assert adjustment.get_upper() > adjustment.get_page_size(), "eight strips scroll in 520 px"
    # A phone: the fitted tile stays inline, every strip still whole.
    window.set_default_size(400, 900)
    settle()
    assert dock.phone and audio.get_mapped(), "a fitted tile stays in view on a phone"
    assert row.get_width() >= MIXER_STRIP["width"]
    for i in range(row.get_n_strips()):
        whole(row.get_strip(i), audio)
    window.destroy()


def generic_channel():
    """An app's channel: its icon heads the strip; an output without monitoring has Mute alone, a fixed one none."""
    from gi.repository import Gio

    from luma_appkit import MixerStrip

    plain = MixerStrip("Desktop audio")
    assert plain.get_gicon() is None and plain.get_mute_visible() and plain.get_monitor_visible()
    app = MixerStrip("Music", "Speakers", icon=Gio.ThemedIcon.new("org.example.Music"), monitor=False)
    assert app.get_gicon().get_names()[0] == "org.example.Music"
    assert app.get_mute_visible() and not app.get_monitor_visible()
    glyph = MixerStrip("Mic", icon="mic", mute=False, monitor=False)
    assert glyph.get_gicon().get_names()[0] == "lumaui-mic-symbolic"
    assert not glyph.get_mute_button().get_parent().get_visible(), "no keys, no key row"
    window = Gtk.Window(child=glyph)
    window.present()
    settle()
    glyph.get_fader().grab_focus()
    key(glyph, Gdk.KEY_m)
    assert not glyph.get_muted(), "M does nothing without a Mute key"
    glyph.set_icon_name(None)
    assert glyph.get_gicon() is None
    window.destroy()


def long_names_rtl():
    """A long name ellipsizes inside the strip; RTL lays the row out from the right."""
    Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL)
    row = MixerRow(strips(3))
    window = Gtk.Window(default_width=600, default_height=420)
    window.set_child(row)
    window.present()
    settle()
    first, second = row.get_strip(0), row.get_strip(1)
    ok_a, a = first.compute_bounds(row)
    ok_b, b = second.compute_bounds(row)
    assert ok_a and ok_b and a.origin.x > b.origin.x, (a.origin.x, b.origin.x)
    long_strip = row.get_strip(2)
    long_strip.set_label("A channel name far longer than any strip is wide")
    settle(0.1)
    assert long_strip.compute_bounds(long_strip)[1].size.width == MIXER_STRIP["width"]
    window.destroy()
    Gtk.Widget.set_default_direction(Gtk.TextDirection.LTR)


if __name__ == "__main__":
    builders()
    fitted_tile()
    generic_channel()
    long_names_rtl()
    GLib.MainContext.default().iteration(False)
    print("ok")
