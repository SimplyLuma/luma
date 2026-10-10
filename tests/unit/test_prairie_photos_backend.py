#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone
import os
import sys
import tempfile
import unittest
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/prairie-core"))
from prairie_apps.photos_backend import (
    PhotoLibrary,
    ThumbnailCache,
    list_photos,
    trash_photo,
    default_database_path,
    _shared_library_mounted,
)


class PhotosBackendTests(unittest.TestCase):
    def test_shared_bind_mount_is_required_even_when_synthetic_home_is_writable(self):
        directory = Path('/home/user/My photos/luma-photos')
        home_only = '10 1 0:1 / /home/user rw - tmpfs tmpfs rw\n'
        self.assertFalse(_shared_library_mounted(directory, home_only))
        family = '11 10 8:1 /luma-photos /home/user/My\\040photos/luma-photos rw - ext4 /dev/sda rw\n'
        self.assertTrue(_shared_library_mounted(directory, home_only + family))
        self.assertFalse(_shared_library_mounted(directory / 'other', family))

    def test_granted_canonical_library_mount_accepts_home_symlink_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_home = root / 'var-home'
            library = canonical_home / '.local/share/luma-photos'
            library.mkdir(parents=True)
            alias_home = root / 'home'
            alias_home.symlink_to(canonical_home, target_is_directory=True)
            mounts = f'11 1 8:1 /luma-photos {library} rw - ext4 /dev/sda rw\n'
            self.assertTrue(_shared_library_mounted(library, mounts))
            self.assertTrue(_shared_library_mounted(alias_home / '.local/share/luma-photos', mounts))
            self.assertFalse(_shared_library_mounted(alias_home / '.local/share/other-library', mounts))

    def test_home_symlink_without_explicit_library_mount_remains_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_home = root / 'var-home'
            library = canonical_home / '.local/share/luma-photos'
            library.mkdir(parents=True)
            alias_home = root / 'home'
            alias_home.symlink_to(canonical_home, target_is_directory=True)
            mounts = f'10 1 0:1 / {canonical_home} rw - tmpfs tmpfs rw\n'
            self.assertFalse(_shared_library_mounted(library, mounts))
            self.assertFalse(_shared_library_mounted(alias_home / '.local/share/luma-photos', mounts))

    def test_camera_and_photos_sandboxes_share_host_catalog_not_private_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            for app in ("org.projectluma.Photos", "org.projectluma.Camera"):
                environment = {"HOME": str(home), "FLATPAK_ID": app,
                               "XDG_DATA_HOME": str(home / ".var/app" / app / "data")}
                self.assertEqual(default_database_path(environment), home / ".local/share/luma-photos/library.sqlite3")
                environment["HOST_XDG_DATA_HOME"] = str(home / "custom-host-data")
                self.assertEqual(default_database_path(environment), home / "custom-host-data/luma-photos/library.sqlite3")
                environment["HOST_XDG_DATA_HOME"] = "relative-unsafe"
                with self.assertRaises(ValueError):
                    default_database_path(environment)
            unrelated = {"HOME": str(home), "FLATPAK_ID": "org.projectluma.Notes",
                         "XDG_DATA_HOME": str(home / "private-data"), "HOST_XDG_DATA_HOME": str(home / "custom-host-data")}
            self.assertEqual(default_database_path(unrelated), home / "private-data/luma-photos/library.sqlite3")

    def test_indexes_images_but_not_symlinks_or_other_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "nested").mkdir(); image = root / "nested" / "photo.JPG"; image.write_bytes(b"\xff\xd8\xff\xd9"); (root / "note.txt").write_text("no"); (root / "fake.jpg").write_bytes(b"not an image"); (root / "link.png").symlink_to(image)
            records = list_photos(root)
            self.assertEqual(tuple(record.path.name for record in records), ("photo.JPG",))

    def test_photo_moves_to_freedesktop_trash_with_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); root = base / "Pictures"; root.mkdir(); photo = root / "proof.jpg"; photo.write_bytes(b"\xff\xd8\xff\xd9")
            destination, info = trash_photo(photo, root, base / "data")
            self.assertFalse(photo.exists()); self.assertEqual(destination.read_bytes(), b"\xff\xd8\xff\xd9"); self.assertIn("[Trash Info]", info.read_text()); self.assertIn("proof.jpg", info.read_text())

    def test_catalog_persists_favorites_albums_and_paths_with_spaces(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "Pictures with spaces"
            root.mkdir()
            (root / "August proof.JPG").write_bytes(b"\xff\xd8\xff\xd9")
            database = base / "state" / "library.sqlite3"

            library = PhotoLibrary(database)
            source = library.add_source(root, "Field disk", "removable")
            result = library.scan_source(source.id)
            self.assertEqual(result.added, 1)
            asset = library.assets()[0]
            self.assertEqual(asset.path, (root / "August proof.JPG").resolve())
            self.assertTrue(asset.at_risk)

            library.set_favorite(asset.id, True)
            album = library.create_album("Field trips")
            library.add_to_album(album.id, (asset.id,))

            reopened = PhotoLibrary(database)
            self.assertTrue(reopened.asset(asset.id).favorite)
            self.assertEqual(reopened.albums()[0].asset_count, 1)
            self.assertEqual(reopened.assets(album_id=album.id)[0].id, asset.id)

    def test_search_indexes_real_dates_albums_sources_and_reachability(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "Pictures"
            root.mkdir()
            photo = root / "August proof.jpg"
            photo.write_bytes(b"\xff\xd8\xffsearchable catalog\xff\xd9")
            timestamp = datetime(2026, 8, 31, 19, 49, tzinfo=timezone.utc).timestamp()
            os.utime(photo, (timestamp, timestamp))
            library = PhotoLibrary(base / "library.sqlite3")
            source = library.add_source(root, "On this device", "local")
            library.scan_source(source.id)
            asset = library.assets()[0]
            album = library.create_album("Design review")
            library.add_to_album(album.id, (asset.id,))

            for query in (
                "August 2026",
                "Design review",
                "On this device",
                "local",
                "in reach",
                "image jpeg",
            ):
                self.assertEqual(library.assets(query=query)[0].id, asset.id)
                page, total = library.page(query=query, limit=1)
                self.assertEqual(total, 1)
                self.assertEqual(page[0].id, asset.id)
            self.assertEqual(library.page(query="no such photograph")[1], 0)
            self.assertEqual(library.collection_counts()["all"], 1)

    def test_summary_counts_all_matching_photos_before_page_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pictures=root/'Pictures';pictures.mkdir()
            for name in ('one','two','three'):(pictures/f'{name}.jpg').write_bytes(b'\xff\xd8\xff'+name.encode()+b'\xff\xd9')
            library=PhotoLibrary(root/'library.sqlite3');source=library.add_source(pictures)
            library.scan_source(source.id)
            with library._connect() as db:
                db.execute("UPDATE assets SET place=display_name")
            summary={};records,total=library.page(limit=1,summary=summary)
            self.assertEqual((len(records),total,summary['places']),(1,3,3))
            self.assertEqual(sum(row['count'] for row in summary['years']),3)
            summary={};records,total=library.page(query='one',limit=1,summary=summary)
            self.assertEqual((len(records),total,summary['places']),(1,1,1))

    def test_scan_releases_write_lock_between_files_and_skips_unchanged(self):
        import sqlite3
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "Pictures"
            root.mkdir()
            first, second = root / "one.jpg", root / "two.jpg"
            for photo in (first, second):
                photo.write_bytes(b"\xff\xd8\xff\xd9")
            library = PhotoLibrary(base / "library.db")
            source = library.add_source(root)

            def walk(*_args):
                yield first
                # A user action can write while the scanner is between files.
                with closing(sqlite3.connect(library.database, timeout=0.1)) as other, other:
                    other.execute("UPDATE assets SET favorite=1")
                yield second

            with patch("prairie_apps.photos_backend._walk_files", walk):
                self.assertEqual(library.scan_source(source.id).added, 2)
            self.assertEqual(library.collection_counts()["favorites"], 1)
            self.assertEqual(library.scan_source(source.id).updated, 0)

    def test_pages_are_bounded_and_searches_cover_later_records(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source_dir = base / "Pictures"
            source_dir.mkdir()
            library = PhotoLibrary(base / "library.db")
            source = library.add_source(source_dir)
            # Catalog-only fixtures: no large image files or decoder workload.
            with library._connect() as db:
                db.executemany(
                    "INSERT INTO assets(id,display_name,media_type,mime_type,modified_at,created_at) VALUES(?,?,'image','image/jpeg',?,?)",
                    ((f"asset-{i:06}", f"Picture {i}", "2026-09-22T12:00:00+00:00", "2026-09-22T12:00:00+00:00") for i in range(1200)))
                db.executemany(
                    "INSERT INTO copies(id,asset_id,source_id,uri,bytes,modified_ns,reachable,last_seen) VALUES(?,?,?,?,100,0,0,?)",
                    ((f"copy-{i}", f"asset-{i:06}", source.id, (source_dir/f"picture-{i}.jpg").as_uri(), "2026-09-22T12:00:00+00:00") for i in range(1200)))
            first, total = library.page(limit=200)
            second, _ = library.page(offset=200, limit=200)
            self.assertEqual((len(first), len(second), total), (200, 200, 1200))
            self.assertFalse({p.id for p in first} & {p.id for p in second})
            found, count = library.page(query="Picture 1199", limit=200)
            self.assertEqual(count, 1)
            self.assertEqual(found[0].id, "asset-001199")
            self.assertEqual(library.collection_counts()["all"], 1200)
            self.assertEqual(library.page(offset=1200)[0], ())
            with self.assertRaises((InterruptedError, __import__('sqlite3').OperationalError)):
                library.page(query="Picture", cancelled=lambda: True)

    def test_source_loss_preserves_last_known_asset_and_return_recovers_it(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "Pictures"
            detached = base / "detached"
            root.mkdir()
            (root / "proof.png").write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + b"\x00\x00\x00\x02\x00\x00\x00\x03"
            )
            library = PhotoLibrary(base / "library.sqlite3")
            source = library.add_source(root)
            library.scan_source(source.id)
            asset_id = library.assets()[0].id

            root.rename(detached)
            result = library.scan_source(source.id)
            self.assertEqual(result.unavailable, 1)
            offline = library.asset(asset_id)
            self.assertFalse(offline.available)
            self.assertEqual(offline.copies[0].path, (root / "proof.png").resolve())

            detached.rename(root)
            library.scan_source(source.id)
            self.assertTrue(library.asset(asset_id).available)

    def test_move_within_source_preserves_logical_asset_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "Pictures"
            root.mkdir()
            original = root / "one.webp"
            original.write_bytes(b"RIFF\x10\x00\x00\x00WEBPVP8X" + b"\x00" * 12)
            library = PhotoLibrary(base / "library.sqlite3")
            source = library.add_source(root)
            library.scan_source(source.id)
            asset_id = library.assets()[0].id

            moved = root / "renamed.webp"
            original.rename(moved)
            library.scan_source(source.id)
            record = library.asset(asset_id)
            self.assertEqual(record.path, moved.resolve())
            self.assertEqual(len(library.assets()), 1)

    def test_lazy_exact_hash_and_thumbnail_location_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "Pictures"
            root.mkdir()
            (root / "proof.jpg").write_bytes(b"\xff\xd8\xffsame bytes\xff\xd9")
            library = PhotoLibrary(base / "library.sqlite3")
            source = library.add_source(root)
            library.scan_source(source.id)
            copy = library.assets()[0].copies[0]

            first = library.exact_hash(copy.id)
            second = library.exact_hash(copy.id)
            self.assertEqual(first, second)
            cache = ThumbnailCache(base / "cache")
            self.assertEqual(cache.path_for_uri(copy.uri), cache.path_for_uri(copy.uri))
            self.assertEqual(cache.path_for_uri(copy.uri).suffix, ".png")

    def test_exact_duplicate_reconciliation_creates_one_asset_with_two_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first = base / "First"
            second = base / "Second"
            first.mkdir()
            second.mkdir()
            payload = b"\xff\xd8\xffidentical original\xff\xd9"
            (first / "proof.jpg").write_bytes(payload)
            (second / "renamed.jpg").write_bytes(payload)
            library = PhotoLibrary(base / "library.sqlite3")
            one = library.add_source(first)
            two = library.add_source(second)
            library.scan_source(one.id)
            library.scan_source(two.id)
            self.assertEqual(len(library.assets()), 2)

            self.assertEqual(library.reconcile_exact_copies(), 1)
            asset = library.assets()[0]
            self.assertEqual(len(asset.copies), 2)
            self.assertEqual({copy.source_id for copy in asset.copies}, {one.id, two.id})

    def test_import_is_atomic_deduplicated_and_preserves_the_source(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            incoming = base / "Camera"
            library_root = base / "Pictures"
            incoming.mkdir()
            original = incoming / "August proof.jpg"
            original.write_bytes(b"\xff\xd8\xffreal import\xff\xd9")
            unsupported = incoming / "notes.txt"
            unsupported.write_text("not media")
            library = PhotoLibrary(base / "library.sqlite3")

            result = library.import_files(
                (original, unsupported), destination=library_root
            )
            self.assertEqual(len(result.completed), 1)
            self.assertEqual(result.unsupported, 1)
            self.assertEqual(original.read_bytes(), b"\xff\xd8\xffreal import\xff\xd9")
            self.assertEqual(len(library.assets()), 1)
            self.assertFalse(tuple(library_root.glob("*.partial")))

            duplicate = library.import_files((original,), destination=library_root)
            self.assertEqual(duplicate.skipped_duplicates, 1)
            self.assertEqual(len(library.assets()), 1)

    def test_export_verifies_original_and_uses_collision_safe_names(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "Pictures"
            output = base / "Export"
            root.mkdir()
            photo = root / "proof.jpg"
            photo.write_bytes(b"\xff\xd8\xffverified export\xff\xd9")
            library = PhotoLibrary(base / "library.sqlite3")
            source = library.add_source(root)
            library.scan_source(source.id)
            asset = library.assets()[0]

            first = library.export_assets((asset.id,), output)
            second = library.export_assets((asset.id,), output)
            self.assertEqual(first.completed[0].name, "proof.jpg")
            self.assertEqual(second.completed[0].name, "proof 2.jpg")
            self.assertEqual(first.completed[0].read_bytes(), photo.read_bytes())

    def test_recently_deleted_survives_reopen_and_restores_original(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "Pictures"
            root.mkdir()
            photo = root / "recover me.jpg"
            payload = b"\xff\xd8\xffrecoverable original\xff\xd9"
            photo.write_bytes(payload)
            database = base / "state" / "library.sqlite3"
            library = PhotoLibrary(database)
            source = library.add_source(root)
            library.scan_source(source.id)
            copy = library.assets()[0].copies[0]

            trashed = library.trash_copy(copy.id, base / "data")
            self.assertFalse(photo.exists())
            self.assertEqual(trashed.read_bytes(), payload)
            self.assertEqual(library.assets(), ())

            reopened = PhotoLibrary(database)
            deleted = reopened.assets(collection="deleted")
            self.assertEqual(len(deleted), 1)
            self.assertTrue(deleted[0].deleted)
            self.assertTrue(deleted[0].copies[0].trashed)
            self.assertEqual(deleted[0].copies[0].original_path, photo.resolve())
            restored = reopened.restore_copy(copy.id)
            self.assertEqual(restored, photo.resolve())
            self.assertEqual(photo.read_bytes(), payload)
            self.assertEqual(reopened.assets(collection="deleted"), ())
            self.assertEqual(len(reopened.assets()), 1)

    def test_restore_preserves_collision_and_permanent_delete_prunes_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "Pictures"
            root.mkdir()
            photo = root / "proof.jpg"
            photo.write_bytes(b"\xff\xd8\xffold original\xff\xd9")
            library = PhotoLibrary(base / "library.sqlite3")
            source = library.add_source(root)
            library.scan_source(source.id)
            copy = library.assets()[0].copies[0]

            library.trash_copy(copy.id, base / "data")
            photo.write_bytes(b"\xff\xd8\xffnew collision\xff\xd9")
            restored = library.restore_copy(copy.id)
            self.assertEqual(restored.name, "proof 2.jpg")
            self.assertEqual(photo.read_bytes(), b"\xff\xd8\xffnew collision\xff\xd9")

            restored_copy = library.assets()[0].copies[0]
            trash_path = library.trash_copy(restored_copy.id, base / "data")
            self.assertTrue(trash_path.exists())
            library.delete_copy_permanently(restored_copy.id)
            self.assertFalse(trash_path.exists())
            self.assertEqual(library.assets(collection="deleted"), ())


if __name__ == "__main__": unittest.main()
