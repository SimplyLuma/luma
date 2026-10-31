# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import unittest
ROOT=Path(__file__).parents[2]
# Available in Depot, but their producers live in the separate product trees.
EXTERNAL_PRODUCTS = {'org.projectluma.Imager', 'org.projectluma.Grid',
                     'org.projectluma.Stage', 'org.projectluma.Session', 'org.projectluma.Reel'}
class Registry(unittest.TestCase):
    def test_connect_published_beta_remains_eligible_for_independent_updates(self):
        catalog=json.loads((ROOT/'src/luma-installer/data/depot-catalog-4.json').read_text())
        entry=next(a for a in catalog['applications'] if a['id']=='connect')
        self.assertEqual((entry['backend'],entry['repository'],entry['source_id'],entry['branch']),
                         ('flatpak','luma','org.projectluma.Connect','beta'))
        self.assertEqual(entry['architectures'],['x86_64','aarch64'])
        self.assertEqual(entry['sources']['flatpak'],
                         {'repository':'luma','source_id':'org.projectluma.Connect','branch':'beta'})
        self.assertEqual(entry['sources']['luma_system'],
                         {'package':'luma-continuity','desktop_id':'org.projectluma.Connect.desktop','removable':False})
        spec=__import__('importlib.util',fromlist=['x']).spec_from_file_location(
            'seed_generator',ROOT/'src/luma-installer/tools/generate-depot-seed.py')
        generator=__import__('importlib.util',fromlist=['x']).module_from_spec(spec)
        spec.loader.exec_module(generator)
        generated=next(a for a in json.loads(generator.generate())['applications'] if a['id']=='connect')
        self.assertEqual(generated,entry)

    def test_bundled_app_catalogue_supports_both_shipping_cpus(self):
        baseline=set(json.loads((ROOT/'config/os/first-party-app-baseline.json').read_text())['applications'])
        # Depot intentionally does not offer itself for installation in its
        # own catalogue; its host package supplies this generated seed.
        baseline.remove('org.projectluma.Depot')
        catalog=json.loads((ROOT/'src/luma-installer/data/depot-catalog-4.json').read_text())
        bundled={entry['app_id']:entry for entry in catalog['applications'] if entry.get('app_id') in baseline}
        self.assertEqual(set(bundled),baseline)
        self.assertEqual(len(bundled),len(baseline))
        for app_id,entry in bundled.items():
            with self.subTest(app_id=app_id):
                self.assertEqual(entry['architectures'],['x86_64','aarch64'])
        # The optional separate product remains constrained to its actual
        # published CPU; broadening the bundled apps is not a global default.
        write=next(entry for entry in catalog['applications'] if entry['id']=='write')
        self.assertEqual(write['architectures'],['x86_64'])

    def test_displays_withdrawn_from_all_active_sources(self):
        registry=json.loads((ROOT/'packaging/flatpak/first-party-updates.json').read_text())
        shipping=json.loads((ROOT/'config/os/first-party-app-baseline.json').read_text())
        catalog=json.loads((ROOT/'src/luma-installer/data/depot-catalog-4.json').read_text())
        self.assertNotIn('org.projectluma.Displays',{a['id'] for a in registry['applications']})
        self.assertNotIn('org.projectluma.Displays',shipping['applications'])
        self.assertNotIn('displays',{a['id'] for a in catalog['applications']})
        history=json.loads((ROOT/'src/luma-installer/data/depot-first-party.json').read_text())
        self.assertEqual(next(a for a in history['applications'] if a['id']=='displays')['visibility'],'withdrawn')

    def test_every_catalogued_image_app_has_maintained_identity_and_producer(self):
        registry=json.loads((ROOT/'packaging/flatpak/first-party-updates.json').read_text())
        apps=registry['applications']
        ids=[a['id'] for a in apps]
        self.assertEqual(len(ids),len(set(ids)))
        catalog=json.loads((ROOT/'src/luma-installer/data/depot-catalog-4.json').read_text())
        shipped={a['app_id'] for a in catalog['applications'] if a.get('sources',{}).get('luma_system')}
        self.assertTrue(shipped <= set(ids))
        for app in apps:
            if app['id'] not in EXTERNAL_PRODUCTS:
                self.assertTrue((ROOT/app['source_tree']).exists(), app['id'])
            self.assertEqual(app['default_channel'],'beta')
            self.assertEqual(app['channels'],['beta','nightly'])
            self.assertEqual(app['qualification'],'pending-installed-update-rollback-and-migration')
            if app['recipe'] and app['id'] not in EXTERNAL_PRODUCTS:
                self.assertTrue((ROOT/app['recipe']).is_file(), app['id'])
if __name__=='__main__': unittest.main()
