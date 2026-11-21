# SPDX-License-Identifier: Apache-2.0
"""LumaUI audio meter, for Python.

The meter is LumaUI-1's C part (``luma-audio-meter.c``), reached through
``gi.repository.LumaUI``; this builder adds the kit's keyword style and
returns the C object. A volume with its level is a ``VolumeSlider``
(``luma-volume-slider.c``): the kit's slider, upright for a mixer's strip or
flat for a row, with the meter laid along the track over the track's length.

    AudioMeter(channels=2, horizontal=True)   # .set_levels_db(l, r), .set_levels(l, r)
    AudioMeter(2, segments=28)                # LED style: 28 segments per bar
    AudioMeter(1, horizontal=True, segments=32, thickness=5, gap=3, radius=2, zones=False)
                                              # an output's level: one green, no zones

``LevelMeter`` is the same part under the name a level display goes by;
``segments=0`` is the continuous bar, N > 0 draws N segments with the peak
hold as a lit segment. ``LevelTap`` (``level_tap.py``) feeds either from a
PipeWire node while it is on screen.
    VolumeSlider(-60, 6, value=0, unity=0, unit="dB", meter=2, horizontal=True,
                 label="Mic volume", on_move=fn)  # .get_meter().set_levels_db(l, r)

A mixer's channel is a ``MixerStrip`` (``luma-mixer-strip.c``): a fixed-width
column of name and device, a dB scale beside an upright VolumeSlider and its
stereo meter, the readout, and Mute and Monitor keys. ``MixerRow`` lays strips
out left to right in a row that scrolls sideways; it fits ``DockTile(fit=True)``.

    MixerStrip("Mic", "USB microphone", db=-6, on_change=fn, on_mute=fn, on_monitor=fn)
        # .set_levels(l, r), .set_levels_db(l, r), .set_db(db), .set_muted(b), .set_monitoring(b)
    MixerRow([strip, ...])  # .append(strip), .remove(strip)

A meter animates only while a level falls, so a silent row costs nothing; a
segmented one redraws only when a segment changes. Feed it from GStreamer's
``level`` element (``rms`` or ``peak`` per channel, in dB) at 10 to 20
updates a second; ``LevelTap`` does that.
"""
from __future__ import annotations

from .creative_parts import lumaui_module

__all__ = ["AudioMeter", "LevelMeter", "MixerRow", "MixerStrip", "VolumeSlider"]

MAX_SEGMENTS = 128


def AudioMeter(channels: int = 2, *, horizontal: bool = False, segments: int = 0, label: str | None = None,
               thickness: float | None = None, gap: float | None = None, radius: float | None = None,
               zones: bool = True):
    """A mono or stereo level meter; ``horizontal`` runs its bars left to right.

    ``segments`` 0 is a continuous bar; N (1 to 128) draws N segments per bar.
    ``thickness``, ``gap`` and ``radius`` (px) shape a segmented bar's
    segments; unset, the tokens' shape. ``zones=False`` is an output's meter:
    every lit segment the quiet green, no loud or hot zone, no peak hold.
    """
    if channels not in (1, 2):
        raise ValueError(f"a meter has 1 or 2 channels, not {channels!r}")
    if isinstance(segments, bool) or not isinstance(segments, int) or not 0 <= segments <= MAX_SEGMENTS:
        raise ValueError(f"a meter has 0 to {MAX_SEGMENTS} segments, not {segments!r}")
    L = lumaui_module()
    meter = L.AudioMeter.new(channels)
    meter.set_horizontal(horizontal)
    meter.set_segments(segments)
    for name, value in (("thickness", thickness), ("gap", gap), ("radius", radius)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
            raise ValueError(f"a meter's segment {name} is a length in px, not {value!r}")
    if thickness is not None or gap is not None or radius is not None:
        meter.set_segment_shape(thickness or 0, -1 if gap is None else gap, -1 if radius is None else radius)
    meter.set_zones(bool(zones))
    if label is not None:
        from gi.repository import Gtk

        meter.update_property([Gtk.AccessibleProperty.LABEL], [label])
    return meter


#: The same part as ``AudioMeter``: ``LevelMeter(channels=2, horizontal=, segments=, label=)``.
LevelMeter = AudioMeter


def VolumeSlider(lower: float = 0, upper: float = 100, *, value: float | None = None,
                 unity: float | None = None, unit: str = "%", meter: int = 0,
                 horizontal: bool = False, step: float = 1, page: float = 10,
                 label: str | None = None, on_move=None):
    """A volume from ``lower`` to ``upper``; ``meter`` (1 or 2) lays a meter along it.

    ``on_move(value)`` hears only the person's moves; ``set_value`` is silent.
    """
    if meter not in (0, 1, 2):
        raise ValueError(f"a volume's meter has 0, 1 or 2 channels, not {meter!r}")
    if upper <= lower:
        raise ValueError("a volume's upper end must be above its lower end")
    L = lumaui_module()
    from gi.repository import Gtk

    orientation = Gtk.Orientation.HORIZONTAL if horizontal else Gtk.Orientation.VERTICAL
    slider = L.VolumeSlider.new(orientation, lower, upper)
    slider.set_steps(step, page)
    slider.set_unit(unit)
    if unity is not None:
        slider.set_unity(unity)
    if meter:
        slider.set_meter_channels(meter)
    if value is not None:
        slider.set_value(value)
    if label is not None:
        slider.update_property([Gtk.AccessibleProperty.LABEL], [label])
    if on_move is not None:
        slider.connect("moved", lambda _s, v: on_move(v))
    return slider


def MixerStrip(name: str, subtitle: str | None = None, *, db: float = 0.0, min_db: float = -60,
               max_db: float = 6, muted: bool = False, monitoring: bool = False, icon=None,
               mute: bool = True, monitor: bool = True, on_change=None, on_mute=None, on_monitor=None):
    """A channel strip; ``on_change(db)``, ``on_mute(muted)`` and ``on_monitor(on)`` hear only the person.

    ``icon`` heads it: a Lucide name ("mic"), a theme or app icon name, or a Gio.Icon (an app's).
    ``mute=False`` / ``monitor=False`` leave that key out (M then does nothing). The setters
    (``set_db``, ``set_muted``, ``set_monitoring``, ``set_gicon``, ``set_icon_name``) are silent.
    """
    if max_db <= min_db:
        raise ValueError("a strip's max_db must be above its min_db")
    L = lumaui_module()
    strip = L.MixerStrip.new(name, subtitle)
    strip.set_range(min_db, max_db)
    strip.set_db(db)
    strip.set_muted(muted)
    strip.set_monitoring(monitoring)
    strip.set_mute_visible(mute)
    strip.set_monitor_visible(monitor)
    if isinstance(icon, str):
        strip.set_icon_name(icon)
    elif icon is not None:
        strip.set_gicon(icon)
    if on_change is not None:
        strip.connect("moved", lambda _s, value: on_change(value))
    if on_mute is not None:
        strip.connect("mute-toggled", lambda _s, value: on_mute(value))
    if on_monitor is not None:
        strip.connect("monitor-toggled", lambda _s, value: on_monitor(value))
    return strip


def MixerRow(strips=(), *, label: str | None = None):
    """Strips left to right, scrolling sideways when they do not fit; ``.append`` and ``.remove`` change it."""
    L = lumaui_module()
    row = L.MixerRow.new()
    for strip in strips:
        row.append(strip)
    if label is not None:
        from gi.repository import Gtk

        row.update_property([Gtk.AccessibleProperty.LABEL], [label])
    return row
