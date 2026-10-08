"""Valet's uninstall window for apps Luma provides, and the data it keeps.

Each test names the case it exists to catch. On the code before this change
the first two raised "managed with Luma's system software" (the window then
fell back to "Application", a generic icon and a red Uninstall), the third had
no keep-by-default decision to test, and the fourth removed an essential app.
"""
import sys
import types
import unittest
from unittest.mock import patch

from luma_installer import manager, workflow
from luma_installer.errors import InstallerError


class FakeIcon:
    def __init__(self, name): self.name = name
    def to_string(self): return self.name


class FakeDesktop:
    def __init__(self, desktop_id, name, icon, strings=None):
        self.desktop_id, self.name, self.icon, self.strings = desktop_id, name, icon, strings or {}
    def get_id(self): return self.desktop_id
    def get_name(self): return self.name
    def get_display_name(self): return self.name
    def get_icon(self): return FakeIcon(self.icon)
    def get_string(self, key): return self.strings.get(key)
    def get_filename(self): return '/usr/share/applications/' + self.desktop_id


def fake_gi(apps):
    """A gi whose Gio sees only ``apps``; installed with patch.dict so it never leaks."""
    by_id = {app.get_id(): app for app in apps}
    gio = types.SimpleNamespace(
        DesktopAppInfo=types.SimpleNamespace(new=lambda desktop_id: by_id.get(desktop_id)),
        AppInfo=types.SimpleNamespace(get_all=lambda: list(apps)))
    gi = types.ModuleType('gi'); gi.require_version = lambda *_: None
    repository = types.ModuleType('gi.repository'); repository.Gio = gio
    gi.repository = repository
    return {'gi': gi, 'gi.repository': repository}


FILER = FakeDesktop('org.gnome.Nautilus.desktop', 'Filer', 'org.gnome.Nautilus')
NOTES = FakeDesktop('org.projectluma.Notes.desktop', 'Notes', 'org.projectluma.Notes')


class FakeWidget:
    def __init__(self, active=True):
        self.visible, self.active, self.classes, self.label = True, active, set(), ''
    def set_visible(self, value): self.visible = value
    def get_visible(self): return self.visible
    def set_active(self, value): self.active = value
    def get_active(self): return self.active
    def remove_css_class(self, name): self.classes.discard(name)


class FakeCard:
    """The parts of AppKit's TransactionCard the uninstall window drives."""
    def __init__(self, keep_active):
        self.primary, self.keep, self.message = FakeWidget(), FakeWidget(keep_active), ''
    def set_phase(self, phase, action, text='', fraction=0.0, *, removing=False, cancellable=False):
        # Mirrors TransactionCard.set_phase: destructive only when removing.
        self.primary.label, self.message = action, text
        self.primary.classes = {'destructive-action' if removing and phase == 'ready' else 'suggested-action'}
        self.keep.set_visible(removing and phase == 'ready')


def no_records():
    return patch.object(manager, '_record', side_effect=InstallerError('no record'))


class SystemAppTests(unittest.TestCase):
    def test_external_firefox_and_gimp_adopt_actual_native_owner(self):
        from luma_installer import layered_apps
        for package, identity in [('firefox', 'firefox.desktop'), ('gimp', 'org.gimp.GIMP.desktop')]:
            desktop = FakeDesktop(identity, package.title(), package)
            with self.subTest(package=package), patch.dict(sys.modules, fake_gi([desktop])), no_records(), \
                 patch.object(layered_apps, 'identity', return_value=package), \
                 patch.object(workflow, 'write_record') as written:
                report, record = workflow.installed_report(identity)
            self.assertTrue(record['removable'])
            self.assertTrue(record['external_layered'])
            self.assertEqual(record['package_name'], package)
            self.assertEqual(report.icon, package)
            self.assertEqual(report.details['Data retention'], 'Personal settings and files are retained')
            written.assert_called_once()
    def test_system_app_resolves_its_real_name_and_icon(self):
        with patch.dict(sys.modules, fake_gi([FILER, NOTES])), no_records(), \
                patch.object(workflow, 'write_record') as written:
            report, record = workflow.installed_report('org.projectluma.Notes.desktop')
        self.assertEqual(report.title, 'Notes')
        self.assertEqual(report.icon, 'org.projectluma.Notes')
        self.assertNotEqual(report.title, 'Application')
        written.assert_not_called()   # describing a system app never adopts it

    def test_system_app_offers_no_uninstall_and_no_data_checkbox(self):
        from luma_installer.removal import apply_view, removal_view
        with patch.dict(sys.modules, fake_gi([NOTES])), no_records(), patch.object(workflow, 'write_record'):
            report, record = workflow.installed_report('org.projectluma.Notes.desktop')
        self.assertFalse(record['removable'])
        card = FakeCard(keep_active=False)
        apply_view(card, removal_view(record, report.title))
        self.assertEqual(card.primary.label, 'Close')
        self.assertNotIn('Uninstall', card.primary.label)
        self.assertFalse(card.keep.visible)
        self.assertNotIn('destructive-action', card.primary.classes)
        self.assertEqual(card.message, 'Notes is part of Luma and can’t be uninstalled.')
        with patch.object(manager, 'remove') as removed:
            with self.assertRaisesRegex(InstallerError, 'part of Luma'):
                workflow.remove(record, keep_data=False)
            removed.assert_not_called()


