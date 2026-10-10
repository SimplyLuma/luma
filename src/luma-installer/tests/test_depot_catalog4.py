# SPDX-License-Identifier: Apache-2.0
"""Schema 4 (ADR-028, section 4.2): richer listings, the same lack of authority."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from luma_installer import depot_catalog
from luma_installer.depot_catalog import CatalogError, validate_catalog

DATA = Path(__file__).resolve().parents[1] / 'data'
SHA = 'a' * 64

CANVAS = {
    "id": "canvas", "app_id": "org.projectluma.Canvas", "name": "Canvas", "tier": "luma",
    "visibility": "public",
    "developer": {"id": "dev_projectluma", "name": "Project Luma", "verified": "organization"},
    "summary": "Design pages, posters and social graphics.", "description": "Long\n\ntext.",
    "categories": ["create"],
    "icon": {"url": "https://dl.simplyluma.com/media/org.projectluma.Canvas/icon-256.png", "sha256": SHA},
    "screenshots": [{"url": "https://dl.simplyluma.com/media/c/1.png", "sha256": SHA, "caption": "Pages",
                     "width": 2560, "height": 1600}],
    "license": "Apache-2.0", "homepage": "https://simplyluma.com/apps/canvas",
    "support_url": "https://hub.simplyluma.com/forum", "privacy_url": "https://simplyluma.com/privacy",
    "source_url": "https://github.com/ProjectLuma/Designer", "backend": "flatpak", "repository": "luma",
    "source_id": "org.projectluma.Canvas", "branch": "stable", "architectures": ["x86_64", "aarch64"],
    "release": {"version": "0.1.0", "date": "2026-09-30", "download_bytes": 41000000,
                "installed_bytes": 120000000, "notes": "First release."},
    "permissions": [{"key": "files.documents", "level": "sensitive"}, {"key": "network", "level": "standard"},
                    {"key": "files.home", "level": "sensitive", "access": "read", "names": ["~/Fonts"]}],
    "permission_changes": [{"key": "network", "change": "added", "level": "standard"}, "files.documents"],
    "sign_in": "none", "age_rating": "oars-1.1:none", "rating": {"average": 4.6, "count": 128},
    "installs": {"total": 5231, "last_30_days": 812}, "distribution": "publisher",
    "qualification": "admitted", "qualification_reason": "Reviewed release.",
    "reference_url": "https://simplyluma.com/apps/canvas",
    "a_field_from_the_future": {"anything": True},
}


def catalog(*entries, collections=()):
    return {"schema_version": 4, "generated_at": "2026-09-30T18:00:00Z",
            "collections": list(collections), "applications": [deepcopy(e) for e in entries]}


class Schema4(unittest.TestCase):
    def test_the_adr_example_parses_completely(self):
        parsed = validate_catalog(catalog(CANVAS, collections=[
            {"id": "creative", "name": "Creative", "summary": "Make things.",
             "applications": ["canvas", "reel", "canvas"]}]))
        entry = parsed.applications[0]
        self.assertEqual((parsed.schema_version, parsed.reviewed_on), (4, '2026-09-30'))
        self.assertEqual((entry.tier, entry.developer.verified, entry.repository), ('luma', 'organization', 'luma'))
        self.assertEqual(entry.release.download_bytes, 41000000)
        self.assertEqual(entry.permissions[2].names, ('~/Fonts',))
        self.assertTrue(entry.permissions[2].read_only)
        self.assertEqual([c.change for c in entry.permission_changes], ['added', 'added'])
        self.assertEqual(entry.rating.count, 128)
        self.assertEqual(entry.identifier, 'org.projectluma.Canvas')
        self.assertFalse(entry.installable)
        # Members that are not in the snapshot are kept, in order, once.
        self.assertEqual(parsed.collections[0].applications, ('canvas', 'reel'))
        self.assertEqual([e.id for e in parsed.members(parsed.collections[0])], ['canvas'])
        self.assertIs(parsed.find('org.projectluma.canvas'), entry)

    def test_private_and_withdrawn_entries_are_never_shown(self):
        for visibility in ('private', 'withdrawn', 'draft'):
            with self.subTest(visibility=visibility):
                parsed = validate_catalog(catalog({**CANVAS, 'visibility': visibility}))
                self.assertEqual((parsed.applications, parsed.skipped), ((), 1))
        self.assertEqual(validate_catalog(catalog({**CANVAS, 'visibility': 'unlisted'})).applications[0].visibility,
                         'unlisted')

    def test_listed_apps_are_never_admitted_or_hosted_on_the_luma_remote(self):
        for change in ({'tier': 'listed'}, {'tier': 'listed', 'qualification': 'pending'},
                       {'tier': 'gold'}, {'qualification': 'ready'}):
            with self.subTest(change=change):
                self.assertEqual(validate_catalog(catalog({**CANVAS, **change})).skipped, 1)

    def test_only_schema_4_may_name_the_luma_remote(self):
        legacy = {key: CANVAS[key] for key in depot_catalog.SCHEMA3_FIELDS}
        legacy['qualification'] = 'pending'
        value = {"schema_version": 3, "reviewed_on": "2026-09-30", "applications": [legacy]}
        self.assertEqual(validate_catalog(value).applications, ())

    def test_images_need_a_digest_and_https(self):
        for icon in ({"url": CANVAS['icon']['url']}, {"url": CANVAS['icon']['url'], "sha256": "A" * 64},
                     {"url": "http://dl.simplyluma.com/i.png", "sha256": SHA},
                     {"url": "https://u:p@dl.simplyluma.com/i.png", "sha256": SHA}):
            with self.subTest(icon=icon), self.assertRaises(CatalogError):
                validate_catalog(catalog({**CANVAS, 'icon': icon}))

    def test_malformed_known_fields_refuse_the_document(self):
        cases = (
            ('developer', {"id": "../x", "name": "X"}), ('developer', "Project Luma"),
            ('categories', ["Create"]), ('categories', ["x"] * 9),
            ('release', {"version": "", "download_bytes": 1}), ('release', {"version": "1", "download_bytes": -1}),
            ('release', {"version": "1", "date": "30/09/2026"}),
            ('permissions', [{"key": "network", "level": "extreme"}]),
            ('permissions', [{"key": "Network", "level": "standard"}]),
            ('permission_changes', [{"key": "network", "change": "grew"}]),
            ('rating', {"average": 6, "count": 1}), ('rating', {"average": "4", "count": 1}),
            ('installs', {"total": "many"}), ('support_url', "javascript:alert(1)"),
            ('app_id', "org.projectluma.Other"), ('branch', "../stable"),
            ('screenshots', [{"url": CANVAS['icon']['url'], "sha256": SHA}] * 13),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value), self.assertRaises(CatalogError):
                validate_catalog(catalog({**CANVAS, field: value}))

    def test_duplicates_are_ambiguous_and_refused(self):
        with self.assertRaises(CatalogError):
            validate_catalog(catalog(CANVAS, CANVAS))
        with self.assertRaises(CatalogError):
            validate_catalog(catalog(CANVAS, {**CANVAS, 'id': 'canvas-two'}))
        with self.assertRaises(CatalogError):
            validate_catalog(catalog(CANVAS, collections=[{"id": "a", "name": "A"}, {"id": "a", "name": "B"}]))

    def test_unknown_sign_in_and_verification_values_are_read_as_unknown(self):
        entry = validate_catalog(catalog({**CANVAS, 'sign_in': 'retina-scan',
                                          'developer': {**CANVAS['developer'], 'verified': 'blood-oath'}})).applications[0]
        self.assertEqual((entry.sign_in, entry.developer.verified), ('', ''))

    def test_the_lifted_bounds(self):
        entries = [{**CANVAS, 'id': f'app-{index}', 'app_id': f'org.example.App{index}',
                    'source_id': f'org.example.App{index}'} for index in range(depot_catalog.MAX_SIGNED_APPLICATIONS)]
        for entry in entries:
            for key in ('description', 'screenshots', 'permissions', 'permission_changes', 'a_field_from_the_future'):
                entry.pop(key)
        self.assertEqual(len(validate_catalog(catalog(*entries)).applications), 5000)
        entries.append({**entries[0], 'id': 'one-too-many', 'source_id': 'org.example.Extra', 'app_id': ''})
        with self.assertRaises(CatalogError):
            validate_catalog(catalog(*entries))

    def test_generation_time_must_be_a_zoned_timestamp(self):
        for value in ('2026-09-30', '2026-09-30T18:00:00', 'yesterday'):
            with self.subTest(value=value), self.assertRaises(CatalogError):
                validate_catalog({**catalog(CANVAS), 'generated_at': value})


FIRST_PARTY = DATA / 'depot-first-party.json'


@unittest.skipUnless(FIRST_PARTY.is_file() and (DATA.parent / 'tools').is_dir(),
                     'the private first-party input is not part of the source package')
class SeedInputs(unittest.TestCase):
    def test_the_committed_seed_is_generated_from_its_inputs(self):
        tool = DATA.parent / 'tools/generate-depot-seed.py'
        result = subprocess.run([sys.executable, str(tool), '--check', str(DATA / 'depot-catalog-4.json')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_first_party_input_is_private(self):
        seed = json.loads((DATA / 'depot-catalog-4.json').read_text())
        first_party = json.loads((DATA / 'depot-first-party.json').read_text())
        private = {row['id'] for row in first_party['applications']
                   if {**first_party['defaults'], **row}['visibility'] not in ('public', 'unlisted')}
        # Six held behind their public gate, plus Sticky Notes, withdrawn.
        self.assertEqual(len(private), 7)
        self.assertIn('sticky-notes', private)
        shipped = {row['id'] for row in seed['applications']}
        self.assertFalse(private & shipped)
        self.assertTrue(all(row['visibility'] in ('public', 'unlisted') for row in seed['applications']))
        # 47 listed apps (7-Zip and Ollama are withdrawn from schema 4), the
        # 23 public first-party identities (21 baseline and optional Darkroom/
        # Imager; Sticky Notes is withdrawn), plus one verified developer.
        self.assertEqual(len(shipped), 71)
        self.assertEqual({c['id'] for c in seed['collections']}, {'office', 'creative', 'studio'})

    def test_first_party_entries_validate_once_public(self):
        first_party = json.loads((DATA / 'depot-first-party.json').read_text())
        rows = []
        for row in first_party['applications']:
            entry = {**first_party['defaults'], **row, 'visibility': 'public'}
            entry.setdefault('reference_url', f"https://simplyluma.com/apps/{entry['id']}")
            rows.append(entry)
        parsed = validate_catalog(catalog(*rows, collections=first_party['collections']))
        self.assertEqual(len(parsed.applications), 30)
        self.assertEqual(parsed.skipped, 0)
        # Optional apps retain their native identity for older installations;
        # that identity does not claim inclusion in the current base image.
        self.assertTrue(all(entry.luma_system is not None for entry in parsed.applications))
        optional = {identifier for collection in first_party['collections']
                    for identifier in collection['applications']}
        self.assertTrue(all(not entry.preinstalled_on_luma for entry in parsed.applications
                            if entry.id in optional))
        self.assertTrue(parsed.find('tide').preinstalled_on_luma)
        self.assertFalse(parsed.find('imager').preinstalled_on_luma)



    def test_verified_listings_install_from_the_luma_remote(self):
        verified = json.loads((DATA / 'depot-verified.json').read_text())
        rows = [{**verified['defaults'], **row} for row in verified['applications']]
        parsed = validate_catalog(catalog(*rows))
        self.assertEqual(parsed.skipped, 0)
        self.assertEqual({entry.id for entry in parsed.applications}, {'viola'})
        for entry in parsed.applications:
            self.assertEqual((entry.tier, entry.backend, entry.repository), ('verified', 'flatpak', 'luma'))
            self.assertEqual(entry.source_id, entry.app_id)
            # Viola also ships in the image; where it does, Depot must not offer the Flatpak.
            self.assertEqual((entry.luma_system.package, entry.luma_system.desktop_id),
                             ('viola-browser-stable', 'com.rhyme.viola.desktop'))
            self.assertTrue(entry.permissions)


class Seed(unittest.TestCase):
    def test_the_seed_ships_no_private_entry(self):
        seed = json.loads((DATA / 'depot-catalog-4.json').read_text())
        self.assertTrue(all(row['visibility'] in ('public', 'unlisted') for row in seed['applications']))
        self.assertFalse({'write', 'grid', 'stage', 'canvas', 'session', 'reel'}
                         & {row['id'] for row in seed['applications']})
        self.assertEqual({c['id'] for c in seed['collections']}, {'office', 'creative', 'studio'})

    def test_the_seed_lists_what_the_schema_3_document_lists(self):
        legacy = depot_catalog.load_catalog(DATA / 'depot-catalog.json')
        seed = depot_catalog.load_catalog(DATA / 'depot-catalog-4.json')
        listed = {e.id for e in seed.applications if e.tier == 'listed'}
        # depot-channels.json withdraws two command-line tools from schema 4
        # only; older Depots keep reading them from catalog-3.
        self.assertEqual({e.id for e in legacy.applications} - {'ollama', 'sevenzip'}, listed)

    def test_first_party_native_identity_and_flatpak_fallback_are_preserved(self):
        seed = depot_catalog.load_catalog(DATA / 'depot-catalog-4.json')
        first_party = [e for e in seed.applications if e.tier == 'luma']
        self.assertEqual(len(first_party), 23)
        # Published first-party apps retain native provenance and use their existing beta Flatpak identity.
        wave1 = {'tide', 'darkroom', 'leaf', 'notes', 'calendar', 'contacts', 'weather', 'tasks',
                 'monitor', 'photos', 'camera', 'connect'}
        for entry in first_party:
            self.assertEqual(entry.app_id + '.desktop', entry.luma_system.desktop_id)
            if entry.id in wave1:
                self.assertEqual((entry.backend, entry.repository, entry.source_id, entry.branch),
                                 ('flatpak', 'luma', entry.app_id, 'beta'), entry.id)
                self.assertEqual(entry.flatpak, depot_catalog.FlatpakSource('luma', entry.app_id, 'beta'))
            else:
                self.assertEqual((entry.backend, entry.repository, entry.source_id),
                                 ('rpm', 'luma', entry.luma_system.package), entry.id)
                self.assertIsNone(entry.flatpak, entry.id)
        by_id = {e.id: e for e in first_party}
        self.assertEqual(by_id['filer'].luma_system, depot_catalog.LumaSystemSource(
            'nautilus', 'org.gnome.Nautilus.desktop', False))
        core = {e.id for e in first_party if e.luma_system.package == 'prairie-core-apps'}
        self.assertEqual(core, {'notes', 'calendar', 'contacts', 'messages', 'phone', 'photos', 'camera',
                                'clock', 'weather', 'tasks', 'voice-memos'})
        # Apps the system or Settings needs are never offered for removal.
        self.assertEqual({e.id for e in first_party if not e.luma_system.removable},
                         {'filer', 'connect', 'monitor', 'displays', 'mods'})

    def test_preinstalled_flags_match_actual_image_pins(self):
        pins_file = DATA.parents[2] / 'config/desktop/packages.txt'
        if not pins_file.is_file():
            self.skipTest('the image pins are not part of the package build')
        pins = pins_file.read_text()
        seed = depot_catalog.load_catalog(DATA / 'depot-catalog-4.json')
        for entry in seed.applications:
            if entry.luma_system is not None:
                included = '\n' + entry.luma_system.package + '-' in pins
                self.assertEqual(entry.preinstalled_on_luma, included, entry.id)

    def test_downloadable_collections_do_not_claim_baseline_installation(self):
        seed = depot_catalog.load_catalog(DATA / 'depot-catalog-4.json')
        optional = {identifier for collection in seed.collections
                    for identifier in collection.applications}
        self.assertNotIn('tide', optional)
        self.assertTrue(seed.find('tide').preinstalled_on_luma)
        for entry in seed.applications:
            if entry.id in optional or entry.id == 'imager':
                self.assertFalse(entry.preinstalled_on_luma, entry.id)


if __name__ == '__main__':
    unittest.main()
