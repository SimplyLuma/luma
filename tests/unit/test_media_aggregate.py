# SPDX-License-Identifier: Apache-2.0
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('media_aggregate', ROOT / 'scripts/os/lib/media_aggregate.py')
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)


class MediaAggregate(unittest.TestCase):
    def setUp(self):
        self.base = 'https://media.simplyluma.com/media/nightly/'
        self.arm = 'https://media.simplyluma.com/media/aarch64/nightly/'
        self.x86 = self.entry('old', 'x86_64', self.base)
        self.new = self.entry('new', 'aarch64', self.arm)
        self.canonical = self.index(self.x86)
        self.incoming = self.index(self.new)

    def entry(self, build, arch, base):
        return {'build_id': build, 'version': '1.0.0', 'arch': arch, 'channel': 'nightly',
                'file': build + '.iso', 'url': base + build + '.iso', 'sha256': 'a' * 64,
                'size_bytes': 1024, 'date': '2026-10-23T01:00:00Z', 'public': True,
                'notes_url': base + 'notes/' + build + '.json'}

    def index(self, entry):
        return {'schema': media.SCHEMA, 'channel': 'nightly', 'generated_utc': '2026-10-23T01:01:00Z',
                'latest': entry, 'entries': [entry], 'superseded': ['prior-validation'],
                'latest_url': self.base + 'latest.json'}

    def run_merge(self, notes=lambda build, existing: (b'identical notes', b'signature')):
        return media.merge(self.canonical, self.incoming, 'nightly', self.base + 'latest.json',
                           self.arm + 'latest.json', '2026-10-23T01:02:00Z', notes)

    def test_real_alias_plan_preserves_x86_and_its_metadata(self):
        old = copy.deepcopy(self.canonical)
        doc, aliases, ignored = self.run_merge()
        self.assertEqual(self.canonical, old)
        self.assertIn(self.x86, doc['entries'])
        self.assertEqual(doc['superseded'], old['superseded'])
        self.assertEqual(doc['latest_url'], old['latest_url'])
        arm = next(row for row in doc['entries'] if row['arch'] == 'aarch64')
        self.assertEqual(arm['url'], self.base + 'new-aarch64.iso')
        self.assertEqual(arm['notes_url'], self.base + 'notes/new.json')
        self.assertEqual(arm['sha256'], self.new['sha256'])
        self.assertEqual({alias['kind'] for alias in aliases}, {'installer-iso', 'checksum', 'notes', 'notes.minisig'})
        self.assertEqual(ignored, [])

    def test_same_build_both_cpus_remain_distinct_with_identical_notes(self):
        self.new.update(build_id='old', notes_url=self.arm + 'notes/old.json')
        doc, aliases, _ = self.run_merge()
        self.assertEqual({(row['build_id'], row['arch']) for row in doc['entries']},
                         {('old', 'x86_64'), ('old', 'aarch64')})
        self.assertFalse(any(alias['kind'].startswith('notes') for alias in aliases))

    def test_same_build_different_notes_cannot_replace_x86_notes(self):
        self.new.update(build_id='old', notes_url=self.arm + 'notes/old.json')
        with self.assertRaisesRegex(ValueError, 'different signed release notes'):
            self.run_merge(lambda build, existing: (b'x86' if existing else b'ARM', b'signature'))

    def test_private_and_interim_artifacts_never_get_public_aliases(self):
        for flag in ({'public': False}, {'interim': True}):
            self.new.update(flag)
            doc, aliases, ignored = self.run_merge()
            self.assertEqual(doc['entries'], [self.x86])
            self.assertEqual(aliases, [])
            self.assertEqual(ignored, [['new', 'aarch64']])
            self.new.pop('interim', None)
            self.new['public'] = True

    def test_wrong_architecture_or_cross_origin_source_fails(self):
        self.new['arch'] = 'x86_64'
        with self.assertRaisesRegex(ValueError, 'another architecture'):
            self.run_merge()
        self.new['arch'] = 'aarch64'
        self.new['url'] = 'https://untrusted.example/new.iso'
        with self.assertRaisesRegex(ValueError, 'not beside scoped index'):
            self.run_merge()

    def test_conflicting_duplicate_identity_fails(self):
        duplicate = copy.deepcopy(self.new)
        duplicate['sha256'] = 'b' * 64
        self.incoming['entries'].append(duplicate)
        with self.assertRaisesRegex(ValueError, 'Conflicting duplicate'):
            self.run_merge()

    def test_new_alias_cannot_overwrite_existing_iso(self):
        self.x86['url'] = self.base + 'new-aarch64.iso'
        self.x86['sha256'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'conflicts with an existing'):
            self.run_merge()

    def test_generation_must_advance_both_input_indexes(self):
        self.incoming['generated_utc'] = '2026-10-23T01:03:00Z'
        with self.assertRaisesRegex(ValueError, 'must advance'):
            self.run_merge()

    def test_prior_index_compare_refuses_a_concurrent_publication(self):
        prior = b'original canonical bytes'
        receipt = {'schema': 'org.projectluma.media-aggregate-preparation/v1',
                   'canonical_prior_sha256': media.digest(prior)}
        media.check_prior(receipt, prior)
        with self.assertRaisesRegex(ValueError, 'Canonical index changed'):
            media.check_prior(receipt, b'newer canonical bytes')
