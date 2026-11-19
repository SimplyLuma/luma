# SPDX-License-Identifier: Apache-2.0
"""Memos-only media surfaces, composed with LumaUI type and action parts."""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Graphene, Gsk, Gtk, Pango  # noqa: E402

from luma_appkit import AudioWaveform, MediaTransport, TypeLabel, apply_type, icons
from .memos_data import Memo, format_duration


def _clear(box: Gtk.Box) -> None:
    while child := box.get_first_child():
        box.remove(child)


class MiniWave(AudioWaveform):
    """The kit's compact waveform with the recording's real peaks."""

    def __init__(self, peaks: tuple[float, ...] = ()) -> None:
        super().__init__(kind="mini")
        self.set_valign(Gtk.Align.CENTER)
        self.set_audio(peaks, 0)


class MemoEmpty(Gtk.Box):
    """The Memos-only quiet centre used when no recording is selected."""

    def __init__(self, title: str, description: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                         halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                         hexpand=True, vexpand=True)
        self.add_css_class("me-empty")
        glyph = icons.image("mic")
        glyph.set_pixel_size(30)
        glyph.set_halign(Gtk.Align.CENTER)
        self.append(glyph)
        heading = apply_type(Gtk.Label(label=title), "body", weight=700)
        heading.add_css_class("me-empty-title")
        self.append(heading)
        self.append(apply_type(Gtk.Label(label=description), "body"))


class MemoRow(Gtk.ListBoxRow):
    """Temporary row until NavigationRow slots and compact AudioWaveform land.

    TODO(kit-request memos-02-recording-row): replace with the shared row.
    """

    def __init__(self, memo: Memo, *, query: str = "", swipe: Callable[[Gtk.Widget], Gtk.Widget] | None = None) -> None:
        super().__init__()
        self.memo = memo
        self.set_name("me-row")
        self.add_css_class("me-row")
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        # v71: a favourite's star sits inline before its name (11 px, 5 px apart), not above it.
        name = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        name.add_css_class("me-row-name")
        if memo.favourite:
            star = icons.image("star-filled")
            star.add_css_class("me-row-star")
            star.set_valign(Gtk.Align.CENTER)
            name.append(star)
        title = apply_type(Gtk.Label(label=memo.title, xalign=0, hexpand=True,
                                     ellipsize=Pango.EllipsizeMode.END), "lead")
        title.add_css_class("me-row-title")
        name.append(title)
        words.append(name)
        meta = apply_type(Gtk.Label(label=f"{memo.when} · {format_duration(memo.duration)}",
                                    xalign=0, ellipsize=Pango.EllipsizeMode.END), "caption")
        meta.add_css_class("me-row-meta")
        words.append(meta)
        if excerpt := memo.search_excerpt(query):
            hit = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            hit.add_css_class("me-row-hit")
            before = apply_type(Gtk.Label(label=excerpt[0], xalign=0,
                                          ellipsize=Pango.EllipsizeMode.END), "body")
            hit.append(before)
            second = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            match = apply_type(Gtk.Label(label=excerpt[1], xalign=0), "body")
            match.add_css_class("me-row-hit-match")
            second.append(match)
            after = apply_type(Gtk.Label(label=excerpt[2], xalign=0, hexpand=True,
                                         ellipsize=Pango.EllipsizeMode.END), "body")
            second.append(after)
            hit.append(second)
            words.append(hit)
        line.append(words)
        self.mini = MiniWave(memo.wave)
        line.append(self.mini)
        # The swipe actions are the kit's SwipeRow around the row's content (v71 swipe rows).
        self.set_child(swipe(line) if swipe else line)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"{memo.title}, {memo.when}, {format_duration(memo.duration)}" +
                              (", favorite" if memo.favourite else "")])


