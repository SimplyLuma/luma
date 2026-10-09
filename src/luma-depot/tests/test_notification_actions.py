# SPDX-License-Identifier: Apache-2.0
"""Exercise the real Gio notification API used by the installed update service."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from gi.repository import Gio, GLib

from luma_depot.autoupdate import AppUpdateRun
from luma_depot.provisioning import ProvisionRun


class Notifications:
    def __init__(self):
        self.sent = []

    def send_notification(self, identifier, notification):
        if not isinstance(notification, Gio.Notification):
            raise TypeError('Expected a real Gio.Notification')
        self.sent.append((identifier, notification))


class NotificationActions(unittest.TestCase):
    def setUp(self):
        self.application = Notifications()
        self.actions = []
        supported = Gio.Notification.set_default_action_and_target

        def capture(notification, action, target):
            # Call the shipped GI method, rather than fabricating an API stub.
            supported(notification, action, target)
            self.actions.append((notification, action, target))

        spy = patch.object(Gio.Notification, 'set_default_action_and_target', capture)
        spy.start()
        self.addCleanup(spy.stop)
        self.update_run = AppUpdateRun(self.application, SimpleNamespace())
        self.item = SimpleNamespace(name='Viola')

    def assert_destination(self, identifier, view):
        self.assertEqual(len(self.application.sent), 1)
        self.assertEqual(len(self.actions), 1)
        sent_id, sent = self.application.sent[0]
        notification, action, target = self.actions[0]
        self.assertEqual(sent_id, identifier)
        self.assertIs(notification, sent)
        self.assertEqual(action, 'app.show-view')
        self.assertIsInstance(target, GLib.Variant)
        self.assertEqual(target.get_type_string(), 's')
        self.assertEqual(target.unpack(), view)

    def test_available_app_update_opens_updates(self):
        self.assertTrue(self.update_run._notify_apps([self.item], completed=False))
        self.assert_destination('available-app-updates', 'updates')

    def test_completed_app_update_opens_updates(self):
        self.assertTrue(self.update_run._notify_apps([self.item], completed=True))
        self.assert_destination('completed-app-updates', 'updates')

    def test_permission_review_opens_updates(self):
        self.assertTrue(self.update_run._notify_held([self.item]))
        self.assert_destination('held-app-updates', 'updates')

    def test_security_firmware_opens_updates_and_records_notice(self):
        update = SimpleNamespace(security=True, device_id='device', version='2',
                                 description=SimpleNamespace(title='Security update'))
        self.update_run.window.firmware = SimpleNamespace(updates=[update])
        state = {'notified': []}
        with (patch('luma_depot.autoupdate.depot_autoupdate.load', return_value=state),
              patch('luma_depot.autoupdate.depot_autoupdate.save') as save):
            self.update_run._remind_security_firmware()
        self.assert_destination('security-firmware', 'updates')
        self.assertEqual(state['notified'], ['firmware:device@2'])
        save.assert_called_once_with(state)

    def test_first_boot_apps_opens_mine(self):
        run = object.__new__(ProvisionRun)
        run.application = self.application
        run.headline = lambda: 'The apps you chose are installed'
        run.detail = lambda: 'Find them in your apps.'
        run._notify()
        self.assert_destination('first-boot-apps', 'mine')


if __name__ == '__main__':
    unittest.main()