class KeepDataTests(unittest.TestCase):
    RECORD = {'application_id': 'flatpak-org.example.app', 'format': 'flatpak',
              'flatpak_id': 'org.example.App', 'name': 'Example', 'removable': True}

    def test_data_is_kept_unless_the_person_unchecks_it(self):
        from luma_installer.removal import apply_view, removal_view
        card = FakeCard(keep_active=False)   # what Nick saw: the box arrived unchecked
        apply_view(card, removal_view(self.RECORD, 'Example'), reset_keep=True)
        self.assertTrue(card.keep.visible)
        self.assertTrue(card.keep.active)
        self.assertEqual(card.primary.label, 'Uninstall')
        self.assertEqual(card.message, '')
        card.keep.set_active(False)
        apply_view(card, removal_view(self.RECORD, 'Example', card.keep.get_active()))
        self.assertEqual(card.primary.label, 'Uninstall and Delete Data')
        self.assertIn('deleted', card.message)
        self.assertIn('destructive-action', card.primary.classes)
        with patch.object(manager, 'remove', return_value='Removed.') as removed:
            workflow.remove(self.RECORD)
        removed.assert_called_once_with('flatpak-org.example.app', delete_data=False)


class EssentialAppTests(unittest.TestCase):
    def test_essential_apps_are_refused_however_installed(self):
        cases = [
            {'application_id': 'flatpak-org.gnome.ptyxis', 'format': 'flatpak',
             'flatpak_id': 'org.gnome.Ptyxis', 'installation': 'user', 'name': 'Terminal'},
            {'application_id': 'nautilus', 'format': 'rpm', 'package': 'nautilus', 'name': 'Filer'},
            {'application_id': 'valet', 'format': 'flatpak', 'flatpak_id': 'io.luma.Valet', 'name': 'Valet'},
            {'application_id': 'shell', 'format': 'portable', 'desktop_id': 'org.gnome.Shell.Extensions.desktop',
             'name': 'Extensions'},
        ]
        for record in cases:
            with self.subTest(record['name']):
                with patch.object(manager, 'remove') as removed, \
                        patch.object(manager, '_record', return_value=record), \
                        patch.object(manager, '_run') as ran:
                    with self.assertRaisesRegex(InstallerError, 'part of Luma'):
                        workflow.remove(record)
                    removed.assert_not_called()
                    ran.assert_not_called()
        # The direct removal path refuses on its own, without the window.
        record = cases[0]
        with patch.object(manager, '_record', return_value=record), patch.object(manager, '_run') as ran:
            with self.assertRaisesRegex(InstallerError, 'part of Luma'):
                manager.remove(record['application_id'], delete_data=True)
            ran.assert_not_called()
        from luma_installer.removal import is_essential
        self.assertTrue(all(is_essential(record) for record in cases))

    def test_an_essential_flatpak_opened_by_desktop_id_is_not_offered(self):
        terminal = FakeDesktop('org.gnome.Ptyxis.desktop', 'Terminal', 'org.gnome.Ptyxis',
                               {'X-Flatpak': 'org.gnome.Ptyxis'})
        with patch.dict(sys.modules, fake_gi([terminal])), no_records(), \
                patch.object(workflow, 'write_record') as written, patch.object(workflow.subprocess, 'run') as ran:
            report, record = workflow.installed_report('org.gnome.Ptyxis.desktop')
        self.assertEqual(report.title, 'Terminal')
        self.assertFalse(record['removable'])
        written.assert_not_called(); ran.assert_not_called()

    def test_ordinary_apps_stay_removable(self):
        from luma_installer.removal import is_removable
        self.assertTrue(is_removable(KeepDataTests.RECORD))
        self.assertFalse(is_removable({'format': 'system', 'name': 'Notes'}))


if __name__ == '__main__':
    unittest.main()
