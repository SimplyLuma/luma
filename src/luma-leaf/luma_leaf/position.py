# SPDX-License-Identifier: Apache-2.0
"""One record per book; every figure is derived from it.

A book is its text plus one position. Chapter, percent, minutes left in the
chapter and time to listen are computed here, from the saved CFI and the
book's word counts, every time something asks — the library hero, the grid,
the page's foot and rail, the contents panel and the narrator all call the
same function, so they cannot disagree.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re

from . import cfi, sentences
from .epub import Epub, EpubError


@dataclass(frozen=True)
class Stats:
    """Counted once at import; describes the text, not the reader."""
    section_words: tuple[int, ...]
    chapters: tuple[tuple[str, int, int, int, str], ...]   # (label, spine, char offset, depth, title)

    @property
    def total_words(self) -> int:
        return sum(self.section_words)

    def to_json(self) -> str:
        return json.dumps({"section_words": list(self.section_words),
                           "chapters": [list(chapter) for chapter in self.chapters]})

    @classmethod
    def from_json(cls, value: str | None) -> "Stats":
        data = json.loads(value or "{}")
        return cls(tuple(data.get("section_words", ())),
                   tuple(tuple(chapter) for chapter in data.get("chapters", ())))


def count(book: Epub) -> Stats:
    words = []
    for section in book.sections:
        try:
            words.append(book.text(section.index).words if section.linear else 0)
        except (KeyError, EpubError):
            words.append(0)
    chapters = []
    for entry in book.toc():
        try:
            offset = book.text(entry.spine).anchors.get(entry.fragment, 0) if entry.fragment else 0
        except (KeyError, EpubError):
            offset = 0
        label, title = chapter_label(entry.label)
        chapters.append((label, entry.spine, offset, entry.depth, title))
    chapters.sort(key=lambda chapter: (chapter[1], chapter[2]))
    return Stats(tuple(words), tuple(chapters))


_KEYWORD = re.compile(r"\b(chapter|letter|book|part|stave|volume|canto|act)\s+"
                      r"([ivxlcdm]+|\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                      r"the\s+\w+)\b[.:]?\s*(.*)$", re.IGNORECASE)
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_WORDS = {w: i + 1 for i, w in enumerate("one two three four five six seven eight nine ten eleven twelve".split())}


def _roman(value: str) -> int | None:
    value = value.lower()
    if not value or any(c not in _ROMAN for c in value):
        return None
    total = 0
    for current, following in zip(value, value[1:] + " "):
        number = _ROMAN[current]
        total += -number if following in _ROMAN and _ROMAN[following] > number else number
    return total


def _tidy(text: str) -> str:
    text = " ".join(text.split()).strip(" .:—–-")
    if text.isupper() and len(text) > 3 and any(len(word) > 1 for word in text.split()):
        small = {"a", "an", "and", "the", "of", "in", "on", "to", "or", "for", "at", "by", "with"}
        words = text.lower().split()
        text = " ".join(w if i and w in small else w[:1].upper() + w[1:] for i, w in enumerate(words))
    return text


def chapter_label(raw: str) -> tuple[str, str]:
    """A table-of-contents entry as the short label the page shows, and its title.

    "I hope Mr. Bingley will like it. CHAPTER II." → ("Chapter 2", "");
    "CHAPTER I. The Period" → ("Chapter 1", "The Period"). An entry that is not
    a numbered division keeps its own words.
    """
    raw = " ".join((raw or "").split())
    match = _KEYWORD.search(raw)
    if match is None:
        return _tidy(raw) or "Untitled", ""
    keyword, number, rest = match.groups()
    number_value = _roman(number) if not number.isdigit() else int(number)
    if number_value is None:
        number_value = _WORDS.get(number.lower())
    shown = str(number_value) if number_value is not None else _tidy(number)
    return f"{keyword.title()} {shown}", _tidy(rest)


@dataclass(frozen=True)
class Place:
    chapter: int            # index into Stats.chapters, -1 before the first
    chapter_label: str
    chapter_count: int
    words_before: int       # words of the whole book before the position
    chapter_words_left: int
    fraction: float

    @property
    def percent(self) -> int:
        return int(self.fraction * 100)

    def minutes_left_in_chapter(self, wpm: float = sentences.READING_WPM) -> int:
        return sentences.minutes(self.chapter_words_left, wpm)

    def status(self) -> str:
        return f"{self.chapter_label} of {self.chapter_count}"


def locate(stats: Stats, spine: int, section_words_before: int, section_offset: int) -> Place:
    total = max(1, stats.total_words)
    before = sum(stats.section_words[:max(0, spine)]) + section_words_before
    chapter = -1
    for index, (_label, c_spine, c_offset, _depth, _title) in enumerate(stats.chapters):
        if (c_spine, c_offset) <= (spine, section_offset):
            chapter = index
        else:
            break
    label = stats.chapters[chapter][0] if chapter >= 0 else (stats.chapters[0][0] if stats.chapters else "Start")
    return Place(chapter=chapter, chapter_label=label, chapter_count=len(stats.chapters),
                 words_before=before, chapter_words_left=0, fraction=min(1.0, before / total))


class Locator:
    """Resolves saved CFIs against a book's text, caching the parse."""

    def __init__(self) -> None:
        self._books: dict[str, Epub] = {}
        self._cache: dict[tuple[str, str], Place] = {}

    def _book(self, path: str) -> Epub:
        if path not in self._books:
            if len(self._books) > 6:
                self._books.pop(next(iter(self._books)))
            self._books[path] = Epub(path)
        return self._books[path]

    def place(self, path: str, stats: Stats, position: str | None) -> Place | None:
        if not position:
            return None
        key = (path, position)
        if key in self._cache:
            return self._cache[key]
        try:
            book = self._book(path)
            point, _end = cfi.parse(position)
            # A position from another edition, or synced from a device holding
            # a different file, can name a section this book does not have.
            if not 0 <= point.spine < len(book.sections):
                return None
            offset = cfi.character_offset(book, point)
            text = book.text(point.spine)
            place = locate(stats, point.spine, text.words_before(offset), offset)
            place = _with_chapter_left(place, stats, book, point.spine, offset)
        except (OSError, ValueError, KeyError, IndexError, EpubError):
            return None
        self._cache[key] = place
        return place

    def chapter_minutes(self, path: str, stats: Stats) -> list[int]:
        """Reading minutes of every chapter, for the contents panel."""
        book = self._book(path)
        spans = []
        starts = [(chapter[1], chapter[2]) for chapter in stats.chapters] + [(len(stats.section_words), 0)]
        for (spine_a, offset_a), (spine_b, offset_b) in zip(starts, starts[1:]):
            spans.append(sentences.minutes(_words_between(book, stats, spine_a, offset_a, spine_b, offset_b),
                                           sentences.READING_WPM))
        return spans

    def words_between(self, path: str, stats: Stats, a: tuple[int, int], b: tuple[int, int]) -> int:
        return _words_between(self._book(path), stats, a[0], a[1], b[0], b[1])


def _words_between(book: Epub, stats: Stats, spine_a: int, offset_a: int, spine_b: int, offset_b: int) -> int:
    if (spine_b, offset_b) <= (spine_a, offset_a):
        return 0
    if spine_a == spine_b:
        text = book.text(spine_a).text
        return sentences.words(text[offset_a:offset_b])
    total = sentences.words(book.text(spine_a).text[offset_a:]) if spine_a < len(book.sections) else 0
    total += sum(stats.section_words[spine_a + 1:spine_b])
    if spine_b < len(book.sections):
        total += sentences.words(book.text(spine_b).text[:offset_b])
    return total


def _with_chapter_left(place: Place, stats: Stats, book: Epub, spine: int, offset: int) -> Place:
    following = place.chapter + 1
    if following < len(stats.chapters):
        end = (stats.chapters[following][1], stats.chapters[following][2])
    else:
        end = (len(stats.section_words), 0)
    left = _words_between(book, stats, spine, offset, end[0], end[1])
    return Place(place.chapter, place.chapter_label, place.chapter_count, place.words_before, left, place.fraction)
