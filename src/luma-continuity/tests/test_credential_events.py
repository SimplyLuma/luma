"""Credential events must not publish stale authorization after a lock."""
from types import SimpleNamespace
import unittest
from luma_continuity.daemon import Daemon


class CredentialEventTests(unittest.TestCase):
    def test_lock_unlock_during_refresh_suppresses_old_ready_result(self):
        d=Daemon.__new__(Daemon)
        d.relay_runtime=None
        d.closed=False;d.busy=True;d.credentials_pending=False
        d.awake=True;d.online=True
        d.lease_timer=None
        d.latest={'account_status':'signed_in','stale':False}
        stopped=[];signals=[];scheduled=[]
        d.model=SimpleNamespace(tokens=SimpleNamespace(collection_path='/collection/login'),
            pause_account=lambda:stopped.append('account'),refresh=lambda:None)
        d.devices=SimpleNamespace(suspend=lambda:stopped.append('receiver'))
        d.calls=SimpleNamespace(suspend=lambda:stopped.append('calls'))
        d._signal=lambda:signals.append(dict(d.latest))
        def schedule(work):
            if d.busy:return False
            d.busy=True;scheduled.append(work);return True
        d._schedule=schedule
        def event(locked):
            value=SimpleNamespace(unpack=lambda:('org.freedesktop.Secret.Collection',{'Locked':locked},[]))
            d._credentials_changed(None,None,'/collection/login',None,None,value)
        event(True);event(False)
        self.assertEqual(d.latest['account_status'],'locked')
        self.assertTrue(d.credentials_pending)
        d._publish({'account_status':'signed_in','stale':False})
        self.assertEqual(d.latest['account_status'],'locked')
        self.assertEqual(scheduled,[d.model.refresh])
        self.assertFalse(d.credentials_pending)
        self.assertEqual(stopped,['account','receiver','calls'])

    def test_unrelated_collection_does_not_interrupt_account(self):
        d=Daemon.__new__(Daemon)
        d.model=SimpleNamespace(tokens=SimpleNamespace(collection_path='/collection/login'))
        value=SimpleNamespace(unpack=lambda:('org.freedesktop.Secret.Collection',{'Locked':True},[]))
        d._credentials_changed(None,None,'/collection/other',None,None,value)

    def test_network_change_while_busy_discards_prior_ready_observation(self):
        d=Daemon.__new__(Daemon);events=[]
        d.closed=False;d.busy=True;d.awake=True;d.online=True;d.environment_generation=2
        d.latest={'account_status':'signed_in','service_status':'offline','stale':True}
        d.recovery=SimpleNamespace(completed=lambda state:events.append(('completed',state['stale'])),
            request=lambda:events.append(('request',)))
        d._publish({'account_status':'signed_in','service_status':'ready','stale':False},None,1)
        self.assertTrue(d.latest['stale']);self.assertEqual(events,[('completed',True),('request',)])

    def test_sleep_cancels_lease_and_suppresses_inflight_completion(self):
        d=Daemon.__new__(Daemon);events=[]
        d.relay_runtime=None
        d.closed=False;d.busy=True;d.awake=True;d.online=True;d.environment_generation=0
        d.lease_timer=7;d.GLib=SimpleNamespace(source_remove=lambda key:events.append(('cancel',key)))
        d.latest={'account_status':'signed_in','service_status':'ready','stale':False}
        d.devices=SimpleNamespace(suspend=lambda:events.append(('suspend',)))
        d.calls=SimpleNamespace(suspend=lambda:events.append(('calls-suspend',)))
        d.network_monitor=None;d.route_check=None;d._signal=lambda:None
        d.recovery=SimpleNamespace(environment=lambda **kwargs:events.append(('environment',kwargs)),
            completed=lambda state:events.append(('completed',state['stale'])),request=lambda:events.append(('request',)))
        d._sleep_changed(SimpleNamespace(unpack=lambda:(True,)))
        d._publish({'account_status':'signed_in','service_status':'ready','stale':False},None,0)
        self.assertFalse(d.awake);self.assertIsNone(d.lease_timer)
        self.assertIn(('calls-suspend',),events)
        self.assertNotIn(('request',),events);self.assertTrue(d.latest['stale'])
