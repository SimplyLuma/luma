# SPDX-License-Identifier: Apache-2.0
"""Folder choices: the device's Music folder and mounted removable drives."""
from pathlib import Path
import tempfile
import unittest

from luma_tide.folders import folder_choices, removable_drives, removable_root


class FolderChoices(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.base = Path(self._directory.name)
        self.run_media = self.base / 'run/media/listener'
        self.media = self.base / 'media'
        self.roots = (self.run_media, self.media / 'listener', self.media)
        self.music = self.base / 'home/Music'
        for path in (self.run_media / 'SD CARD', self.run_media / 'Backup', self.media / 'listener' / 'USB',
                     self.media / 'Legacy', self.music):
            path.mkdir(parents=True)
        (self.run_media / '.hidden').mkdir()
        (self.run_media / 'not-a-drive.txt').write_text('')

    def tearDown(self):
        self._directory.cleanup()

    def test_mounted_drives_in_every_media_root_once(self):
        drives = removable_drives(self.roots)
        self.assertEqual([d.name for d in drives], ['Backup', 'SD CARD', 'USB', 'Legacy'])
        # /media/<user> is a root of its own, never offered as a drive under /media.
        self.assertNotIn(self.media / 'listener', drives)

    def test_missing_roots_offer_nothing(self):
        self.assertEqual(removable_drives((self.base / 'absent',)), [])
        self.assertEqual(folder_choices((), music=self.base / 'absent', roots=(self.base / 'absent',)), [])

    def test_choices_skip_sources_already_in_the_library(self):
        choices = folder_choices([self.music, self.run_media / 'Backup'], music=self.music, roots=self.roots)
        self.assertEqual([c.name for c in choices], ['SD CARD', 'USB', 'Legacy'])
        self.assertTrue(all(c.removable and c.icon == 'hard-drive' for c in choices))

    def test_music_folder_is_offered_first_when_it_is_not_a_source(self):
        choices = folder_choices([], music=self.music, roots=self.roots)
        self.assertEqual(choices[0].path, self.music)
        self.assertFalse(choices[0].removable)
        self.assertEqual(choices[0].icon, 'folder')

    def test_removable_root_names_the_drive_a_folder_is_on(self):
        card = self.run_media / 'SD CARD'
        self.assertEqual(removable_root(card / 'Music/Albums', self.roots), card)
        self.assertEqual(removable_root(card, self.roots), card)
        self.assertIsNone(removable_root(self.run_media, self.roots))
        self.assertIsNone(removable_root(self.music, self.roots))


if __name__ == '__main__':
    unittest.main()
