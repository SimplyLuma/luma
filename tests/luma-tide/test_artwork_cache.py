# SPDX-License-Identifier: Apache-2.0
"""Tracks that share embedded album art all index, however many at once.

The artwork cache is content-addressed: every track of an album maps to one
file. Metadata workers extract in parallel, and the temporary file each wrote
was named from the digest and the process id alone, so two workers holding the
same picture collided on it and the second track failed to index.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from luma_tide.indexer import LibraryIndexer
from luma_tide.metadata import MutagenMetadataReader
from luma_tide.model import LibraryStore

try:
    import mutagen  # noqa: F401

    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

COVER = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 64


def _media(data: bytes = COVER) -> SimpleNamespace:
    return SimpleNamespace(pictures=[SimpleNamespace(data=data, mime="image/png")])


class ArtworkCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.cache = Path(self.temporary.name) / "artwork"
        self.reader = MutagenMetadataReader(cache_root=self.cache)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_parallel_extractions_of_one_picture_all_succeed(self) -> None:
        workers = 16
        barrier = threading.Barrier(workers)
        results: list[str | None] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def extract() -> None:
            try:
                barrier.wait()
                uri = self.reader._extract_artwork(_media(), {})
                with lock:
                    results.append(uri)
            except BaseException as error:  # noqa: BLE001 - recorded for the assertion
                with lock:
                    errors.append(error)

        threads = [threading.Thread(target=extract) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), workers)
        self.assertEqual(len(set(results)), 1)
        digest = hashlib.sha256(COVER).hexdigest()
        self.assertEqual(sorted(p.name for p in self.cache.iterdir()), [f"{digest}.png"])
        self.assertEqual((self.cache / f"{digest}.png").read_bytes(), COVER)

    def test_a_leftover_temporary_file_does_not_block_extraction(self) -> None:
        digest = hashlib.sha256(COVER).hexdigest()
        # The name the old code always used in this process.
        (self.cache / f".{digest}.{os.getpid()}.tmp").write_bytes(b"partial")
        uri = self.reader._extract_artwork(_media(), {})
        self.assertEqual(uri, (self.cache / f"{digest}.png").as_uri())

    def test_an_unwritable_cache_keeps_the_track(self) -> None:
        with mock.patch("luma_tide.metadata.tempfile.mkstemp", side_effect=OSError(28, "No space left")):
            with self.assertLogs("tide", level="WARNING"):
                self.assertIsNone(self.reader._extract_artwork(_media(), {}))
        self.assertEqual([p for p in self.cache.iterdir()], [])


@unittest.skipUnless(MUTAGEN_AVAILABLE, "mutagen is not installed in this environment")
class SharedAlbumArtIndexTests(unittest.TestCase):
    def test_every_track_of_an_album_with_one_cover_is_indexed(self) -> None:
        from mutagen.flac import Picture

        from test_metadata import _make_flac

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            music = root / "Music"
            music.mkdir()
            tracks = 24
            for number in range(tracks):
                flac = _make_flac(music / f"{number:02d} Track.flac")
                flac["title"] = [f"Track {number}"]
                flac["album"] = ["One Cover"]
                picture = Picture()
                picture.type = 3
                picture.mime = "image/png"
                picture.data = COVER
                flac.add_picture(picture)
                flac.save()
            store = LibraryStore(root / "library.db")
            source = store.add_source("This device", "local-folder", music, local=True)
            reader = MutagenMetadataReader(cache_root=root / "artwork")
            indexer = LibraryIndexer(store, reader, workers=8)
            try:
                result = indexer.scan_async(source.id).result(timeout=60)
                self.assertEqual((result.discovered, result.indexed, result.failed), (tracks, tracks, 0),
                                 result.errors)
                self.assertEqual(len({track.title for track in store.tracks()}), tracks)
                self.assertEqual(len(list((root / "artwork").glob("*.png"))), 1)
                self.assertEqual(list((root / "artwork").glob(".*.tmp")), [])
            finally:
                indexer.close()
                store.close()


if __name__ == "__main__":
    unittest.main()
