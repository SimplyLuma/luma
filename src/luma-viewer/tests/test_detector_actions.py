# SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from luma_viewer.composition import ViewerUI


class DetectorActionTests(unittest.TestCase):
    def registry(self, kind, handler=None):
        window=SimpleNamespace(fixture=False, _detector_action=Mock())
        with (patch('luma_viewer.composition.Menu') as menu,
              patch('luma_viewer.composition.Gio.AppInfo.get_default_for_uri_scheme', return_value=handler)):
            ViewerUI._detector_menu(window, Mock(), kind, 'Sample')
            registry=menu.call_args.args[0]
            states={key:command.enabled() for key,command in registry._commands.items()}
            return window, registry, states

    def test_no_contact_calendar_or_currency_write_can_be_invoked(self):
        for kind,key in (('tel','contact'), ('date','calendar'), ('money','convert')):
            with self.subTest(kind=kind):
                window,registry,states=self.registry(kind)
                self.assertFalse(states[key]);self.assertFalse(registry.invoke(key))
                window._detector_action.assert_not_called()
                self.assertTrue(registry.invoke('copy'))
                window._detector_action.assert_called_once_with('copy','Sample')

    def test_call_and_maps_require_an_existing_handler(self):
        for kind,key in (('tel','call'), ('addr','maps')):
            self.assertFalse(self.registry(kind)[2][key])
            self.assertTrue(self.registry(kind,Mock())[2][key])

    def test_fixture_copy_does_not_touch_clipboard(self):
        window=SimpleNamespace(fixture=True,host=Mock(),get_clipboard=Mock())
        with patch('luma_viewer.composition.Toast.show'):
            ViewerUI._detector_action(window,'copy','Sample')
        window.get_clipboard.assert_not_called()
