# SPDX-License-Identifier: Apache-2.0
"""GTK-free values and ordering for Tide's v70 library presentation."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Iterable


def duration_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def parse_duration(text: str) -> int:
    minutes, seconds = text.split(":")
    minutes, seconds = int(minutes), int(seconds)
    if minutes < 0 or not 0 <= seconds < 60:
        raise ValueError("invalid duration")
    return minutes * 60 + seconds


@dataclass(frozen=True)
class Song:
    id: str
    title: str
    album_id: str
    duration: int
    number: int
    loved: bool = False
    library_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class Album:
    id: str
    title: str
    artist: str
    year: int
    songs: tuple[Song, ...] = ()
    artwork: str | None = None
    hues: tuple[int, int, int] | None = None
    label: str = ""
    note: str = ""

    @property
    def minutes(self) -> int:
        # JS Math.round, rather than Python's round-to-even.
        return int(sum(song.duration for song in self.songs) / 60 + 0.5)


@dataclass(frozen=True)
class MusicSource:
    id: str
    name: str
    icon: str
    state: str
    subtitle: str
    facts: tuple[tuple[str, str], ...]
    remote: bool = False


@dataclass(frozen=True)
class Library:
    albums: tuple[Album, ...] = ()
    sources: tuple[MusicSource, ...] = ()

    def album(self, identifier: str) -> Album:
        return next(album for album in self.albums if album.id == identifier)

    def source(self, identifier: str) -> MusicSource:
        return next(source for source in self.sources if source.id == identifier)

    def song(self, identifier: str) -> tuple[Album, Song]:
        subject = self.find_song(identifier)
        if subject is None:
            raise StopIteration
        return subject

    @cached_property
    def _song_subjects(self) -> dict[str, tuple[Album, Song]]:
        subjects = {}
        for album in self.albums:
            for song in album.songs:
                subjects.setdefault(song.id, (album, song))
        return subjects

    def find_song(self, identifier: str | None) -> tuple[Album, Song] | None:
        """A player subject may disappear while a source or library is refreshing."""
        return self._song_subjects.get(identifier)

    @property
    def artists(self) -> tuple[tuple[str, tuple[Album, ...]], ...]:
        releases = {}
        for album in self.albums:
            releases.setdefault(album.artist, []).append(album)
        return tuple((name, tuple(albums)) for name, albums in releases.items())


@dataclass(frozen=True)
class Player:
    song_id: str | None = None
    playing: bool = False
    position: int = 0
    volume: int = 70
    shuffle: bool = False
    repeat: bool = False
    queue: tuple[str, ...] = ()
    output: str = "This computer"
    outputs: tuple[tuple[str, str, str], ...] = ()


def search_library(library: Library, query: str) -> tuple[list[tuple[str, tuple[Album, ...]]], list[Album], list[tuple[Album, Song]]]:
    """Group matches by artist, album, then song, with closer names first."""
    terms = query.casefold().split()
    if not terms:
        return [], [], []

    def matches(*values: str) -> bool:
        haystack = ' '.join(values).casefold()
        return all(term in haystack for term in terms)

    def rank(value: str) -> tuple[int, str]:
        name = value.casefold()
        return (0 if name == ' '.join(terms) else 1 if name.startswith(terms[0]) else 2, name)

    artists = [item for item in library.artists if matches(item[0])]
    artists.sort(key=lambda item: rank(item[0]))
    albums = [album for album in library.albums if matches(album.title, album.artist)]
    albums.sort(key=lambda album: (rank(album.title), rank(album.artist)))
    songs = [(album, song) for album in library.albums for song in album.songs
             if matches(song.title, album.title, album.artist)]
    songs.sort(key=lambda item: (rank(item[1].title), rank(item[0].title)))
    return artists, albums, songs


def songs_matching(library: Library, query: str = "", key: str = "album", descending: bool = False
                   ) -> list[tuple[Album, Song]]:
    """v70 matches the whole query, then sorts one column with stable album order."""
    query = query.strip().casefold()
    rows = [(album, song) for album in library.albums for song in album.songs
            if not query or query in f"{song.title} {album.title} {album.artist}".casefold()]
    if key == "time":
        sort_key = lambda row: row[1].duration
    elif key == "title":
        sort_key = lambda row: row[1].title.casefold()
    elif key == "album":
        sort_key = lambda row: (row[0].title.casefold(), row[1].number)
    else:
        raise ValueError(f"unknown song sort {key!r}")
    return sorted(rows, key=sort_key, reverse=descending)


def more_albums(library: Library, current: Album, limit: int = 7) -> tuple[Album, ...]:
    other = [album for album in library.albums if album.id != current.id]
    return tuple((sorted(other, key=lambda album: album.artist != current.artist))[:limit])


SONG_ROW_HEIGHT = 46
#: v71 on a phone: an album's rows are 52 tall; a song list's two-line rows are 60.
PHONE_ALBUM_ROW_HEIGHT = 52
PHONE_SONG_ROW_HEIGHT = 60


def quiet_pick(library: Library, current: Album | None = None) -> Album | None:
    """Listen now's second feature, "For a quiet evening": the album with the longest songs, not the one playing."""
    albums = [album for album in library.albums if album.songs and (current is None or album.id != current.id)]
    return max(albums, key=lambda album: sum(song.duration for song in album.songs) / len(album.songs), default=None)


def song_viewport(count: int, offset: float, height: float, *, buffer: int = 8,
                  row_height: int = SONG_ROW_HEIGHT) -> tuple[int, int]:
    """Rows covering a viewport, including neighbours for smooth scrolling."""
    import math

    first_visible = min(count, max(0, math.floor(offset / row_height)))
    last_visible = min(count, max(0, math.ceil((offset + max(0, height)) / row_height)))
    return max(0, first_visible - buffer), min(count, max(first_visible, last_visible) + buffer)


def next_queue(player: Player) -> tuple[str, ...]:
    if player.song_id not in player.queue:
        return ()
    return player.queue[player.queue.index(player.song_id):]
