# SPDX-License-Identifier: Apache-2.0
"""ADR-031: schema 4 ``sources`` and ``preinstalled_on_luma``."""

from copy import deepcopy
import unittest

from luma_installer.depot_catalog import (CatalogError, FlatpakSource, LumaSystemSource,
                                          validate_catalog)

BASE = {
    "tier": "luma", "visibility": "public", "distribution": "publisher",
    "architectures": ["x86_64", "aarch64"], "qualification": "pending",
    "qualification_reason": "Included with Luma.",
    "developer": {"id": "dev_projectluma", "name": "Project Luma", "verified": "organization"},
}

NOTES = {**BASE, "id": "notes", "name": "Notes", "app_id": "org.projectluma.Notes",
         "backend": "rpm", "repository": "luma", "source_id": "prairie-core-apps",
         "reference_url": "https://simplyluma.com/apps/notes",
         "sources": {"luma_system": {"package": "prairie-core-apps",
                                     "desktop_id": "org.projectluma.Notes.desktop", "removable": True}},
         "preinstalled_on_luma": True}

CALENDAR = {**NOTES, "id": "calendar", "name": "Calendar", "app_id": "org.projectluma.Calendar",
            "reference_url": "https://simplyluma.com/apps/calendar",
            "sources": {"luma_system": {"package": "prairie-core-apps",
                                        "desktop_id": "org.projectluma.Calendar.desktop", "removable": True}}}

TIDE = {**BASE, "id": "tide", "name": "Tide", "app_id": "org.projectluma.Tide",
        "backend": "flatpak", "repository": "luma", "source_id": "org.projectluma.Tide", "branch": "beta",
        "reference_url": "https://simplyluma.com/apps/tide",
        "sources": {"luma_system": {"package": "luma-tide", "desktop_id": "org.projectluma.Tide.desktop",
                                    "removable": True},
                    "flatpak": {"repository": "luma", "source_id": "org.projectluma.Tide", "branch": "beta"}},
        "preinstalled_on_luma": True}

LEGACY_CANVAS = {**BASE, "id": "canvas", "name": "Canvas", "app_id": "org.projectluma.Canvas",
                 "backend": "flatpak", "repository": "luma", "source_id": "org.projectluma.Canvas",
                 "reference_url": "https://simplyluma.com/apps/canvas"}


def catalog(*entries):
    return {"schema_version": 4, "generated_at": "2026-09-30T18:00:00Z",
            "applications": [deepcopy(entry) for entry in entries]}


