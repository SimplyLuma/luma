# SPDX-License-Identifier: Apache-2.0
"""Undo retains the removed city's metadata and later changes from sync."""
import json
from pathlib import Path
import tempfile
import unittest
from prairie_apps.weather_backend import PlaceStore


class UndoMetadataTests(unittest.TestCase):
    def test_undo_retains_opaque_fields_and_latest_synced_document(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'places.json'
            removed = {'uid': 'a', 'name': 'First', 'latitude': 1.0, 'longitude': 2.0,
                       'provider_id': 'original-provider', 'opaque': {'values': [1, 2]}}
            other = {'uid': 'b', 'name': 'Other', 'latitude': 3.0, 'longitude': 4.0,
                     'foreign_field': 'keep'}
            unparsed = {'foreign_record': True}
            original = {'revision': 1, 'places': [removed, other, unparsed]}
            original_bytes = json.dumps(original).encode()
            path.write_bytes(original_bytes)
            store = PlaceStore(path)
            index, place = store.remove('a')
            self.assertEqual(index, 0)
            # Another process changes the remaining city and root metadata
            # while the person's Undo toast is still available.
            synced = json.loads(path.read_text())
            synced['revision'] = 2
            synced['places'][0]['name'] = 'Changed by sync'
            path.write_text(json.dumps(synced))
            store.insert(index, place)
            expected = {**synced, 'places': [removed, synced['places'][0], unparsed]}
            self.assertEqual(json.loads(path.read_text()), expected)
            backup = path.with_name('places.json.lumaui-backup')
            self.assertEqual(backup.read_bytes(), original_bytes)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
