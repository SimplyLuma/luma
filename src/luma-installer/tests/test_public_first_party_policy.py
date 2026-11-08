# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import tempfile
import unittest
from public_first_party_policy import public_ids


class PublicIdentityProjection(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.source = {'schema_version': 4, 'applications': [
            {'id': 'listed', 'tier': 'listed', 'visibility': 'public'},
            {'id': 'published', 'tier': 'luma', 'visibility': 'public'},
            {'id': 'held', 'tier': 'luma', 'visibility': 'private'}]}
        (self.data / 'depot-catalog-4.json').write_text(json.dumps(self.source))

    def test_only_active_public_ids_are_projected(self):
        self.assertEqual(public_ids(self.data), ['published'])

    def test_packaged_public_projection_survives_private_input_removal(self):
        fixture = self.root / 'tests/fixtures/depot-public-first-party.json'
        fixture.parent.mkdir(parents=True)
        fixture.write_text(json.dumps({'first_party_ids': public_ids(self.data)}))
        (self.data / 'depot-catalog-4.json').unlink()
        self.assertEqual(public_ids(self.data), ['published'])
        self.assertNotIn('listed', fixture.read_text())

    def test_duplicate_public_id_is_refused(self):
        self.source['applications'].append({'id': 'published', 'tier': 'luma', 'visibility': 'public'})
        (self.data / 'depot-catalog-4.json').write_text(json.dumps(self.source))
        with self.assertRaises(ValueError):
            public_ids(self.data)

    def test_invalid_packaged_public_projection_is_refused(self):
        fixture = self.root / 'tests/fixtures/depot-public-first-party.json'
        fixture.parent.mkdir(parents=True)
        (self.data / 'depot-catalog-4.json').unlink()
        for value in ([], ['duplicate', 'duplicate'], [''], ['valid', 42], 'not-a-list'):
            fixture.write_text(json.dumps({'first_party_ids': value}))
            with self.subTest(value=value), self.assertRaises(ValueError):
                public_ids(self.data)


if __name__ == '__main__':
    unittest.main()
