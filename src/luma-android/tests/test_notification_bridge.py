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

    def test_host_bridge_does_not_retain_delivered_image_or_message_payload(self):
        import gc
        import weakref
        class Image:
            width = 2
            height = 2
            rowstride = 8
            has_alpha = True
            data = bytes(range(16))
        image = Image()
        record = replace(self.record, image=image)
        image_ref, record_ref = weakref.ref(image), weakref.ref(record)
        bridge = NotificationBridge(SimpleNamespace(notify=lambda *args: 42))
        bridge.post(record)
        del image, record
        gc.collect()
        self.assertIsNone(image_ref())
        self.assertIsNone(record_ref())
        self.assertEqual(bridge.records[42].package, self.record.package)
        self.assertEqual(bridge.records[42].actions, frozenset(('default', 'read')))
        self.assertFalse(hasattr(bridge.records[42], 'body'))
        self.assertFalse(hasattr(bridge.records[42], 'image'))

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


class NotificationReplyOwnership(unittest.TestCase):
    def setUp(self):
        NotificationLifecycle.setUp(self)
        self.record = replace(self.record, reply_action='read')
        self.sink.notify.side_effect = lambda record, replacement, token: replacement or 42

    def post(self):
        return self.bridge.post(self.record, reply_listener=self.listener)

    def test_host_token_is_generated_and_reply_cannot_use_plain_action_dispatch(self):
        self.post()
        owner = self.bridge.records[42]
        self.assertRegex(owner.reply_token, r'^[0-9a-f]{32}$')
        self.assertEqual(self.sink.notify.call_args.args[2], owner.reply_token)
        grant = self.bridge.resolve_reply(42, owner.reply_token)
        self.assertIs(grant.endpoint, self.listener)
        self.assertEqual(grant.action, 'read')
        self.bridge.action(42, 'read')
        self.listener.action.assert_not_called()
        self.bridge.action(42, 'default')
        self.listener.action.assert_called_once_with(42, 'default', '')

    def test_replacement_rotates_token_and_retires_old_authority(self):
        self.post()
        token = self.bridge.records[42].reply_token
        generation = self.bridge.records[42].reply_generation
        self.bridge.post(self.record, 42, self.listener)
        current = self.bridge.records[42]
        self.assertNotEqual(current.reply_token, token)
        self.assertGreater(current.reply_generation, generation)
        self.assertIsNone(self.bridge.resolve_reply(42, token))
        self.assertIsNotNone(self.bridge.resolve_reply(42, current.reply_token))

    def test_withdrawal_dismissal_and_server_reset_retire_grants(self):
        for retire in (lambda: self.bridge.withdraw(42),
                       lambda: self.bridge.closed(42, 2), self.bridge.server_changed):
            self.post()
            token = self.bridge.records[42].reply_token
            retire()
            self.assertIsNone(self.bridge.resolve_reply(42, token))

    def test_listener_death_cannot_retarget_reply_to_new_system_server(self):
        from luma_android.notification_reply import ReplyError
        self.post()
        token = self.bridge.records[42].reply_token
        self.bridge.unregister(self.listener)
        self.bridge.register(Mock())
        with self.assertRaisesRegex(ReplyError, 'NotReady'):
            self.bridge.resolve_reply(42, token)

    def test_unknown_action_and_unregistered_reply_listener_fail_before_notify(self):
        for record, listener in ((replace(self.record, reply_action='missing'), self.listener),
                                 (self.record, Mock())):
            with self.assertRaises(ValueError):
                self.bridge.post(record, reply_listener=listener)
        self.sink.notify.assert_not_called()

    def test_forged_token_or_notification_cannot_resolve(self):
        self.post()
        token = self.bridge.records[42].reply_token
        self.assertIsNone(self.bridge.resolve_reply(43, token))
        self.assertIsNone(self.bridge.resolve_reply(42, 'b' * 32))

    def test_accepted_reply_survives_withdrawal_without_replaying_or_changing_listener(self):
        import uuid
        from luma_android.notification_reply import ReplyError
        self.post()
        token = self.bridge.records[42].reply_token
        done = Mock()
        request = dict(sender=':1.8', owner=':1.8', notification_id=42, token=token,
                       request_id=str(uuid.uuid4()), text='synthetic reply', complete=done)
        receipt = self.bridge.reply_ledger.request(**request)
        self.listener.reply.assert_called_once()
        callback = self.listener.reply.call_args.args[-1]
        self.bridge.withdraw(42)
        with self.assertRaisesRegex(ReplyError, 'StaleNotification'):
            self.bridge.reply_ledger.request(**request)
        callback(True)
        done.assert_called_once_with(receipt, 'sent')
        self.assertFalse(self.bridge.reply_ledger.requests)
