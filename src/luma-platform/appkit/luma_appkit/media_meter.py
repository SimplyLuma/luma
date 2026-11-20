# SPDX-License-Identifier: Apache-2.0
"""LumaUI audio meter, for Python.

The meter is LumaUI-1's C part (``luma-audio-meter.c``), reached through
``gi.repository.LumaUI``; this builder adds the kit's keyword style and
returns the C object. Volume beside a meter is a ``ValueSlider`` (the one
slider), in dB.

    AudioMeter(channels=2, horizontal=True)   # .set_levels_db(l, r), .set_levels(l, r)

A meter animates only while a level falls, so a silent row costs nothing.
Feed it from GStreamer's ``level`` element (``rms`` or ``peak`` per channel,
in dB) at 10 to 20 updates a second.
"""
from __future__ import annotations

from .creative_parts import lumaui_module

__all__ = ["AudioMeter"]


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
