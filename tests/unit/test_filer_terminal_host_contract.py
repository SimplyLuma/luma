# SPDX-License-Identifier: Apache-2.0
"""The independently updated Filer must declare its native Terminal API floor."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]

class FilerTerminalHostContract(unittest.TestCase):
    def test_signed_filer_manifest_declares_terminal_and_compatible_depot(self):
        manifest = json.loads((ROOT / 'packaging/flatpak/apps/org.gnome.Nautilus/org.gnome.Nautilus.yml').read_text())
        args = manifest['finish-args']
        self.assertIn('--talk-name=org.projectluma.Terminal', args)
        self.assertIn('--metadata=X-Luma=min-host-terminal=7', args)
        self.assertIn('--metadata=X-Luma=min-host-installer=77', args)

if __name__ == '__main__':
    unittest.main()
