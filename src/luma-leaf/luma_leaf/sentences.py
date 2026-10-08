# SPDX-License-Identifier: Apache-2.0
"""Sentences: the unit Leaf highlights, bookmarks and reads aloud in.

A sentence ends after `.`, `!` or `?` (and any closing quote or bracket) when
a capital, a digit or an opening quote follows — never after a title such as
Mr. or St., or "Mr. Bingley" is read as two sentences with a pause between.

The page (data/reader/reader.js) splits with the same rule. This copy exists
so the library can count words and sentences without laying anything out, and
so the rule is tested in one place; tests/test_sentences.py holds both to the
same fixtures.
"""
from __future__ import annotations

import re

ABBREVIATIONS = ("Mr", "Mrs", "Ms", "Dr", "St", "Mt", "Jr", "Sr", "Messrs", "Capt", "Col", "Rev", "No")
_END = re.compile(r"[.!?]+[”’\")\]]*(?=\s+[“‘\"(]?[A-Z0-9])")
_TITLE = re.compile(r"(?:^|[\s“\"(])(?:" + "|".join(ABBREVIATIONS) + r")\.$")
_WORD = re.compile(r"\S+")

# Reading and listening pace, until a reader's own history replaces the first.
READING_WPM = 238
LISTENING_WPM = 160


def boundaries(text: str) -> list[tuple[int, int]]:
    """(start, end) character offsets of each sentence, whitespace trimmed."""
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _END.finditer(text):
        if _TITLE.search(text[max(start, match.start() - 9):match.start() + 1]):
            continue
        end = match.end()
        spans.append((start, end))
        start = end
    spans.append((start, len(text)))
    trimmed = []
    for a, b in spans:
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if b > a:
            trimmed.append((a, b))
    return trimmed


def split(text: str) -> list[str]:
    return [text[a:b] for a, b in boundaries(text)]


def words(text: str) -> int:
    return len(_WORD.findall(text))


def minutes(word_count: int, wpm: float) -> int:
    """Minutes for this many words, never less than one while any remain."""
    if word_count <= 0:
        return 0
    return max(1, round(word_count / wpm))


def hours_and_minutes(word_count: int, wpm: float) -> str:
    total = round(word_count / wpm) if word_count > 0 else 0
    hours, rest = divmod(total, 60)
    if hours:
        return f"{hours} h {rest} min" if rest else f"{hours} h"
    return f"{rest} min"
