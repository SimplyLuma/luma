import json
from pathlib import Path
import socket
import tempfile
import time
import unittest
from luma_continuity.bootstrap import create_identity,approve_peer
from luma_continuity.message_devices import MessageDevices
from luma_continuity.read_receiver import ReadReceiver
from luma_continuity.local import PairedExchange
from luma_continuity.policy import Journal


class DeviceConsentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.directory=self.root/'identity';create_identity(self.directory)
        self.peer='a'*64;self.epoch='b'*32;self.current='synthetic-account'
        j=Journal(self.directory/'continuity.db');j.approve(self.peer,self.epoch,['messages.read'],account=self.current,outgoing_grants=['messages.read']);j.close()
        self.path=self.root/'config/messages-phone.json'
        self.broker=MessageDevices(self.directory,self.path,account=lambda:self.current)

    def test_explicit_selection_survives_broker_restart_and_failure_preserves_it(self):
        self.broker.select(self.peer,'Synthetic phone','127.0.0.1',18444)
        before=self.path.read_bytes();self.assertEqual(self.path.stat().st_mode&0o777,0o600)
        fresh=MessageDevices(self.directory,self.path,account=lambda:self.current)
        self.assertEqual(len(fresh.devices()),1)
        with self.assertRaises(ValueError):fresh.select(self.peer,'Synthetic phone','not-an-address',18444)
        self.assertEqual(self.path.read_bytes(),before)
        self.current='other-account'
        with self.assertRaises(PermissionError):fresh.select(self.peer,'Synthetic phone','127.0.0.1',18444)
        self.assertEqual(self.path.read_bytes(),before)

    def test_revoke_preserves_local_selection_but_removes_access(self):
        self.broker.select(self.peer,'Synthetic phone','127.0.0.1',18444)
        self.broker.revoke(self.peer)
        self.assertTrue(self.path.exists());self.assertEqual(self.broker.devices(),[])
        with self.assertRaises(PermissionError):self.broker.select(self.peer,'Synthetic phone','127.0.0.1',18444)

    def test_ui_broker_invitation_roundtrip_requires_independent_codes(self):
        desktop=MessageDevices(self.root/'desktop',self.root/'desktop-config/selection.json',account=lambda:'synthetic')
        phone=MessageDevices(self.root/'phone',self.root/'phone-config/selection.json',account=lambda:'synthetic')
        offer=desktop.pairing_document('create')
        with self.assertRaises(PermissionError):phone.pairing_document('accept',offer['document'],'0'*64)
        self.assertEqual(phone.devices(),[])
        response=phone.pairing_document('accept',offer['document'],offer['fingerprint'])
        finished=desktop.pairing_document('finish',response['document'],response['fingerprint'])
        self.assertEqual(finished['kind'],'complete')
        self.assertTrue(desktop.devices()[0]['can_read']);self.assertFalse(desktop.devices()[0]['can_share'])
        self.assertTrue(phone.devices()[0]['can_share']);self.assertFalse(phone.devices()[0]['can_read'])
        desktop.select(response['fingerprint'],'Synthetic phone','127.0.0.1',18444)
        self.assertTrue(desktop.selection_path.exists())
        journal=Journal(phone.directory/'continuity.db')
        try:self.assertNotIn('messages.send',json.loads(journal.db.execute('SELECT grants FROM peers').fetchone()[0]))
        finally:journal.close()

    def test_incoming_only_grant_cannot_select_phone(self):
        j=Journal(self.directory/'continuity.db');j.set_grants(self.peer,['messages.read'],outgoing_grants=[]);j.close()
        with self.assertRaises(PermissionError):self.broker.select(self.peer,'Synthetic phone','127.0.0.1',18444)
        self.assertFalse(self.path.exists())

    def test_sharing_requires_explicit_start_and_stops_on_revoke(self):
        events=[]
        class Receiver:
            def start(self):events.append('start')
            def stop(self):events.append('stop')
        self.broker.receiver_factory=lambda *args:Receiver()
        self.assertEqual(events,[])
        self.broker.share(self.peer,'127.0.0.1',18444)
        self.broker.revoke(self.peer)
        self.assertEqual(events,['start','stop'])

    def test_sending_consent_preserves_epoch_and_other_direction(self):
        self.broker.set_sending(self.peer,True,True)
        row=self.broker.devices()[0]
        self.assertEqual(row['epoch'],self.epoch)
        self.assertTrue(row['can_receive_sends']);self.assertFalse(row['can_send'])
        self.broker.set_sending(self.peer,False,True)
        self.broker.set_sending(self.peer,True,False)
        row=self.broker.devices()[0]
        self.assertTrue(row['can_read']);self.assertTrue(row['can_share'])
        self.assertTrue(row['can_send']);self.assertFalse(row['can_receive_sends'])
        self.current='wrong-account'
        with self.assertRaises(PermissionError):self.broker.set_sending(self.peer,False,True)

    def test_suspend_resume_retains_only_explicit_same_identity_sharing(self):
        events=[]
        class Receiver:
            def start(self):events.append('start')
            def stop(self):events.append('stop')
        self.broker.receiver_factory=lambda *args:Receiver()
        self.broker.resume();self.assertEqual(events,[])
        self.broker.share(self.peer,'127.0.0.1',18444)
        self.broker.suspend();self.assertIsNotNone(self.broker.desired_sharing)
        self.current=None;self.broker.resume();self.assertIsNotNone(self.broker.desired_sharing)
        self.current='synthetic-account'
        self.broker.resume();self.assertEqual(events,['start','stop','start'])
        self.broker.suspend();self.current='other-account'
        with self.assertRaises(PermissionError):self.broker.resume()
        self.assertIsNone(self.broker.desired_sharing)
        self.current='synthetic-account';self.broker.resume()
        self.assertEqual(events,['start','stop','start','stop'])

    def test_explicit_stop_and_revoke_while_suspended_clear_resume_intent(self):
        class Receiver:
            def start(self):pass
            def stop(self):pass
        self.broker.receiver_factory=lambda *args:Receiver()
        self.broker.share(self.peer,'127.0.0.1',18444);self.broker.suspend();self.broker.stop()
        self.assertIsNone(self.broker.desired_sharing)
        self.broker.share(self.peer,'127.0.0.1',18444);self.broker.suspend();self.broker.revoke(self.peer)
        self.assertIsNone(self.broker.desired_sharing)


