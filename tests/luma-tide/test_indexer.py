# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import tempfile
from dataclasses import replace
import unittest
import wave
from pathlib import Path

from luma_tide.indexer import LibraryIndexer
from luma_tide.metadata import BasicMetadataReader
from luma_tide.model import CopyAvailability, LibraryStore


class IndexerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.music = self.root / "Music"
        self.music.mkdir()
        self.store = LibraryStore(self.root / "library.db")
        self.source = self.store.add_source("This device", "local-folder", self.music, local=True)
        self.indexer = LibraryIndexer(self.store, BasicMetadataReader(), workers=2)

    def tearDown(self) -> None:
        self.indexer.close()
        self.store.close()
        self.temporary.cleanup()

    @staticmethod
    def _wav(path: Path, frames: int = 8000) -> None:
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b"\0\0" * frames)

    def test_real_scan_indexes_audio_and_marks_disappeared_copy_unavailable(self) -> None:
        path = self.music / "Harbour Light.wav"
        self._wav(path)
        result = self.indexer.scan_async(self.source.id).result(timeout=10)
        self.assertEqual((result.discovered, result.indexed, result.failed), (1, 1, 0))
        track = self.store.tracks()[0]
        self.assertEqual(track.title, "Harbour Light")
        self.assertEqual(track.duration_ns, 1_000_000_000)
        unchanged = self.indexer.scan_async(self.source.id).result(timeout=10)
        self.assertEqual((unchanged.indexed, unchanged.unchanged), (0, 1))
        path.unlink()
        result = self.indexer.scan_async(self.source.id).result(timeout=10)
        self.assertEqual(result.missing, 1)
        self.assertEqual(
            self.store.copies_for_track(track.id)[0].availability,
            CopyAvailability.UNAVAILABLE,
        )

    def test_explicit_open_refreshes_unchanged_file_but_not_neighbor(self) -> None:
        # Indexing regression: reader fallback, not an MP3 decoding test.
        path = self.music / "6 Money.mp3"
        path.write_bytes(b"unchanged media fixture")
        neighbor = self.music / "Neighbor.mp3"
        neighbor.write_bytes(b"different unchanged media fixture")
        old = replace(self.indexer.reader.read(path), title="6%20Money")
        track, copy_id = self.store.upsert_copy(self.source.id, old)
        self.store.upsert_copy(self.source.id, self.indexer.reader.read(neighbor))
        playlist = self.store.create_playlist("Preserved")
        self.store.set_playlist_tracks(playlist, [track.id])
        self.store.replace_queue([track.id])
        before = path.stat()
        cached = self.indexer.scan_async(self.source.id).result(timeout=10)
        self.assertEqual((cached.indexed, cached.unchanged), (0, 2))
        self.assertEqual(self.store.track(track.id).title, "6%20Money")
        refreshed = self.indexer.scan_async(
            self.source.id, refresh_paths=[path]
        ).result(timeout=10)
        self.assertEqual((refreshed.indexed, refreshed.unchanged, refreshed.failed), (1, 1, 0))
        self.assertEqual(self.store.track(track.id).title, "6 Money")
        self.assertEqual(self.store.copies_for_track(track.id)[0].id, copy_id)
        self.assertEqual(self.store.queue()[0].track.id, track.id)
        self.assertEqual(self.store.playlist_tracks(playlist)[0].id, track.id)
        self.assertEqual((path.stat().st_size, path.stat().st_mtime_ns), (before.st_size, before.st_mtime_ns))
        cached = self.indexer.scan_async(self.source.id).result(timeout=10)
        self.assertEqual((cached.indexed, cached.unchanged), (0, 2))

    def test_non_audio_files_are_ignored(self) -> None:
        (self.music / "notes.txt").write_text("not music")
        result = self.indexer.scan_async(self.source.id).result(timeout=10)
        self.assertEqual(result.discovered, 0)
        self.assertEqual(self.store.tracks(), [])


if __name__ == "__main__":
    unittest.main()
