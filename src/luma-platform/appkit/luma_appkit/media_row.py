# SPDX-License-Identifier: Apache-2.0
"""LumaUI media row, for Python.

One playing app as its content: the artwork badged with the app, a title and
a line, the transport its player offers and its volume. The row is LumaUI-1's
C part (``luma-media-row.c``), reached through ``gi.repository.LumaUI``; this
builder adds the kit's keyword style and returns the C object.

    row = MediaRow("God Only Knows", "Tide · The Beach Boys", app="Tide", icon=gicon,
                   art=paintable, transport=("previous", "play", "next"), playing=True,
                   volume=100, meter=28, on_play=fn, on_volume=fn, on_mute=fn, on_raise=fn)
    row.set_trailing(MediaRowChip("volume-2", "Speakers"))
    row.set_level(0.6)          # the sound service's meter position, 0..1

``density`` is "regular" (a card's block), "compact" (a row in a panel's
well) or "touch" (the compact row on a phone or tablet). The callbacks hear
only the person; the setters are silent. The row never talks to a player or
a mixer itself.
"""
from __future__ import annotations

from .creative_parts import lumaui_module
from .icons import icon_name

__all__ = ["MediaRow", "MediaRowChip", "MEDIA_ROW_DENSITIES"]

MEDIA_ROW_DENSITIES = ("regular", "compact", "touch")
_TRANSPORT = {"play": "PLAY_PAUSE", "pause": "PLAY_PAUSE", "play-pause": "PLAY_PAUSE",
              "previous": "PREVIOUS", "next": "NEXT"}


def MediaRow(title: str, line: str | None = None, *, app: str | None = None, icon=None, art=None,
             density: str = "regular", transport=(), playing: bool = False, sounding: bool = False,
             volume: float = 100, muted: bool = False, meter: int = 0,
             on_play=None, on_previous=None, on_next=None, on_volume=None, on_mute=None, on_raise=None):
    """A playing app's row. ``art`` is a paintable or a ``Gio.File``; without
    it the app's ``icon`` sits on a neutral tile. ``meter`` is the number of
    lights under the slider (regular rows), 0 for none."""
    if density not in MEDIA_ROW_DENSITIES:
        raise ValueError(f"a media row is one of {MEDIA_ROW_DENSITIES}, not {density!r}")
    unknown = [name for name in transport if name not in _TRANSPORT]
    if unknown:
        raise ValueError(f"a media row's transport is play, previous and next, not {unknown!r}")
    if meter < 0:
        raise ValueError("a media row's meter cannot have fewer than 0 segments")
    L = lumaui_module()
    from gi.repository import Gio

    row = L.MediaRow.new()
    row.set_density(getattr(L.MediaRowDensity, density.upper()))
    row.set_title(title)
    row.set_line(line)
    row.set_app(app, icon)
    if isinstance(art, Gio.File):
        row.set_art_file(art)
    elif art is not None:
        row.set_art(art)
    flags = L.MediaRowTransport(0)
    for name in transport:
        flags |= getattr(L.MediaRowTransport, _TRANSPORT[name])
    row.set_transport(flags)
    row.set_playing(playing)
    row.set_sounding(sounding)
    row.set_volume(volume)
    row.set_muted(muted)
    if meter:
        row.set_meter_segments(meter)
    for signal, callback, unpack in (("play-pause", on_play, False), ("previous", on_previous, False),
                                     ("next", on_next, False), ("raise", on_raise, False),
                                     ("volume-changed", on_volume, True), ("mute-toggled", on_mute, True)):
        if callback is not None:
            row.connect(signal, (lambda _r, value, cb=callback: cb(value)) if unpack
                        else (lambda _r, cb=callback: cb()))
    return row


def MediaRowChip(icon: str, label: str, *, raised: bool = False):
    """A row's trailing picker: a glyph, the choice and a chevron. A
    ``Gtk.MenuButton``: give it a popover or a menu model. ``.set(icon, label,
    raised)`` changes it; raised says the choice is not the default one."""
    L = lumaui_module()
    # Functions of the row's namespace that take the chip, not a row.
    chip = L.MediaRow.chip_new(icon_name(icon), label)
    if raised:
        L.MediaRow.chip_set(chip, icon_name(icon), label, True)
    chip.set = lambda new_icon, new_label, new_raised=False: L.MediaRow.chip_set(
        chip, icon_name(new_icon), new_label, new_raised)
    return chip
