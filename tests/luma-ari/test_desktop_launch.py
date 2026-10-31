# SPDX-License-Identifier: Apache-2.0
"""An accepted desktop launch request does not prove a window was opened."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from ari.tools import desktop


class DesktopLaunch(unittest.TestCase):
    def setUp(self):
        self.info=SimpleNamespace(get_id=lambda:'org.projectluma.Tide.desktop',get_name=lambda:'Tide',launch=Mock(return_value=True))

    def test_existing_application_is_reported_without_a_new_opened_receipt(self):
        with patch.object(desktop,'_match_app',return_value=self.info),patch.object(desktop,'_running_app',return_value=True),patch.object(desktop,'DRY_RUN',False):
            result=desktop.open_app({'name':'Tide'})
        self.assertTrue(result['unchanged'])
        self.assertIn('already running',result['summary'])
        self.assertNotIn('Opened',result['summary'])
        self.assertFalse(result['completed'])
        self.info.launch.assert_called_once()

    def test_launch_acknowledgement_and_failure_are_not_window_verification(self):
        with patch.object(desktop,'_match_app',return_value=self.info),patch.object(desktop,'_running_app',return_value=None),patch.object(desktop,'DRY_RUN',False):
            result=desktop.open_app({'name':'Tide'})
            self.assertIn('Requested',result['summary'])
            self.assertTrue(result['verification_required'])
            self.assertFalse(result['completed'])
            self.info.launch.return_value=False
            self.assertFalse(desktop.open_app({'name':'Tide'})['ok'])

    def test_bus_absence_does_not_prove_a_non_gapplication_is_closed(self):
        for owned,expected in ((True,True),(False,None)):
            bus=SimpleNamespace(call_sync=Mock(return_value=SimpleNamespace(unpack=lambda:(owned,))))
            with patch.object(desktop.Gio,'bus_get_sync',return_value=bus):
                self.assertIs(desktop._running_app(self.info),expected)


if __name__=='__main__':unittest.main()
