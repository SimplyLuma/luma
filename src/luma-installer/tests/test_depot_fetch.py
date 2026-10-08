# SPDX-License-Identifier: Apache-2.0
"""A served catalogue is signed data. It cannot widen what may be installed."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from luma_installer import depot_catalog, depot_counting
from luma_installer.depot_catalog import (
    CatalogError, current_catalog, fetch_catalog, load_verified, local_catalog, signature_path)

import minisign_signer as signer

SEED = Path(__file__).resolve().parents[1] / 'data/depot-catalog-4.json'
URL = 'https://dl.example.invalid/catalog/catalog-4.json'


def document(generated='2026-09-30T18:00:00Z', applications=None):
    value = json.loads(SEED.read_text())
    value['generated_at'] = generated
    if applications is not None:
        value['applications'] = applications
    return json.dumps(value).encode()


class Server:
    """Stands in for urlopen: a catalogue, its signature, and a request log."""

    def __init__(self, content, signature=None):
        self.content = content
        self.signature = signature if signature is not None else signer.signature_file(content)
        self.requests = []
        self.fail = False

    def __call__(self, request, timeout=None):
        self.requests.append(request.full_url)
        if self.fail:
            raise OSError('no route')
        body = self.signature if request.full_url.endswith('.minisig') else self.content

        class Response:
            def read(self, limit=None):
                return body if limit is None else body[:limit]

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False
        return Response()


class Signed(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.cache = Path(self.dir.name) / 'catalog-4.json'
        self.key = Path(self.dir.name) / 'depot-catalog.pub'
        self.key.write_bytes(signer.public_key_file())
        depot_catalog._last_failure = 0.0

    def serve(self, server):
        return mock.patch.object(depot_catalog.urllib.request, 'urlopen', server)

    def test_a_signed_catalogue_is_verified_and_kept_with_its_signature(self):
        server = Server(document())
        with self.serve(server):
            catalog = fetch_catalog(URL, cache=self.cache, key=self.key)
        self.assertTrue(catalog.verified)
        self.assertEqual(catalog.schema_version, 4)
        self.assertEqual(server.requests, [URL, URL + '.minisig'])
        self.assertTrue(signature_path(self.cache).is_file())
        self.assertEqual(load_verified(self.cache, key=self.key).applications, catalog.applications)

    def test_an_unsigned_or_wrongly_signed_catalogue_is_never_parsed_or_kept(self):
        good = document()
        for signature in (b'', signer.signature_file(good + b' '),
                          signer.signature_file(good, seed=bytes(range(32)))):
            server = Server(good, signature)
            with self.subTest(signature=signature[:16]), self.serve(server), \
                    mock.patch.object(depot_catalog, 'validate_catalog') as validate:
                with self.assertRaises(CatalogError):
                    fetch_catalog(URL, cache=self.cache, key=self.key)
                validate.assert_not_called()
            self.assertFalse(self.cache.exists())

    def test_no_installed_key_means_no_network_catalogue(self):
        server = Server(document())
        with self.serve(server), self.assertRaises(CatalogError):
            fetch_catalog(URL, cache=self.cache, key=Path(self.dir.name) / 'missing.pub')
        self.assertEqual(server.requests, [])

    def test_the_size_bound_applies_before_the_signature(self):
        big = b' ' * (depot_catalog.MAX_SIGNED_BYTES + 1)
        server = Server(big, b'irrelevant')
        with self.serve(server), mock.patch.object(depot_catalog, 'verify_file') as verify:
            with self.assertRaises(CatalogError):
                fetch_catalog(URL, cache=self.cache, key=self.key)
            verify.assert_not_called()

    def test_a_replayed_older_catalogue_does_not_roll_the_device_back(self):
        with self.serve(Server(document('2026-10-02T00:00:00Z'))):
            fetch_catalog(URL, cache=self.cache, key=self.key)
        with self.serve(Server(document('2026-09-01T00:00:00Z'))), self.assertRaises(CatalogError):
            fetch_catalog(URL, cache=self.cache, key=self.key)
        self.assertEqual(load_verified(self.cache, key=self.key).generated_at, '2026-10-02T00:00:00Z')

    def test_a_signed_catalogue_still_cannot_invent_a_repository_or_admit_a_listed_app(self):
        value = json.loads(document())
        value['applications'][0]['repository'] = 'an-attacker-repo'
        value['applications'][1]['qualification'] = 'admitted'
        invented = {value['applications'][0]['id'], value['applications'][1]['id']}
        content = json.dumps(value).encode()
        with self.serve(Server(content)):
            catalog = fetch_catalog(URL, cache=self.cache, key=self.key)
        self.assertFalse(invented & {entry.id for entry in catalog.applications})
        self.assertEqual(catalog.skipped, 2)
        self.assertTrue(all(not entry.installable for entry in catalog.applications))

    def test_a_tampered_cache_is_not_trusted(self):
        with self.serve(Server(document())):
            fetch_catalog(URL, cache=self.cache, key=self.key)
        self.cache.write_bytes(self.cache.read_bytes().replace(b'"listed"', b'"luma"', 1))
        with self.assertRaises(CatalogError):
            load_verified(self.cache, key=self.key)
        catalog = local_catalog(cache=self.cache, seed=SEED, key=self.key)
        self.assertFalse(catalog.verified)


class FallingBack(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.cache = Path(self.dir.name) / 'catalog-4.json'
        self.key = Path(self.dir.name) / 'depot-catalog.pub'
        self.key.write_bytes(signer.public_key_file())
        self.state = {'XDG_STATE_HOME': str(Path(self.dir.name) / 'state'),
                      'XDG_CONFIG_HOME': str(Path(self.dir.name) / 'config')}
        depot_catalog._last_failure = 0.0

    def test_offline_falls_back_to_the_verified_copy_then_the_seed(self):
        server = Server(document())
        with mock.patch.object(depot_catalog.urllib.request, 'urlopen', server):
            current_catalog(url=URL, cache=self.cache, seed=SEED, key=self.key, refresh=True)
        server.fail = True
        with mock.patch.object(depot_catalog.urllib.request, 'urlopen', server):
            kept = current_catalog(url=URL, cache=self.cache, seed=SEED, key=self.key, refresh=True)
        self.assertTrue(kept.verified)
        self.cache.unlink()
        with mock.patch.object(depot_catalog.urllib.request, 'urlopen', server):
            seeded = current_catalog(url=URL, cache=self.cache, seed=SEED, key=self.key, refresh=True)
        self.assertFalse(seeded.verified)
        self.assertEqual(len(seeded.applications), 71)

    def test_a_fresh_copy_answers_without_the_network(self):
        server = Server(document())
        with mock.patch.object(depot_catalog.urllib.request, 'urlopen', server):
            current_catalog(url=URL, cache=self.cache, seed=SEED, key=self.key, refresh=True)
            current_catalog(url=URL, cache=self.cache, seed=SEED, key=self.key)
        self.assertEqual(len(server.requests), 2)

    def test_the_weekly_count_rides_on_the_first_fetch_of_the_week_only(self):
        now = [1790000000.0]
        counter = depot_counting.Countme(environment=self.state, clock=lambda: now[0],
                                         settings=depot_counting.Settings())
        server = Server(document())
        with mock.patch.object(depot_catalog.urllib.request, 'urlopen', server):
            current_catalog(url=URL, cache=self.cache, seed=SEED, key=self.key, refresh=True, counter=counter)
            current_catalog(url=URL, cache=self.cache, seed=SEED, key=self.key, refresh=True, counter=counter)
            now[0] += 7 * 24 * 3600
            current_catalog(url=URL, cache=self.cache, seed=SEED, key=self.key, refresh=True, counter=counter)
        catalog_requests = [url for url in server.requests if not url.endswith('.minisig')]
        self.assertEqual(catalog_requests, [URL + '?countme=1', URL, URL + '?countme=1'])
        self.assertTrue(all('countme' not in url for url in server.requests if url.endswith('.minisig')))

    def test_turning_the_count_off_sends_no_bucket(self):
        counter = depot_counting.Countme(environment=self.state,
                                         settings=depot_counting.Settings(countme=False))
        server = Server(document())
        with mock.patch.object(depot_catalog.urllib.request, 'urlopen', server):
            current_catalog(url=URL, cache=self.cache, seed=SEED, key=self.key, refresh=True, counter=counter)
        self.assertEqual(server.requests, [URL, URL + '.minisig'])


if __name__ == '__main__':
    unittest.main()
