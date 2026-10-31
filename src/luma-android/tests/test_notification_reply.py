# SPDX-License-Identifier: Apache-2.0
import unittest
from unittest.mock import Mock
import uuid

from luma_android.notification_reply import ReplyError, ReplyGrant, ReplyLedger


class ReplyPolicy(unittest.TestCase):
    def setUp(self):
        self.grant = ReplyGrant(42, 'a' * 32, 'reply-action', 1)
        self.resolve = Mock(return_value=self.grant)
        self.dispatch = Mock()
        self.ledger = ReplyLedger(self.resolve, self.dispatch)
        self.request_id = str(uuid.uuid4())
        self.completed = Mock()

    def request(self, **changes):
        values = dict(sender=':1.7', owner=':1.7', notification_id=42,
                      token=self.grant.token, request_id=self.request_id,
                      text='synthetic reply', complete=self.completed)
        values.update(changes)
        return self.ledger.request(**values)

    def test_foreign_or_missing_shell_owner_is_rejected_before_grant_lookup(self):
        for values in ({'sender': ':1.8'}, {'owner': None}, {'owner': 'org.gnome.Shell'}):
            with self.assertRaisesRegex(ReplyError, 'Unauthorized'):
                self.request(**values)
        self.resolve.assert_not_called()
        self.dispatch.assert_not_called()

    def test_duplicate_while_pending_and_after_ack_dispatches_only_once(self):
        receipt = self.request()
        other = Mock()
        self.assertEqual(self.request(complete=other), receipt)
        done = self.dispatch.call_args.args[-1]
        done(True)
        self.request()
        self.dispatch.assert_called_once()
        other.assert_called_once_with(receipt, 'sent')
        self.assertEqual(self.completed.call_count, 2)
        self.assertEqual(self.ledger.pending, 0)

    def test_timeout_is_uncertain_and_late_acceptance_reconciles_without_replay(self):
        receipt = self.request()
        done = self.dispatch.call_args.args[-1]
        done(None)
        self.completed.assert_called_once_with(receipt, 'uncertain')
        self.request()
        self.completed.assert_called_with(receipt, 'uncertain')
        done(True)
        self.assertEqual(self.completed.call_count, 2)
        self.request()
        self.completed.assert_called_with(receipt, 'sent')
        self.dispatch.assert_called_once()
        done(False)
        self.request()
        self.completed.assert_called_with(receipt, 'sent')

    def test_exception_after_dispatch_never_authorizes_second_send(self):
        self.dispatch.side_effect = RuntimeError('ambiguous transport failure')
        receipt = self.request()
        self.request()
        self.completed.assert_called_with(receipt, 'uncertain')
        self.dispatch.assert_called_once()

    def test_reused_request_id_with_different_text_or_generation_is_rejected(self):
        self.request()
        with self.assertRaisesRegex(ReplyError, 'InvalidInput'):
            self.request(text='different synthetic reply')
        self.resolve.return_value = ReplyGrant(42, self.grant.token, 'new-action', 2)
        with self.assertRaisesRegex(ReplyError, 'InvalidInput'):
            self.request()
        self.dispatch.assert_called_once()

    def test_retired_grant_cannot_dispatch_again_but_original_ack_is_reported(self):
        receipt = self.request()
        done = self.dispatch.call_args.args[-1]
        self.ledger.revoke(self.grant.token)
        self.resolve.return_value = None
        with self.assertRaisesRegex(ReplyError, 'StaleNotification'):
            self.request()
        done(True)
        self.completed.assert_called_once_with(receipt, 'sent')
        self.assertFalse(self.ledger.requests)
        self.assertEqual(self.ledger.pending, 0)

    def test_text_is_not_retained_and_utf8_budget_is_enforced(self):
        self.request(text='synthetic private reply')
        self.assertNotIn('synthetic private reply', repr(self.ledger.requests))
        for text in ('', '   ', 'bad\0text', '\ud800', '\U0001f319' * 1025):
            with self.assertRaisesRegex(ReplyError, 'InvalidInput'):
                self.request(text=text, request_id=str(uuid.uuid4()))
        self.dispatch.assert_called_once()

    def test_pending_and_waiter_budgets_are_bounded_without_dropping_acks(self):
        self.request()
        for _ in range(7):
            self.request(complete=Mock())
        with self.assertRaisesRegex(ReplyError, 'Busy'):
            self.request(complete=Mock())
        for _ in range(7):
            self.request(request_id=str(uuid.uuid4()))
        with self.assertRaisesRegex(ReplyError, 'Busy'):
            self.request(request_id=str(uuid.uuid4()))
        self.assertEqual(self.dispatch.call_count, 8)
        for call in self.dispatch.call_args_list:
            call.args[-1](None)
        self.assertEqual(self.ledger.pending, 0)

    def test_revocation_during_resolution_cannot_admit_stale_request(self):
        def resolve(*args):
            self.ledger.revoke(self.grant.token)
            return self.grant
        self.resolve.side_effect = resolve
        with self.assertRaisesRegex(ReplyError, 'NotReady'):
            self.request()
        self.dispatch.assert_not_called()
        self.assertFalse(self.ledger.requests)

    def test_failed_waiter_does_not_discard_other_callers_ack(self):
        self.request(complete=Mock(side_effect=RuntimeError('disconnected caller')))
        receipt = self.request()
        self.dispatch.call_args.args[-1](True)
        self.completed.assert_called_once_with(receipt, 'sent')

    def test_resolver_cannot_attach_a_different_notification_or_token(self):
        self.resolve.return_value = ReplyGrant(43, self.grant.token, 'reply', 1)
        with self.assertRaisesRegex(ReplyError, 'StaleNotification'):
            self.request()
        self.resolve.return_value = ReplyGrant(42, 'b' * 32, 'reply', 1)
        with self.assertRaisesRegex(ReplyError, 'StaleNotification'):
            self.request()
        self.dispatch.assert_not_called()

    def test_concurrent_duplicate_requests_dispatch_once(self):
        import threading
        barrier = threading.Barrier(8)
        results = []
        errors = []
        def run():
            try:
                barrier.wait(timeout=3)
                results.append(self.request(complete=Mock()))
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=run) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertFalse(errors)
        self.assertEqual(len(results), 8)
        self.assertEqual(len(set(results)), 1)
        self.dispatch.assert_called_once()
        self.dispatch.call_args.args[-1](True)
        self.assertEqual(self.ledger.pending, 0)


if __name__ == '__main__':
    unittest.main()
