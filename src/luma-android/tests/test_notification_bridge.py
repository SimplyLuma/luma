# SPDX-License-Identifier: Apache-2.0
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from luma_android.notification_bridge import Notification, NotificationBridge, escaped_body


class NotificationLifecycle(unittest.TestCase):
    def setUp(self):
        self.sink = Mock()
        self.sink.notify.side_effect = lambda record, replacement: replacement or 42
        self.bridge = NotificationBridge(self.sink)
        self.listener = Mock()
        self.bridge.register(self.listener)
        self.record = Notification('org.example.Chat', 'Chat', 'Message', 'Hello',
                                   (('default', ''), ('read', 'Mark read')))

    def test_user_dismissal_returns_once_to_the_owning_android_record(self):
        self.bridge.post(self.record)
        self.bridge.closed(42, 2)
        self.bridge.closed(42, 2)
        self.listener.dismissed.assert_called_once_with(42)
        self.assertFalse(self.bridge.records)

    def test_banner_expiry_and_producer_withdrawal_never_cancel_android(self):
        for reason in (1, 3, 4):
            self.bridge.post(self.record)
            self.bridge.closed(42, reason)
        self.bridge.post(self.record)
        self.bridge.withdraw(42)
        self.bridge.closed(42, 2)
        self.sink.close.assert_called_once_with(42)
        self.listener.dismissed.assert_not_called()

    def test_only_owned_declared_actions_consume_activation_token(self):
        self.bridge.post(self.record)
        self.bridge.activation_token(42, 'single-use-token')
        self.bridge.action(42, 'injected')
        self.bridge.action(999, 'default')
        self.bridge.action(42, 'read')
        self.listener.action.assert_called_once_with(42, 'read', 'single-use-token')
        self.bridge.action(42, 'default')
        self.listener.action.assert_called_with(42, 'default', '')

    def test_replacement_cannot_target_a_different_package_or_native_id(self):
        self.bridge.post(self.record)
        other = replace(self.record, package='org.example.Other')
        self.bridge.post(other, 42)
        self.sink.notify.assert_called_with(other, 0)
        self.bridge.post(other, 999)
        self.sink.notify.assert_called_with(other, 0)

    def test_replacement_and_restart_retire_stale_tokens_and_ids(self):
        self.bridge.post(self.record)
        self.bridge.activation_token(42, 'stale')
        self.sink.notify.side_effect = lambda record, replacement: 43
        self.bridge.post(self.record, 42)
        self.assertNotIn(42, self.bridge.records)
        self.assertFalse(self.bridge.tokens)
        self.bridge.server_changed()
        self.listener.reset.assert_called_once_with()
        self.bridge.action(43, 'default')
        self.listener.action.assert_not_called()
        self.bridge.post(self.record, 43)
        self.sink.notify.assert_called_with(self.record, 0)

    def test_server_restart_during_post_does_not_adopt_a_stale_reply(self):
        def notify(*args):
            self.bridge.server_changed()
            return 42
        self.sink.notify.side_effect = notify
        self.assertEqual(self.bridge.post(self.record), 0)
        self.assertFalse(self.bridge.records)

    def test_unregistered_listener_receives_no_future_actions(self):
        self.bridge.post(self.record)
        self.bridge.unregister(self.listener)
        self.bridge.unregister(self.listener)
        self.bridge.action(42, 'default')
        self.listener.action.assert_not_called()

    def test_malformed_content_cannot_reach_the_host(self):
        image = SimpleNamespace(width=100, height=100, rowstride=4,
                                has_alpha=True, data=b'\x00' * 400)
        for record in (replace(self.record, package='../native'),
                       replace(self.record, body='\x00'),
                       replace(self.record, actions=(('a', 'A'), ('a', 'B'))),
                       replace(self.record, urgency=3),
                       replace(self.record, image=image)):
            with self.subTest(record=record), self.assertRaises(ValueError):
                self.bridge.post(record)
        self.sink.notify.assert_not_called()
        self.assertEqual(escaped_body(replace(self.record, body='<b>& image')), '&lt;b&gt;&amp; image')


if __name__ == '__main__':
    unittest.main()
