# SPDX-License-Identifier: Apache-2.0
"""Every application Depot lists draws something, on any computer.

The machine these describe is the one somebody has just switched on: no icon
cache, no downloaded application metadata, and either no network at all or a
slow one. The bug these exist to stop is an application drawing as an empty
box, which is what a toolkit does when it is handed an icon *name* that
nothing in the icon theme answers to -- no error, no exception, no icon.

Three states are covered for the whole packaged catalogue: first boot with
the network blocked, first boot with the network available but the metadata
not downloaded yet, and a settled machine that has it. In all three every
entry must resolve to a real file, a theme name the theme really has, or the
monogram placeholder -- and once the data is present, no entry may still be
falling back.
"""
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from luma_installer import depot_icons


DATA = Path(__file__).resolve().parent.parent / 'data'
CATALOG = DATA / 'depot-catalog-4.json'


def catalog_entries():
    return json.loads(CATALOG.read_text())['applications']


class NoTheme:
    """An icon theme with nothing in it: a first boot before anything exports."""

    calls: list

    def __init__(self):
        self.calls = []

    def __call__(self, name):
        self.calls.append(name)
        return False


class FirstBoot(unittest.TestCase):
    """No media cache, no metadata, nothing installed, network blocked."""

    def setUp(self):
        self.theme = NoTheme()

    def choose(self, entry, **extra):
        return depot_icons.choose(name=entry['name'], theme_has=self.theme, **extra)

    def test_every_listed_application_has_something_to_draw(self):
        entries = catalog_entries()
        self.assertTrue(entries)
        for entry in entries:
            choice = self.choose(entry)
            self.assertEqual(choice.kind, 'placeholder', entry['id'])
            self.assertTrue(choice.value.strip(), entry['id'])
            self.assertNotEqual(choice.value, '?', entry['id'])

    def test_no_application_is_handed_a_name_the_theme_does_not_have(self):
        """The empty box. A refused name must become the placeholder instead."""
        for entry in catalog_entries():
            choice = self.choose(entry, icon_name=entry['id'] + '.desktop')
            self.assertNotEqual(choice.kind, 'themed', entry['id'])

    def test_a_cached_appstream_file_name_is_never_a_theme_name(self):
        for spelling in ('org.mozilla.firefox.png', 'vlc_8f21.svg', 'Gimp.jpeg'):
            choice = depot_icons.choose(name='Some App', icon_name=spelling,
                                        theme_has=lambda _name: True)
            self.assertEqual(choice.kind, 'placeholder', spelling)

    def test_the_anonymous_cog_is_not_an_icon(self):
        for sentinel in sorted(depot_icons.SENTINELS):
            choice = depot_icons.choose(name='Some App', icon_name=sentinel,
                                        theme_has=lambda _name: True)
            self.assertEqual(choice.kind, 'placeholder', sentinel)

    def test_a_missing_media_file_does_not_win_over_the_placeholder(self):
        """An icon the catalogue names but the network never delivered."""
        choice = self.choose({'name': 'Android Studio', 'id': 'android-studio'},
                             media_path='/var/cache/nothing/abc.png')
        self.assertEqual(choice.kind, 'placeholder')
        self.assertEqual(choice.value, 'AS')

    def test_the_placeholder_is_the_same_on_every_run(self):
        first = [self.choose(entry).value for entry in catalog_entries()]
        second = [self.choose(entry).value for entry in catalog_entries()]
        self.assertEqual(first, second)


