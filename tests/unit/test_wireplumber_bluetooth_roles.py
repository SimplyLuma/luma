"""The desktop image's WirePlumber default for Luma Connect phone calls (ADR-021)."""
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src/luma-continuity'))

from luma_continuity.companion_calls import ROLES_WITH_HANDS_FREE  # noqa: E402


class ShippedRolesTest(unittest.TestCase):
    def test_shipped_default_removes_the_hands_free_role(self):
        text = (REPO / 'config/desktop/wireplumber/60-luma-bluetooth-roles.conf').read_text()
        self.assertIn('ADR-021', text)
        roles_line = next(line for line in text.splitlines() if 'override.bluez5.roles' in line and not line.lstrip().startswith('#'))
        self.assertNotIn('hfp_hf', roles_line)
        self.assertNotIn('hsp_hs', roles_line)
        for role in set(ROLES_WITH_HANDS_FREE) - {'hfp_hf'}:
            self.assertIn(role, roles_line)
        self.assertIn('bluez5.telephony-dbus-service = false', text)


if __name__ == '__main__':
    unittest.main()
