# SPDX-License-Identifier: Apache-2.0
"""ADR-028 section 7: sandbox metadata to plain-language permissions.

The cases follow the distribution pipeline's reference implementation
(scripts/depot/flatpak-permissions.py), so Depot's reading of an installed
app agrees with the catalogue's reading of the same release.
"""

import unittest

from luma_installer.depot_permissions import VOCABULARY, Grant, diff, from_metadata, widens


def metadata(context='', session='', system=''):
    text = '[Application]\nname=org.example.App\nruntime=org.example.Platform/x86_64/1\n\n'
    text += '[Context]\n' + context + '\n'
    if session:
        text += '\n[Session Bus Policy]\n' + session + '\n'
    if system:
        text += '\n[System Bus Policy]\n' + system + '\n'
    return text


def keys(grants):
    return {grant.key: grant for grant in grants}


class Mapping(unittest.TestCase):
    def test_the_vocabulary_table(self):
        cases = (
            ('shared=network;ipc;', 'network', 'standard'),
            ('filesystems=xdg-documents;', 'files.documents', 'sensitive'),
            ('filesystems=xdg-pictures/Screenshots;', 'files.pictures', 'sensitive'),
            ('filesystems=xdg-music;', 'files.music', 'sensitive'),
            ('filesystems=xdg-videos;', 'files.videos', 'sensitive'),
            ('filesystems=xdg-download;', 'files.downloads', 'sensitive'),
            ('filesystems=home;', 'files.home', 'high'),
            ('filesystems=host;', 'files.host', 'high'),
            ('filesystems=/;', 'files.host', 'high'),
            ('filesystems=host-os:ro;', 'files.host', 'high'),
            ('filesystems=/run/media;', 'files.removable', 'sensitive'),
            ('filesystems=~/Games;', 'files.other', 'sensitive'),
            ('filesystems=xdg-config/kdeglobals:ro;', 'files.other', 'sensitive'),
            ('devices=all;', 'devices.all', 'high'),
            ('devices=all;', 'devices.camera', 'sensitive'),
            ('devices=input;', 'devices.input', 'sensitive'),
            ('devices=usb;', 'devices.usb', 'sensitive'),
            ('devices=kvm;', 'devices.kvm', 'high'),
            ('features=bluetooth;', 'devices.bluetooth', 'sensitive'),
            ('features=devel;', 'sandbox.devel', 'high'),
            ('sockets=pulseaudio;', 'devices.microphone', 'sensitive'),
            ('filesystems=xdg-run/pipewire-0;', 'devices.microphone', 'sensitive'),
            ('sockets=pcsc;', 'devices.smartcard', 'sensitive'),
            ('sockets=cups;', 'printing', 'standard'),
            ('sockets=ssh-auth;', 'system.ssh-agent', 'high'),
            ('sockets=gpg-agent;', 'system.gpg-agent', 'high'),
            ('sockets=x11;', 'display.x11', 'high'),
            ('sockets=system-bus;', 'system.bus', 'high'),
            ('sockets=session-bus;', 'session.bus', 'high'),
        )
        for context, key, level in cases:
            with self.subTest(context=context, key=key):
                grants = keys(from_metadata(metadata(context)))
                self.assertIn(key, grants)
                self.assertEqual(grants[key].level, level)
                self.assertNotIn('cannot describe', grants[key].title)

    def test_bus_names(self):
        grants = keys(from_metadata(metadata(
            session='org.freedesktop.Flatpak=talk\norg.freedesktop.Notifications=talk\n'
                    'org.kde.StatusNotifierWatcher=talk\norg.freedesktop.portal.Desktop=talk\n'
                    'org.mpris.MediaPlayer2.example=own\norg.example.App.Helper=own\n'
                    'org.example.None=none\norg.example.Seen=see',
            system='org.freedesktop.GeoClue2=talk\norg.freedesktop.login1=talk'), 'org.example.App'))
        self.assertEqual(grants['sandbox.escape'].level, 'high')
        self.assertEqual(grants['notifications'].level, 'standard')
        self.assertEqual(grants['session.bus'].names, ('org.kde.StatusNotifierWatcher',))
        self.assertEqual(grants['session.bus'].level, 'sensitive')
        self.assertEqual(grants['location'].level, 'sensitive')
        self.assertEqual(grants['system.bus'].names, ('org.freedesktop.login1',))
        self.assertIn('org.freedesktop.login1', grants['system.bus'].detail)
        whole = keys(from_metadata(metadata('sockets=session-bus;')))['session.bus']
        self.assertEqual(whole.title, 'Talks to every app')

    def test_the_file_chooser_is_listed_unless_the_app_can_already_reach_everything(self):
        self.assertEqual(set(keys(from_metadata(metadata()))), {'files.portal'})
        self.assertNotIn('files.portal', keys(from_metadata(metadata('filesystems=home;'))))
        self.assertNotIn('files.portal', keys(from_metadata(metadata('filesystems=host:ro;'))))

    def test_fallback_x11_is_not_a_window_snooper(self):
        self.assertNotIn('display.x11', keys(from_metadata(metadata('sockets=x11;fallback-x11;wayland;'))))

    def test_read_only_negation_and_read_write_winning(self):
        grants = keys(from_metadata(metadata('filesystems=xdg-documents:ro;!home;')))
        self.assertTrue(grants['files.documents'].read_only)
        self.assertEqual(grants['files.documents'].title, 'Reads your Documents')
        self.assertNotIn('files.home', grants)
        both = keys(from_metadata(metadata('filesystems=xdg-documents:ro;xdg-documents/Work;')))
        self.assertFalse(both['files.documents'].read_only)

    def test_named_folders_are_named(self):
        grant = keys(from_metadata(metadata('filesystems=~/Games;/opt/tools:ro;')))['files.other']
        self.assertEqual(grant.names, ('/opt/tools:ro', '~/Games:rw'))
        self.assertEqual(grant.detail, '/opt/tools, ~/Games')

    def test_order_puts_the_most_serious_first(self):
        grants = from_metadata(metadata('shared=network;\nfilesystems=host;\nsockets=pulseaudio;'))
        self.assertEqual([grant.level for grant in grants], ['high', 'sensitive', 'standard'])

    def test_garbage_is_not_a_permission_list(self):
        self.assertEqual(from_metadata('this is [not a keyfile'), ())

    def test_unknown_keys_are_described_honestly(self):
        self.assertIn('cannot describe', Grant('devices.teleport', 'high').title)
        # 30 keys, plus the underscore spellings of the two hyphenated ones.
        self.assertEqual(len(VOCABULARY), 32)
        self.assertEqual(Grant('system.ssh_agent', 'high').title, Grant('system.ssh-agent', 'high').title)


