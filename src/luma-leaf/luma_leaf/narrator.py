# SPDX-License-Identifier: Apache-2.0
"""The narrator: which sentence is being read, and what happens next.

Any book is an audiobook. The narrator walks the book sentence by sentence —
the page supplies each sentence's text and words, the speaker says them, and
the page shades the sentence and underlines the word — and it is the one
thing the player bar, the dock's media module (MPRIS) and the keyboard all
drive. Previous and next mean a sentence, never ±15 seconds: a book has no
timeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from gi.repository import GLib

from . import sentences
from .speech import SPEEDS, Speaker, Voice

SLEEP_CHOICES = (("off", "No sleep timer"), ("chapter", "End of chapter"), ("15", "15 minutes"), ("30", "30 minutes"))


@dataclass
class NarratorState:
    active: bool = False            # the player is showing
    playing: bool = False
    book_id: str | None = None
    title: str = ""
    author: str = ""
    chapter_label: str = ""
    minutes_left: int = 0
    chapter_progress: float = 0.0
    speed: float = 1.0
    voice: str = ""
    sleep: str = "off"
    can_previous: bool = False
    can_next: bool = True


class Narrator:
    def __init__(self, speaker: Speaker, page, library, locator) -> None:
        """`page` is the reader view: it runs the page's `leaf.sentence` and friends."""
        self.speaker = speaker
        self.page = page
        self.library = library
        self.locator = locator
        self.state = NarratorState()
        self.spine = 0
        self.index = 0
        self._chapter_at_sleep: int | None = None
        self._stop_after_sentence = False
        self._sleep_source = 0
        self._listeners: list = []
        self._generation = 0

    # observers: the player bar and MPRIS
    def subscribe(self, callback):
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def _notify(self) -> None:
        for callback in list(self._listeners):
            callback(self.state)

    # starting and stopping
    def start(self, book, *, spine: int | None = None, index: int | None = None) -> None:
        self.state.active = True
        self.state.book_id = book.id
        self.state.title = getattr(book, "short_title", book.title)
        self.state.author = book.author
        self._generation += 1
        if spine is not None and index is not None:
            self.spine, self.index = spine, index
            self._play()
            return
        generation = self._generation

        def located(result):
            if generation != self._generation:
                return
            result = result or {}
            self.spine = int(result.get("spine", 0))
            self.index = int(result.get("index", 0))
            self._play()

        self.page.call("leaf.currentSentence()", located)

    def _play(self) -> None:
        self.state.playing = True
        self._notify()
        self._speak_current()

    def toggle(self) -> None:
        if not self.state.active:
            return
        if self.state.playing:
            self.pause()
        else:
            self.resume()

    def pause(self) -> None:
        if not self.state.playing:
            return
        self._generation += 1
        self.speaker.cancel()
        self.state.playing = False
        self.page.run("leaf.word(-1)")
        self._notify()

    def resume(self) -> None:
        """Resume restarts the sentence it paused in."""
        if self.state.active and not self.state.playing:
            self._generation += 1
            self._play()

    def stop(self) -> None:
        self._generation += 1
        self.speaker.cancel()
        self._cancel_sleep()
        self.state = NarratorState(speed=self.state.speed, voice=self.state.voice)
        self.page.run("leaf.stopVoice()")
        self._notify()

    def next_sentence(self) -> None:
        if not self.state.active:
            return
        self._generation += 1
        self.index += 1
        self._speak_or_show()

    def previous_sentence(self) -> None:
        if not self.state.active:
            return
        self._generation += 1
        if self.index > 0:
            self.index -= 1
        else:
            previous = self._linear_before(self.spine)
            if previous is None:
                self.index = 0
            else:
                self.spine, self.index = previous, -1
        self._speak_or_show()

    def _speak_or_show(self) -> None:
        if self.state.playing:
            self._speak_current()
        else:
            self._fetch(lambda sentence: self._describe(sentence))

    # settings
    def set_speed(self, speed: float) -> None:
        speed = min(SPEEDS, key=lambda s: abs(s - speed))
        self.state.speed = speed
        self.speaker.set_speed(speed)
        self.library.set_pref("listen.speed", speed)
        self._restart_if_playing()

    def faster(self) -> None:
        position = SPEEDS.index(min(SPEEDS, key=lambda s: abs(s - self.state.speed)))
        self.set_speed(SPEEDS[min(len(SPEEDS) - 1, position + 1)])

    def cycle_speed(self) -> None:
        """The reader bar cycles through Studio's five spoken speeds."""
        position = SPEEDS.index(min(SPEEDS, key=lambda s: abs(s - self.state.speed)))
        self.set_speed(SPEEDS[(position + 1) % len(SPEEDS)])

    def slower(self) -> None:
        position = SPEEDS.index(min(SPEEDS, key=lambda s: abs(s - self.state.speed)))
        self.set_speed(SPEEDS[max(0, position - 1)])

    def set_voice(self, voice: Voice | None) -> None:
        self.speaker.set_voice(voice)
        self.state.voice = voice.label if voice else ""
        if voice:
            self.library.set_pref("listen.voice", voice.id)
        self._restart_if_playing()

    def set_sleep(self, choice: str) -> None:
        self._cancel_sleep()
        self.state.sleep = choice
        self._chapter_at_sleep = None
        self._stop_after_sentence = False
        if choice in ("15", "30"):
            self._sleep_source = GLib.timeout_add_seconds(int(choice) * 60, self._sleep_elapsed)
        self._notify()

    def _sleep_elapsed(self) -> bool:
        self._sleep_source = 0
        self._stop_after_sentence = True
        return GLib.SOURCE_REMOVE

    def _cancel_sleep(self) -> None:
        if self._sleep_source:
            GLib.source_remove(self._sleep_source)
            self._sleep_source = 0

    def _restart_if_playing(self) -> None:
        # A change of speed or voice takes effect at once: restart the sentence.
        if self.state.playing:
            self._generation += 1
            self._speak_current()
        else:
            self._notify()

    # the loop
    def _fetch(self, then) -> None:
        generation = self._generation
        spine, index = self.spine, self.index

        def got(result):
            if generation != self._generation:
                return
            then(result)

        self.page.call(f"leaf.sentence({int(spine)}, {int(index)})", got)

    def _speak_current(self) -> None:
        generation = self._generation

        def got(sentence):
            if generation != self._generation:
                return
            if not sentence:
                self.stop()
                return
            if sentence.get("done"):
                following = self._linear_after(self.spine)
                if following is None:
                    self.stop()   # the end of the book
                    return
                self.spine, self.index = following, 0
                self._speak_current()
                return
            self.spine = int(sentence["spine"])
            self.index = int(sentence["index"])
            place = self._describe(sentence)
            # End of chapter: finish the sentence that crosses into the next
            # chapter, then stop — so stop before the next chapter's first.
            if self.state.sleep == "chapter" and place is not None:
                if self._chapter_at_sleep is None:
                    self._chapter_at_sleep = place.chapter
                elif place.chapter != self._chapter_at_sleep:
                    self.pause()
                    self.set_sleep("off")
                    return
            words = sentence.get("words") or []
            if not words:
                self._advance(generation)
                return
            self.speaker.speak(words,
                               on_word=lambda i: generation == self._generation and self.page.run(f"leaf.word({int(i)})"),
                               on_done=lambda finished: self._finished(generation, finished))

        self._fetch(got)

    def _finished(self, generation: int, finished: bool) -> None:
        if generation != self._generation:
            return
        if not finished:
            self.pause()
            return
        if self._stop_after_sentence:
            self._stop_after_sentence = False
            self.pause()
            self.set_sleep("off")
            return
        self._advance(generation)

    def _advance(self, generation: int) -> None:
        if generation != self._generation or not self.state.playing:
            return
        self.index += 1
        self._speak_current()

    def _describe(self, sentence: dict):
        book = self.library.book(self.state.book_id) if self.state.book_id else None
        place = None
        if book is not None and book.path and sentence.get("cfi"):
            from .position import Stats
            stats = Stats.from_json(book.stats)
            place = self.locator.place(book.path, stats, sentence["cfi"])
            if place is not None:
                self.state.chapter_label = place.chapter_label
                wpm = sentences.LISTENING_WPM * self.state.speed
                self.state.minutes_left = sentences.minutes(place.chapter_words_left, wpm)
                total = self._chapter_words(book, stats, place)
                self.state.chapter_progress = 1 - place.chapter_words_left / total if total else 0.0
        self.state.can_previous = self.index > 0 or self._linear_before(self.spine) is not None
        self._notify()
        return place

    def _chapter_words(self, book, stats, place) -> int:
        chapters = stats.chapters
        if place.chapter < 0 or place.chapter >= len(chapters):
            return 0
        start = (chapters[place.chapter][1], chapters[place.chapter][2])
        end = (chapters[place.chapter + 1][1], chapters[place.chapter + 1][2]) if place.chapter + 1 < len(chapters) \
            else (len(stats.section_words), 0)
        try:
            return self.locator.words_between(book.path, stats, start, end)
        except Exception:
            return 0

    def _linear_after(self, spine: int) -> int | None:
        sections = self.page.sections
        return next((s["index"] for s in sections if s["index"] > spine and s["linear"]), None)

    def _linear_before(self, spine: int) -> int | None:
        sections = self.page.sections
        return next((s["index"] for s in reversed(sections) if s["index"] < spine and s["linear"]), None)
