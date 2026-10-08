# SPDX-License-Identifier: Apache-2.0
"""The small pieces between the update data and the window."""

import unittest
from types import SimpleNamespace

try:
    from luma_depot import autoupdate
    from luma_depot.providers import InstalledApp, Permission
    from luma_depot.system_updates import plain
except (ImportError, ValueError):  # no GObject introspection here
    autoupdate = None


@unittest.skipIf(autoupdate is None, 'PyGObject is not available')
class Glue(unittest.TestCase):
    def test_release_notes_markup_becomes_plain_text(self):
        self.assertEqual(plain('<p>Fixes <em>boot</em>.</p><ul><li>One</li><li>Two</li></ul>'),
                         'Fixes boot.\n• One\n• Two')
        self.assertEqual(plain(''), '')

    def test_only_managed_apps_with_updates_are_considered_and_widening_is_detected(self):
        grow = Permission('devices.camera', 'Uses the camera', '', True, 'sensitive', 'added')
        shrink = Permission('network', 'Uses the internet', '', False, 'standard', 'removed')
        records = (
            InstalledApp('catalog:reel', '0.1', 1, update_version='0.2', managed=True, permission_changes=(grow,),
                         app=SimpleNamespace(name='Reel')),
            InstalledApp('catalog:canvas', '0.1', 1, update_version='0.1.1', managed=True, permission_changes=(shrink,)),
            InstalledApp('org.example.Rpm.desktop', '1', 1, update_version='2', managed=False),
            InstalledApp('catalog:write', '0.1', 1, managed=True),
        )
        pending = autoupdate.pending_for(records)
        self.assertEqual([(p.app_id, p.name, p.release, p.widens) for p in pending],
                         [('catalog:reel', 'Reel', '0.2', True), ('catalog:canvas', 'catalog:canvas', '0.1.1', False)])


if __name__ == '__main__':
    unittest.main()
