"""Public call readiness follows channel changes and the account lease."""
import time
import sqlite3
import unittest
from types import SimpleNamespace
from luma_continuity.daemon import Daemon


class CallPublicStateTests(unittest.TestCase):
    def setUp(self):
        self.d=Daemon.__new__(Daemon)
        self.d.closed=False;self.d.enabled=True;self.d.awake=True
        self.d.online=True;self.d.credential_locked=False
        self.row=dict(peer='peer',connected=True,sharing=True,
                      connection=dict(reason='connected',retry_at=None),
                      permissions={'calls.read':True,'calls.control':True})
        self.d.calls=SimpleNamespace(relay=SimpleNamespace(devices=lambda:[self.row]))
        self.d.latest=dict(account_status='signed_in',service_status='ready',stale=False,
                           valid_until=time.time()+15,lease_deadline_monotonic=time.monotonic()+15,
                           call_devices=[dict(self.row)])

    def test_channel_loss_is_visible_without_account_refresh(self):
        self.row=dict(self.row,connected=False,connection=dict(reason='retry_required',retry_at=None))
        self.assertFalse(self.d._public_state()['call_devices'][0]['connected'])
        self.assertTrue(self.d.latest['call_devices'][0]['connected'])

    def test_expired_lease_suppresses_cached_ready_and_recovers(self):
        self.d.latest['lease_deadline_monotonic']=time.monotonic()-1
        row=self.d._public_state()['call_devices'][0]
        self.assertFalse(row['connected']);self.assertFalse(row['permissions']['calls.control'])
        self.assertTrue(row['sharing'])
        self.assertTrue(self.row['connected']);self.assertTrue(self.row['permissions']['calls.control'])
        self.d.latest['lease_deadline_monotonic']=time.monotonic()+15
        self.assertTrue(self.d._public_state()['call_devices'][0]['connected'])

    def test_stale_service_suppresses_runtime_ready(self):
        self.d.latest.update(stale=True,service_status='unavailable')
        self.assertFalse(self.d._public_state()['call_devices'][0]['connected'])

    def test_no_runtime_drops_old_worker_rows(self):
        self.d.calls.relay=None
        self.assertEqual(self.d._public_state()['call_devices'],[])

    def test_unreadable_policy_never_reuses_cached_authority(self):
        def unreadable():raise sqlite3.DatabaseError('synthetic invalid database')
        self.d.calls.relay.devices=unreadable
        row=self.d._public_state()['call_devices'][0]
        self.assertFalse(row['connected']);self.assertFalse(row['permissions']['calls.read'])
        self.assertTrue(row['sharing'])
