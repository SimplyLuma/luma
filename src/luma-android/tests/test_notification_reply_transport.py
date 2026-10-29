# SPDX-License-Identifier: Apache-2.0
"""Explicit binding-lifetime fixtures, not actual Android Binder IPC."""
import gc
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import weakref

from luma_android.notification_reply import ReplyError
from luma_android.notification_reply_transport import ReplyCalls


class Client:
    # Models the actual binding's single callback slot and delayed C destroy.
    def __init__(self):
        self.reply_func = None
        self.destroy_notif = None
        self.cancelled = []
        self.request = Mock()
    def new_request(self):
        return self.request
    def transact(self, code, flags, request, reply, destroy):
        self.code, self.flags = code, flags
        self.reply_func, self.destroy_notif = reply, destroy
        return 17
    def cancel(self, transaction):
        self.cancelled.append(transaction)


class ReplyCallLifetime(unittest.TestCase):
    def setUp(self):
        self.clients = []
        self.timers = {}
        def factory(*_):
            client = Client()
            self.clients.append(weakref.ref(client))
            return client
        def schedule(ms, callback):
            self.assertEqual(ms, 15000)
            number = len(self.timers) + 1
            self.timers[number] = callback
            return number
        self.pool = ReplyCalls(factory, schedule, lambda timer: self.timers.pop(timer))
        self.done = Mock()
    def tearDown(self):
        self.pool.close()
        for reference in self.clients:
            client = reference()
            if client is not None and client.destroy_notif:
                client.destroy_notif()
        self.assertFalse(ReplyCalls._live)
    def send(self, done=None):
        self.pool.send(object(), 'fixed.interface', 42, 'reply-action',
                       'fixed-request-id', 'synthetic reply', done or self.done)
    def ack(self, client):
        client.reply_func(SimpleNamespace(read_int32=Mock(side_effect=[(True, 0), (True, 1)])), 0)
    def test_concurrent_calls_keep_independent_callbacks_and_real_wire_layout(self):
        first, second = Mock(), Mock()
        self.send(first); self.send(second)
        a, b = [ref() for ref in self.clients]
        self.assertIsNot(a, b)
        self.assertEqual((a.code, a.flags), (4, 0))
        a.request.append_int32.assert_called_once_with(42)
        self.assertEqual([c.args for c in a.request.append_string16.call_args_list],
                         [('reply-action',), ('fixed-request-id',), ('synthetic reply',)])
        self.ack(b); second.assert_called_once_with(True); first.assert_not_called()
        self.ack(a); first.assert_called_once_with(True)
    def test_timeout_retains_c_userdata_until_destroy_and_never_reports_sent(self):
        self.send(); client = self.clients[0]()
        timer = next(iter(self.timers)); callback = self.timers.pop(timer)
        self.assertFalse(callback())
        del callback  # GLib drops its callback reference after returning False.
        self.done.assert_called_once_with(None)
        self.assertEqual(client.cancelled, [17])
        reference = self.clients[0]; del client; gc.collect()
        self.assertIsNotNone(reference())
        reference().destroy_notif(); gc.collect()
        self.assertIsNone(reference())
    def test_destroy_without_reply_completes_uncertain_once(self):
        self.send(); client = self.clients[0]()
        client.destroy_notif()
        self.done.assert_called_once_with(None)
        self.assertFalse(self.timers)
    def test_error_and_noncanonical_ack_do_not_claim_acceptance(self):
        for status, words in ((-32, []), (0, [(True, -1)]),
                              (0, [(True, 0), (False, 1)]),
                              (0, [(True, 0), (True, 2)])):
            done = Mock(); self.send(done); client = self.clients[-1]()
            client.reply_func(SimpleNamespace(read_int32=Mock(side_effect=words)), status)
            done.assert_called_once_with(None)
            client.destroy_notif()
    def test_close_is_bounded_and_retains_only_pending_c_lifetimes(self):
        self.send(); client = self.clients[0]()
        self.pool.close()
        self.done.assert_called_once_with(None)
        self.assertEqual(client.cancelled, [17])
        self.assertFalse(self.timers)
        with self.assertRaisesRegex(ReplyError, 'NotReady'):
            self.send()
        self.assertTrue(ReplyCalls._live)
    def test_all_timed_out_workers_still_count_against_capacity(self):
        for _ in range(8):
            self.send()
        for timer, callback in list(self.timers.items()):
            self.timers.pop(timer); callback()
        self.assertEqual(len(ReplyCalls._live), 8)
        with self.assertRaisesRegex(ReplyError, 'NotReady'):
            self.send()

    def test_exception_after_c_entry_retains_userdata_until_destroy(self):
        original = Client.transact
        def entered(client, *args):
            original(client, *args)
            raise MemoryError('after C entry')
        from unittest.mock import patch
        with patch.object(Client, 'transact', entered):
            self.send()
        self.done.assert_called_once_with(None)
        gc.collect()
        client = self.clients[0]()
        self.assertIsNotNone(client)
        self.assertTrue(ReplyCalls._live)
        client.destroy_notif()
        self.assertFalse(ReplyCalls._live)

    def test_zero_id_releases_without_waiting_for_a_c_callback(self):
        from unittest.mock import patch
        with patch.object(Client, 'transact', return_value=0):
            self.send()
        self.done.assert_called_once_with(None)
        self.assertFalse(ReplyCalls._live)
        self.assertFalse(self.timers)

    def test_repeated_close_does_not_cancel_or_complete_twice(self):
        self.send(); client = self.clients[0]()
        self.pool.close(); self.pool.close()
        self.done.assert_called_once_with(None)
        self.assertEqual(client.cancelled, [17])


if __name__ == '__main__':
    unittest.main()
