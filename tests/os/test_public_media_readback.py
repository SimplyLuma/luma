#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Bounded full-stream and exact immutable URL profile checks; no public traffic."""
from email.message import Message
import hashlib
import importlib.util
import io
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[2]/'scripts/os/public-media-readback.py'
spec = importlib.util.spec_from_file_location('client', PATH)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
BODY = b'actual test ISO body\0'
EXPECTED = {'bytes': len(BODY), 'sha256': hashlib.sha256(BODY).hexdigest()}
TARGET = {'url': 'https://media.simplyluma.com/media/aarch64/nightly/luma-nightly-20261025.4.iso',
          'filename': 'luma-nightly-20261025.4.iso'}


def profile():
    return {'schema': client.SCHEMA, 'build_id': '20261025.4', 'architecture': 'aarch64',
            'source_revision': 'a'*40, 'candidate': 'b'*64, 'prepared_sha256': 'c'*64,
            'aggregate_sha256': 'd'*64, 'iso': dict(EXPECTED), 'targets': [dict(TARGET),
                {'url': 'https://media.simplyluma.com/media/nightly/luma-nightly-20261025.4-aarch64.iso',
                 'filename': 'luma-nightly-20261025.4-aarch64.iso'},
                {'url': 'https://media.simplyluma.com/media/nightly/luma-nightly-20261025.4.iso',
                 'filename': 'luma-nightly-20261025.4.iso'}]}


class Response(io.BytesIO):
    def __init__(self, body=BODY, **overrides):
        super().__init__(body)
        self.status = overrides.get('status', 200)
        self.url = overrides.get('url', TARGET['url'])
        self.headers = Message()
        self.headers['Content-Type'] = overrides.get('mime', 'application/x-iso9660-image')
        self.headers['Content-Disposition'] = 'attachment; filename="'+overrides.get('filename', TARGET['filename'])+'"'
        self.headers['Content-Length'] = str(overrides.get('length', len(BODY)))
        self.headers['ETag'] = '"actual-fixture-object"'
    def geturl(self):
        return self.url


class Tests(unittest.TestCase):
    def test_full_readback_retains_headers_and_identity(self):
        response = Response()
        result = client.read_iso(TARGET, EXPECTED, opener=lambda *a, **kw: response)
        self.assertEqual((result['bytes'], result['sha256']), (len(BODY), EXPECTED['sha256']))
        self.assertTrue(result['full_body_verified'])
        self.assertEqual(result['headers']['ETag'], '"actual-fixture-object"')
    def test_reject_wrong_http_identity(self):
        for value in ({'status': 206}, {'url': TARGET['url']+'?redirected'}, {'mime': 'text/plain'},
                      {'filename': TARGET['filename']+'.other'}, {'length': len(BODY)+1}):
            with self.subTest(value=value), self.assertRaises(AssertionError):
                client.read_iso(TARGET, EXPECTED, opener=lambda *a, **kw: Response(**value))
    def test_reject_truncated_excess_and_corrupt_full_body(self):
        for body in (BODY[:-1], BODY+b'x', b'x'*len(BODY)):
            with self.subTest(body=body), self.assertRaises(AssertionError):
                client.read_iso(TARGET, EXPECTED, opener=lambda *a, **kw: Response(body))
    def test_bounded_whole_body_duration(self):
        times = iter((0, 1801))
        with self.assertRaises(AssertionError):
            client.read_iso(TARGET, EXPECTED, opener=lambda *a, **kw: Response(), clock=lambda: next(times))
    def test_exact_profile(self):
        self.assertEqual(client.validate_profile(profile()), profile())
    def test_maintained_initial_arm_plan_has_only_two_iso_urls(self):
        spec = importlib.util.spec_from_file_location('aggregate', PATH.parent/'lib/media_aggregate.py')
        aggregate = importlib.util.module_from_spec(spec); spec.loader.exec_module(aggregate)
        old = {'build_id': '20261021.1', 'arch': 'x86_64', 'channel': 'nightly',
               'file': 'luma-nightly-20261021.1.iso', 'url': client.ORIGIN+'/media/nightly/luma-nightly-20261021.1.iso',
               'date': '2026-10-21T00:00:00Z', 'public': True}
        new = {'build_id': '20261025.4', 'arch': 'aarch64', 'channel': 'nightly',
               'file': TARGET['filename'], 'url': TARGET['url'],
               'notes_url': client.ORIGIN+'/media/aarch64/nightly/notes/20261025.4.json',
               'date': '2026-10-25T00:00:00Z', 'public': True,
               'sha256': EXPECTED['sha256'], 'size_bytes': EXPECTED['bytes']}
        canonical = {'schema': aggregate.SCHEMA, 'channel': 'nightly',
                     'generated_utc': '2026-10-21T00:00:00Z', 'entries': [old], 'latest': old}
        incoming = {'schema': aggregate.SCHEMA, 'channel': 'nightly',
                    'generated_utc': '2026-10-25T00:00:00Z', 'entries': [new], 'latest': new}
        merged, aliases, ignored = aggregate.merge(canonical, incoming, 'nightly',
            client.ORIGIN+'/media/nightly/latest.json', client.ORIGIN+'/media/aarch64/nightly/latest.json',
            '2026-10-25T01:00:00Z', lambda *args: (b'fixture notes', b'fixture signature'))
        targets = [dict(TARGET)]+[{'url': alias['target_url'], 'filename': alias['target_url'].rsplit('/', 1)[1]}
                                 for alias in aliases if alias['kind'] == 'installer-iso']
        self.assertEqual(len(targets), 2)
        self.assertEqual(aggregate.entries(merged, 'nightly')[('20261021.1', 'x86_64')], old)
        value = profile(); value['targets'] = targets
        self.assertEqual(client.validate_profile(value)['targets'], targets)
        self.assertEqual(ignored, [])

    def test_reject_wrong_profile_binding(self):
        for key, value in (('schema', 'other'), ('architecture', 'x86_64'), ('candidate', 'short'),
                           ('source_revision', 'short'), ('prepared_sha256', ''), ('unknown', True)):
            data = profile(); data[key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError): client.validate_profile(data)
    def test_reject_duplicate_foreign_or_unbounded_targets(self):
        mutations = [lambda d: d.__setitem__('targets', d['targets'][:1]),
                     lambda d: d['targets'].__setitem__(1, d['targets'][0]),
                     lambda d: d['targets'][0].__setitem__('url', 'https://attacker.invalid/iso'),
                     lambda d: d['iso'].__setitem__('bytes', True),
                     lambda d: d['iso'].__setitem__('bytes', 33*2**30),
                     lambda d: d['targets'][0].__setitem__('filename', 'latest.iso')]
        for mutate in mutations:
            data = profile(); mutate(data)
            with self.subTest(data=data), self.assertRaises(AssertionError): client.validate_profile(data)


if __name__ == '__main__': unittest.main()
