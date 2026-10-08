# SPDX-License-Identifier: Apache-2.0
"""Caller custody/bounds tests; stand-ins never establish signed deployment proof."""
import json
import threading
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock, patch
from gi.repository import GLib
from ari import host_identity, service
from ari.host_limits import Admission, Owners, Busy, MAX_INPUT

class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.daemon = service.Daemon.__new__(service.Daemon)
        d = self.daemon
        d.admission = Admission(); d.owners = Owners()
        d._events = deque(); d._event_source = None; d._event_lock = threading.Lock(); d._closed = False
        d._model_lock = threading.RLock(); d._model_mutation = None; d._mutation_owner = None
        d.connection = Mock(); d.requests = {}; d.downloads = {}; d.settings = None
        d.runtime = Mock(); d.agent = Mock(); d.store = Mock()
        self.auth = patch.object(service, 'authenticate', return_value='org.projectluma.Ari')
        self.auth.start()
    def tearDown(self):
        self.auth.stop(); self.daemon.admission.close()
    def call(self, method, parameters, sender=':1.11'):
        invocation = Mock()
        self.daemon.handle(None, sender, '', '', method, parameters, invocation)
        return invocation
    def test_authentication_precedes_unpack_and_mutation(self):
        params = Mock()
        with patch.object(service, 'authenticate', side_effect=host_identity.Refused('no')):
            result = self.call('Ask', params)
        params.unpack.assert_not_called(); self.daemon.store.new_conversation.assert_not_called()
        self.assertEqual(result.return_dbus_error.call_args.args[0], 'org.projectluma.Ari1.Refused')
    def test_aggregate_size_precedes_unpack(self):
        params = Mock(); params.get_size.return_value = MAX_INPUT + 1
        result = self.call('SetCloudOptions', params)
        params.unpack.assert_not_called(); result.return_dbus_error.assert_called_once()
    def test_fifth_request_is_busy_before_store_mutation(self):
        for _ in range(4): self.daemon.admission.reserve()
        result = self.call('Ask', GLib.Variant('(ss)', ('', 'hello')))
        self.daemon.store.new_conversation.assert_not_called()
        self.assertEqual(result.return_dbus_error.call_args.args[0], 'org.projectluma.Ari1.Busy')
        for _ in range(4): self.daemon.admission.slots.release()
    def test_foreign_stop_and_approval_refused(self):
        self.daemon.owners.add('request', ':1.12')
        self.daemon.owners.event('request', {'type':'approval', 'approval':'approve'})
        turn = SimpleNamespace(stop=Mock()); self.daemon.requests['request'] = turn
        for method, args in [('Stop', GLib.Variant('(s)', ('request',))),
                             ('Approve', GLib.Variant('(sb)', ('approve', True)))]:
            self.assertEqual(self.call(method,args).return_dbus_error.call_args.args[0], 'org.projectluma.Ari1.Refused')
        turn.stop.set.assert_not_called(); self.daemon.agent.approve.assert_not_called()
    def test_owner_can_approve(self):
        self.daemon.owners.add('r', ':1.11')
        self.daemon.owners.event('r', {'type':'approval', 'approval':'a'})
        self.daemon.agent.approve.return_value = True
        result = self.call('Approve', GLib.Variant('(sb)', ('a', True)))
        result.return_value.assert_called_once(); self.daemon.agent.approve.assert_called_once_with('a', True)
    def test_event_unicast_and_stream_coalescing(self):
        self.daemon.owners.add('r', ':1.11')
        with patch.object(service.GLib, 'idle_add', return_value=1) as idle:
            for _ in range(1000): self.daemon.emit('r', {'type':'text', 'text':'a'})
        idle.assert_called_once(); self.assertEqual(len(self.daemon._events), 1)
        self.daemon._drain_events()
        self.assertEqual(self.daemon.connection.emit_signal.call_args.args[0], ':1.11')
        self.assertEqual(json.loads(self.daemon.connection.emit_signal.call_args.args[-1].unpack()[1])['text'], 'a'*1000)
    def test_vanished_sender_cancels_and_removes_approval_and_pending_events(self):
        self.daemon.owners.add('r', ':1.11'); self.daemon.owners.event('r', {'type':'approval','approval':'a'})
        turn = SimpleNamespace(stop=Mock()); self.daemon.requests['r'] = turn
        self.daemon._events.append((':1.11','r','{}'))
        self.daemon._name_changed(None,None,None,None,None,GLib.Variant('(sss)',(':1.11',':1.11','')))
        turn.stop.set.assert_called_once(); self.assertFalse(self.daemon.owners.approvals); self.assertFalse(self.daemon._events)
    def test_admin_lock_refuses_host_settings_mutation(self):
        self.daemon.settings = Mock(); self.daemon.settings.is_writable.return_value = False
        self.call('SetEnabled', GLib.Variant('(b)', (False,))).return_dbus_error.assert_called_once()
        self.call('SetApprovalMode', GLib.Variant('(s)', ('never',))).return_dbus_error.assert_called_once()
        self.daemon.settings.set_boolean.assert_not_called(); self.daemon.settings.set_string.assert_not_called()
    def test_unknown_approval_mode_refused_even_when_writable(self):
        self.daemon.settings = Mock(); self.daemon.settings.is_writable.return_value = True
        self.call('SetApprovalMode', GLib.Variant('(s)', ('custom',))).return_dbus_error.assert_called_once()
        self.daemon.settings.set_string.assert_not_called()
    def test_queued_cloud_setter_excludes_turn_and_other_setter(self):
        queued=[]
        def save(work): queued.append(work); return None
        with patch.object(self.daemon.admission,'submit_reserved',side_effect=save), patch.object(service.GLib,'idle_add',return_value=1):
            self.daemon.set_cloud_key=Mock(return_value=(True,''))
            result=self.call('SetCloudKey',GLib.Variant('(s)',('sk-or-private-test',)))
            self.assertIsNotNone(self.daemon._model_mutation)
            self.assertEqual(self.call('Ask',GLib.Variant('(ss)',('','hello'))).return_dbus_error.call_args.args[0],'org.projectluma.Ari1.Busy')
            self.assertEqual(self.call('SetCloudModel',GLib.Variant('(sb)',('model',False))).return_dbus_error.call_args.args[0],'org.projectluma.Ari1.Busy')
            self.daemon.store.new_conversation.assert_not_called()
            queued.pop()()
            self.assertIsNone(self.daemon._model_mutation)
        # This test's deferred executor owns and releases the transferred slot.
        self.daemon.admission.slots.release()
    def test_failed_setter_releases_model_reservation_and_reports_late_error(self):
        queued=[];callbacks=[]
        with patch.object(self.daemon.admission,'submit_reserved',side_effect=lambda work:queued.append(work)), patch.object(service.GLib,'idle_add',side_effect=lambda callback:callbacks.append(callback) or 1):
            self.daemon.set_cloud_key=Mock(side_effect=ValueError('provider unavailable'))
            result=self.call('SetCloudKey',GLib.Variant('(s)',('sk-or-private-test',)))
            queued.pop()()
            self.assertIsNone(self.daemon._model_mutation)
            callbacks.pop()()
            self.assertEqual(result.return_dbus_error.call_args.args[1],'provider unavailable')
        self.daemon.admission.slots.release()
    def test_remove_model_refused_during_download(self):
        self.daemon.downloads['request']=threading.Event()
        with patch.object(service.models,'remove') as remove:
            result=self.call('RemoveModel',GLib.Variant('(s)',('model',)))
        self.assertEqual(result.return_dbus_error.call_args.args[0],'org.projectluma.Ari1.Busy')
        remove.assert_not_called()
    def test_disconnected_setter_cannot_commit_cloud_key(self):
        cancelled=threading.Event();cancelled.set()
        with patch.object(service.cloud,'key_status',return_value={}),patch.object(service.cloud,'store_key') as store:
            self.assertEqual(self.daemon.set_cloud_key('sk-or-private-test',cancelled),(False,'Cancelled.'))
        store.assert_not_called()
    def test_owner_bound_maps_cleanup(self):
        owners=Owners()
        for i in range(4): owners.add(str(i), ':1.11')
        with self.assertRaises(Busy): owners.add('fifth', ':1.12')
        self.assertEqual(len(owners.vanished(':1.11')),4)
        owners.add('new', ':1.12')

if __name__ == '__main__': unittest.main()
