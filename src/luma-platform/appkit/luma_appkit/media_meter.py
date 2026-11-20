# SPDX-License-Identifier: Apache-2.0
"""LumaUI audio meter, for Python.

The meter is LumaUI-1's C part (``luma-audio-meter.c``), reached through
``gi.repository.LumaUI``; this builder adds the kit's keyword style and
returns the C object. A volume with its level is a ``VolumeSlider``
(``luma-volume-slider.c``): the kit's slider, upright for a mixer's strip or
flat for a row, with the meter laid along the track over the track's length.

    AudioMeter(channels=2, horizontal=True)   # .set_levels_db(l, r), .set_levels(l, r)
    VolumeSlider(-60, 6, value=0, unity=0, unit="dB", meter=2, horizontal=True,
                 label="Mic volume", on_move=fn)  # .get_meter().set_levels_db(l, r)

A meter animates only while a level falls, so a silent row costs nothing.
Feed it from GStreamer's ``level`` element (``rms`` or ``peak`` per channel,
in dB) at 10 to 20 updates a second.
"""
from __future__ import annotations

from .creative_parts import lumaui_module

__all__ = ["AudioMeter", "VolumeSlider"]


def AudioMeter(channels: int = 2, *, horizontal: bool = False, label: str | None = None):
    """A mono or stereo level meter; ``horizontal`` runs its bars left to right."""
    if channels not in (1, 2):
        raise ValueError(f"a meter has 1 or 2 channels, not {channels!r}")
    L = lumaui_module()
    meter = L.AudioMeter.new(channels)
    meter.set_horizontal(horizontal)
    if label is not None:
        from gi.repository import Gtk

        meter.update_property([Gtk.AccessibleProperty.LABEL], [label])
    return meter


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