class ReadReceiverTests(unittest.TestCase):
    def test_owned_idle_listener_has_no_periodic_authorization_wakeups(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);client,phone=root/'client',root/'phone'
            client_pin=create_identity(client);create_identity(phone)
            approve_peer(phone,client/'device.pem',client_pin,'f'*32,['messages.read'],account='synthetic')
            probe=socket.socket();probe.bind(('127.0.0.1',0));port=probe.getsockname()[1];probe.close()
            checks=[]
            receiver=ReadReceiver(phone,client_pin,address='127.0.0.1',port=port,
                authorized=lambda:checks.append(True) or True,event_driven=True,
                adapter_factory=lambda:(lambda *_:{},lambda:None))
            receiver.start()
            try:
                time.sleep(.8)
                self.assertLessEqual(len(checks),2)
                self.assertTrue(receiver.running)
            finally:receiver.stop(wait=True)
            self.assertFalse(receiver.running)

    def test_real_tls_read_only_and_close(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);client,phone=root/'client',root/'phone'
            client_pin,phone_pin=create_identity(client),create_identity(phone);epoch='c'*32;account='synthetic'
            approve_peer(client,phone/'device.pem',phone_pin,epoch,['messages.read','messages.send'],account=account)
            approve_peer(phone,client/'device.pem',client_pin,epoch,['messages.read','messages.send'],account=account)
            probe=socket.socket();probe.bind(('127.0.0.1',0));port=probe.getsockname()[1];probe.close()
            allowed=[True];calls=[];closed=[]
            receiver=ReadReceiver(phone,client_pin,address='127.0.0.1',port=port,authorized=lambda:allowed[0],
                adapter_factory=lambda:(lambda cap,payload:calls.append(cap) or {'threads':[]},lambda:closed.append(True)))
            receiver.start()
            exchange=PairedExchange(client,phone_pin,epoch,'127.0.0.1',port,timeout=2)
            request=dict(version=1,epoch=epoch,id='d'*32,account=account,capability='messages.read',expires=int(time.time())+60,payload={})
            try:
                self.assertEqual(exchange(request)['result'],{'threads':[]})
                request.update(id='e'*32,capability='messages.send')
                self.assertEqual(exchange(request)['result'],{'error':'unavailable'})
                self.assertEqual(calls,['messages.read'])
                allowed[0]=False
            finally:receiver.stop(wait=True)
            self.assertFalse(receiver.thread.is_alive());self.assertEqual(closed,[True])
            with self.assertRaises(OSError):socket.create_connection(('127.0.0.1',port),timeout=.2)

    def test_idle_receiver_exits_when_account_observation_becomes_stale(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);client,phone=root/'client',root/'phone'
            client_pin,phone_pin=create_identity(client),create_identity(phone)
            approve_peer(phone,client/'device.pem',client_pin,'f'*32,['messages.read'],account='synthetic')
            probe=socket.socket();probe.bind(('127.0.0.1',0));port=probe.getsockname()[1];probe.close()
            allowed=[True]
            receiver=ReadReceiver(phone,client_pin,address='127.0.0.1',port=port,authorized=lambda:allowed[0],
                adapter_factory=lambda:(lambda *_: {},lambda:None))
            receiver.start();allowed[0]=False;receiver.thread.join(1)
            try:self.assertFalse(receiver.running)
            finally:receiver.stop(wait=True)

    def test_no_wildcard_or_unauthorized_listener(self):
        for address in ('0.0.0.0','::','224.0.0.1'):
            with self.assertRaises(ValueError):ReadReceiver('.', 'a'*64,address=address,port=18444,authorized=lambda:True,adapter_factory=None)
        receiver=ReadReceiver('.', 'a'*64,address='127.0.0.1',port=18444,authorized=lambda:False,adapter_factory=None)
        with self.assertRaises(PermissionError):receiver.start()
        self.assertIsNone(receiver.listener)

    def test_actual_daemon_sender_tls_deduplicates_and_checks_send_consent(self):
        from unittest.mock import patch
        from luma_continuity.daemon import Daemon
        from luma_continuity.account import AccountModel
        from prairie_apps.messages_backend import ModemMessagingTransport,MessagingCapability
        class Bus:
            def register_object(self,*_):return 1
            def signal_subscribe(self,*_):return 1
            def signal_unsubscribe(self,*_):pass
            def unregister_object(self,*_):pass
            def emit_signal(self,*_):pass
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);home=root/'phone-home';home.mkdir()
            client=root/'client';phone=home/'.local/share/luma-connect/device'
            client_pin=create_identity(client);phone.parent.mkdir(parents=True);phone_pin=create_identity(phone)
            epoch='a'*32;account='synthetic-daemon'
            approve_peer(client,phone/'device.pem',phone_pin,epoch,['messages.read','messages.send'],account=account)
            approve_peer(phone,client/'device.pem',client_pin,epoch,['messages.read'],account=account)
            model=AccountModel();model.state.update(account_status='signed_in',service_status='ready',stale=False,
                valid_until=time.time()+15,lease_deadline_monotonic=time.monotonic()+15,identity={'account_id':account})
            sent=[]
            with patch.object(Path,'home',return_value=home),patch.dict('os.environ',{'XDG_DATA_HOME':str(home/'.local/share')}), \
                 patch.object(ModemMessagingTransport,'inspect',return_value=MessagingCapability(True,'Synthetic ready')), \
                 patch.object(ModemMessagingTransport,'send',side_effect=lambda *args:sent.append(args) or 'synthetic-modem'):
                daemon=Daemon(Bus(),model,observe_environment=False)
                probe=socket.socket();probe.bind(('127.0.0.1',0));port=probe.getsockname()[1];probe.close()
                try:
                    daemon.devices.share(client_pin,'127.0.0.1',port)
                    exchange=PairedExchange(client,phone_pin,epoch,'127.0.0.1',port,timeout=3)
                    request=dict(version=1,epoch=epoch,id='b'*32,account=account,capability='messages.send',expires=int(time.time())+60,
                                 payload={'address':'+12025550123','body':'Synthetic daemon test'})
                    self.assertEqual(exchange(request)['state'],'denied');self.assertEqual(sent,[])
                    daemon.devices.set_sending(client_pin,True,True)
                    # Treat the first receipt as lost, then retry the SAME operation.
                    exchange(request)
                    result=exchange(request)
                    self.assertEqual(result['state'],'complete');self.assertEqual(result['result']['state'],'sent')
                    self.assertEqual(len(sent),1)
                    daemon.devices.set_sending(client_pin,True,False)
                    request['id']='c'*32
                    self.assertEqual(exchange(request)['state'],'denied');self.assertEqual(len(sent),1)
                    self.assertTrue(daemon.devices.devices()[0]['can_share'])
                finally:
                    receiver=daemon.devices.receiver
                    daemon.close()
                    if receiver:receiver.stop(wait=True)