class Sources(unittest.TestCase):
    def test_system_only_entries_share_a_package_and_keep_their_own_desktop_file(self):
        parsed = validate_catalog(catalog(NOTES, CALENDAR))
        notes, calendar = parsed.applications
        self.assertEqual(notes.luma_system,
                         LumaSystemSource('prairie-core-apps', 'org.projectluma.Notes.desktop', True))
        self.assertEqual(calendar.luma_system.desktop_id, 'org.projectluma.Calendar.desktop')
        self.assertIsNone(notes.flatpak)
        self.assertTrue(notes.preinstalled_on_luma)
        self.assertEqual(parsed.skipped, 0)

    def test_both_sources(self):
        entry = validate_catalog(catalog(TIDE)).applications[0]
        self.assertEqual(entry.flatpak, FlatpakSource('luma', 'org.projectluma.Tide', 'beta'))
        self.assertEqual(entry.luma_system.package, 'luma-tide')

    def test_entries_without_sources_still_describe_their_flatpak(self):
        entry = validate_catalog(catalog(LEGACY_CANVAS)).applications[0]
        self.assertEqual(entry.flatpak, FlatpakSource('luma', 'org.projectluma.Canvas', 'stable'))
        self.assertIsNone(entry.luma_system)
        self.assertFalse(entry.preinstalled_on_luma)

    def test_schema3_flatpak_entries_get_a_flatpak_source(self):
        parsed = validate_catalog({"schema_version": 3, "reviewed_on": "2026-09-01", "applications": [{
            "id": "gimp", "name": "GIMP", "backend": "flatpak", "source_id": "org.gimp.GIMP",
            "repository": "flathub", "distribution": "community", "architectures": ["x86_64"],
            "reference_url": "https://flathub.org/apps/org.gimp.GIMP", "qualification": "pending",
            "qualification_reason": "Listed."}]})
        self.assertEqual(parsed.applications[0].flatpak, FlatpakSource('flathub', 'org.gimp.GIMP', 'stable'))

    def test_removable_defaults_to_false(self):
        entry = deepcopy(NOTES)
        del entry['sources']['luma_system']['removable']
        self.assertFalse(validate_catalog(catalog(entry)).applications[0].luma_system.removable)

    def test_unknown_source_kinds_and_fields_are_ignored(self):
        entry = deepcopy(TIDE)
        entry['sources']['snap'] = {"name": "tide"}
        self.assertEqual(validate_catalog(catalog(entry)).applications[0].luma_system.package, 'luma-tide')

    def test_malformed_sources_refuse_the_catalogue(self):
        for mutate in (
            lambda e: e.__setitem__('sources', []),
            lambda e: e['sources']['luma_system'].__setitem__('package', '-rf'),
            lambda e: e['sources']['luma_system'].__setitem__('package', 'a b'),
            lambda e: e['sources']['luma_system'].__setitem__('package', '../x'),
            lambda e: e['sources']['luma_system'].__setitem__('desktop_id', 'Notes'),
            lambda e: e['sources']['luma_system'].__setitem__('desktop_id', '../x.desktop'),
            lambda e: e['sources']['luma_system'].__setitem__('removable', 'yes'),
            lambda e: e.__setitem__('preinstalled_on_luma', 1),
        ):
            entry = deepcopy(NOTES)
            mutate(entry)
            with self.assertRaises(CatalogError):
                validate_catalog(catalog(entry))
        entry = deepcopy(TIDE)
        entry['sources']['flatpak']['source_id'] = 'not a flatpak id'
        with self.assertRaises(CatalogError):
            validate_catalog(catalog(entry))

    def test_only_luma_tier_entries_may_name_an_image_package(self):
        entry = deepcopy(TIDE)
        entry['tier'] = 'verified'
        entry['sources']['luma_system']['desktop_id'] = 'org.gnome.Nautilus.desktop'
        parsed = validate_catalog(catalog(entry)).applications[0]
        self.assertIsNone(parsed.luma_system)
        self.assertFalse(parsed.preinstalled_on_luma)
        self.assertIsNotNone(parsed.flatpak)
        # A developer's Flatpak may say the image ships the same app (its own
        # desktop file), so Depot never installs a second copy; never removable.
        entry = deepcopy(TIDE)
        entry['tier'] = 'verified'
        parsed = validate_catalog(catalog(entry)).applications[0]
        self.assertEqual(parsed.luma_system,
                         LumaSystemSource('luma-tide', 'org.projectluma.Tide.desktop', False))
        self.assertIsNotNone(parsed.flatpak)
        # With no Flatpak either, a non-Luma rpm/luma listing has nothing to describe.
        entry = deepcopy(NOTES)
        entry['tier'] = 'verified'
        self.assertEqual(validate_catalog(catalog(entry)).skipped, 1)

    def test_rpm_luma_needs_its_package_and_no_flatpak(self):
        for mutate in (
            lambda e: e.pop('sources'),
            lambda e: e.__setitem__('source_id', 'luma-tide'),
            lambda e: e['sources'].__setitem__('flatpak', {"repository": "luma",
                                                           "source_id": "org.projectluma.Notes"}),
        ):
            entry = deepcopy(NOTES)
            mutate(entry)
            parsed = validate_catalog(catalog(entry))
            self.assertEqual((len(parsed.applications), parsed.skipped), (0, 1))

    def test_a_flatpak_source_that_disagrees_with_the_top_level_is_left_out(self):
        for key, value in (('source_id', 'org.projectluma.Other'), ('branch', 'stable'),
                           ('repository', 'flathub')):
            entry = deepcopy(TIDE)
            entry['sources']['flatpak'][key] = value
            parsed = validate_catalog(catalog(entry))
            self.assertEqual((len(parsed.applications), parsed.skipped), (0, 1), key)

    def test_one_desktop_file_is_listed_once(self):
        other = deepcopy(CALENDAR)
        other['sources']['luma_system']['desktop_id'] = 'org.projectluma.Notes.desktop'
        with self.assertRaises(CatalogError):
            validate_catalog(catalog(NOTES, other))
        flatpak = deepcopy(TIDE)
        flatpak['id'] = 'tide-two'
        flatpak['source_id'] = flatpak['app_id'] = flatpak['sources']['flatpak']['source_id'] = 'org.projectluma.Tide2'
        with self.assertRaises(CatalogError):
            validate_catalog(catalog(TIDE, flatpak))

    def test_an_older_schema4_client_skips_system_only_entries(self):
        # A Depot without ADR-031 knows no rpm repository called luma. Its
        # schema 4 validator skips the source rather than refusing the catalogue.
        from luma_installer import depot_catalog
        older = {**depot_catalog._REPOSITORIES, 'flatpak': {'flathub', 'luma'}}
        self.assertFalse(depot_catalog._check_source(deepcopy(NOTES), older, 4))
        self.assertTrue(depot_catalog._check_source(deepcopy(TIDE), older, 4))

    def test_private_entries_stay_out(self):
        entry = deepcopy(NOTES)
        entry['visibility'] = 'private'
        self.assertEqual(validate_catalog(catalog(entry)).skipped, 1)

    def test_rpm_luma_entries_are_never_installable(self):
        self.assertFalse(validate_catalog(catalog(NOTES)).applications[0].installable)


if __name__ == '__main__':
    unittest.main()
