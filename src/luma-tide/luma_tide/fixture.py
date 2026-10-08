# SPDX-License-Identifier: Apache-2.0
"""The v70 sample, entirely in memory; no database, keyring, audio or sync.

Artwork is read from the fixture directory or LUMA_TIDE_ARTWORK_ROOT.
Reference artwork may be read locally, but is never copied into source.
"""
from __future__ import annotations

import json
import os
import random
from dataclasses import replace
from pathlib import Path

from .presentation import Album, Library, MusicSource, Player, Song


class FixtureLibrary:
    fixture = True

    def __init__(self, path: Path):
        path = Path(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        loved = set(raw.get("loved", ()))
        artwork_root = Path(os.environ.get("LUMA_TIDE_ARTWORK_ROOT",
            str(Path(os.environ["LUMAUI_CONFORM_STUDIO_ROOT"]) / "social-luma/photos")
            if os.environ.get("LUMAUI_CONFORM_STUDIO_ROOT") else str(path.parent)))
        albums = []
        for item in raw["albums"]:
            songs = tuple(Song(f"{item['id']}:{index}", title, item["id"], self._seconds(duration),
                               index + 1, f"{item['id']}:{index}" in loved)
                          for index, (title, duration) in enumerate(item.get("tr", ())))
            artwork = str(artwork_root / item["img"]) if item.get("img") else None
            albums.append(Album(item["id"], item["t"], item["a"], item["y"], songs, artwork,
                                (item["h"], item["h2"], item["h3"]), item.get("label", ""), item.get("note", "")))
        if not albums or len({a.id for a in albums}) != len(albums):
            raise ValueError("fixture must contain albums with unique identifiers")
        sources = tuple(MusicSource(s["id"], s["name"], s["icon"], s["state"], s["subtitle"],
                                    tuple(tuple(row) for row in s["facts"]), s.get("remote", False))
                        for s in raw["sources"])
        self.library = Library(tuple(albums), sources)
        player = raw["player"]
        album, song = self.library.song(player["song"])
        self.player = Player(song.id, player["playing"], player["position"], player["volume"],
                             player["shuffle"], player["repeat"], tuple(s.id for s in album.songs),
                             player["output"], tuple(tuple(row) for row in player["outputs"]))
        self.view, self.album_id = raw.get("view", "album"), raw.get("album", album.id)
        self._listeners = []
        self.album_loves: set[str] = set()

    @staticmethod
    def _seconds(text):
        from .presentation import parse_duration
        return parse_duration(text)

    def load(self):
        return self.library

    def subscribe(self, callback):
        self._listeners.append(callback)
        callback(self.player)
        return lambda: self._listeners.remove(callback)

    def _publish(self, **changes):
        self.player = replace(self.player, **changes)
        for callback in tuple(self._listeners):
            callback(self.player)

    def play(self, album: Album, song: Song | None = None, shuffle=False):
        song = song or (random.choice(album.songs) if shuffle else album.songs[0])
        if song.id == self.player.song_id and not shuffle:
            self.toggle()
        else:
            self._publish(song_id=song.id, playing=True, position=0, shuffle=shuffle,
                          queue=tuple(s.id for s in album.songs))

    def advance(self):
        """The mock player's clock; never starts an audio engine."""
        if self.player.playing and self.player.song_id:
            _album, song = self.library.song(self.player.song_id)
            if self.player.position + 1 >= song.duration:
                self.step(1)
            else:
                self._publish(position=self.player.position + 1)
        return True

    def toggle(self):
        self._publish(playing=not self.player.playing)

    def step(self, delta):
        if delta < 0 and self.player.position > 3:
            self.seek(0)
            return
        queue = self.player.queue
        if not queue or self.player.song_id not in queue:
            return
        index = queue.index(self.player.song_id) + delta
        if self.player.shuffle and delta > 0:
            index = random.randrange(len(queue))
        if index >= len(queue) and not self.player.repeat:
            self._publish(playing=False, position=0)
            return
        self._publish(song_id=queue[max(0, index) % len(queue)], position=0, playing=True)

    def seek(self, seconds):
        _, song = self.library.song(self.player.song_id)
        self._publish(position=min(song.duration, max(0, int(seconds))))

    def set_volume(self, value):
        self._publish(volume=max(0, min(100, int(value))))

    def shuffle(self):
        self._publish(shuffle=not self.player.shuffle)

    def repeat(self):
        self._publish(repeat=not self.player.repeat)

    def love(self, song_id):
        self.library = replace(self.library, albums=tuple(
            replace(album, songs=tuple(replace(song, loved=not song.loved) if song.id == song_id else song
                                      for song in album.songs)) for album in self.library.albums))
        self._publish()

    def love_album(self, album_id):
        if album_id in self.album_loves:
            self.album_loves.remove(album_id)
        else:
            self.album_loves.add(album_id)

    def output(self, name):
        if name not in {item[0] for item in self.player.outputs}:
            raise ValueError("unknown output")
        self._publish(output=name)

    def clear_queue(self):
        # Keep the playing subject; remove only what comes after it.
        self._publish(queue=(self.player.song_id,) if self.player.song_id else ())

    def sync(self, source_id):
        sources = tuple(replace(source, state="ok", subtitle="Kept offline · 41 songs",
                                facts=(("Kept offline", "41 songs · 338 MB"),))
                        if source.id == "dl" and source.id == source_id else
                        replace(source, state="ok") if source.id == source_id else source
                        for source in self.library.sources)
        self.library = replace(self.library, sources=sources)

    def sign_out(self, source_id):
        self.library = replace(self.library, sources=tuple(
            replace(source, state="bad", subtitle="Signed out",
                    facts=tuple(row for row in source.facts if row[0] != "Password"))
            if source.id == source_id else source for source in self.library.sources))

    def remove_source(self, source_id):
        self.library = replace(self.library, sources=tuple(s for s in self.library.sources if s.id != source_id))

    def connect(self, address, username, password, source_id=None):
        if not address.strip() or not username.strip():
            raise ValueError("Enter a server address and username.")
        identifier = source_id or f"n{len(self.library.sources)}"
        source = MusicSource(identifier, "Navidrome", "server", "ok", "Up to date", (
            ("Server", address.removeprefix("https://").removeprefix("http://").split("/")[0]),
            ("Account", username), ("Songs", "19,333" if source_id else "Counting"),
            ("Last sync", "Now"), ("Password", "In your keyring")), True)
        # Password is deliberately never stored, even in the fixture object.
        sources = list(self.library.sources)
        if source_id:
            sources = [source if s.id == source_id else s for s in sources]
        else:
            sources.insert(1, source)
        self.library = replace(self.library, sources=tuple(sources))

    def close(self):
        self._listeners.clear()
