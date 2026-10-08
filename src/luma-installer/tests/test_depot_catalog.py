from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import tempfile
import unittest

from luma_installer.depot_catalog import CatalogError, MAX_BYTES, load_catalog, validate_catalog


CATALOG = Path(__file__).resolve().parents[1] / 'data/depot-catalog.json'


class DepotCatalogTests(unittest.TestCase):
    def setUp(self):
        self.value = json.loads(CATALOG.read_text())

    def test_curated_data_is_valid_and_never_install_authority(self):
        catalog = load_catalog(CATALOG)
        self.assertEqual(len(catalog.applications), 49)
        self.assertTrue(all(not app.installable for app in catalog.applications))
        by_id = {app.id: app for app in catalog.applications}
        self.assertEqual(by_id['steam'].distribution, 'community')
        self.assertEqual(by_id['obsidian'].distribution, 'publisher-verified-community')
        self.assertEqual(by_id['thunderbird'].source_id, 'org.mozilla.thunderbird')
        self.assertEqual(by_id['nordvpn'].architectures, ())
        with self.assertRaises(FrozenInstanceError):
            by_id['steam'].qualification = 'admitted'

    def test_a_newer_catalogue_lists_sources_an_older_depot_leaves_out(self):
        value = deepcopy(self.value)
        value['applications'].append({**value['applications'][0], 'id': 'future-app',
                                      'backend': 'download', 'repository': 'not-known-yet',
                                      'source_id': 'future-app'})
        catalog = validate_catalog(value)
        self.assertNotIn('future-app', {app.id for app in catalog.applications})
        self.assertEqual(len(catalog.applications), 49)
        for backend, repository in (('download', 'https://evil.example'), ('shell;rm', 'x')):
            bad = deepcopy(value)
            bad['applications'][-1].update(backend=backend, repository=repository)
            with self.subTest(backend=backend, repository=repository), self.assertRaises(CatalogError):
                validate_catalog(bad)
        value['schema_version'] = 2
        value['applications'] = [row for row in value['applications'] if row['id'] != 'figma' and row['id'] != 'termius']
        value['applications'].append({**value['applications'][0], 'id': 'future-app',
                                      'backend': 'download', 'repository': 'not-known-yet',
                                      'source_id': 'future-app'})
        for row in value['applications']:
            row.pop('publisher', None); row.pop('summary', None); row.pop('description', None); row.pop('homepage', None)
        with self.assertRaises(CatalogError):
            validate_catalog(value)

    def test_the_two_assistants_are_listed_against_their_publishers(self):
        """Both ship an official Linux package, by different routes.

        OpenAI publishes a Fedora rpm. Anthropic publishes for Debian and
        Ubuntu only and states that Fedora is not supported, so Claude is
        listed against the deb backend, which Valet installs by building a
        Debian capsule. Listing it as a native package would claim something
        that does not exist.
        """
        catalog = load_catalog(CATALOG)
        entries = {app.id: app for app in catalog.applications}
        self.assertEqual(entries['chatgpt'].backend, 'rpm')
        self.assertEqual(entries['chatgpt'].repository, 'openai')
        self.assertEqual(entries['claude'].backend, 'deb')
        self.assertEqual(entries['claude'].repository, 'anthropic')
        for name in ('chatgpt', 'claude'):
            self.assertEqual(entries[name].distribution, 'publisher')
            self.assertEqual(entries[name].qualification, 'pending')
            self.assertFalse(entries[name].installable)

    def test_executable_or_claimed_authority_fields_fail_closed(self):
        for field in ('command', 'exec', 'download_url', 'installable', 'verified', 'trust_key'):
            value = deepcopy(self.value)
            value['applications'][0][field] = 'arbitrary'
            with self.subTest(field=field), self.assertRaises(CatalogError):
                validate_catalog(value)
        for status in ('admitted', 'verified', 'ready'):
            self.value['applications'][0]['qualification'] = status
            with self.subTest(status=status), self.assertRaises(CatalogError):
                validate_catalog(self.value)

    def test_malformed_identity_source_and_architecture_rejected(self):
        cases = [('id', '../escape'), ('source_id', '--user'), ('source_id', 'a;touch /tmp/x'),
                 ('backend', 'shell'), ('repository', 'https://evil.example'),
                 ('architectures', ['x86_64', 'x86_64']), ('architectures', [False]),
                 ('architectures', ['arm64']), ('name', 'line\nbreak')]
        for field, content in cases:
            value = deepcopy(self.value)
            # Schema 3 leaves out a well-formed source it does not know; older
            # schemas refuse it, and no schema accepts a malformed one.
            value['schema_version'] = 2
            value['applications'][0][field] = content
            with self.subTest(field=field, content=content), self.assertRaises(CatalogError):
                validate_catalog(value)

    def test_duplicate_application_and_source_rejected(self):
        self.value['applications'].append(deepcopy(self.value['applications'][0]))
        with self.assertRaises(CatalogError):
            validate_catalog(self.value)
        self.value['applications'][-1]['id'] = 'another-name'
        with self.assertRaises(CatalogError):
            validate_catalog(self.value)

    def test_reference_urls_cannot_be_commands_or_credentials(self):
        for url in ('file:///tmp/a', 'http://example.org', 'https://user:pass@example.org/',
                    'https://example.org:bad/', 'https://example.org:22/', 'https://example.org/has space',
                    'https://example.org\\evil', 'javascript:alert(1)'):
            self.value['applications'][0]['reference_url'] = url
            with self.subTest(url=url), self.assertRaises(CatalogError):
                validate_catalog(self.value)

    def test_unknown_schema_and_fields_are_not_ignored(self):
        for version in (True, 4, '1', '2', '3', None):
            self.value['schema_version'] = version
            with self.subTest(version=version), self.assertRaises(CatalogError):
                validate_catalog(self.value)

    def test_schema_two_describes_but_cannot_admit(self):
        entry = self.value['applications'][0]
        entry.update(publisher='Example', summary='Short', description='Longer words.', homepage='https://example.org/get')
        self.value['schema_version'] = 1
        with self.assertRaises(CatalogError):
            validate_catalog(self.value)  # schema 1 still refuses the extra fields
        self.value['schema_version'] = 2
        parsed = validate_catalog(self.value).applications[0]
        self.assertEqual((parsed.publisher, parsed.summary, parsed.homepage), ('Example', 'Short', 'https://example.org/get'))
        self.assertFalse(parsed.installable)
        for bad in ('http://example.org', 'javascript:alert(1)', 'https://u:p@example.org/'):
            entry['homepage'] = bad
            with self.subTest(homepage=bad), self.assertRaises(CatalogError):
                validate_catalog(self.value)
        entry['homepage'] = 'https://example.org/get'
        entry['installable'] = True
        with self.assertRaises(CatalogError):
            validate_catalog(self.value)  # unknown fields stay refused in schema 2

    def test_file_limits_and_duplicate_json_fields(self):
        for content in (b'x' * (MAX_BYTES + 1), b'{"schema_version":1,"schema_version":1}',
                        b'\xff', b'[]', b'{'):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'catalog.json'
                path.write_bytes(content)
                with self.subTest(content=content[:32]), self.assertRaises(CatalogError):
                    load_catalog(path)

    def test_empty_catalog_is_valid(self):
        self.value['applications'] = []
        self.assertEqual(validate_catalog(self.value).applications, ())


if __name__ == '__main__':
    unittest.main()
