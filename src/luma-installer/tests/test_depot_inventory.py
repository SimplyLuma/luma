import unittest
from luma_installer.depot_inventory import visible_applications, removal_arguments


class App:
    def __init__(self, identity, name='App', show=True, **metadata):
        self.identity, self.name, self.show, self.metadata = identity, name, show, metadata
    def get_id(self): return self.identity
    def get_name(self): return self.name
    def get_display_name(self): return self.name
    def should_show(self): return self.show
    def get_string(self, key): return self.metadata.get(key)


class InventoryTests(unittest.TestCase):
    def test_matches_filer_visibility_and_identity_deduplication(self):
        apps = [App('b.desktop', 'B'), App('a.desktop', 'A'), App('a.desktop', 'duplicate'),
                App(None), App('hidden.desktop', show=False)]
        result = visible_applications(apps)
        self.assertEqual([a.name for a in result], ['A', 'B'])
        self.assertTrue(all(not a.can_review_removal for a in result))

    def test_flatpak_can_open_existing_removal_review(self):
        app = visible_applications([App('org.app.Name.desktop', **{'X-Flatpak': 'org.app.Name'})])[0]
        self.assertEqual(app.provider, 'Flatpak')
        self.assertEqual(removal_arguments(app), ['luma-installer', '--remove', 'org.app.Name.desktop'])

    def test_snap_without_exact_receipt_is_protected(self):
        apps = [App('spotify_spotify.desktop', **{'X-SnapInstanceName': 'spotify'})]
        app = visible_applications(apps)[0]
        self.assertFalse(app.can_review_removal)
        with self.assertRaises(ValueError): removal_arguments(app)
        record = {'format': 'snap', 'package_name': 'spotify', 'system_receipt': 'receipt'}
        self.assertTrue(visible_applications(apps, [record])[0].can_review_removal)
        self.assertFalse(visible_applications(apps, [record, record])[0].can_review_removal)

    def test_managed_native_record_not_app_name_controls_review(self):
        apps = [App('org.projectluma.Installed.test.desktop', 'System'), App('other.desktop', 'System')]
        record = {'application_id': 'test', 'format': 'rpm'}
        result = visible_applications(apps, [record])
        by_id = {a.desktop_id: a for a in result}
        self.assertTrue(by_id[apps[0].get_id()].can_review_removal)
        self.assertFalse(by_id['other.desktop'].can_review_removal)

    def test_invalid_desktop_id_not_forwarded_to_process(self):
        for identity in ('../app.desktop', '-option.desktop', 'no-suffix'):
            app = visible_applications([App(identity, **{'X-Flatpak': 'org.app.Name'})])[0]
            with self.subTest(identity=identity), self.assertRaises(ValueError): removal_arguments(app)


if __name__ == '__main__': unittest.main()
