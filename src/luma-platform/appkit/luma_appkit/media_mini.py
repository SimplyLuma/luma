# SPDX-License-Identifier: Apache-2.0
"""Compact media identity, transport keys and neutral playback progress.

The caller owns playback and artwork loading. This Python media-family component
has no native C twin yet, like MediaTile and MediaCollectionCard.
"""
from __future__ import annotations
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango
from . import icons, lumaui_tokens as tokens
from .action_center import BarAction, make_control
from .content_type import apply_type
from .rows_progress import ProgressLine

M = tokens.MEDIA["mini"]

class MediaMiniPlayer(Gtk.Box):
    """An accessible open-media button, Play/Pause, Next and progress.

    Artwork is a caller-owned widget, normally CoverArt or MediaTile at44px.
    Callbacks request actions; update() reflects the player's actual state.
    The row does not start playback or load media itself.
    """
    __gtype_name__ = "LumaUIMediaMiniPlayer"

    def __init__(self, *, on_open=None, on_play=None, on_next=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.add_css_class("lumaui-media-mini")
        overlay=Gtk.Overlay(hexpand=True)
        row=Gtk.Box(spacing=M["gap"], hexpand=True)
        row.set_size_request(-1,M["row_height"])
        row.set_margin_bottom(M["progress_slot"])
        overlay.set_child(row)
        self.open=Gtk.Button(hexpand=True)
        self.open.add_css_class("lumaui-media-mini-open")
        line=Gtk.Box(spacing=M["content_gap"])
        self.artwork=Gtk.Box(valign=Gtk.Align.CENTER)
        line.append(self.artwork)
        words=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,valign=Gtk.Align.CENTER,hexpand=True)
        self.title=apply_type(Gtk.Label(), "media_mini_title")
        self.detail=apply_type(Gtk.Label(), "media_mini_detail")
        self.detail.add_css_class("detail")
        for label in (self.title,self.detail):
            label.set_xalign(0);label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(1);label.set_hexpand(True);words.append(label)
        line.append(words);self.open.set_child(line);row.append(self.open)
        if on_open:self.open.connect("clicked",lambda *_:on_open())
        self.play=make_control(BarAction("play",tooltip="Play",on_activate=on_play))
        self.next=make_control(BarAction("skip-forward",tooltip="Next",on_activate=on_next))
        self.play.set_valign(Gtk.Align.CENTER);self.next.set_valign(Gtk.Align.CENTER)
        row.append(self.play);row.append(self.next)
        self.progress=ProgressLine(tone="neutral",label="Playback progress")
        self.progress.add_css_class("media-mini-progress")
        self.progress.set_valign(Gtk.Align.END)
        self.progress.set_margin_start(M["progress_side"]);self.progress.set_margin_end(M["progress_side"])
        self.progress.set_margin_bottom(M["progress_bottom"])
        overlay.add_overlay(self.progress)
        self.append(overlay)

    def set_artwork(self, widget):
        child=self.artwork.get_first_child()
        if child:self.artwork.remove(child)
        if widget is not None:self.artwork.append(widget)

    def update(self, title, detail="", *, playing=False, fraction=0):
        self.title.set_label(title);self.detail.set_label(detail)
        self.open.update_property([Gtk.AccessibleProperty.LABEL],[f"Now playing: {title}, {detail}".rstrip(", ")])
        name="Pause" if playing else "Play"
        self.play.set_child(icons.image("pause" if playing else "play"))
        self.play.set_tooltip_text(name)
        self.play.update_property([Gtk.AccessibleProperty.LABEL],[name])
        self.progress.set_fraction(fraction)
