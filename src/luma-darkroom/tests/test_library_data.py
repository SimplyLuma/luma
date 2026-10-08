# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from luma_darkroom.library_data import load_fixture, load_real_library
from luma_darkroom.library_metadata import MetadataStore


FIXTURE = (Path(__file__).resolve().parents[1] if (Path(__file__).resolve().parents[1]/'fixtures').is_dir() else Path(__file__).resolve().parents[3]) / ("fixtures/darkroom-v70.json" if (Path(__file__).resolve().parents[1]/"fixtures").is_dir() else "tests/fixtures/darkroom-v70.json")


class LibraryFixtureTests(unittest.TestCase):
    def test_v70_library_has_real_sample_images_and_exact_visible_metadata(self) -> None:
        library = load_fixture(FIXTURE)
        self.assertTrue(library.fixture)
        self.assertEqual(len(library.photos), 18)
        self.assertEqual((library.total_count, library.pick_count), (1284, 212))
        self.assertEqual((library.current().name, library.current().stars), ("Terrace", 4))
        self.assertTrue(library.current().edited)
        self.assertEqual(library.photos[1].look, "Portra")
        self.assertEqual(tuple(map(len, library.current().histogram)), (64, 64, 64))
        self.assertEqual(library.indices_for("shoot:Terrace"), list(range(18)))
        self.assertEqual(library.indices_for("picks"), [i for i, photo in enumerate(library.photos) if photo.flag == 1])
        self.assertTrue(all(photo.path.is_file() for photo in library.photos))

    def test_fixture_rejects_paths_outside_its_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = json.loads(FIXTURE.read_text(encoding="utf-8"))
            data["photos"][0]["image"] = "../outside.webp"
            fixture = root / "fixture.json"
            fixture.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_fixture(fixture)

    def test_real_library_reads_photos_database_without_writing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            database = data / "luma-photos/library.sqlite3"
            database.parent.mkdir(parents=True)
            image = root / "photo.webp"
            image.write_bytes(b"sample")
            with sqlite3.connect(database) as connection:
                connection.executescript("""
                    CREATE TABLE assets(id TEXT, display_name TEXT, captured_at TEXT, modified_at TEXT, media_type TEXT);
                    CREATE TABLE copies(asset_id TEXT, source_id TEXT, uri TEXT, trashed INT, reachable INT, modified_ns INT);
                    CREATE TABLE sources(id TEXT, name TEXT);
                    CREATE TABLE albums(id TEXT, name TEXT);
                    CREATE TABLE album_assets(album_id TEXT, asset_id TEXT);
                """)
                connection.execute("INSERT INTO assets VALUES(?,?,?,?,?)",
                                   ("a1", "Terrace", "2026-09-21T08:10:00", "2026-09-21T08:10:00", "image"))
                connection.execute("INSERT INTO copies VALUES(?,?,?,?,?,?)",
                                   ("a1", "s1", image.as_uri(), 0, 1, 1))
                connection.execute("INSERT INTO sources VALUES(?,?)", ("s1", "Luma SSD"))
                connection.execute("INSERT INTO albums VALUES(?,?)", ("album-1", "Portfolio"))
                connection.execute("INSERT INTO album_assets VALUES(?,?)", ("album-1", "a1"))
            before = database.read_bytes()
            metadata = MetadataStore(data / "luma-darkroom/library-metadata.json")
            metadata.update("a1", {"stars": 4, "flag": 1})
            library = load_real_library(metadata, {"HOME": str(root), "XDG_DATA_HOME": str(data)})
            self.assertEqual((library.source, library.total_count, library.pick_count), ("Luma SSD", 1, 1))
            self.assertEqual((library.current().name, library.current().stars), ("Terrace", 4))
            self.assertEqual(library.albums, (("Portfolio", 1),))
            self.assertEqual(library.indices_for("album:Portfolio"), [0])
            self.assertEqual(library.indices_for("picks"), [0])
            self.assertEqual(library.indices_for("last-import"), [0])
            self.assertEqual(database.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
