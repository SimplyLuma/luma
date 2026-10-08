import unittest
from types import SimpleNamespace
from unittest.mock import patch
from luma_depot.native import identity_for_installed, NativeInstallation

class IdentityTest(unittest.TestCase):
    def test_curated_identity_survives_installed_desktop(self):
        record=SimpleNamespace(desktop_id='org.audacityteam.Audacity.desktop',
            app_info=SimpleNamespace(get_string=lambda key:'org.audacityteam.Audacity'))
        self.assertEqual(identity_for_installed(record),'catalog:audacity')
        with patch('luma_depot.native.inventory',return_value=[record]):
            self.assertIs(NativeInstallation()._record('catalog:audacity'),record)
            self.assertIs(NativeInstallation()._record(record.desktop_id),record)
    def test_uncurated_identity_preserved(self):
        record=SimpleNamespace(desktop_id='example.desktop',app_info=SimpleNamespace(get_string=lambda key:None))
        self.assertEqual(identity_for_installed(record),'example.desktop')

if __name__=='__main__':unittest.main()