class Changes(unittest.TestCase):
    def test_an_update_that_asks_for_more_is_flagged(self):
        before = from_metadata(metadata('shared=network;\nfilesystems=xdg-documents:ro;'))
        after = from_metadata(metadata('shared=network;\nfilesystems=xdg-documents;\nsockets=pulseaudio;',
                                       system='org.freedesktop.login1=talk'))
        changes = {change.key: change.change for change in diff(before, after)}
        self.assertEqual(changes, {'files.documents': 'widened', 'devices.microphone': 'added',
                                   'system.bus': 'added'})
        self.assertTrue(widens(diff(before, after)))
        self.assertEqual(diff(before, after)[0].change, 'added')

    def test_narrowing_and_removal_are_not_alarms(self):
        before = from_metadata(metadata('shared=network;\nfilesystems=home;'))
        after = from_metadata(metadata('shared=network;'))
        found = {(c.key, c.change) for c in diff(before, after)}
        # Losing the home folder brings back the file chooser; that asks for nothing.
        self.assertEqual(found, {('files.home', 'removed'), ('files.portal', 'added')})
        self.assertFalse(widens(diff(before, after)))
        self.assertEqual(diff(before, from_metadata(metadata('shared=network;\nfilesystems=home:ro;'))), ())
        self.assertEqual(diff(before, before), ())


if __name__ == '__main__':
    unittest.main()


class UpdateMetadata(unittest.TestCase):
    def test_unreadable_or_wrong_application_is_not_treated_as_no_permissions(self):
        for text in ('', 'broken', '[Application]\nname=other.app\n',
                     '[Application]\nname=org.example.App\nname=other.app\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                from_metadata(text, 'org.example.App', strict=True)
        self.assertEqual(keys(from_metadata(metadata('shared=network;'), 'org.example.App', strict=True)),
                         keys(from_metadata(metadata('shared=network;'), 'org.example.App')))


class FilesystemScopeUpdates(unittest.TestCase):
    def test_a_larger_or_different_folder_requires_review(self):
        for before, after in (('xdg-download/project:ro', 'xdg-download:ro'),
                              ('xdg-documents/project', 'xdg-documents'),
                              ('xdg-download/project-a', 'xdg-download/project-b'),
                              ('/media/drive-a:ro', '/media:ro'),
                              ('host-etc:ro', 'host:ro'),
                              ('host:ro', 'host-os:ro'), ('host:ro', 'host-etc:ro'),
                              ('xdg-documents/a:ro;xdg-documents/b', 'xdg-documents/a;xdg-documents/b')):
            with self.subTest(before=before, after=after):
                self.assertTrue(widens(diff(from_metadata(metadata('filesystems=' + before + ';')),
                                            from_metadata(metadata('filesystems=' + after + ';')))))

    def test_same_or_narrower_folder_does_not_require_review(self):
        for before, after in (('xdg-download:ro', 'xdg-download/project:ro'),
                              ('xdg-documents/project', 'xdg-documents/project:ro'),
                              ('/media', '/media/drive-a:ro'), ('home', '~:ro')):
            with self.subTest(before=before, after=after):
                self.assertFalse(widens(diff(from_metadata(metadata('filesystems=' + before + ';')),
                                             from_metadata(metadata('filesystems=' + after + ';')))))
