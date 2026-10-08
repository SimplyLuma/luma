# SPDX-License-Identifier: GPL-3.0-only
"""Portable lease cleanup remains safe after release and partial startup."""
from unittest.mock import patch
import unittest
from engine_display import EngineDisplay


class Connection:
    def __init__(self):
        self.releases = 0

    def call_sync(self, *args):
        assert args[3] == 'Release'
        self.releases += 1


class Cleanup(unittest.TestCase):
    def test_portable_close_twice_releases_once(self):
        connection = Connection()
        def acquire(display):
            display.remote_connection = connection
            display.remote_handle = 'a' * 32
        with patch.dict('os.environ', {'FLATPAK_ID': 'com.rhyme.viola'}), \
                patch.object(EngineDisplay, '_acquire_host', acquire):
            display = EngineDisplay(None)
        display.close()
        display.close()
        self.assertEqual(connection.releases, 1)

    def test_partial_portable_startup_can_close_twice(self):
        with patch.dict('os.environ', {'FLATPAK_ID': 'com.rhyme.viola'}), \
                patch.object(EngineDisplay, '_acquire_host', lambda display: None):
            display = EngineDisplay(None)
        display.close()
        display.close()


if __name__ == '__main__':
    unittest.main()
