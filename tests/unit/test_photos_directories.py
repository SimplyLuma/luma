# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/prairie-core'))
from prairie_apps.user_directories import photos_directory
from prairie_apps.photos_backend import PhotoLibrary
spec = importlib.util.spec_from_loader('migration', __import__('importlib.machinery').machinery.SourceFileLoader('migration', str(ROOT / 'src/prairie-core/bin/luma-migrate-photos-directory')))
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class PhotosDirectories(unittest.TestCase):
    def test_media_family_reads_host_user_directory_config_only(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            host = home / 'host-config'; host.mkdir()
            private = home / 'private-config'; private.mkdir()
            (host / 'user-dirs.dirs').write_text('XDG_PICTURES_DIR="$HOME/Family photographs"\n')
            (private / 'user-dirs.dirs').write_text('XDG_PICTURES_DIR="$HOME/Unrelated private folder"\n')
            for app in ('org.projectluma.Camera', 'org.projectluma.Photos'):
                env = {'HOME': str(home), 'FLATPAK_ID': app,
                       'HOST_XDG_CONFIG_HOME': str(host), 'XDG_CONFIG_HOME': str(private)}
                self.assertEqual(photos_directory(env), home / 'Family photographs')
                env['FLATPAK_ID'] = 'org.projectluma.Notes'
                self.assertEqual(photos_directory(env), home / 'Unrelated private folder')

    def test_xdg_config_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp); config = home / '.config'; config.mkdir()
            env = {'HOME': str(home)}
            self.assertEqual(photos_directory(env), home / 'Photos')
            (config / 'user-dirs.dirs').write_text('XDG_PICTURES_DIR="$HOME/My Photos"\n')
            self.assertEqual(photos_directory(env), home / 'My Photos')
            self.assertEqual(photos_directory({**env, 'XDG_PICTURES_DIR': '/media/camera'}), Path('/media/camera'))
            (config / 'user-dirs.dirs').write_text('XDG_PICTURES_DIR="$(touch bad)"\n')
            self.assertEqual(photos_directory(env), home / 'Photos')
            self.assertFalse((home / 'bad').exists())

    def test_migrate_repeat_catalog_identity_and_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp); config = home / '.config'; config.mkdir()
            old = home / 'Pictures'; old.mkdir(); photo = old / 'One photo.jpg'
            photo.write_bytes(b'\xff\xd8\xff\xd9')
            before = 'XDG_PICTURES_DIR="$HOME/Pictures"\nXDG_MUSIC_DIR="$HOME/Music"\n'
            (config / 'user-dirs.dirs').write_text(before)
            library = PhotoLibrary(home / 'library.sqlite3')
            source = library.add_source(old, 'Pictures'); library.scan_source(source.id)
            asset = library.assets()[0]; library.set_favorite(asset.id, True)
            inode = photo.stat().st_ino
            migration.migrate(home, config); migration.migrate(home, config)
            self.assertEqual(photo.stat().st_ino, inode)
            self.assertTrue(old.is_symlink())
            self.assertIn('Pictures', (home / '.hidden').read_text().splitlines())
            with patch.dict(os.environ, {'HOME': str(home), 'XDG_CONFIG_HOME': str(config)}):
                source2 = library.ensure_default_source()
            self.assertEqual(source.id, source2.id)
            library.scan_source(source2.id)
            self.assertEqual(len(library.sources()), 1)
            self.assertEqual(library.assets()[0].id, asset.id)
            self.assertTrue(library.assets()[0].favorite)
            self.assertEqual(library.assets()[0].path, (home / 'Photos/One photo.jpg').resolve())
            migration.migrate(home, config, rollback=True)
            self.assertFalse(old.is_symlink())
            self.assertEqual(photo.stat().st_ino, inode)
            self.assertEqual((config / 'user-dirs.dirs').read_text(), before)
            self.assertIn('Photos', (home / '.hidden').read_text().splitlines())
            self.assertEqual(library.assets()[0].path.read_bytes(), photo.read_bytes())

    def test_refuses_custom_or_existing_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp); config = home / '.config'; config.mkdir()
            (home / 'Pictures').mkdir(); (home / 'Photos').mkdir()
            with self.assertRaises(ValueError): migration.migrate(home, config)
            (home / 'Photos').rmdir()
            (config / 'user-dirs.dirs').write_text('XDG_PICTURES_DIR="/media/private"\n')
            with self.assertRaises(ValueError): migration.migrate(home, config)
            self.assertTrue((home / 'Pictures').is_dir())
            self.assertFalse((home / 'Photos').exists())


    def test_bookmark_labels_uris_repeat_and_rollback(self):
        with tempfile.TemporaryDirectory(prefix='luma home ') as temp:
            home = Path(temp); config = home / '.config'; config.mkdir()
            old = home / 'Pictures'; old.mkdir(); new = home / 'Photos'
            original = (f'{old.as_uri()} Pictures\n'
                        f'{old.as_uri()} Family album\n'
                        f'{old.as_uri()}\n'
                        f'{new.as_uri()} Pictures\n'
                        f'{new.as_uri()} My library\n'
                        f'{(home / "Pictures elsewhere").as_uri()} Pictures\n'
                        'smb://server/Pictures Pictures\n'
                        'file://[invalid Pictures\n')
            expected = (f'{new.as_uri()} Photos\n'
                        f'{new.as_uri()} Family album\n'
                        f'{new.as_uri()}\n'
                        f'{new.as_uri()} Photos\n'
                        f'{new.as_uri()} My library\n'
                        f'{(home / "Pictures elsewhere").as_uri()} Pictures\n'
                        'smb://server/Pictures Pictures\n'
                        'file://[invalid Pictures\n')
            paths = [config / toolkit / 'bookmarks' for toolkit in ('gtk-3.0', 'gtk-4.0')]
            for path in paths:
                path.parent.mkdir(); path.write_text(original)
            migration.migrate(home, config)
            migration.migrate(home, config)
            for path in paths:
                self.assertEqual(path.read_text(), expected)
            migration.migrate(home, config, rollback=True)
            for path in paths:
                self.assertEqual(path.read_text(), original)

    def test_old_journal_bookmark_resume_and_missing_file(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp); config = home / '.config'; config.mkdir()
            old = home / 'Pictures'; old.mkdir()
            migration.migrate(home, config)
            journal = config / 'luma-photos-directory-migration.json'
            state = json.loads(journal.read_text())
            del state['bookmarks']
            journal.write_text(json.dumps(state))
            bookmark = config / 'gtk-3.0/bookmarks'; bookmark.parent.mkdir()
            original = f'{(home / "Photos").as_uri()} Pictures\n'
            bookmark.write_text(original)
            # Simulate interruption after the journal update but before the write.
            real_write = migration.atomic_write
            def interrupted_write(path, content):
                if path == bookmark:
                    raise OSError('interrupted bookmark write')
                return real_write(path, content)
            with patch.object(migration, 'atomic_write', side_effect=interrupted_write):
                with self.assertRaises(OSError): migration.migrate(home, config)
            migration.migrate(home, config)
            self.assertEqual(bookmark.read_text(), f'{(home / "Photos").as_uri()} Photos\n')
            self.assertFalse((config / 'gtk-4.0/bookmarks').exists())
            migration.migrate(home, config, rollback=True)
            self.assertEqual(bookmark.read_text(), original)
            self.assertFalse((config / 'gtk-4.0/bookmarks').exists())


    def test_bookmark_rollback_preserves_later_additions_and_custom_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp); config = home / '.config'; config.mkdir()
            old = home / 'Pictures'; old.mkdir(); new = home / 'Photos'
            bookmark = config / 'gtk-3.0/bookmarks'; bookmark.parent.mkdir()
            bookmark.write_text(f'{old.as_uri()} Pictures\n{old.as_uri()} Camera\n')
            migration.migrate(home, config)
            unrelated = f'{(home / "Projects").as_uri()} Work projects\n'
            # The user renames the first bookmark and adds an unrelated one.
            bookmark.write_text(f'{new.as_uri()} Family moments\n'
                                f'{new.as_uri()} Camera\n' + unrelated)
            migration.migrate(home, config, rollback=True)
            self.assertEqual(bookmark.read_text(),
                             f'{new.as_uri()} Family moments\n'
                             f'{old.as_uri()} Camera\n' + unrelated)

    def test_bookmark_rollback_does_not_resurrect_deleted_file(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp); config = home / '.config'; config.mkdir()
            old = home / 'Pictures'; old.mkdir()
            bookmark = config / 'gtk-4.0/bookmarks'; bookmark.parent.mkdir()
            bookmark.write_text(f'{old.as_uri()} Pictures\n')
            migration.migrate(home, config)
            bookmark.unlink()
            migration.migrate(home, config, rollback=True)
            self.assertFalse(bookmark.exists())
