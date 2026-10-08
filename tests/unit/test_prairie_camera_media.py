# SPDX-License-Identifier: Apache-2.0
"""Temporary SQLite catalogs only; no Photos library or GTK application opens."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/prairie-core"))
from prairie_apps.camera_media import ensure_catalog_backup


class CameraCatalogBackupTests(unittest.TestCase):
    def test_new_catalog_does_not_create_a_backup_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertIsNone(ensure_catalog_backup(root / "missing.sqlite3", root / "backups"))
            self.assertFalse((root / "backups").exists())

    def test_first_snapshot_includes_wal_and_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "photos.sqlite3"
            source = sqlite3.connect(database)
            try:
                source.execute("PRAGMA journal_mode=WAL")
                source.execute("CREATE TABLE assets(id TEXT PRIMARY KEY, favorite INTEGER, caption TEXT)")
                source.execute("INSERT INTO assets VALUES('a',0,'original caption')")
                source.commit()
                self.assertTrue(Path(str(database) + "-wal").is_file())
                saved = ensure_catalog_backup(database, root / "backups")
                before = saved.read_bytes()
                with closing(sqlite3.connect(saved)) as snapshot:
                    self.assertEqual(snapshot.execute("SELECT * FROM assets").fetchall(), [('a', 0, 'original caption')])
                    self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone(), ('ok',))
                self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
                self.assertEqual(saved.parent.stat().st_mode & 0o777, 0o700)
                source.execute("UPDATE assets SET favorite=1 WHERE id='a'")
                source.commit()
                self.assertEqual(ensure_catalog_backup(database, root / "backups"), saved)
                self.assertEqual(saved.read_bytes(), before)
                self.assertEqual(source.execute("SELECT * FROM assets").fetchall(), [('a', 1, 'original caption')])
            finally:
                source.close()

    def test_simultaneous_callers_keep_one_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "photos.sqlite3"
            with closing(sqlite3.connect(database)) as source:
                source.execute("CREATE TABLE saved(value TEXT)")
                source.execute("INSERT INTO saved VALUES('keep')")
                source.commit()
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(ensure_catalog_backup, database, root / "backups") for _ in range(4)]
                saved = [future.result() for future in futures]
            self.assertEqual(len(set(saved)), 1)
            self.assertEqual(len(list((root / "backups").glob("*.sqlite3"))), 1)
            self.assertFalse(list((root / "backups").glob("*.partial")))
            with closing(sqlite3.connect(saved[0])) as snapshot:
                self.assertEqual(snapshot.execute("SELECT * FROM saved").fetchall(), [('keep',)])

    def test_backup_failure_preserves_catalog_and_removes_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "photos.sqlite3"
            database.write_bytes(b"invalid catalog must not be replaced")
            with self.assertRaises(OSError):
                ensure_catalog_backup(database, root / "backups")
            self.assertEqual(database.read_bytes(), b"invalid catalog must not be replaced")
            self.assertFalse(list((root / "backups").glob("*.sqlite3")))
            self.assertFalse(list((root / "backups").glob("*.partial")))


if __name__ == "__main__":
    unittest.main()
