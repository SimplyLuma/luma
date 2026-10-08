# SPDX-License-Identifier: Apache-2.0
"""LumaUI Gallery: the media family's pages (KB-C).

The gallery takes `PAGES` and `BUILDERS` from here. Run this file directly to
see one page on its own (the scratch window the conform and screenshot tools use):

    python3 pages_media.py --page media-grid [--appearance dark] [--phone] [--screenshot out.png]
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

HERE = Path(__file__).resolve().parent
if str(HERE.parents[1] / "appkit") not in sys.path:
    sys.path.insert(0, str(HERE.parents[1] / "appkit"))

from luma_appkit import TypeLabel  # noqa: E402
from luma_appkit import AudioWaveform, ImageViewport, MediaGrid, MediaItem, MediaTile, MediaTransport, Timeline  # noqa: E402
from luma_appkit import AdjustmentGroup, AdjustmentPanel, CoverArt, ValueSlider, VoiceClip  # noqa: E402

# (key, title, Lucide glyph, family, module) in the gallery's PAGES shape.
PAGES = [
    ("media-grid", "Media tile and grid", "layout-grid", "media", None),
    ("media-transport", "Media transport", "play", "media", None),
    ("media-waveform", "Audio waveform", "audio-lines", "media", None),
    ("media-adjust", "Adjustment sliders", "sliders-horizontal", "media", None),
    ("media-cover", "Cover art", "disc", "media", None),
    ("media-viewport", "Image viewport", "image", "media", None),
    ("media-voice", "Voice clip", "mic", "media", None),
    ("media-timeline", "Timeline", "film", "media", None),
]


def peaks(count: int = 480, seed: int = 3) -> list[float]:
    """A speech-like envelope (the gallery has no recordings)."""
    return [max(0.06, min(1.0, 0.25 + 0.75 * abs(math.sin(i * 0.37 + seed)) * ((i * 7919 + seed) % 97) / 97))
            for i in range(count)]


# ── helpers ────────────────────────────────────────────────────────────────

def _label(text: str, *css: str, wrap: bool = True) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0, wrap=wrap)
    for name in css:
        label.add_css_class(name)
    return label


def _page(title: str, lede: str, api: str) -> tuple[Gtk.Box, Gtk.Box]:
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    page.add_css_class("gallery-page")
    page.append(TypeLabel(title, role="title-1"))
    page.append(_label(lede, "gallery-lede"))
    page.append(_label(api, "gallery-code"))
    demo = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    demo.add_css_class("gallery-demo")
    page.append(demo)
    return page, demo


def _section(text: str) -> Gtk.Widget:
    return TypeLabel(text, role="label")


_TEXTURES: dict[tuple, Gdk.Texture] = {}


def picture(seed: int, width: int = 240, height: int = 180) -> Gdk.Texture:
    """A soft two-hue photograph stand-in (the gallery ships no pictures)."""
    key = (seed, width, height)
    if key in _TEXTURES:
        return _TEXTURES[key]
    hue_a, hue_b = (seed * 47) % 360, (seed * 47 + 70) % 360

    def rgb(hue: float, light: float) -> tuple[float, float, float]:
        k = [(n + hue / 30) % 12 for n in (0, 8, 4)]
        a = 0.45 * min(light, 1 - light)
        return tuple(light - a * max(-1, min(k_ - 3, 9 - k_, 1)) for k_ in k)

    top, bottom = rgb(hue_a, 0.62), rgb(hue_b, 0.38)
    rows = []
    for y in range(height):
        t = y / max(1, height - 1)
        row = bytearray()
        for x in range(0, width, 8):
            glow = 0.12 * math.exp(-(((x / width) - 0.3) ** 2 + (t - 0.35) ** 2) * 9)
            r, g, b = (min(1.0, top[i] * (1 - t) + bottom[i] * t + glow) for i in range(3))
            row += bytes((int(r * 255), int(g * 255), int(b * 255))) * min(8, width - x)
        rows.append(bytes(row))
    texture = Gdk.MemoryTexture.new(width, height, Gdk.MemoryFormat.R8G8B8, GLib.Bytes.new(b"".join(rows)), width * 3)
    _TEXTURES[key] = texture
    return texture


def _stars(count: int) -> Gtk.Widget:
    from luma_appkit import lumaui_icon

    row = Gtk.Box(spacing=1)
    for index in range(5):
        star = Gtk.Image.new_from_icon_name(lumaui_icon("star"))
        star.set_pixel_size(10)
        star.set_opacity(1.0 if index < count else 0.35)
        row.append(star)
    return row


# ── pages ──────────────────────────────────────────────────────────────────

def page_media_grid() -> Gtk.Widget:
    page, demo = _page(
        "Media tile and grid",
        "A picture as a tile, and a recycling grid of them. Photos' joined groups round only their outside "
        "corners, worked out from each tile's real neighbours; Darkroom's library rings the selection and dims "
        "what's set aside; Reel's clips carry their length.",
        'MediaGrid(items, kind="photo"|"library"|"clip", min_side=, big=, scrolls=, on_activate=, on_open=, '
        'on_select=(item, mode), meta=fn)\nMediaItem(id, picture, title=, favourite=, selected=, dimmed=, badge=, '
        'caption=)\nMediaTile(picture, kind=, selected=, dimmed=, favourite=, badge=, caption=, meta=, on_activate=, '
        'on_open=)')
    photos = [MediaItem(str(i), picture(i), title=f"Photo {i + 1}", favourite=i in (1, 5)) for i in range(7)]
    demo.append(_section("Photos: a day, seven pictures"))
    demo.append(MediaGrid(photos, kind="photo", scrolls=False, label="Today"))

    library = [MediaItem(str(i), picture(i + 11), title=f"Terrace {i + 1}", selected=i == 1, dimmed=i == 3,
                         badge="Edited" if i in (0, 2) else "", data=(5 - i) % 6) for i in range(6)]

    def pick(item: MediaItem, mode: str) -> None:
        for other in library:
            if mode == "replace":
                other.selected = other is item
            elif other is item:
                other.selected = not other.selected if mode == "toggle" else True

    demo.append(_section("Darkroom: the library"))
    demo.append(MediaGrid(library, kind="library", scrolls=False, on_select=pick, meta=lambda item: _stars(item.data),
                          label="Terrace"))

    clips = [MediaItem(str(i), picture(i + 23, 320, 180), caption=name, badge=length, selected=i == 0)
             for i, (name, length) in enumerate((("Harbour", "0:12"), ("Walk home", "0:48"), ("Kitchen", "1:05"),
                                                  ("Night bus", "0:21")))]
    browser = Gtk.Box(halign=Gtk.Align.START)
    browser.set_size_request(240, -1)
    clip_grid = MediaGrid(clips, kind="clip", scrolls=False, label="Media")
    clip_grid.set_size_request(240, -1)
    browser.append(clip_grid)
    demo.append(_section("Reel: the browser's clips"))
    demo.append(browser)

    demo.append(_section("One tile on its own"))
    row = Gtk.Box(spacing=16)
    for kind, kwargs in (("photo", {"favourite": True}), ("library", {"selected": True, "badge": "Edited"}),
                         ("clip", {"badge": "0:12", "caption": "Harbour"})):
        tile = MediaTile(picture(40 + len(kind)), kind=kind, title=kind.title(), **kwargs)
        tile.set_size_request(150, -1)
        tile.set_valign(Gtk.Align.START)
        row.append(tile)
    demo.append(row)
    return page


def page_media_transport() -> Gtk.Widget:
    page, demo = _page(
        "Media transport",
        "One transport for everything that plays: play, seek, time, step and loop in one state model. Tide's deck "
        "keys over the scrub, Reel's bar under the picture with its LCD, Session's LCD bar, and Memos' deck around "
        "the waveform. Keys report what the listener asked; the app moves the position.",
        'MediaTransport("deck"|"attached"|"lcd"|"waveform", duration=, position=, playing=, fps=, loop=, shuffle=, '
        'repeat=, volume=, speed=, recording=, lcd=, readouts=, waveform=, on_play=, on_seek=, on_step=, on_skip=, '
        'on_loop=, on_shuffle=, on_repeat=, on_volume=, on_speed=, on_record=, on_fullscreen=)\n'
        '.set_position(s) .set_playing(b) .set_duration(s) .volume_control .attach_shortcuts(window)')
    demo.append(_section("Tide: the deck's keys and scrub, and its volume"))
    deck = MediaTransport("deck", duration=184, position=43, shuffle=False, repeat=True, volume=0.62)
    deck.set_halign(Gtk.Align.CENTER)
    demo.append(deck)
    volume = deck.volume_control
    volume.set_halign(Gtk.Align.END)
    demo.append(volume)
    demo.append(_section("Reel: attached under the picture"))
    demo.append(MediaTransport("attached", duration=95.5, position=12.2, fps=24, loop=True, on_fullscreen=lambda: None))
    demo.append(_section("Session: the LCD bar"))
    demo.append(MediaTransport("lcd", lcd=[(("12", "3", "1"), "Bar"), ("0:21.4", "Time")], recording=False, loop=False,
                               readouts=[("112", "BPM", lambda: None), ("4/4", "Time", lambda: None),
                                         ("D min", "Key", lambda: None)]))
    demo.append(_section("Memos: the deck around the waveform"))
    wave = AudioWaveform()
    wave.set_audio(peaks(), 63)
    wave.set_marks([31])
    demo.append(MediaTransport("waveform", duration=63, position=21, speed=1.0, waveform=wave))
    return page


def page_media_waveform() -> Gtk.Widget:
    page, demo = _page(
        "Audio waveform",
        "A recording's real peaks as a seekable range: a recessed well on its own, quieter inside Memos' deck, "
        "red bars growing in while recording, and a 44 px glance for a row. Marks show their time on hover.",
        'AudioWaveform(kind="well"|"deck"|"mini") .set_audio(peaks, duration) .set_position(s) .set_marks([s]) '
        '.set_live(b) .push_peak(v) .set_on(b)  signals: seek(s), selection-changed(a, b)')
    demo.append(_section("On its own"))
    well = AudioWaveform("well")
    well.set_audio(peaks(), 63)
    well.set_position(20)
    well.set_marks([10, 44])
    well.set_margin_top(8)
    demo.append(well)
    demo.append(_section("Recording"))
    live = AudioWaveform("well")
    live.set_live(True)
    for value in peaks(90, 7):
        live.push_peak(value)
    demo.append(live)
    demo.append(_section("In a row"))
    row = Gtk.Box(spacing=12)
    for on in (False, True):
        mini = AudioWaveform("mini")
        mini.set_audio(peaks(160, 5), 60)
        mini.set_on(on)
        row.append(mini)
    demo.append(row)
    return page


def page_media_adjust() -> Gtk.Widget:
    page, demo = _page(
        "Adjustment sliders",
        "A name, a range and its value. Ranges that span zero fill from zero; a moved slider turns to full ink and "
        "its group shows a dot; a double-click puts it back. The Adjust panel floats over the picture and is a "
        "sheet at the bottom edge on a phone.",
        'ValueSlider(label, value=0, minimum=-100, maximum=100, unit=, default=, step=, layout="row"|"stacked", '
        'on_change=, on_reset=)\nAdjustmentGroup(title, sliders, open=, on_toggle=)\n'
        'AdjustmentPanel([(heading, sliders) | AdjustmentGroup], title="Adjust", on_reset=, on_close=)'
        '.attach(overlay) .set_shown(b)')
    row = Gtk.Box(spacing=24)
    inspector = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    inspector.set_size_request(280, -1)
    inspector.append(AdjustmentGroup("Light", [ValueSlider("Exposure", 12), ValueSlider("Contrast"),
                                               ValueSlider("Highlights", -34), ValueSlider("Shadows", 18)], open=True))
    inspector.append(AdjustmentGroup("Colour", [ValueSlider("Temperature"), ValueSlider("Saturation")]))
    inspector.append(AdjustmentGroup("Audio", [ValueSlider("Volume", -3, -60, 12, unit="dB"),
                                               ValueSlider("Opacity", 80, 0, 100, unit="%", default=100)]))
    row.append(inspector)
    stage = Gtk.Overlay(hexpand=True)
    stage.set_size_request(-1, 460)
    stage.set_child(Gtk.Picture(paintable=picture(3, 480, 320), content_fit=Gtk.ContentFit.COVER))
    panel = AdjustmentPanel([
        ("Light", [ValueSlider("Exposure", 20, layout="stacked"), ValueSlider("Contrast", layout="stacked"),
                   ValueSlider("Highlights", layout="stacked")]),
        ("Colour", [ValueSlider("Warmth", -12, layout="stacked"), ValueSlider("Tint", layout="stacked")]),
        (None, [ValueSlider("Straighten", 0, -15, 15, unit="°", layout="stacked")]),
    ])
    panel.attach(stage)
    panel.set_shown(True)
    row.append(stage)
    demo.append(row)
    return page


def page_media_cover() -> Gtk.Widget:
    page, demo = _page(
        "Cover art",
        "Real art always wins. Without it an album gets a lit diagonal in its own hues with the title set in the "
        "reading serif, and a book gets its ground, its title and author and its page edges. The same title gives "
        "the same cover everywhere.",
        'CoverArt(title, subtitle="", picture=None, hues=None, shape="album"|"book", size="hero"|"tile"|"mini")')
    row = Gtk.Box(spacing=28)
    row.append(CoverArt("Blue Hour", "Mara Sol", hues=(250, 285, 210), size="hero"))
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    tile = CoverArt("Salt and Honey", "The Lanterns", size="tile")
    tile.set_size_request(160, -1)
    column.append(tile)
    minis = Gtk.Box(spacing=8)
    for name in ("Night Bus", "Harbour", "Ember"):
        minis.append(CoverArt(name, size="mini"))
    minis.append(CoverArt("Real art", picture=picture(9, 120, 120), size="mini"))
    column.append(minis)
    row.append(column)
    demo.append(row)
    books = Gtk.Box(spacing=28)
    books.append(CoverArt("The Salt Road", "I. Varga", shape="book", size="hero"))
    books.append(CoverArt("A Winter Garden", "Hana Obi", shape="book", size="tile"))
    books.append(CoverArt("Edition art", "Leaf", picture=picture(12, 200, 300), shape="book", size="tile"))
    books.append(CoverArt("Small", "X", shape="book", size="mini"))
    demo.append(books)
    return page


def page_media_viewport() -> Gtk.Widget:
    page, demo = _page(
        "Image viewport",
        "The black stage fills its allocation. Insets reserve space around the fitted photo; previews and rotation "
        "leave the source texture untouched.",
        "ImageViewport(surround='black'); .set_paintable(texture); .set_fit_insets(top, right, bottom, left); "
        ".set_adjustments(mapping); .get_content_bounds()")
    viewport = ImageViewport(surround="black")
    viewport.set_paintable(picture(9, 480, 320))
    viewport.set_fit_insets(56, 32, 72, 32)
    viewport.set_size_request(360, 400)
    demo.append(viewport)
    return page


def page_media_voice() -> Gtk.Widget:
    page, demo = _page(
        "Voice clip",
        "A short recording you play where it is: a voice message with its shape, a voicemail with a thin bar. "
        "The time is the length at rest and the time left once it plays.",
        "VoiceClip(duration, peaks=None, position=0, playing=False, on_toggle=, on_seek=) .set_position(s) "
        ".set_playing(b)")
    demo.append(_section("Messages: a voice message"))
    message = VoiceClip(12.4, peaks=peaks(64, 2), position=4.1, playing=True)
    message.set_halign(Gtk.Align.START)
    demo.append(message)
    demo.append(_section("Phone: a voicemail"))
    voicemail = VoiceClip(31)
    voicemail.set_size_request(360, -1)
    voicemail.set_halign(Gtk.Align.START)
    demo.append(voicemail)
    return page


def page_media_timeline() -> Gtk.Widget:
    from types import SimpleNamespace

    page, demo = _page(
        "Timeline",
        "The shared ruler, title, picture and audio lanes. Clip data comes from the editor; actions report "
        "intent without changing its project or starting media work.",
        "Timeline(lanes, duration, fps, position, selected, tool, snapping, zoom, callbacks, markers) "
        ".set_content(lanes, duration=, fps=, markers=, selected=) .set_position(seconds) "
        ".set_export_status(label, fraction)")
    def clip(identifier, title, kind, start, duration, **extra):
        return SimpleNamespace(id=identifier, name=title, kind=kind, start_frame=start,
                               duration_frames=duration, text="", **extra)
    lanes = (
        SimpleNamespace(id="over", kind="over", clips=(clip("title", "Opening title", "title", 0, 72),)),
        SimpleNamespace(id="main", kind="main", clips=(clip("arrival", "Arrival", "video", 0, 120,
                                                               transition_after="dissolve"),
                                                       clip("garden", "Garden", "video", 120, 144))),
        SimpleNamespace(id="audio", kind="audio", clips=(clip("music", "Music", "music", 0, 264),)),
    )
    timeline = Timeline(lanes, duration=12, fps=24, position=3.5, selected=("arrival",),
                        markers=(72, 240), callbacks={"seek": lambda seconds: timeline.set_position(seconds)})
    demo.append(timeline)
    return page


BUILDERS = {"media-grid": page_media_grid, "media-viewport": page_media_viewport, "media-voice": page_media_voice, "media-adjust": page_media_adjust, "media-cover": page_media_cover, "media-transport": page_media_transport,
            "media-waveform": page_media_waveform, "media-timeline": page_media_timeline}


# ── conform surfaces ───────────────────────────────────────────────────────
#
# tools/lumaui-conform/scenarios/media-*.json run this module with
# LUMAUI_MEDIA_CONFORM=<surface>: a window holding only that part, in v70's
# own state for the matching surface, so the capture compares like with like.

def _conform_transport_attached() -> Gtk.Widget:
    # v70 Reel's opening state: 12 s and 7 frames into a 44 s story at 24 fps, loop off.
    return MediaTransport("attached", duration=44, position=12 + 7 / 24, fps=24, loop=False,
                          on_fullscreen=lambda: None)


def _conform_transport_deck() -> Gtk.Widget:
    # v70 Tide's opening song, paused at 1:06 of 2:51 (the scenario pauses the spec there), shuffle and repeat off.
    return MediaTransport("deck", duration=171, position=66, playing=False, shuffle=False, repeat=False)


CONFORM = {"transport-attached": _conform_transport_attached, "transport-deck": _conform_transport_deck}


# ── standalone ─────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--page", default=PAGES[0][0], choices=list(BUILDERS))
    parser.add_argument("--appearance", default="dark", choices=("light", "dark", "high-contrast"))
    parser.add_argument("--phone", action="store_true")
    parser.add_argument("--screenshot")
    options = parser.parse_args(argv)
    import os

    surface = os.environ.get("LUMAUI_MEDIA_CONFORM", "")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Graphene

    from luma_appkit import install_lumaui

    app = Adw.Application()

    def activate(app: Adw.Application) -> None:
        if surface:
            install_lumaui()
            window = Gtk.ApplicationWindow(application=app, title="LumaUI media conform", decorated=False)
            window.add_css_class("lumaui-media-conform")
            window.add_css_class("luma-no-native-surfaces")
            window.set_child(CONFORM[surface]())
            window.present()
            return
        manager = Adw.StyleManager.get_default()
        manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT if options.appearance == "light"
                                 else Adw.ColorScheme.FORCE_DARK)
        install_lumaui()
        window = Gtk.ApplicationWindow(application=app, default_width=390 if options.phone else 900,
                                       default_height=820, title="LumaUI media")
        scroller = Gtk.ScrolledWindow(child=BUILDERS[options.page](), hscrollbar_policy=Gtk.PolicyType.NEVER)
        window.set_child(scroller)
        window.present()
        if options.screenshot:
            def grab() -> bool:
                child = window.get_child()
                snapshot = Gtk.Snapshot()
                found, ground = window.get_style_context().lookup_color("luma_window")
                bounds = Graphene.Rect().init(0, 0, child.get_width(), child.get_height())
                if found:
                    snapshot.append_color(ground, bounds)
                Gtk.WidgetPaintable(widget=child).snapshot(snapshot, child.get_width(), child.get_height())
                texture = window.get_native().get_renderer().render_texture(snapshot.to_node(), bounds)
                texture.save_to_png(options.screenshot)
                app.quit()
                return False

            GLib.timeout_add(900, grab)

    app.connect("activate", activate)
    return app.run([])


if __name__ == "__main__":
    raise SystemExit(main())
