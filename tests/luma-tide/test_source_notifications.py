# SPDX-License-Identifier: Apache-2.0
"""Server configuration signals are independent of library/playback churn."""
import os
from pathlib import Path
import tempfile
import unittest

from luma_tide.model import LibraryStore, SourceState


class SourceNotifications(unittest.TestCase):
    def test_add_account_change_and_remove_signal_after_commit(self):
        with tempfile.TemporaryDirectory() as directory, LibraryStore(Path(directory) / 'library.db') as store:
            marker = store.path.with_name('sources-changed')
            self.assertFalse(marker.exists())
            source = store.add_source('Music', 'subsonic', 'https://music.example.test', local=False)
            self.assertTrue(marker.exists())
            os.utime(marker, ns=(1, 1))
            store.set_source_account(source.id, 'listener')
            self.assertGreater(marker.stat().st_mtime_ns, 1)
            os.utime(marker, ns=(1, 1))
            store.remove_source(source.id)
            self.assertGreater(marker.stat().st_mtime_ns, 1)

    def test_network_and_incremental_library_state_do_not_signal(self):
        with tempfile.TemporaryDirectory() as directory, LibraryStore(Path(directory) / 'library.db') as store:
            source = store.add_source('Music', 'subsonic', 'https://music.example.test', local=False)
            marker = store.path.with_name('sources-changed')
            initial = marker.stat().st_mtime_ns
            store.set_source_state(source.id, SourceState.OFFLINE)
            store.set_sync_marker(source.id, 'revision-3')
            self.assertEqual(marker.stat().st_mtime_ns, initial)


if __name__ == '__main__':
    unittest.main()
