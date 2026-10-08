# SPDX-License-Identifier: Apache-2.0
"""Playback commands keep the current subject and seek position when pausing."""
import unittest
from pathlib import Path
from types import SimpleNamespace

from luma_tide.fixture import FixtureLibrary
from luma_tide.ui import TideWindow


class PlaybackCommandTests(unittest.TestCase):
    def setUp(self):
        self.source = FixtureLibrary(Path(__file__).resolve().parents[1] / 'fixtures/tide-v70.json')
        self.album, self.song = self.source.library.song(self.source.player.song_id)

    def window(self):
        return SimpleNamespace(source=self.source, player=self.source.player)

    def test_album_pause_and_resume_preserve_song_position_and_queue(self):
        opening = self.source.player
        TideWindow.play_album(self.window(), self.album)
        paused = self.source.player
        self.assertFalse(paused.playing)
        self.assertEqual((paused.song_id, paused.position, paused.queue),
                         (opening.song_id, opening.position, opening.queue))
        TideWindow.play_album(self.window(), self.album)
        self.assertEqual(self.source.player, opening)

    def test_current_song_command_pauses_without_restarting(self):
        TideWindow.play_song(self.window(), self.album, self.song)
        self.assertFalse(self.source.player.playing)
        self.assertEqual(self.source.player.position, 64)
        self.assertEqual(self.source.player.song_id, 'pet:7')

    def test_another_song_starts_at_the_beginning(self):
        TideWindow.play_song(self.window(), self.album, self.album.songs[0])
        self.assertTrue(self.source.player.playing)
        self.assertEqual(self.source.player.position, 0)
        self.assertEqual(self.source.player.song_id, 'pet:0')

    def test_shuffle_album_replaces_the_playback_order(self):
        TideWindow.play_album(self.window(), self.album, shuffle=True)
        self.assertTrue(self.source.player.playing)
        self.assertTrue(self.source.player.shuffle)
        self.assertEqual(self.source.player.position, 0)
        self.assertIn(self.source.player.song_id, self.source.player.queue)