class WhenTheDataArrives(unittest.TestCase):
    """The metadata has been downloaded: nothing may still be a placeholder."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        for size in ('64x64', '128x128'):
            (self.base / 'icons' / size).mkdir(parents=True)

    def publish(self, entry, size='128x128'):
        name = entry['id'] + '.png'
        (self.base / 'icons' / size / name).write_bytes(b'\x89PNG\r\n\x1a\n')
        return name

    def test_every_listed_application_draws_its_own_artwork(self):
        entries = catalog_entries()
        for entry in entries:
            name = self.publish(entry)
            found = depot_icons.appstream_icon(self.base, name, size=128)
            self.assertIsNotNone(found, entry['id'])
            choice = depot_icons.choose(name=entry['name'], appstream_path=str(found),
                                        theme_has=NoTheme())
            self.assertEqual(choice.kind, 'file', entry['id'])
            self.assertTrue(Path(choice.value).is_file(), entry['id'])

    def test_an_installed_application_uses_the_theme_it_exported_to(self):
        choice = depot_icons.choose(name='Tide', icon_name='org.projectluma.Tide',
                                    theme_has=lambda name: name == 'org.projectluma.Tide')
        self.assertEqual(choice, depot_icons.Choice('themed', 'org.projectluma.Tide', 'theme'))

    def test_a_serialised_themed_icon_with_fallbacks_takes_the_first_it_has(self):
        value = '. GThemedIcon firefox firefox-symbolic'
        choice = depot_icons.choose(name='Firefox', icon_name=value,
                                    theme_has=lambda name: name == 'firefox-symbolic')
        self.assertEqual(choice.value, 'firefox-symbolic')

    def test_the_catalogue_icon_is_preferred_over_everything(self):
        media = self.base / 'verified.png'
        media.write_bytes(b'\x89PNG\r\n\x1a\n')
        appstream = self.base / 'icons/128x128/other.png'
        appstream.write_bytes(b'\x89PNG\r\n\x1a\n')
        choice = depot_icons.choose(name='Viola', media_path=str(media),
                                    appstream_path=str(appstream),
                                    theme_has=lambda _name: True)
        self.assertEqual(choice.source, 'catalog')
        self.assertEqual(choice.value, str(media))


class SizeAndScale(unittest.TestCase):
    """The right pixels for a 1x display and for a 1.25x one."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)

    def publish(self, *sizes, name='app.png'):
        for size in sizes:
            target = self.base / 'icons' / f'{size}x{size}' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'\x89PNG\r\n\x1a\n')
        return name

    def test_a_small_tile_at_1x_does_not_carry_the_large_artwork(self):
        name = self.publish(64, 128)
        found = depot_icons.appstream_icon(self.base, name, size=40, scale=1)
        self.assertEqual(found, self.base / 'icons/64x64' / name)

    def test_the_same_tile_at_a_fractional_scale_takes_the_larger_artwork(self):
        """A 1.25x display reports a scale factor of 2: 64 would be upscaled."""
        name = self.publish(64, 128)
        found = depot_icons.appstream_icon(self.base, name, size=40, scale=2)
        self.assertEqual(found, self.base / 'icons/128x128' / name)

    def test_a_large_tile_at_a_fractional_scale_takes_the_largest_there_is(self):
        name = self.publish(64, 128)
        found = depot_icons.appstream_icon(self.base, name, size=76, scale=2)
        self.assertEqual(found, self.base / 'icons/128x128' / name)

    def test_only_one_size_published_is_still_an_icon(self):
        name = self.publish(64)
        self.assertEqual(depot_icons.appstream_icon(self.base, name, size=76, scale=2),
                         self.base / 'icons/64x64' / name)

    def test_nothing_published_is_not_an_icon(self):
        self.assertIsNone(depot_icons.appstream_icon(self.base, 'absent.png', size=64))

    def test_a_component_cannot_name_a_file_outside_the_cache(self):
        secret = self.base / 'secret.png'
        secret.write_bytes(b'\x89PNG\r\n\x1a\n')
        for escape in ('../secret.png', '../../secret.png', '/etc/shadow', '.', '..', ''):
            self.assertIsNone(depot_icons.appstream_icon(self.base, escape, size=64), escape)


