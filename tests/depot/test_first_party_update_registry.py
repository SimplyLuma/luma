# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import unittest
ROOT=Path(__file__).parents[2]
class Registry(unittest.TestCase):
    def test_every_catalogued_image_app_has_maintained_identity_and_producer(self):
        registry=json.loads((ROOT/'packaging/flatpak/first-party-updates.json').read_text())
        apps=registry['applications']
        ids=[a['id'] for a in apps]
        self.assertEqual(len(ids),len(set(ids)))
        catalog=json.loads((ROOT/'src/luma-installer/data/depot-catalog-4.json').read_text())
        shipped={a['app_id'] for a in catalog['applications'] if a.get('sources',{}).get('luma_system')}
        self.assertTrue(shipped <= set(ids))
        for app in apps:
            self.assertTrue((ROOT/app['source_tree']).exists(), app['id'])
            self.assertEqual(app['default_channel'],'beta')
            self.assertEqual(app['channels'],['beta','nightly'])
            self.assertEqual(app['qualification'],'pending-installed-update-rollback-and-migration')
            if app['recipe']: self.assertTrue((ROOT/app['recipe']).is_file(), app['id'])
if __name__=='__main__': unittest.main()
