# SPDX-License-Identifier: Apache-2.0
"""Pure Memos presentation data, including the read-only Studio fixture.

The fixture contains only v70's five sample recordings. It never opens a
recording directory, starts audio, or writes to a user's library.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class Word:
    text: str
    speaker: str
    paragraph: int
    at: float


@dataclass(frozen=True)
class Memo:
    id: str
    title: str
    when: str
    group: str
    duration: float
    place: str
    favourite: bool
    marks: tuple[float, ...]
    transcript: tuple[tuple[str, str], ...]
    words: tuple[Word, ...]
    wave: tuple[float, ...]
    music: bool = False

    def matches(self, query: str) -> bool:
        needle = query.casefold().strip()
        return not needle or needle in (self.title + " " + " ".join(text for _, text in self.transcript)).casefold()

    def search_excerpt(self, query: str) -> tuple[str, str, str] | None:
        """Text before, at and after the first transcript hit, as v70's row shows."""
        needle = query.casefold().strip()
        if not needle:
            return None
        full = " ".join(text for _, text in self.transcript)
        index = full.casefold().find(needle)
        if index < 0:
            return None
        start = max(0, index - 24)
        return ("…" + full[start:index], full[index:index + len(needle)],
                full[index + len(needle):index + len(needle) + 30] + "…")


def filter_memos(memos: tuple[Memo, ...], view: str, query: str = "") -> tuple[Memo, ...]:
    if view not in {"all", "fav", "deleted"}:
        raise ValueError(f"unknown Memos view: {view}")
    return tuple(m for m in memos if (view != "fav" or m.favourite) and m.matches(query))


def format_duration(seconds: float) -> str:
    seconds = max(0, math.floor(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _v70_wave(identifier: str, count: int = 160, music: bool = False) -> tuple[float, ...]:
    """v70's deterministic sample peaks. Call only for fixture data."""
    state = 0
    for character in identifier:
        state = (state * 31 + ord(character)) & 0xffffffff

    def draw() -> float:
        nonlocal state
        # JavaScript rounds the Number multiplication and then the addition
        # separately before >>> 0 converts it to uint32.
        state = int(float(float(state * 1103515245) + 12345)) & 0xffffffff
        return state / 4294967296

    volume = 0.4
    peaks = []
    for index in range(count):
        volume = max(0.08, min(1.0, volume + (draw() - 0.5) * (0.25 if music else 0.5)))
        gap = 0 if music else (0.06 if draw() < 0.06 else 0)
        peaks.append(gap or volume * (0.55 + 0.45 * math.sin(index / (3 if music else 7)) ** 2))
    return tuple(peaks)


def load_fixture(path: str | Path) -> tuple[str, tuple[Memo, ...], tuple[Memo, ...]]:
    """Load the explicit conform fixture; never fall back to a real store."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))

    def parse(items: list[dict]) -> tuple[Memo, ...]:
        result = []
        for item in items:
            transcript = tuple((str(who), str(text)) for who, text in item.get("tx", []))
            pieces = [(speaker, paragraph, word)
                      for paragraph, (speaker, text) in enumerate(transcript)
                      for word in text.split(" ")]
            duration = float(item["dur"])
            words = tuple(Word(word, speaker, paragraph,
                               (index + 0.5) / max(1, len(pieces)) * duration * 0.96)
                          for index, (speaker, paragraph, word) in enumerate(pieces))
            result.append(Memo(
                id=str(item["id"]), title=str(item["t"]), when=str(item["when"]),
                group=str(item["grp"]), duration=duration, place=str(item.get("where", "")),
                favourite=bool(item.get("fav", False)),
                marks=tuple(float(value) for value in item.get("marks", [])),
                transcript=transcript, words=words,
                wave=_v70_wave(str(item["id"]), music=bool(item.get("music", False))),
                music=bool(item.get("music", False))))
        return tuple(result)

    return str(payload["selected"]), parse(payload["memos"]), parse(payload["deleted"])