class TheMetadataFile(unittest.TestCase):
    """Which file libflatpak left behind must not decide whether icons work."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)

    def test_nothing_downloaded_yet(self):
        self.assertIsNone(depot_icons.appstream_document(self.base))
        self.assertIsNone(depot_icons.appstream_document(None))

    def test_plain_xml(self):
        (self.base / 'appstream.xml').write_text('<components/>')
        self.assertEqual(depot_icons.appstream_document(self.base),
                         self.base / 'appstream.xml')

    def test_only_the_compressed_copy(self):
        with gzip.open(self.base / 'appstream.xml.gz', 'wb') as stream:
            stream.write(b'<components/>')
        self.assertEqual(depot_icons.appstream_document(self.base),
                         self.base / 'appstream.xml.gz')

    def test_the_flatpak_directory_is_absent_on_a_new_computer(self):
        self.assertIsNone(depot_icons.flatpak_appstream_directory(
            'flathub', 'x86_64', roots=(str(self.base / 'appstream'),)))

    def test_the_flatpak_directory_is_found_once_it_is_there(self):
        active = self.base / 'appstream/flathub/x86_64/active'
        active.mkdir(parents=True)
        (active / 'appstream.xml').write_text('<components/>')
        self.assertEqual(
            depot_icons.flatpak_appstream_directory(
                'flathub', 'x86_64', roots=(str(self.base / 'appstream'),)),
            active)


class CatalogueIcons(unittest.TestCase):
    """An icon nothing could verify is not an icon."""

    def test_https_and_a_digest_are_both_required(self):
        digest = 'a' * 64
        self.assertTrue(depot_icons.usable_catalog_icon(
            {'url': 'https://dl.simplyluma.com/media/a.png', 'sha256': digest}))
        self.assertFalse(depot_icons.usable_catalog_icon(
            {'url': 'http://dl.simplyluma.com/media/a.png', 'sha256': digest}))
        self.assertFalse(depot_icons.usable_catalog_icon(
            {'url': 'https://dl.simplyluma.com/media/a.png', 'sha256': ''}))
        self.assertFalse(depot_icons.usable_catalog_icon(
            {'url': 'https://dl.simplyluma.com/media/a.png', 'sha256': 'A' * 64}))
        self.assertFalse(depot_icons.usable_catalog_icon(None))

    def test_the_packaged_catalogue_states_its_icon_coverage(self):
        """Whatever the seed carries, an icon it does state must be usable."""
        for entry in catalog_entries():
            icon = entry.get('icon')
            if icon is not None:
                self.assertTrue(depot_icons.usable_catalog_icon(icon), entry['id'])


class Monogram(unittest.TestCase):
    def test_two_words_give_their_initials(self):
        self.assertEqual(depot_icons.monogram('Google Chrome'), 'GC')
        self.assertEqual(depot_icons.monogram('Android Studio'), 'AS')

    def test_an_acronym_is_already_the_short_form(self):
        self.assertEqual(depot_icons.monogram('VLC'), 'VLC')
        self.assertEqual(depot_icons.monogram('GIMP'), 'GIMP')

    def test_a_long_capitalised_name_is_not_treated_as_an_acronym(self):
        self.assertEqual(depot_icons.monogram('REAPER'), 'R')

    def test_a_name_that_capitalises_inside_itself_shows_both_parts(self):
        self.assertEqual(depot_icons.monogram('qBittorrent'), 'QB')
        self.assertEqual(depot_icons.monogram('NordVPN'), 'NV')
        self.assertEqual(depot_icons.monogram('1Password'), '1P')

    def test_one_plain_word_gives_one_letter(self):
        self.assertEqual(depot_icons.monogram('Firefox'), 'F')

    def test_punctuation_and_emptiness_do_not_raise(self):
        self.assertEqual(depot_icons.monogram(''), '?')
        self.assertEqual(depot_icons.monogram('   '), '?')
        self.assertEqual(depot_icons.monogram('!!!'), '?')
        self.assertEqual(depot_icons.monogram('Foo-Bar'), 'FB')


if __name__ == '__main__':
    unittest.main()
