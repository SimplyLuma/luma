# SPDX-License-Identifier: Apache-2.0
"""How Depot shows and drives apps from their publisher's official channel."""

from pathlib import Path
import sys
import unittest
from unittest import mock

HERE = Path(__file__).resolve()
if (HERE.parents[2] / 'luma-depot').is_dir():
    sys.path.insert(0, str(HERE.parents[2] / 'luma-depot'))

try:
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import GLib  # noqa: F401
    from luma_depot import channels
    from luma_depot.providers import ProviderError
except (ImportError, ValueError):  # pragma: no cover - needs PyGObject and Depot
    gi = None

from luma_installer.depot_catalog import validate_catalog

sys.path.insert(0, str(HERE.parent))
from test_depot_channels import MEGA, NORDVPN  # noqa: E402

FLATHUB = {"id": "gimp", "name": "GIMP", "tier": "listed", "visibility": "public", "backend": "flatpak",
           "repository": "flathub", "source_id": "org.gimp.GIMP", "app_id": "org.gimp.GIMP",
           "distribution": "publisher", "architectures": ["x86_64"], "reference_url": "https://flathub.org/apps/org.gimp.GIMP",
           "qualification": "pending", "qualification_reason": "Listed.",
           "channel": {"kind": "flathub", "title": "Flathub", "publisher_verified": True}}
FIGMA = {"id": "figma", "name": "Figma", "tier": "listed", "visibility": "public", "backend": "download",
         "repository": "figma-linux", "source_id": "figma-desktop", "distribution": "publisher",
         "architectures": ["x86_64"], "reference_url": "https://www.figma.com/downloads/",
         "qualification": "pending", "qualification_reason": "No Linux app.", "homepage": "https://www.figma.com/",
         "channel": {"kind": "publisher", "title": "figma.com", "web": True,
                     "reason": "Figma publishes no Linux app; it runs in the browser."}}


def catalog(*entries):
    return validate_catalog({"schema_version": 4, "generated_at": "2026-09-18T00:00:00Z",
                             "applications": list(entries)})


@unittest.skipIf(gi is None, 'needs PyGObject and luma_depot')
class Presentation(unittest.TestCase):
    def entries(self):
        return {e.id: e for e in catalog(NORDVPN, MEGA, FLATHUB, FIGMA).applications}

    def test_source_reads_honestly(self):
        entries = self.entries()
        snap = channels.presentation(entries['nordvpn'], installable=True)
        self.assertEqual(snap['source_label'], 'From NordVPN’s snap · Verified')
        self.assertTrue(snap['source_verified'])
        self.assertEqual(snap['source_verifier'], 'the Snap Store')
        self.assertEqual(snap['update_note'], 'Automatic, from the Snap Store')
        self.assertEqual(channels.presentation(entries['mega'], installable=True)['source_label'],
                         'From MEGA’s software repository')
        self.assertEqual(channels.presentation(entries['gimp'], installable=True)['source_label'],
                         'From Flathub · Verified')
        figma = channels.presentation(entries['figma'], installable=False)
        self.assertTrue(figma['web_app'])
        self.assertIn('no Linux app', figma['publisher_reason'])

    def test_kind_and_availability(self):
        entries = self.entries()
        self.assertEqual(channels.kind(entries['nordvpn']), 'snap')
        self.assertEqual(channels.kind(entries['mega']), 'repository')
        self.assertEqual(channels.kind(entries['gimp']), '')
        present = Path(__file__)
        self.assertEqual(channels.available(entries['nordvpn'], 'x86_64', snap_path=present, helper=present),
                         (True, ''))
        self.assertFalse(channels.available(entries['nordvpn'], 'x86_64', snap_path=Path('/nonexistent'),
                                            helper=present)[0])
        self.assertFalse(channels.available(entries['mega'], 'riscv64', rpm_ostree=present, helper=present)[0])


@unittest.skipIf(gi is None, 'needs PyGObject and luma_depot')
class InstalledState(unittest.TestCase):
    def test_snaps_and_packages_map_to_their_listings(self):
        snaps = {'nordvpn': {'version': '5.4.0', 'publisher': 'nordvpn', 'installed_bytes': 60,
                             'desktop_files': ('/var/lib/snapd/desktop/applications/nordvpn_nordvpn.desktop',)}}
        packages = {'megasync': ('6.6.2', ('/usr/share/applications/megasync.desktop',))}
        state = channels.State(catalog(NORDVPN, MEGA), snaps=snaps, packages=packages)
        self.assertEqual(state.installed['nordvpn']['version'], '5.4.0')
        self.assertTrue(state.installed['nordvpn']['managed'])
        self.assertEqual(state.identities(), {'nordvpn_nordvpn.desktop': 'catalog:nordvpn',
                                              'megasync.desktop': 'catalog:mega'})

    def test_a_snap_from_someone_else_is_not_depots_to_remove(self):
        snaps = {'nordvpn': {'version': '1', 'publisher': 'impostor', 'installed_bytes': 0, 'desktop_files': ()}}
        state = channels.State(catalog(NORDVPN), snaps=snaps, packages={})
        self.assertFalse(state.installed['nordvpn']['managed'])

    def test_installed_snaps_reads_snapd(self):
        reply = [{'name': 'nordvpn', 'version': '5.4.0', 'publisher': {'username': 'nordvpn'},
                  'installed-size': 12, 'apps': [{'name': 'nordvpn', 'desktop-file': '/x/nordvpn_nordvpn.desktop'},
                                                 {'name': 'nordvpnd'}]}]
        found = channels.installed_snaps(reader=lambda target: reply)
        self.assertEqual(found['nordvpn']['desktop_files'], ('/x/nordvpn_nordvpn.desktop',))

    def test_installed_packages_reads_rpm(self):
        def run(arguments, **_kwargs):
            if arguments[:2] == ['rpm', '-q']:
                return mock.Mock(stdout='megasync\t6.6.2\n')
            return mock.Mock(stdout='/usr/bin/megasync\n/usr/share/applications/megasync.desktop\n')
        self.assertEqual(channels.installed_packages(['megasync', 'teamviewer'], runner=run),
                         {'megasync': ('6.6.2', ('/usr/share/applications/megasync.desktop',))})


