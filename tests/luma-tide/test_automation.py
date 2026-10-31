# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from luma_tide.automation import MediaLibrary
from luma_tide.model import LibraryStore, MediaMetadata
from luma_tide.playback import PlaybackController
from test_playback import FakeEngine


class Automation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = LibraryStore(Path(self.temp.name)/'library.db')
        source = self.store.add_source('Local', 'local-folder', Path(self.temp.name), local=True)
        self.track, _ = self.store.upsert_copy(source.id, MediaMetadata(uri='file:///fixture.flac',
            content_digest='a'*64, title='Have a Cigar', artist='Pink Floyd', album='Wish You Were Here'))
        self.engine = FakeEngine()
        self.controller = PlaybackController(self.store, self.engine)
        self.api = MediaLibrary.__new__(MediaLibrary)
        self.api.connection = Mock(); self.api.registration = 1
        self.api.store = self.store; self.api.controller = self.controller; self.api.targets = {}

    def tearDown(self):
        self.api.close(); self.controller.close(); self.store.close(); self.temp.cleanup()

    def test_owner_controller_really_changes_queue_without_exposing_copy_uris(self):
        result = self.api.search(':1.10', 'Have a Cigar')
        self.assertEqual(len(result['tracks']), 1)
        self.assertNotIn('uri', str(result)); self.assertNotIn('fixture.flac', str(result))
        state = self.api.play(':1.10', result['tracks'][0]['target'])
        self.assertEqual(state['state'], 'playing')
        self.assertEqual(state['track']['id'], self.track.id)
        self.assertEqual(self.engine.loaded[-1][0], 'file:///fixture.flac')
        self.assertEqual(self.store.queue()[0].track.id, self.track.id)

    def test_targets_are_sender_revision_expiry_bound_and_single_use(self):
        def token(): return self.api.search(':1.10', 'Have a Cigar')['tracks'][0]['target']
        target = token()
        with self.assertRaises(ValueError): self.api.play(':1.11', target)
        target = token()
        with patch.object(self.store, 'track', return_value=replace(self.track, title='Changed')):
            with self.assertRaises(ValueError): self.api.play(':1.10', target)
        target = token(); self.api.targets[target]['expires'] = 0
        with self.assertRaises(ValueError): self.api.play(':1.10', target)
        target = token(); self.api.play(':1.10', target)
        with self.assertRaises(ValueError): self.api.play(':1.10', target)
        self.assertEqual(len(self.engine.loaded), 1)


if __name__ == '__main__': unittest.main()
