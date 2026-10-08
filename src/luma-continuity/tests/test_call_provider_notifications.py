"""Call notifications must revalidate without inventing a disconnect."""
import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch
from gi.repository import Gio
from luma_continuity.call_provider import CallProvider, _selected_provider


class Reply:
    def __init__(self, value): self.value = value
    def unpack(self): return (json.dumps(self.value),)


class Connection:
    def __init__(self):
        self.callbacks = []
        self.connected = True
        self.on_control = None
    def call_sync(self, bus, path, interface, method, *args):
        if method == 'GetCallState':
            return Reply(dict(account='account', epoch='e'*32,
                              connected=self.connected, control=self.connected))
        if method == 'ExchangeCall':
            request=json.loads(args[0].unpack()[0])
            if request['capability']=='calls.control':
                if self.on_control:self.on_control()
                return Reply(dict(state='complete',result=dict(accepted=True)))
            return Reply(dict(state='complete', result=dict(calls=[],
                dial_token='a'*32, voice_available=True)))
        raise AssertionError(method)
    def signal_subscribe(self, *args):
        self.callbacks.append(args[-1]); return len(self.callbacks)
    def signal_unsubscribe(self, identifier): pass
    def call(self, *args): pass


class NotificationTests(unittest.TestCase):
    def test_change_revalidates_and_real_loss_closes_admission(self):
        connection = Connection()
        published = []; dispatch = queue.Queue()
        with tempfile.TemporaryDirectory() as root:
            selection = Path(root)/'.config/luma-connect/calls-phone.json'
            selection.parent.mkdir(parents=True, mode=0o700)
            selection.write_text(json.dumps(dict(version=1, phone=dict(
                peer='a'*64, epoch='e'*32, account='account', label='Phone',
                address='127.0.0.1', port=1234))))
            selection.chmod(0o600)
            with patch.object(Path, 'home', return_value=Path(root)), \
                 patch.object(Gio, 'bus_get_sync', return_value=connection):
                provider = _selected_provider(dispatch=dispatch.put)
            try:
                provider.start(lambda snapshot, ready: published.append(ready))
                dispatch.get(timeout=3)()
                self.assertEqual(published, [True])
                # A normal call update schedules a fresh check without
                # inventing loss of the current connection.
                connection.callbacks[0]()
                self.assertEqual(published, [True])
                dispatch.get(timeout=3)()
                self.assertEqual(published, [True, True])
                # The phone may emit a normal update before an accepted dial
                # response arrives. That update is not loss of authority.
                connection.on_control=connection.callbacks[0]
                provider.dial('+12025550123')
                dispatch.get(timeout=3)()
                self.assertTrue(published[-1])
                connection.on_control=None
                connection.connected = False
                connection.callbacks[0]()
                # Even before async UI refresh, each command rechecks the
                # daemon and cannot use the old displayed connection.
                with self.assertRaises(PermissionError):
                    provider.dial('+12025550123')
                dispatch.get(timeout=3)()
                self.assertFalse(published[-1])
                with self.assertRaises(PermissionError):
                    provider.dial('+12025550123')
            finally:
                provider.close()


class RecoveryTests(unittest.TestCase):
    def test_transient_failure_recovers_without_another_notification(self):
        dispatch=queue.Queue(); published=[]; requests=[]
        def exchange(request):
            requests.append(request['capability'])
            if len(requests)==1:raise TimeoutError()
            return dict(state='complete',result=dict(calls=[],dial_token='a'*32,voice_available=True))
        provider=CallProvider(exchange,account='account',epoch='e'*32,
            authorized=lambda:True,control_authorized=lambda:True,
            subscribe=lambda callback:lambda:None,dispatch=dispatch.put)
        try:
            provider.start(lambda snapshot,ready:published.append(ready))
            dispatch.get(timeout=3)()
            self.assertEqual(published,[False])
            dispatch.get(timeout=3)()
            self.assertEqual(published,[False,True])
            self.assertEqual(requests,['calls.read','calls.read'])
            self.assertIsNone(provider.retry_timer)
            self.assertEqual(provider.retry_delay,1)
        finally:provider.close()

    def test_close_cancels_pending_recovery(self):
        dispatch=queue.Queue()
        def exchange(request):raise TimeoutError()
        provider=CallProvider(exchange,account='account',epoch='e'*32,
            authorized=lambda:True,control_authorized=lambda:True,
            subscribe=lambda callback:lambda:None,dispatch=dispatch.put)
        provider.start(lambda *args:None)
        dispatch.get(timeout=3)()
        timer=provider.retry_timer
        provider.close()
        timer.join(timeout=1)
        self.assertFalse(timer.is_alive())
        self.assertIsNone(provider.retry_timer)