class FakeProcess:
    def __init__(self, lines, code):
        self.stdout = iter(line + '\n' for line in lines)
        self.code = code

    def wait(self):
        return self.code


@unittest.skipIf(gi is None, 'needs PyGObject and luma_depot')
class Helper(unittest.TestCase):
    def setUp(self):
        self.entry = catalog(NORDVPN).applications[0]
        self.progress = []
        patcher = mock.patch.object(channels.GLib, 'idle_add', lambda fn, value: self.progress.append(value))
        patcher.start()
        self.addCleanup(patcher.stop)
        journal = mock.patch('luma_installer.depot_errors.journal')
        journal.start()
        self.addCleanup(journal.stop)

    def run_helper(self, lines, code, action='install'):
        calls = []

        def popen(arguments, **_kwargs):
            calls.append(arguments)
            return FakeProcess(lines, code)
        return channels.run('snap', action, self.entry, 'catalog:nordvpn', lambda p: None, popen=popen), calls

    def test_success_reports_progress_and_result(self):
        result, calls = self.run_helper(['progress: 0.40 Downloading', 'result: installed'], 0)
        self.assertEqual(result, 'installed')
        self.assertEqual(calls, [['pkexec', channels.HELPER, 'snap-install', 'nordvpn']])
        self.assertEqual([p.stage for p in self.progress], ['Waiting for permission', 'Downloading'])

    def test_dismissed_password_prompt_is_plain(self):
        with self.assertRaises(ProviderError) as caught:
            self.run_helper([], 126)
        self.assertIn('did not get permission', caught.exception.hint)

    def test_refusal_is_shown_as_said(self):
        with self.assertRaises(ProviderError) as caught:
            self.run_helper(['refused: The Snap Store has not verified who publishes NordVPN.'], 3)
        self.assertEqual(caught.exception.hint, 'The Snap Store has not verified who publishes NordVPN.')

    def test_network_failure_is_explained(self):
        with self.assertRaises(ProviderError) as caught:
            self.run_helper(['error: cannot install "nordvpn": Post https://api.snapcraft.io: dial tcp: '
                             'lookup api.snapcraft.io: Temporary failure in name resolution (connection)'], 1)
        self.assertIn('could not be reached', caught.exception.hint)
        self.assertIn('api.snapcraft.io', caught.exception.detail)

    def test_repository_updates_have_no_verb(self):
        entry = catalog(MEGA).applications[0]
        with self.assertRaises(ProviderError) as caught:
            channels.run('repository', 'update', entry, 'catalog:mega', lambda p: None,
                         popen=lambda *a, **k: FakeProcess([], 0))
        self.assertIn('system updates', caught.exception.hint)


if __name__ == '__main__':
    unittest.main()


@unittest.skipIf(gi is None, 'needs PyGObject and luma_depot')
class InstalledWithoutALauncher(unittest.TestCase):
    """An installed app counts as installed even before the session sees its launcher."""

    def test_flatpak_and_snap_without_visible_launchers(self):
        from types import SimpleNamespace
        from luma_depot import native
        weather = {"id": "weather", "name": "Weather", "tier": "luma", "visibility": "public",
                   "backend": "flatpak", "repository": "luma", "source_id": "org.projectluma.Weather",
                   "app_id": "org.projectluma.Weather", "distribution": "publisher",
                   "architectures": ["x86_64"], "reference_url": "https://simplyluma.com/apps/weather",
                   "qualification": "pending", "qualification_reason": "Listed."}
        cat = catalog(weather, NORDVPN)
        ref = SimpleNamespace(get_appdata_version=lambda: '1.0', get_installed_size=lambda: 5,
                              get_origin=lambda: 'luma', get_commit=lambda: 'abc', get_branch=lambda: 'beta',
                              load_metadata=lambda *_: None)
        snaps = {'nordvpn': {'version': '5.4.0', 'publisher': 'nordvpn', 'installed_bytes': 1,
                             'desktop_files': ('/var/lib/snapd/desktop/applications/nordvpn_nordvpn-gui.desktop',)}}
        results = []
        real_state = channels.State
        with mock.patch.object(native, 'inventory', return_value=()), \
                mock.patch.object(native, 'installed_flatpak_refs',
                                  return_value={'org.projectluma.Weather': (None, ref)}), \
                mock.patch.object(native, 'local_permissions', return_value=()), \
                mock.patch.object(native.NativeInstallation, '_pending_updates', return_value={}), \
                mock.patch('luma_installer.depot_catalog.local_catalog', return_value=cat), \
                mock.patch.object(native.channel_apps, 'State',
                                  lambda c: real_state(c, snaps=snaps, packages={})), \
                mock.patch.object(native, 'run_async', lambda work, callback, *_: results.append(work())):
            native.NativeInstallation().installed(lambda *_: None)
        found = {record.app_id: record for record in results[0]}
        self.assertEqual(found['catalog:weather'].version, '1.0')
        self.assertEqual(found['catalog:weather'].update_channel, 'beta')
        self.assertEqual(found['catalog:nordvpn'].version, '5.4.0')
        self.assertTrue(found['catalog:nordvpn'].managed)