class MemoDeck(Gtk.Box):
    """Kit-owned playback deck, plus its pending live recording variant.

    TODO(kit-request memos-01-audio-deck): MediaTransport needs a live clock
    slot so the recording view can also use the shared transport surface.
    """

    def __init__(self, *, on_seek: Callable[[float], None], on_play: Callable[[], None],
                 on_skip: Callable[[float], None], on_speed: Callable[[float], None],
                 speed_extras: Callable | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_name("me-deck")
        self.add_css_class("me-deck-host")
        self.wave = AudioWaveform("deck")
        self.wave.set_name("me-wave")
        self.transport = MediaTransport(
            "waveform", waveform=self.wave, speed=1.0,
            on_seek=on_seek, on_play=lambda _playing: on_play(),
            on_skip=on_skip, on_speed=on_speed, speeds=(0.75, 1, 1.25, 1.5, 2), speed_extras=speed_extras,
        )
        self.transport.set_name("me-transport")
        self.transport.key("play").set_name("me-play")
        self.transport.key("speed").set_name("me-speed")
        self.append(self.transport)

        self.record_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.record_box.add_css_class("me-recording-deck")
        self.clock = TypeLabel("0:00", role="display", unit=".0")
        self.clock.add_css_class("me-live-clock")
        self.clock.set_halign(Gtk.Align.START)
        # v70's live clock has a taller line box than the shared display role.
        self.clock.set_margin_top(6)
        self.clock.set_margin_bottom(7)
        self.record_box.append(self.clock)
        self.live_wave = AudioWaveform("deck")
        self.live_wave.set_live(True)
        self.live_wave.set_hexpand(True)
        self.record_box.append(self.live_wave)
        self.record_box.set_visible(False)
        self.append(self.record_box)
        self._last_peaks: tuple[float, ...] = ()

    def show_audio(self, memo: Memo, position: float = 0.0, *,
                   playing: bool = False, speed: float = 1.0) -> None:
        self.remove_css_class("recording")
        self.record_box.set_visible(False)
        self.transport.set_visible(True)
        self.wave.set_audio(memo.wave, memo.duration)
        # v70 stores cue marks in the sample but does not render them on this deck.
        self.wave.set_marks(())
        self.transport.set_duration(memo.duration)
        self.transport.set_position(position)
        self.transport.set_playing(playing)
        self.transport.set_speed(speed)

    def show_recording(self, elapsed: float, peaks: tuple[float, ...]) -> None:
        self.add_css_class("recording")
        self.transport.set_visible(False)
        self.record_box.set_visible(True)
        self.clock.set_text(format_duration(elapsed))
        self.clock.set_unit(f".{int(elapsed * 10) % 10}")
        if peaks[:len(self._last_peaks)] != self._last_peaks:
            self.live_wave.set_live(True)
            self._last_peaks = ()
        for peak in peaks[len(self._last_peaks):]:
            self.live_wave.push_peak(peak)
        self._last_peaks = peaks

    def set_position(self, position: float, duration: float) -> None:
        if self.transport.duration != duration:
            self.transport.set_duration(duration)
        self.transport.set_position(position)

    def set_playing(self, playing: bool) -> None:
        self.transport.set_playing(playing)


class _TranscriptLayout(Gtk.LayoutManager):
    """Speaker paragraphs as one flow: words wrap on Pango's fractional advances and every line, speaker
    and paragraph sits at its fractional offset, so a long transcript keeps the CSS line pitch (v71 .metx:
    17 px at 1.55 on a phone) instead of gaining a rounded pixel per line. Advances are read at layout
    time, after the type the words carry on this tier has been applied."""

    def do_get_request_mode(self, _widget):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, widget, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return (0, 0, -1, -1)
        width = for_size if for_size > 0 else (widget.get_width() or 360)
        height = widget._flow(width, None)
        return (height, height, -1, -1)

    def do_allocate(self, widget, width, _height, _baseline):
        widget._flow(width, lambda child, x, y, w, h: child.allocate(
            w, h, -1, Gsk.Transform().translate(Graphene.Point().init(x, y))))


def _line_height(label: Gtk.Label) -> float:
    return label.get_layout().get_size()[1] / Pango.SCALE


class _TranscriptFlow(Gtk.Box):
    """The paragraphs of one memo: (speaker label or None, word labels), placed by _TranscriptLayout."""

    def __init__(self, paragraph_gap: float, speaker_gap: float) -> None:
        super().__init__(hexpand=True)
        self.set_name("me-words")
        self.add_css_class("me-words")
        self.paragraphs: list[tuple[Gtk.Label | None, list[Gtk.Label]]] = []
        self.paragraph_gap, self.speaker_gap = paragraph_gap, speaker_gap
        self.set_layout_manager(_TranscriptLayout())

    def _flow(self, width: float, place) -> int:
        y = 0.0
        for index, (speaker, words) in enumerate(self.paragraphs):
            if index:
                y += self.paragraph_gap
            if speaker is not None:
                if place:
                    place(speaker, 0.0, y, max(1, int(width)), speaker.measure(Gtk.Orientation.VERTICAL, -1)[1])
                y += _line_height(speaker) + self.speaker_gap
            if not words:
                continue
            pitch = _line_height(words[0])
            space = words[0].create_pango_layout(" ").get_size()[0] / Pango.SCALE
            x = 0.0
            for word in words:
                advance = word.get_layout().get_size()[0] / Pango.SCALE
                if x and x + space + advance > width:
                    x, y = 0.0, y + pitch
                elif x:
                    x += space
                if place:
                    place(word, x, y, word.measure(Gtk.Orientation.HORIZONTAL, -1)[1],
                          word.measure(Gtk.Orientation.VERTICAL, -1)[1])
                x += advance
            y += pitch
        return int(-(-y // 1))


class TimedTranscript(Gtk.Box):
    """Speaker paragraphs with clickable words; the app owns their timing."""

    def __init__(self, on_seek: Callable[[float], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_name("me-transcript")
        self.add_css_class("me-transcript")
        self.on_seek = on_seek
        self._words: list[tuple[Gtk.Label, float]] = []
        self._paragraph_gap, self._speaker_gap = 17.0, 4.0
        self._flow_box: _TranscriptFlow | None = None

    def set_spacing(self, paragraph: float, speaker: float) -> None:
        """The gap between paragraphs and under a speaker's name (v71: 16 and 2 on a phone)."""
        self._paragraph_gap, self._speaker_gap = paragraph, speaker
        if self._flow_box is not None:
            self._flow_box.paragraph_gap, self._flow_box.speaker_gap = paragraph, speaker
            self._flow_box.queue_resize()

    def _clear_words(self) -> None:
        _clear(self)
        self._flow_box = None
        self._words.clear()

    def show_memo(self, memo: Memo) -> None:
        self._clear_words()
        self.remove_css_class("live")
        if memo.music:
            notice_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            glyph = icons.image("music")
            glyph.set_pixel_size(18)
            glyph.set_valign(Gtk.Align.CENTER)
            glyph.add_css_class("me-no-speech-icon")
            notice_line.append(glyph)
            notice = apply_type(Gtk.Label(label="No speech in this one. It sounds like music, so Memos kept it as audio.",
                                          wrap=True, xalign=0, hexpand=True), "body")
            notice.add_css_class("me-no-speech")
            notice_line.append(notice)
            self.append(notice_line)
            return
        flow = self._flow_box = _TranscriptFlow(self._paragraph_gap, self._speaker_gap)
        previous_speaker = None
        for index, (speaker, _text) in enumerate(memo.transcript):
            person = None
            if speaker != previous_speaker:
                person = apply_type(Gtk.Label(label=speaker, xalign=0), "body")
                person.add_css_class("me-speaker")
                flow.append(person)
            previous_speaker = speaker
            labels = []
            for word in (word for word in memo.words if word.paragraph == index):
                label = apply_type(Gtk.Label(label=word.text), "body", muted=True)
                label.add_css_class("me-word")
                click = Gtk.GestureClick()
                click.connect("released", lambda _c, _n, _x, _y, at=word.at: self.on_seek(at))
                label.add_controller(click)
                flow.append(label)
                labels.append(label)
                self._words.append((label, word.at))
            flow.paragraphs.append((person, labels))
        self.append(flow)

    def show_live(self, words: tuple[str, ...]) -> None:
        self._clear_words()
        self.add_css_class("live")
        # The words as one wrapping label, then the record-red caret (v71 .metx.live, .mecaret).
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        if words:
            said = apply_type(Gtk.Label(label=" ".join(words), wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                        xalign=0), "body")
            said.add_css_class("me-word")
            said.add_css_class("me-live-word")
            line.append(said)
        caret = Gtk.Box(width_request=2, height_request=19, valign=Gtk.Align.END)
        caret.add_css_class("me-live-caret")
        line.append(caret)
        self.append(line)

    def set_position(self, seconds: float, *, playing: bool = False) -> None:
        current = None
        for label, at in self._words:
            (label.add_css_class if at <= seconds else label.remove_css_class)("said")
            label.remove_css_class("current")
            if at <= seconds:
                current = label
        if playing and current is not None:
            current.add_css_class("current")
