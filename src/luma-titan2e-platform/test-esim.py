# SPDX-License-Identifier: Apache-2.0
import importlib.machinery
import os
import tempfile
import threading
import pathlib
import socket
import struct
import time
import unittest
from unittest.mock import patch

mod = importlib.machinery.SourceFileLoader('titan_esim', str(pathlib.Path(__file__).with_name('luma-titan2e-esim'))).load_module()


class FakeAt:
    calls = []
    call_active = False
    fail_open = False
    def __init__(self):
        self.broken = False
        self.closed = False
    def close(self):
        self.closed = True
    def command(self, command, timeout=5, sim=2):
        self.calls.append(command)
        if command == 'AT+CLCC':
            return '+CLCC: 1' if self.call_active else 'OK'
        if command == 'AT+CPIN?':
            return '+CPIN: EMPTY_EUICC'
        if command.startswith('AT+CCHO'):
            if self.fail_open:
                self.broken = True
                raise mod.Refused('uncertain')
            return '+CCHO: 7\r\nOK\r\n'
        if command.startswith('AT+CGLA'):
            return '+CGLA: 4,"9000"\r\nOK\r\n'
        return 'OK'


class UartDeadlineTests(unittest.TestCase):
    def test_sms_error_is_synchronized_and_allows_next_read(self):
        a, peer = socket.socketpair()
        self.addCleanup(peer.close)
        with patch.object(mod.os, 'open', return_value=a.detach()):at=mod.At()
        self.addCleanup(at.close)
        def answer():
            for reply in (b'\r\n+CMS ERROR: 314\r\n', b'\r\nOK\r\n'):
                peer.recv(4096);peer.sendall(reply)
        worker=threading.Thread(target=answer);worker.start()
        self.addCleanup(worker.join,2)
        with self.assertRaises(mod.Rejected):at.command('AT+CPMS=?',timeout=.2)
        self.assertFalse(at.broken)
        self.assertIn('OK',at.command('AT+CPMS=?',timeout=.2))

    def test_deadline_crossing_before_select_is_a_bounded_failure(self):
        a, peer = socket.socketpair()
        self.addCleanup(peer.close)
        with patch.object(mod.os, 'open', return_value=a.detach()):
            at = mod.At()
        self.addCleanup(at.close)
        # The deadline can pass between the loop condition and select().
        with patch.object(mod.time, 'monotonic', side_effect=[0, 0, 6, 6]):
            with self.assertRaises(mod.Refused):
                at.command('AT', timeout=5)
        self.assertTrue(at.broken)


class MipcHarness(unittest.TestCase):
    def make(self, responder):
        a, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        with patch.object(mod.os, 'open', return_value=a.detach()):
            at = mod.MipcAt()
        self.addCleanup(at.close)
        self.addCleanup(peer.close)
        def answer():
            request = peer.recv(4096)
            responder(peer, request)
        worker = threading.Thread(target=answer)
        worker.start()
        self.addCleanup(worker.join, 2)
        return at
    def reply(self, request, body=None, txid=None, sim=None, message=4):
        _, _, ps, _, _, tid, _ = struct.unpack_from('<IIBBHHH', request)
        if body is None:
            value = b'\r\nOK\r\n\0'
            body = struct.pack('<HHI', 0, 4, 0) + struct.pack('<HH', 0x100, len(value)) + value + b'\0' * (-(4+len(value))&7)
        return struct.pack('<IIBBHHH', 0x24541984, 0, ps if sim is None else sim,
                           0, message, tid if txid is None else txid, len(body)) + body

class MipcTests(MipcHarness):
    def test_coordinated_sim2_reset_requires_matching_native_completion(self):
        requests = []
        def answer(peer, request):
            requests.append(request)
            peer.sendall(self.reply(request, struct.pack('<HHI', 0, 4, 0), message=0x052a))
        at = self.make(answer)
        at.reset_sim2()
        self.assertEqual(struct.unpack_from('<BBH', requests[0], 8), (2, 0, 0x0529))
        self.assertEqual(requests[0][16:], struct.pack('<HHB3x', 0x0100, 1, 0))
        self.assertFalse(at.broken)
    def test_reset_missing_native_result_is_not_completion(self):
        def answer(peer, request):
            peer.sendall(self.reply(request, b'', message=0x052a))
        at = self.make(answer)
        with self.assertRaises(mod.Refused):at.reset_sim2()
        self.assertTrue(at.broken)
    def test_explicit_sim2_request_and_acknowledgement(self):
        requests = []
        def answer(peer, request):
            requests.append(request)
            peer.sendall(self.reply(request))
        at = self.make(answer)
        self.assertIn('OK', at.command('AT+CPIN?'))
        request = requests[0]
        self.assertEqual(struct.unpack_from('<BBH', request, 8), (2, 0, 3))
        self.assertEqual(struct.unpack_from('<HH', request, 16), (0x8100, 8))
        self.assertEqual(request[20:28], b'AT+CPIN?')
        self.assertEqual(len(request), 32)
        self.assertEqual(request[28:], b'\0' * 4)
        self.assertFalse(at.broken)
    def test_eight_byte_aligned_fields_before_terminal_result(self):
        # Firmware serializes each four-byte TLV header plus payload to eight
        # bytes. A 40-byte field was physically observed before the result;
        # synthetic payloads keep the regression free of device identifiers.
        def answer(peer, request):
            unknown = struct.pack('<HH', 0x107, 40) + bytes(40) + bytes(4)
            text = struct.pack('<HH', 0x100, 8) + b'\r\nOK\r\n\0\0' + bytes(4)
            peer.sendall(self.reply(request, unknown + text + struct.pack('<HHI', 0, 4, 0)))
        at = self.make(answer)
        self.assertIn('OK', at.command('AT'))
        self.assertFalse(at.broken)
    def test_boot_transport_uses_same_eight_byte_wire_alignment(self):
        daemon = importlib.machinery.SourceFileLoader('modem_boot_test', str(pathlib.Path(__file__).with_name('luma-modemd'))).load_module()
        a, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(a.close)
        self.addCleanup(peer.close)
        requests = []
        def answer():
            request = peer.recv(4096)
            requests.append(request)
            peer.sendall(self.reply(request, struct.pack('<HHI', 0, 4, 0)))
        worker = threading.Thread(target=answer)
        worker.start()
        self.addCleanup(worker.join, 2)
        self.assertEqual(daemon.mipc_request(a.fileno(), 3, 2, [(0x8100, b'AT+CPIN?')], 0x4c00), 0)
        self.assertEqual(requests[0], struct.pack('<IIBBHHH', 0x24541984, 0, 2, 0, 3, 0x4c00, 16) + b'\x00\x81\x08\x00AT+CPIN?\0\0\0\0')
    def test_foreign_transaction_cannot_satisfy_request(self):
        def answer(peer, request):
            peer.sendall(self.reply(request, txid=1))
            peer.sendall(self.reply(request))
        self.assertIn('OK', self.make(answer).command('AT'))
    def test_explicit_rejection_is_not_an_uncertain_write(self):
        def answer(peer, request):
            peer.sendall(self.reply(request, struct.pack('<HHI', 0, 4, 17)))
        at = self.make(answer)
        with self.assertRaises(mod.Rejected):at.command('AT')
        self.assertFalse(at.broken)
    def test_sms_error_is_synchronized_in_mipc_payload(self):
        def answer(peer, request):
            value=b'\r\n+CMS ERROR: 314\r\n\0'
            body=struct.pack('<HHI',0,4,0)+struct.pack('<HH',0x100,len(value))+value+b'\0'*(-(4+len(value))&7)
            peer.sendall(self.reply(request,body))
        at=self.make(answer)
        with self.assertRaises(mod.Rejected):at.command('AT+CPMS=?')
        self.assertFalse(at.broken)

    def test_truncated_tlv_poisoned(self):
        def answer(peer, request):
            peer.sendall(self.reply(request, struct.pack('<HHIHH', 0, 4, 0, 0x100, 900)))
        at = self.make(answer)
        with self.assertRaises(mod.Refused):at.command('AT')
        self.assertTrue(at.broken)
    def test_wrong_sim_confirmation_times_out(self):
        def answer(peer, request):peer.sendall(self.reply(request, sim=1))
        at = self.make(answer)
        with self.assertRaises(mod.Refused):at.command('AT', timeout=.05)
        self.assertTrue(at.broken)
    def test_duplicate_response_refused(self):
        def answer(peer, request):
            value = struct.pack('<HH', 0x100, 4) + b'OK\r\n'
            peer.sendall(self.reply(request, struct.pack('<HHI', 0, 4, 0) + value + value))
        at = self.make(answer)
        with self.assertRaises(mod.Refused):at.command('AT')
        self.assertTrue(at.broken)



class HandoverApi:
    def __init__(self, state=3):
        self.identity = ':1.50'
        self.deadline = None
        self.state = state
        self.events = []
        self.call_active = False
    def owner(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise mod.Refused('Cellular handover deadline reached')
        return self.identity
    def objects(self, owner):
        result = {'/modem/1': {mod.MmApi.name+'.Modem': {'Device':'titan2e-md1', 'State':self.state}}}
        if self.call_active:result['/call/1'] = {mod.MmApi.name+'.Call': {'State':3}}
        return result
    def inhibit(self, owner, value):
        self.events.append(('inhibit', value))
        if not value:self.state=3
    def enable(self, owner, path):
        self.events.append(('enable', path));self.state=6

class SubscriptionHarness(unittest.TestCase):
    def setUp(self):
        self.api=HandoverApi()
        self.s=mod.PrimarySubscription(self.api)
        self.slot=4
        self.closed=False
        self.commands=[]
        test=self
        class Uart:
            broken=False
            def close(inner):test.closed=True
            def command(inner,command,timeout=5):
                test.commands.append(command)
                if command=='AT+ESUO?':return '+ESUO: %d, 4\r\nOK\r\n'%test.slot
                if command.startswith('AT+ESUO='):test.slot=int(command[-1])
                if command=='AT+CPIN?':return '+CPIN: READY\r\nOK\r\n'
                if command=='AT+CPMS=?':return '+CPMS: ("SM","ME"),("SM","ME"),("SM","ME")\r\nOK\r\n'
                if command=='AT+CGDCONT=?':return '+CGDCONT: (0-49),"IP"\r\nOK\r\n'
                return 'OK'
        self.patch=patch.object(mod,'At',Uart);self.patch.start();self.addCleanup(self.patch.stop)
        FakeAt.calls=[];FakeAt.call_active=False
        self.mipc=FakeAt()

class SubscriptionTests(SubscriptionHarness):
    def test_unchanged_subscription_never_inhibits(self):
        self.s.select(4,4,self.mipc)
        self.assertEqual(self.api.events,[]);self.assertEqual(self.commands,[])
    def test_verified_selection_preserves_radio_off(self):
        self.s.select(4,5,self.mipc)
        self.assertEqual(self.slot,5);self.assertTrue(self.closed)
        self.assertEqual(self.api.events,[('inhibit',True),('inhibit',False)])
        self.assertFalse(self.s.inhibited)
    def test_previously_enabled_radio_restored_through_mm(self):
        self.api.state=8
        self.s.select(4,5,self.mipc)
        self.assertEqual(self.api.events[-1],('enable','/modem/1'))
    def test_handover_waits_for_complete_sms_and_pdp_capabilities(self):
        self.s.begin(4,self.mipc)
        command=self.s.uart.command
        attempts=[]
        def delayed(value,timeout=5):
            if value=='AT+CPMS=?':
                self.assertTrue(self.s.inhibited)
                self.assertFalse(self.closed)
                attempts.append(value)
                if len(attempts)==1:return 'OK'
            return command(value,timeout)
        self.s.uart.command=delayed
        self.s.finish(5)
        self.assertEqual(len(attempts),2)
        self.assertIn('AT+CGDCONT=?',self.commands)
        self.assertFalse(self.s.inhibited)
    def test_incomplete_telephony_capabilities_retain_exclusion(self):
        self.s.begin(4,self.mipc)
        command=self.s.uart.command
        self.s.uart.command=lambda value,timeout=5: 'OK' if value=='AT+CGDCONT=?' else command(value,timeout)
        clock=[100]
        def advance(delay):clock[0]+=delay
        with patch.object(mod.time,'monotonic',side_effect=lambda:clock[0]),patch.object(mod.time,'sleep',side_effect=advance):
            with self.assertRaises(mod.Refused):self.s.finish(5)
        self.assertTrue(self.s.inhibited)
        self.assertFalse(self.closed)
        self.assertNotIn(('inhibit',False),self.api.events)
    def test_readiness_preserves_existing_uart_response_budget(self):
        self.s.begin(4,self.mipc)
        command=self.s.uart.command
        clock=[100]
        def delayed(value,timeout=5):
            if value=='AT+CPIN?':
                if timeout < 3.5:raise mod.Refused('Response not complete within shortened budget')
                clock[0]+=3.5
            return command(value,timeout)
        self.s.uart.command=delayed
        with patch.object(mod.time,'monotonic',side_effect=lambda:clock[0]):
            self.s.finish(5)
        self.assertTrue(self.closed)
        self.assertFalse(self.s.inhibited)
    def test_call_refused_before_port_handover(self):
        self.api.call_active=True
        with self.assertRaises(mod.Refused):self.s.select(4,5,self.mipc)
        self.assertEqual(self.api.events,[]);self.assertEqual(self.commands,[])
    def test_mipc_call_refused_before_port_handover(self):
        FakeAt.call_active=True
        with self.assertRaises(mod.Refused):self.s.select(4,5,self.mipc)
        self.assertEqual(self.api.events,[])
    def test_owner_change_never_reclaims_uart(self):
        original=self.api.inhibit
        def changed(owner,value):original(owner,value);self.api.identity=':1.51'
        self.api.inhibit=changed
        with self.assertRaises(mod.Refused):self.s.select(4,5,self.mipc)
        self.assertEqual(self.commands,[]);self.assertTrue(self.s.inhibited)
    def test_prepared_identity_check_has_its_own_bounded_deadline(self):
        self.s.begin(4,self.mipc)
        self.api.deadline=0
        with patch.object(mod.time,'monotonic',return_value=100):
            self.s.validate_prepared()
        self.assertEqual(self.api.deadline,105)
    def test_prepared_validation_requires_same_owner_and_exclusive_uart(self):
        with self.assertRaises(mod.Refused):self.s.validate_prepared()
        self.s.begin(4,self.mipc)
        self.s.validate_prepared()
        self.api.identity=':1.51'
        with self.assertRaises(mod.Refused):self.s.validate_prepared()
        self.assertTrue(self.s.inhibited)
        self.assertFalse(self.closed)
    def test_unexpected_selection_retains_exclusion(self):
        self.slot=5
        with self.assertRaises(mod.Refused):self.s.select(4,5,self.mipc)
        self.assertFalse(self.closed);self.assertTrue(self.s.inhibited)
        self.assertFalse(any(c.startswith('AT+ESUO=') for c in self.commands))
    def test_busy_uart_never_force_reclaimed(self):
        with patch.object(mod,'At',side_effect=OSError(16,'busy')):
            with self.assertRaises(mod.Refused):self.s.select(4,5,self.mipc)
        self.assertTrue(self.s.inhibited);self.assertIsNone(self.s.uart)


class OwnerTests(unittest.TestCase):
    def setUp(self):
        FakeAt.calls = []
        FakeAt.call_active = FakeAt.fail_open = False
        self.mock = patch.multiple(mod, At=FakeAt, MipcAt=FakeAt)
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.o = mod.Owner(lambda *a: None)
        self.sender, self.token = ':1.12', 'a'*32
    def acquire(self):
        self.assertTrue(self.o.acquire(self.sender, self.token))
    def packet(self, peer, seq, op, payload=b''):
        peer.sendall(struct.pack('!IIII',mod.MAGIC,seq,op,len(payload))+payload)
        result = peer.recv(65554)
        self.assertGreaterEqual(len(result),16)
        self.assertEqual(struct.unpack('!IIII',result[:16]),(mod.MAGIC,seq,0,len(result)-16))
        return result[16:]
    def terminal(self, handle):
        end=time.monotonic()+2
        while self.o.active and time.monotonic()<end:
            time.sleep(.005)
        return self.o.lookup(self.sender,handle)
    def test_prepared_mutation_refreshes_before_select_and_release(self):
        events=[]
        class Prepared:
            def validate_prepared(inner):pass
            inhibited=False
            uart=None
            def begin(inner,previous,at):
                events.append(('begin',previous));inner.inhibited=True
            def finish(inner,desired):
                events.append(('finish',desired));inner.inhibited=False
        self.o.subscription=Prepared()
        self.acquire()
        before=self.o.epoch
        self.assertFalse(self.o.prepare(':1.99',self.token,False))
        self.assertTrue(self.o.prepare(self.sender,self.token,False))
        self.assertFalse(self.o.prepare(self.sender,self.token,False))
        self.o.generation += 1  # a card channel was opened after preparation
        self.o.at.reset_sim2=lambda:events.append(('reset',2))
        self.assertTrue(self.o.release(self.sender,self.token))
        self.assertEqual(events,[('begin',4),('reset',2),('finish',4)])
        self.assertGreater(self.o.epoch,before)
        self.assertFalse(self.o.lease)
    def test_cancel_before_card_channel_restores_without_reset(self):
        events=[]
        class Prepared:
            def validate_prepared(inner):pass
            inhibited=False
            uart=None
            def begin(inner,previous,at):
                events.append(('begin',previous));inner.inhibited=True
            def finish(inner,desired):
                events.append(('finish',desired));inner.inhibited=False
        self.o.subscription=Prepared()
        self.acquire()
        epoch=self.o.epoch
        self.assertTrue(self.o.prepare(self.sender,self.token,True))
        self.o.at.reset_sim2=lambda:self.fail('unopened card must not be reset')
        self.assertTrue(self.o.release(self.sender,self.token))
        self.assertEqual(events,[('begin',4),('finish',4)])
        self.assertEqual(self.o.epoch,epoch)
        self.assertFalse(self.o.poisoned)
        self.assertFalse(self.o.lease)
    def test_stale_readiness_cannot_complete_profile_enable(self):
        self.acquire();self.o.prepared=True;self.o.expected_enabled=True
        class Prepared:
            def validate_prepared(inner):pass
        self.o.subscription=Prepared()
        self.o.prepared_generation=self.o.generation-1
        self.o.at.reset_sim2=lambda:None
        clock=iter((0,11,22))
        with patch.object(mod.time,'monotonic',side_effect=lambda:next(clock)):
            with patch.object(mod.time,'sleep'):
                self.assertFalse(self.o.release(self.sender,self.token))
        self.assertTrue(self.o.poisoned)
        self.assertFalse(self.o.at.closed)
    def test_failed_native_reset_retains_handover_and_kernel_exclusion(self):
        class Prepared:
            def validate_prepared(inner):pass
            inhibited=True
            uart=object()
            def finish(inner,desired):raise AssertionError('must not resume uncertain reset')
        self.acquire();self.o.prepared=True;self.o.subscription=Prepared()
        self.o.prepared_generation=self.o.generation-1
        def reset():raise mod.Refused('uncertain')
        self.o.at.reset_sim2=reset
        self.assertFalse(self.o.release(self.sender,self.token))
        self.assertTrue(self.o.poisoned)
        self.assertFalse(self.o.at.closed)
        self.assertTrue(self.o.subscription.inhibited)
    def prepare_rejected_close(self, prepared=True, reset_error=None):
        self.acquire()
        self.events=[]
        if prepared:
            class Prepared:
                inhibited=True
                uart=object()
                def begin(inner,previous,at):pass
                def validate_prepared(inner):pass
                def finish(inner,desired):self.events.append(('finish',desired))
            self.o.subscription=Prepared()
            self.assertTrue(self.o.prepare(self.sender,self.token,False))
        original=self.o.at.command
        def command(value,*args,**kwargs):
            if value.startswith('AT+CCHC'):
                self.events.append(('close',value))
                raise mod.Rejected('explicit rejection')
            return original(value,*args,**kwargs)
        def reset():
            self.events.append(('reset',2))
            if reset_error:raise reset_error('reset failed')
        self.o.at.command=command
        self.o.at.reset_sim2=reset
        peer,handle=self.o.open(self.sender,self.token)
        self.packet(peer,1,1);self.packet(peer,2,2,mod.AID)
        return peer,handle
    def test_rejected_close_requires_reset_and_readback_before_terminal(self):
        peer,handle=self.prepare_rejected_close()
        epoch=self.o.epoch
        self.packet(peer,3,4,b'\1');self.packet(peer,4,5);peer.close()
        self.assertTrue(self.terminal(handle)['safe'])
        self.assertEqual(self.events,[('close','AT+CCHC=7'),('reset',2)])
        self.assertEqual(self.o.epoch,epoch)
        # The broker can independently read the card in the same reservation.
        check,h=self.o.open(self.sender,self.token,epoch)
        self.packet(check,1,1);self.packet(check,2,5);check.close()
        self.assertTrue(self.terminal(h)['safe'])
        self.assertTrue(self.o.release(self.sender,self.token))
        self.assertEqual(self.events[-1],('finish',4))
        self.assertEqual(self.events.count(('reset',2)),1)
        self.assertGreater(self.o.epoch,epoch)
    def test_unprepared_close_rejection_cannot_reset(self):
        peer,handle=self.prepare_rejected_close(prepared=False)
        peer.close()
        self.assertFalse(self.terminal(handle)['safe'])
        self.assertTrue(self.o.poisoned)
        self.assertFalse(self.o.at.closed)
        self.assertFalse(any(e[0]=='reset' for e in self.events))
    def test_rejected_or_uncertain_reset_retains_exclusion_without_retry(self):
        for error in (mod.Rejected,mod.Refused):
            with self.subTest(error=error):
                self.o=mod.Owner(lambda *a:None)
                peer,handle=self.prepare_rejected_close(reset_error=error)
                peer.close()
                self.assertFalse(self.terminal(handle)['safe'])
                self.assertTrue(self.o.poisoned)
                self.assertFalse(self.o.at.closed)
                self.assertEqual(self.events.count(('reset',2)),1)
                self.assertFalse(self.o.release(self.sender,self.token))
    def test_changed_prepared_owner_refuses_reset(self):
        peer,handle=self.prepare_rejected_close()
        def changed():raise mod.Refused('owner changed')
        self.o.subscription.validate_prepared=changed
        peer.close()
        self.assertFalse(self.terminal(handle)['safe'])
        self.assertFalse(any(e[0]=='reset' for e in self.events))
    def test_stale_readiness_after_reset_cannot_prove_close(self):
        peer,handle=self.prepare_rejected_close()
        self.o.expected_enabled=True
        original=mod.time.monotonic
        # Fast-forward only the worker's readiness deadline, leaving record
        # expiry and the test's own terminal wait on their real clocks.
        count=[0]
        def clock():
            if threading.current_thread() is not threading.main_thread():
                count[0]+=1
                return original()+count[0]*21
            return original()
        with patch.object(mod.time,'monotonic',side_effect=clock):
            peer.close()
            self.assertFalse(self.terminal(handle)['safe'])
        self.assertTrue(self.o.poisoned)
        self.assertFalse(self.o.at.closed)
        self.assertEqual(self.events.count(('reset',2)),1)
    def test_second_close_rejection_cannot_reset_again(self):
        peer,handle=self.prepare_rejected_close()
        self.packet(peer,3,4,b'\1');self.packet(peer,4,5);peer.close()
        self.assertTrue(self.terminal(handle)['safe'])
        peer,h=self.o.open(self.sender,self.token)
        self.packet(peer,1,1);self.packet(peer,2,2,mod.AID);peer.close()
        self.assertFalse(self.terminal(h)['safe'])
        self.assertEqual(self.events.count(('reset',2)),1)
    def test_status_stays_pending_while_native_reset_runs(self):
        peer,handle=self.prepare_rejected_close()
        started=threading.Event();resume=threading.Event()
        def reset():
            started.set()
            self.assertTrue(resume.wait(3))
        self.o.at.reset_sim2=reset
        peer.close();self.assertTrue(started.wait(1))
        try:
            # Run lookup separately so an accidental hardware-lock dependency
            # fails in bounded time rather than hanging the suite.
            result=[]
            reader=threading.Thread(target=lambda:result.append(self.o.lookup(self.sender,handle)))
            reader.start();reader.join(.5)
            self.assertFalse(reader.is_alive())
            self.assertEqual((result[0]['state'],result[0]['safe']),(0,False))
        finally:resume.set()
        self.assertTrue(self.terminal(handle)['safe'])
    def test_kernel_busy_never_force_reclaims(self):
        with patch.object(mod,'MipcAt',side_effect=OSError(16,'busy')):
            self.assertFalse(self.o.acquire(self.sender,self.token))
        self.assertFalse(self.o.lease)
    def test_invalid_token_and_concurrent_lease_refused(self):
        self.assertFalse(self.o.acquire(self.sender,'invalid'))
        self.acquire()
        self.assertFalse(self.o.acquire(':1.13','b'*32))
        self.assertFalse(self.o.release(':1.13',self.token))
        self.assertFalse(self.o.release(self.sender,'b'*32))
        self.assertTrue(self.o.release(self.sender,self.token))
    def test_call_refused_before_protocol_selection(self):
        FakeAt.call_active=True
        self.assertFalse(self.o.acquire(self.sender,self.token))
        self.assertNotIn('AT+ESUO=5',FakeAt.calls)
        self.assertFalse(any(x.startswith('AT+ESUO') for x in FakeAt.calls))
    def test_both_sim_calls_checked_without_uart_switch(self):
        original=FakeAt.command
        checked=[]
        def calls(at,command,timeout=5,sim=2):
            if command=='AT+CLCC':
                checked.append(sim)
                return '+CLCC: 1' if sim==2 else 'OK'
            return original(at,command,timeout,sim)
        with patch.object(FakeAt,'command',calls):
            self.assertFalse(self.o.acquire(self.sender,self.token))
        self.assertEqual(checked,[1,2])
        self.assertFalse(any(x.startswith('AT+ESUO') for x in FakeAt.calls))
    def test_uncertain_acquire_retains_kernel_exclusion(self):
        original=FakeAt.command
        def uncertain(at,command,timeout=5,sim=2):
            if command=='AT+CPIN?':
                at.broken=True
                raise mod.Refused('uncertain')
            return original(at,command,timeout)
        with patch.object(FakeAt,'command',uncertain):
            self.assertFalse(self.o.acquire(self.sender,self.token))
        self.assertTrue(self.o.poisoned)
        self.assertFalse(self.o.at.closed)
        self.assertFalse(self.o.acquire(self.sender,self.token))
    def test_boot_selects_physical_sim_when_euicc_empty(self):
        card=object.__new__(mod.Euicc)
        self.assertEqual(card.boot_select(),4)
        self.assertEqual(FakeAt.calls[-1],'AT+ESUO=4')
    def test_boot_selects_enabled_euicc_after_ready(self):
        original=FakeAt.command
        def ready(at,command,timeout=5):
            return '+CPIN: READY' if command=='AT+CPIN?' else original(at,command,timeout)
        with patch.object(FakeAt,'command',ready):
            self.assertEqual(object.__new__(mod.Euicc).boot_select(),5)
        self.assertNotIn('AT+ESUO=4',FakeAt.calls)
    def test_enabled_card_transition_hands_over_before_release(self):
        self.acquire()
        calls=[]
        class Subscription:
            def select(inner,previous,desired,at):calls.append((previous,desired,at.closed))
        self.o.subscription=Subscription()
        original=FakeAt.command
        def ready(at,command,timeout=5,sim=2):
            return '+CPIN: READY' if command=='AT+CPIN?' else original(at,command,timeout,sim)
        with patch.object(FakeAt,'command',ready):
            self.assertTrue(self.o.release(self.sender,self.token))
        self.assertEqual(calls,[(4,5,False)])
        self.assertEqual(self.o.primary,5);self.assertIsNone(self.o.lease)
    def test_failed_handover_retains_native_exclusion(self):
        self.acquire()
        class Subscription:
            def select(inner,*args):raise mod.Refused('unverified')
        self.o.subscription=Subscription()
        original=FakeAt.command
        def ready(at,command,timeout=5,sim=2):
            return '+CPIN: READY' if command=='AT+CPIN?' else original(at,command,timeout,sim)
        with patch.object(FakeAt,'command',ready):
            self.assertFalse(self.o.release(self.sender,self.token))
        self.assertTrue(self.o.poisoned);self.assertFalse(self.o.at.closed)
        self.assertIsNotNone(self.o.lease);self.assertEqual(self.o.primary,4)
    def test_bound_open_wrong_epoch_or_sender_refused(self):
        self.acquire()
        for sender,token,epoch in [(':1.13',self.token,0),(self.sender,'b'*32,0),(self.sender,self.token,self.o.epoch+1)]:
            with self.assertRaises(mod.Refused):
                self.o.open(sender,token,epoch)
    def test_full_channel_roundtrip_and_native_close(self):
        self.acquire()
        peer,handle=self.o.open(self.sender,self.token,self.o.epoch)
        self.packet(peer,1,1)
        self.assertEqual(self.packet(peer,2,2,mod.AID),b'\1')
        self.assertEqual(self.packet(peer,3,3,b'\x81\xe2\x91\0\0'),b'\x90\0')
        self.packet(peer,4,4,b'\1')
        self.packet(peer,5,5)
        peer.close()
        self.assertTrue(self.terminal(handle)['safe'])
        self.assertIn('AT+CCHC=7',FakeAt.calls)
        self.assertTrue(self.o.release(self.sender,self.token))
    def test_eof_closes_actual_channel_before_release(self):
        self.acquire()
        peer,handle=self.o.open(self.sender,self.token)
        self.packet(peer,1,1);self.packet(peer,2,2,mod.AID)
        self.assertFalse(self.o.release(self.sender,self.token))
        peer.close()
        self.assertTrue(self.terminal(handle)['safe'])
        self.assertIn('AT+CCHC=7',FakeAt.calls)
        self.assertTrue(self.o.release(self.sender,self.token))
    def test_uncertain_open_retains_exclusion(self):
        self.acquire()
        peer,handle=self.o.open(self.sender,self.token)
        self.packet(peer,1,1);FakeAt.fail_open=True
        peer.sendall(struct.pack('!IIII',mod.MAGIC,2,2,len(mod.AID))+mod.AID)
        self.assertEqual(peer.recv(16),b'');peer.close()
        record=self.terminal(handle)
        self.assertEqual(record['state'],3)
        self.assertFalse(record['safe'])
        self.assertFalse(self.o.release(self.sender,self.token))
        self.assertIsNotNone(self.o.at)
    def test_wrong_aid_never_reaches_modem(self):
        self.acquire()
        peer,handle=self.o.open(self.sender,self.token)
        self.packet(peer,1,1)
        peer.sendall(struct.pack('!IIII',mod.MAGIC,2,2,4)+b'bad!')
        self.assertEqual(peer.recv(16),b'');peer.close()
        self.assertFalse(any(x.startswith('AT+CCHO') for x in FakeAt.calls))
        self.assertTrue(self.terminal(handle)['safe'])
    def test_bad_sequence_refused(self):
        self.acquire()
        peer,handle=self.o.open(self.sender,self.token)
        peer.sendall(struct.pack('!IIII',mod.MAGIC,9,1,0))
        self.assertEqual(peer.recv(16),b'');peer.close()
        self.assertTrue(self.terminal(handle)['safe'])
    def test_record_sender_scoped(self):
        self.acquire()
        peer,handle=self.o.open(self.sender,self.token)
        with self.assertRaises(mod.Refused):
            self.o.lookup(':1.13',handle)
        peer.close();self.terminal(handle)

class ResultTests(MipcHarness):
    def test_result_reads_only_first_tlv_of_long_confirmation(self):
        requests = []
        def answer(peer, request):
            requests.append(request)
            # Stock-shaped APN_SET_IA confirmation: result, then a TLV whose
            # declared length runs past the envelope.
            body = struct.pack('<HHI', 0, 4, 0) + struct.pack('<HH', 0x2a2a, 10794) + b'*' * 20
            peer.sendall(self.reply(request, body, message=0x0102))
        at = self.make(answer)
        self.assertEqual(at.result(0x0101, [(0x0101, b'apn\0'), (0x0102, b'\x02')], 2), 0)
        self.assertEqual(struct.unpack_from('<BBH', requests[0], 8), (2, 0, 0x0101))
        self.assertEqual(requests[0][16:], struct.pack('<HH', 0x0101, 4) + b'apn\0' + bytes(0) +
                         struct.pack('<HH', 0x0102, 1) + b'\x02' + bytes(3))
        self.assertFalse(at.broken)
    def test_result_reports_modem_refusal_code(self):
        at = self.make(lambda peer, request: peer.sendall(
            self.reply(request, struct.pack('<HHI', 0, 4, 7), message=0x020e)))
        self.assertEqual(at.result(0x020d, [], 2), 7)
    def test_result_without_result_tlv_is_unverified(self):
        at = self.make(lambda peer, request: peer.sendall(
            self.reply(request, struct.pack('<HHI', 0x100, 4, 0), message=0x0102)))
        with self.assertRaises(mod.Refused):at.result(0x0101, [], 2)
        self.assertTrue(at.broken)

class AttachHookTests(SubscriptionHarness):
    def test_attach_runs_after_selection_before_reprobe(self):
        order = []
        self.s.attach = lambda: order.append(('attach', self.slot, self.s.inhibited))
        self.s.select(4,5,self.mipc)
        self.assertEqual(order, [('attach', 5, True)])
        self.assertEqual(self.api.events,[('inhibit',True),('inhibit',False)])
    def test_attach_failure_does_not_fail_handover(self):
        def fail():raise mod.Refused('no answer')
        self.s.attach = fail
        self.s.select(4,5,self.mipc)
        self.assertFalse(self.s.inhibited);self.assertEqual(self.slot,5)

modemd = importlib.machinery.SourceFileLoader(
    'luma_modemd', str(pathlib.Path(__file__).with_name('luma-modemd'))).load_module()

class IaApnTests(unittest.TestCase):
    IMSI = '310240123456789'
    ICCID = '8901240000000000000'
    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory();self.addCleanup(self.dir.cleanup)
        d = pathlib.Path(self.dir.name)
        (d / 'nm').mkdir()
        self.providers = d / 'serviceproviders.xml'
        self.providers.write_text('''<serviceproviders><country code="us">
          <provider><name>T-Mobile</name><gsm><network-id mcc="310" mnc="260"/><network-id mcc="310" mnc="240"/>
            <apn value="fast.t-mobile.com"><usage type="internet"/></apn></gsm></provider>
          <provider><name>Mint Mobile</name><gsm><network-id mcc="310" mnc="240"/>
            <apn value="wholesale"><usage type="internet"/></apn></gsm></provider>
          <provider><name>Other</name><gsm><network-id mcc="310" mnc="410"/>
            <apn value="other"><usage type="internet"/></apn></gsm></provider>
        </country></serviceproviders>''')
        for name, value in (('NM_CONNECTIONS', str(d / 'nm')), ('PROVIDERS', str(self.providers))):
            p = patch.object(modemd, name, value);p.start();self.addCleanup(p.stop)
        p = patch.object(modemd, 'log', lambda *a: self.logs.append(a));p.start();self.addCleanup(p.stop)
        self.loaded = []
        p = patch.object(modemd, 'nm_load_connection', self.loaded.append);p.start();self.addCleanup(p.stop)
        self.logs = []
    def nm(self, text):
        (pathlib.Path(modemd.NM_CONNECTIONS) / 'c.nmconnection').write_text(text)
    def test_nm_connection_bound_to_this_sim_wins(self):
        self.nm('[gsm]\napn=wholesale\ninitial-eps-bearer-apn=ia.example\nsim-id=%s\n' % self.ICCID)
        self.assertEqual(modemd.ia_apn(self.ICCID, self.IMSI), ('ia.example', 'NetworkManager'))
    def test_nm_connection_for_another_sim_is_ignored(self):
        self.nm('[gsm]\napn=wholesale\nsim-id=8901240000000000001\n')
        self.assertEqual(modemd.ia_apn(self.ICCID, self.IMSI),
                         ('fast.t-mobile.com', 'mobile-broadband-provider-info'))
    def test_provider_named_like_spn_wins_over_first_listed(self):
        self.assertEqual(modemd.provider_apn(self.IMSI, 'Mint Mobile', str(self.providers)), 'wholesale')
        self.assertEqual(modemd.provider_apn(self.IMSI, None, str(self.providers)), 'fast.t-mobile.com')
        self.assertIsNone(modemd.provider_apn('208010000000000', None, str(self.providers)))
    def test_built_in_table_only_without_provider_database(self):
        self.providers.unlink()
        self.assertEqual(modemd.ia_apn(self.ICCID, self.IMSI), ('fast.t-mobile.com', 'built-in'))
        self.assertEqual(modemd.ia_apn(self.ICCID, '208010000000000'), (None, None))
    def test_attach_config_sends_stock_ia_request_then_allows_data_and_restarts_radio(self):
        sent = []
        class Mipc:
            def command(inner, command, timeout=5, sim=2):
                return {'AT+ICCID?': '+ICCID: %sF\r\nOK\r\n' % self.ICCID,
                        'AT+CIMI': '\r\n%s\r\n\r\nOK\r\n' % self.IMSI,
                        'AT+CRSM=176,28486,0,0,17': '+CRSM: 144,0,"014578616D706C65FFFFFFFFFFFFFFFFFF"\r\nOK\r\n'}[command]
            def result(inner, request, tlvs, sim, timeout=5):
                sent.append((request, tlvs, sim));return 0
        self.assertTrue(modemd.attach_config(Mipc(), 2))
        self.assertEqual(sent, [(0x0101, [(0x8106, b'\0'), (0x0101, b'fast.t-mobile.com\0'),
                                          (0x0102, b'\x02'), (0x0103, b'\x01'), (0x0104, b'\x00'),
                                          (0x8105, b'\0')], 2),
                                (0x020d, [], 2),
                                (0x0403, [(0x0100, b'\x00')], 2),
                                (0x0403, [(0x0100, b'\x01')], 2)])
        self.assertFalse(any(self.IMSI in str(a) or self.ICCID in str(a) for a in self.logs))
    def profiles(self):
        return sorted(pathlib.Path(modemd.NM_CONNECTIONS).glob('*.nmconnection'))
    def test_attach_config_creates_the_mobile_data_profile_once(self):
        class Mipc:
            def command(inner, command, timeout=5, sim=2):
                return {'AT+ICCID?': '+ICCID: %sF\r\nOK\r\n' % self.ICCID,
                        'AT+CIMI': '\r\n%s\r\n\r\nOK\r\n' % self.IMSI,
                        'AT+CRSM=176,28486,0,0,17': '+CRSM: 144,0,"014578616D706C65FFFFFFFFFFFFFFFFFF"\r\nOK\r\n'}[command]
            def result(inner, request, tlvs, sim, timeout=5):
                return 0
        self.assertTrue(modemd.attach_config(Mipc(), 2))
        (path,) = self.profiles()
        text = path.read_text()
        for line in ('type=gsm', 'autoconnect=true', 'apn=fast.t-mobile.com', 'initial-eps-bearer-apn=fast.t-mobile.com',
                     'initial-eps-bearer-configure=true', 'sim-id=%s' % self.ICCID, '[ipv6]\naddr-gen-mode=default\nmethod=auto'):
            self.assertIn(line, text)
        self.assertNotIn(self.ICCID, path.name)
        self.assertEqual(oct(path.stat().st_mode & 0o777), '0o600')
        self.assertEqual(self.loaded, [str(path)])
        self.assertFalse(any(self.IMSI in str(a) or self.ICCID in str(a) for a in self.logs))
        self.assertEqual(modemd.ia_apn(self.ICCID, self.IMSI), ('fast.t-mobile.com', 'NetworkManager'))
        self.assertTrue(modemd.attach_config(Mipc(), 2))                     # next boot: profile serves it
        self.assertEqual(len(self.profiles()), 1)
    def test_profiles_serving_the_sim_are_never_replaced(self):
        self.nm('[connection]\nid=Mine\n[gsm]\nsim-id=%s\n' % self.ICCID)  # user's, even without an APN
        self.assertIsNone(modemd.ensure_nm_profile(self.ICCID, self.IMSI, None, 'fast.t-mobile.com'))
        self.nm('[connection]\nid=Any SIM\n[gsm]\napn=wholesale\n')      # bound to no SIM
        self.assertIsNone(modemd.ensure_nm_profile(self.ICCID, self.IMSI, None, 'fast.t-mobile.com'))
        self.nm('[gsm]\napn=other\nsim-operator-id=310410\n')              # another operator
        path = modemd.ensure_nm_profile(self.ICCID, self.IMSI, 'Example', 'wholesale')
        self.assertIn('id=Mobile data (Example)', pathlib.Path(path).read_text())
        self.assertIsNone(modemd.ensure_nm_profile(self.ICCID, self.IMSI, 'Example', 'wholesale'))
        other = modemd.ensure_nm_profile('8901240000000000002', self.IMSI, None, 'wholesale')   # SIM swap
        self.assertNotEqual(other, path)
        self.assertEqual(len(self.profiles()), 3)
        self.assertIsNone(modemd.ensure_nm_profile(self.ICCID, self.IMSI, None, None))
    def test_provider_apns_listed_best_first(self):
        self.assertEqual(modemd.provider_apns(self.IMSI), ['fast.t-mobile.com', 'wholesale'])
        self.assertEqual(modemd.provider_apns(self.IMSI, 'Mint Mobile'), ['wholesale', 'fast.t-mobile.com'])
        self.assertEqual(modemd.provider_apns('208010000000000'), [])
    def test_apn_fallback_steps_only_its_own_profile(self):
        modemd.SIM_IDENTITY[2] = (self.ICCID, self.IMSI, None)
        self.addCleanup(modemd.SIM_IDENTITY.clear)
        fallback = modemd.ApnFallback(2)
        self.assertIsNone(fallback.next())                                   # no auto profile yet
        self.nm('[connection]\nid=Mine\n[gsm]\napn=mine\nsim-operator-id=310410\n')
        path = pathlib.Path(modemd.ensure_nm_profile(self.ICCID, self.IMSI, None, 'fast.t-mobile.com'))
        self.assertEqual(fallback.next(), 'wholesale')
        text = path.read_text()
        self.assertIn('\napn=wholesale\n', text)
        self.assertIn('initial-eps-bearer-apn=wholesale', text)
        self.assertIn('sim-id=%s' % self.ICCID, text)
        self.assertEqual(oct(path.stat().st_mode & 0o777), '0o600')
        self.assertIsNone(fallback.next())                                   # both APNs tried
        self.assertIn('apn=mine', (pathlib.Path(modemd.NM_CONNECTIONS) / 'c.nmconnection').read_text())
        self.assertEqual(self.loaded[-1], str(path))
    def test_apn_fallback_tries_mvno_apn_second_and_stops_after_three(self):
        modemd.SIM_IDENTITY[2] = (self.ICCID, '310260123456789', None)
        self.addCleanup(modemd.SIM_IDENTITY.clear)
        self.providers.write_text('''<serviceproviders><country code="us"><provider><name>T-Mobile</name><gsm>
            <network-id mcc="310" mnc="260"/>
            <apn value="fast.t-mobile.com"><usage type="internet"/></apn><apn value="epc.tmobile.com"><usage type="internet"/></apn>
            <apn value="wap.voicestream.com"><usage type="internet"/></apn><apn value="pwg"><usage type="internet"/></apn>
            </gsm></provider></country></serviceproviders>''')
        modemd.ensure_nm_profile(self.ICCID, '310260123456789', None, 'fast.t-mobile.com')
        fallback = modemd.ApnFallback(2)
        self.assertEqual([fallback.next(), fallback.next(), fallback.next()], ['wholesale', 'epc.tmobile.com', None])
    def test_dead_path_tries_the_next_apn_after_a_cycle(self):
        now, dead, apns = [100.0], [], iter(['wholesale', None])
        data = modemd.DataCalls(2)
        interface, cid, settings = data.connect(FakeReader(), 'fast.t-mobile.com', 'ipv4v6')
        service = modemd.DataService(None, data, clock=lambda: now[0], on_dead=dead.append,
                                     works=lambda i, s: False, next_apn=lambda: next(apns))
        service.check_data_path(cid, interface, settings)                     # dead: cycle the modem
        now[0] += 60
        service.check_data_path(cid, interface, settings)                     # still dead: next APN
        now[0] += 60
        service.check_data_path(cid, interface, settings)                     # no APN left: stop
        self.assertEqual(len(dead), 2)
        self.assertIn('trying APN wholesale', dead[1])
    def test_attach_config_without_identity_sends_nothing(self):
        class Mipc:
            def command(inner, command, timeout=5, sim=2):return 'OK'
            def result(inner, *a, **k):raise AssertionError('no request without identity')
        self.assertFalse(modemd.attach_config(Mipc(), 2))


def ind(msg_id, tlvs, ps=2, txid=0):
    body = b''.join(struct.pack('<HH', t, len(v)) + v + b'\0' * (-(4+len(v))&7) for t, v in tlvs)
    return struct.pack('<IIBBHHH', 0x24541984, 0, ps, 0, msg_id, txid, len(body)) + body


def start_reader(test, reader, sock):
    """Run reader on sock until the test ends (its fd is closed then, which ends
    the thread; a reader left running would consume other tests' time mocks)."""
    def run():
        try:
            reader.run()
        except (OSError, ValueError):
            pass
    with patch.object(modemd.os, 'open', return_value=sock.detach()):
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        end = time.monotonic() + 2
        while reader.fd < 0 and time.monotonic() < end:
            time.sleep(0.01)
    def stop():
        os.close(reader.fd)
        thread.join(3)
    test.addCleanup(stop)


class BootConfigTests(unittest.TestCase):
    def test_boot_registers_incoming_call_approval_on_both_protocols(self):
        ats = [(ps, dict(t)[0x8100]) for _, m, ps, t in modemd.MIPC_BOOT if m == 0x0003]
        for ps in (1, 2):
            self.assertIn((ps, b'AT+EIAREG=1'), ats)
            self.assertIn((ps, b'AT+ECCAUSE=1'), ats)
            self.assertIn((ps, b'AT+EINFO=1074'), ats)


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(modemd, 'log', lambda *a: None);p.start();self.addCleanup(p.stop)
        p = patch.dict(modemd.LAST_IA, {2: 'wholesale'}, clear=True);p.start();self.addCleanup(p.stop)
    def test_registration_enables_ims_with_stock_switches_once(self):
        r = modemd.Registration(2)
        out = r.indication(0x4403, {0x0100: b'\x01'}, 0)
        self.assertEqual([m for _, m, _ in out], [0x0a1c, 0x0a00])
        self.assertEqual(out[0][2], [(0x0100, bytes(64))])
        self.assertEqual(out[1][2], [(0x0100, bytes(4)), (0x0102, bytes([1, 1, 0, 0, 1, 1]))])
        self.assertEqual(r.indication(0x4403, {0x0100: b'\x01'}, 1), [])
    def test_ims_disabled_sends_no_ims_requests(self):
        r = modemd.Registration(2, ims=False)
        self.assertEqual(r.indication(0x4403, {0x0100: b'\x01'}, 0), [])
        self.assertEqual(r.indication(0x4a03, {0x0107: b'\x02'}, 0), [])
    def test_ims_pdn_request_answered_with_ims_data_call(self):
        r = modemd.Registration(2)
        (name, msg_id, tlvs), = r.indication(0x4a03, {0x0107: b'\x02'}, 0)
        self.assertEqual(msg_id, 0x0201)
        profile = dict(tlvs)[0x010d]
        self.assertEqual((len(profile), profile[1], profile[4:8], profile[414]), (672, 3, b'ims\0', 0xff))
        self.assertEqual(dict(tlvs)[0x010a], b'\x03')
        self.assertEqual(r.indication(0x4a03, {0x0107: b'\x00'}, 0), [])
    def test_radio_restarts_only_after_sustained_loss_and_not_too_often(self):
        r = modemd.Registration(2)
        r.indication(0x4403, {0x0100: b'\x01'}, 0)
        r.indication(0x4403, {0x0100: b'\x00'}, 100)
        self.assertEqual(r.tick(159), [])
        out = r.tick(160)
        self.assertEqual([m for _, m, _ in out], [0x0101, 0x020d, 0x0403, 0x0403])
        self.assertEqual(out[0][2][1], (0x0101, b'wholesale\0'))
        self.assertEqual([t for _, _, t in out[2:]], [[(0x0100, b'\x00')], [(0x0100, b'\x01')]])
        self.assertEqual(r.tick(279), [])
        self.assertEqual(len(r.tick(280)), 4)
        r.indication(0x4403, {0x0100: b'\x01'}, 281)
        self.assertEqual(r.tick(1000), [])
    def test_incoming_call_is_approved_with_its_id_and_sequence(self):
        r = modemd.Registration(2)
        rsp = r.command(0x8901, {0x0100: struct.pack('<I', 3), 0x8101: b'+15550100\0',
                                 0x0102: struct.pack('<I', 145), 0x0103: struct.pack('<I', 7),
                                 0x0104: struct.pack('<I', 20), 0x0105: b'\x00'})
        # firmware: AT+EAIC=<0 approve>,<0x0102 call id>,<0x0103 seq>
        self.assertEqual(rsp, [(0x0100, b'\x01'), (0x0101, bytes(4)),
                               (0x0102, struct.pack('<I', 3)), (0x0103, struct.pack('<I', 7))])
        self.assertIsNone(r.command(0x8903, {}))
    def test_approval_and_unsolicited_lines_never_log_numbers(self):
        logs = []
        with patch.object(modemd, 'log', lambda *a: logs.append(a)):
            r = modemd.Registration(2)
            r.command(0x8901, {0x0100: struct.pack('<I', 1), 0x0101: b'+19135550100\0',
                               0x0103: struct.pack('<I', 5)})
            r.indication(0x4001, {0x0100: b'+CLIP: "+19135550100",145\0'}, 0)
        r.indication(0x4001, {0x0100: b'+CREG: 1,"0300","1012AE12E",11\0'}, 0)
        self.assertFalse(any('CREG' in repr(a) for a in logs))   # location lines are not logged
        self.assertTrue(any('+CLIP' in repr(a) for a in logs))
        self.assertNotIn('9135550100', repr(logs))
        self.assertNotIn(b'9135550100'.hex(), repr(logs))
    def test_no_restart_while_the_euicc_is_leased(self):
        r = modemd.Registration(2)
        r.indication(0x4403, {0x0100: b'\x00'}, 0)
        r.busy = lambda: True
        self.assertEqual(r.tick(100), [])
        r.busy = lambda: False
        self.assertEqual(r.tick(159), [])
        self.assertEqual(len(r.tick(160)), 4)
    def test_no_restart_while_the_radio_is_off_on_request(self):
        r = modemd.Registration(2)
        r.indication(0x4403, {0x0100: b'\x01'}, 0)
        r.indication(0x4404, {0x0100: b'\x00'}, 1)          # airplane mode
        r.indication(0x4403, {0x0100: b'\x00'}, 2)
        self.assertEqual(r.tick(500), [])
        r.indication(0x4404, {0x0100: b'\x01'}, 600)        # radio back on, not yet registered
        r.indication(0x4403, {0x0100: b'\x00'}, 601)
        self.assertEqual(r.tick(659), [])                     # 60 s counted from radio on
        self.assertEqual(len(r.tick(660)), 4)
    def test_radio_off_is_reported_once(self):
        r, offs = modemd.Registration(2), []
        r.on_radio_off.append(lambda: offs.append(1))
        r.indication(0x4404, {0x0100: b'\x01'}, 0)
        r.indication(0x4404, {0x0100: b'\x00'}, 1)
        r.indication(0x4404, {0x0100: b'\x00'}, 2)
        self.assertEqual(offs, [1])
    def test_no_restart_without_a_known_ia_apn(self):
        modemd.LAST_IA.clear()
        r = modemd.Registration(2)
        r.indication(0x4403, {0x0100: b'\x00'}, 0)
        self.assertEqual(r.tick(1000), [])


class FakeReader:
    def __init__(self, act=(0, {0x0108: b'\x01'})):
        self.sent, self.act = [], act
    def at(self, command, ps):
        if command == 'AT+CGDCONT?':
            return self.cgdcont
        self.sent.append(('AT', command, ps))
        return 'OK'
    cgdcont = '+CGDCONT: 2,"IPV4V6","wholesale"\r\n+CGDCONT: 1,"IPV6","ims"\r\nOK'
    def request_tlvs(self, name, msg_id, ps, tlvs, timeout=5.0):
        self.sent.append(('MIPC', msg_id, ps, tlvs))
        return self.act
    def request(self, name, msg_id, ps, tlvs, timeout=5.0):
        self.sent.append(('MIPC', msg_id, ps, tlvs))
        return 0


class TlvTests(unittest.TestCase):
    def test_zero_padding_before_the_result_is_skipped(self):
        # DATA_ACT_CALL confirmation: 0x0126, eight zero bytes, then the result
        frame = ind(0x0202, [(0x0126, b'\x01')]) [:16]
        body = (struct.pack('<HH', 0x0126, 1) + b'\x01' + bytes(3) + bytes(8) +
                struct.pack('<HH', 0, 4) + bytes(4) + struct.pack('<HH', 0x0108, 1) + b'\x01' + bytes(3))
        tlvs = modemd.mipc_tlvs(frame + body)
        self.assertEqual((tlvs[0], tlvs[0x0108]), (bytes(4), b'\x01'))


class DataCallTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(modemd, 'log', lambda *a: None);p.start();self.addCleanup(p.stop)
    def test_connect_sets_up_pdn_then_binds_interface(self):
        ind = FakeReader()
        self.assertEqual(modemd.DataCalls(2).connect(ind, 'wholesale', 'ipv4v6'), ('ccmni2', 2, {'dns6': []}))
        self.assertEqual(ind.sent[0:2], [('AT', 'AT+CGDCONT=2,"IPV4V6","wholesale"', 2), ('AT', 'AT+CGACT=1,2', 2)])
        # the data switch follows the activation
        self.assertEqual(ind.sent[2], ('MIPC', 0x0211, 2, [(0x0100, b'\x01'), (0x0101, b'\x00'), (0x0103, b'\x00')]))
        _, msg_id, ps, tlvs = ind.sent[3]
        fields = dict(tlvs)
        self.assertEqual((msg_id, ps), (0x0201, 2))
        self.assertEqual((fields[0x0101], fields[0x0102], fields[0x0103], fields[0x010f]),
                         (b'wholesale\0', struct.pack('<I', 1), b'\x03', struct.pack('<I', 1)))
    def test_data_call_uses_the_context_pdp_type(self):
        ind = FakeReader()
        ind.cgdcont = '+CGDCONT: 2,"IPV6","wholesale"\r\nOK'     # redefined by ModemManager
        modemd.DataCalls(2).connect(ind, 'wholesale', 'ipv4v6')
        fields = dict(ind.sent[-1][3])
        self.assertEqual((fields[0x0103], fields[0x0104]), (b'\x02', b'\x02'))
    def test_connect_while_up_sends_nothing(self):
        ind = FakeReader()
        data = modemd.DataCalls(2)
        data.connect(ind, 'wholesale', 'ipv4v6')
        del ind.sent[:]
        self.assertEqual(data.connect(ind, 'wholesale', 'ipv4v6'), ('ccmni2', 2, {'dns6': []}))
        self.assertEqual(ind.sent, [])
        with self.assertRaises(OSError):
            data.connect(ind, 'fast.t-mobile.com', 'ipv4v6')
    def test_ipv6_only_and_refused_call(self):
        ind = FakeReader()
        modemd.DataCalls(2).connect(ind, 'fast.t-mobile.com', 'ipv6')
        self.assertEqual(ind.sent[0][1], 'AT+CGDCONT=2,"IPV6","fast.t-mobile.com"')
        with self.assertRaises(OSError):
            modemd.DataCalls(2).connect(FakeReader(act=(0x1416f1, {})), 'wholesale', 'ipv4v6')
    def test_apn_is_validated_before_any_command(self):
        ind = FakeReader()
        for apn in ('', 'a"b', 'x' * 100, 'wholesale\r\nAT+CFUN=0'):
            with self.assertRaises(ValueError):
                modemd.DataCalls(2).connect(ind, apn, 'ipv4v6')
        self.assertEqual(ind.sent, [])
    def test_reconnect_reactivates_and_binds_again_after_radio_off(self):
        ind = FakeReader()
        data = modemd.DataCalls(2)
        data.connect(ind, 'wholesale', 'ipv4v6')
        del ind.sent[:]
        data.disconnect(ind, 2)
        self.assertEqual([x[1] for x in ind.sent], ['AT+CGACT=0,2'])
        del ind.sent[:]
        self.assertEqual(data.connect(ind, 'wholesale', 'ipv4v6'), ('ccmni2', 2, {'dns6': []}))
        self.assertEqual([x[1] for x in ind.sent], ['AT+CGACT=1,2', 0x0211])   # no second data call
        data.radio_off()                # the network released the PDN and the binding
        del ind.sent[:]
        data.connect(ind, 'wholesale', 'ipv4v6')
        self.assertEqual([x[1] for x in ind.sent],
                         ['AT+CGDCONT=2,"IPV4V6","wholesale"', 'AT+CGACT=1,2', 0x0211, 0x0201])
        data.disconnect(ind, 2)
        del ind.sent[:]
        data.connect(ind, 'fast.t-mobile.com', 'ipv6')                         # new APN, same binding
        self.assertEqual([x[1] for x in ind.sent],
                         ['AT+CGDCONT=2,"IPV6","fast.t-mobile.com"', 'AT+CGACT=1,2', 0x0211])
    def test_initial_attach_settings_from_modemmanager(self):
        modemd.LAST_IA.clear()
        modemd.LAST_IA[2] = 'wholesale'
        self.addCleanup(modemd.LAST_IA.clear)
        path = os.path.join(tempfile.mkdtemp(), 'state', 'initial-eps.json')
        data, ind = modemd.DataCalls(2, settings_path=path), FakeReader()
        self.assertEqual(data.initial_eps(), {'apn': ['s', 'wholesale']})
        nm = {'apn': ['s', 'wholesale'], 'ip-type': ['u', 8], 'allowed-auth': ['u', 0x3f]}
        data.set_initial_eps(ind, nm)                          # unchanged APN: nothing sent
        self.assertEqual(ind.sent, [])
        self.assertEqual(modemd.DataCalls(2, settings_path=path).initial_eps(), nm)   # kept as given
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), '0o600')
        data.set_initial_eps(ind, {'apn': ['s', 'fast.t-mobile.com']})
        self.assertEqual(ind.sent, [('MIPC', 0x0101, 2, modemd.ia_tlvs('fast.t-mobile.com'))])
        self.assertEqual(modemd.LAST_IA[2], 'fast.t-mobile.com')
        modemd.LAST_IA[2] = 'wholesale'                        # IA changed elsewhere (new profile)
        self.assertEqual(data.initial_eps(), {'apn': ['s', 'wholesale']})
        for bad in ({}, {'apn': ['s', 'a"b']}, {'apn': ['u', 1]}, {'apn': ['s', 'ok'], 'x': ['a{sv}', {}]}):
            with self.assertRaises(ValueError):
                data.set_initial_eps(ind, bad)
        class Refused(FakeReader):
            def request(self, name, msg_id, ps, tlvs, timeout=5.0):
                return 0x1416f1
        with self.assertRaises(OSError):
            data.set_initial_eps(Refused(), {'apn': ['s', 'other']})
    def test_name_servers_from_the_confirmation(self):
        dns1, dns2 = bytes.fromhex('fd00976a' + '00' * 11 + '09'), bytes.fromhex('fd00976a' + '00' * 11 + '10')
        ind = FakeReader(act=(0, {0x0108: b'\x01', 0x0113: dns1, 0x0114: dns2, 0x7ffd: dns1,
                                  0x0109: bytes(16)}))
        self.assertEqual(modemd.DataCalls(2).connect(ind, 'wholesale', 'ipv4v6')[2],
                         {'dns6': ['fd00:976a::9', 'fd00:976a::10']})
        ind = FakeReader(act=(0, {0x0108: b'\x01', 0x0113: bytes(16), 0x0114: b'\x01'}))
        self.assertEqual(modemd.DataCalls(2).connect(ind, 'wholesale', 'ipv4v6')[2], {'dns6': []})
    def test_quick_reconnect_waits_after_deactivation(self):
        now = [100.0]
        data = modemd.DataCalls(2, clock=lambda: now[0])
        ind = FakeReader()
        self.assertEqual(data.reactivate_wait(), 0)                   # never deactivated
        data.connect(ind, 'wholesale', 'ipv4v6')
        data.disconnect(ind, 2)
        now[0] += 0.2                                                 # NetworkManager reconnecting
        self.assertAlmostEqual(data.reactivate_wait(), 4.8)
        now[0] += 30
        self.assertEqual(data.reactivate_wait(), 0)
    def test_data_call_waits_for_ims_registration(self):
        now, ready, waits = [0.0], [False], []
        def sleep(t):
            waits.append(t); now[0] += t
            if now[0] >= 3: ready[0] = True                         # IMS registers after 3 s
        service = modemd.DataService(None, modemd.DataCalls(2, clock=lambda: now[0]),
                                     ready=lambda: ready[0], sleep=sleep, clock=lambda: now[0])
        service.wait_until_ready()
        self.assertEqual((sum(waits[:-1]), waits[-1]), (3.0, 0.0))
        waits[:] = []                                                 # IMS never registers: give up
        never = modemd.DataService(None, modemd.DataCalls(2, clock=lambda: now[0]), ready=lambda: False,
                                   sleep=lambda t: (waits.append(t), now.__setitem__(0, now[0] + t)),
                                   clock=lambda: now[0])
        never.wait_until_ready()
        self.assertEqual(sum(waits), modemd.DATA_WAIT_FOR_IMS)
    def test_ims_registration_tracked_and_cleared_on_radio_off(self):
        r = modemd.Registration(2)
        r.indication(0x4a05, {0x0100: b'\x01'}, 0)
        self.assertTrue(r.ims_registered)
        r.indication(0x4404, {0x0100: b'\x01'}, 1)
        r.indication(0x4404, {0x0100: b'\x00'}, 2)
        self.assertFalse(r.ims_registered)
    def test_data_path_probe(self):
        now, slept = [0.0], []
        sleep = lambda t: (slept.append(t), now.__setitem__(0, now[0] + t))
        clock = lambda: now[0]
        works = lambda probe, has=lambda i: True: modemd.data_path_works('ccmni2', ['fd00::9'], probe=probe,
                                                                        has_address=has, sleep=sleep, clock=clock)
        self.assertIsNone(modemd.data_path_works('ccmni2', [], probe=lambda i, s: False))
        self.assertTrue(works(lambda i, s: True))
        answers = iter([False, True])
        self.assertTrue(works(lambda i, s: next(answers)))                    # answers in round 2
        self.assertFalse(works(lambda i, s: False))                           # three silent rounds
        self.assertFalse(works(lambda i, s: True, has=lambda i: False))       # never got an address

    def test_global_ipv6_address_detected_on_the_interface(self):
        path = os.path.join(tempfile.mkdtemp(), 'if_inet6')
        with open(path, 'w') as f:
            f.write('fe800000000000000000000000000001 07 40 20 80   ccmni2\n'
                    '20010db8000000000000000000000001 08 40 00 00    wlan0\n')
        self.assertFalse(modemd.interface_has_global_ipv6('ccmni2', path))     # link-local only
        with open(path, 'a') as f:
            f.write('2607fb90000000000000000000000001 07 40 00 00   ccmni2\n')
        self.assertTrue(modemd.interface_has_global_ipv6('ccmni2', path))

    def test_logged_boot_refusal_window_recovers_once(self):
        # 2026-10-23 boot: IMS registered at 43.6 s, CGACT refused (5847) until 78.7 s,
        # the bound PDN carried no traffic: exactly one modem cycle is asked for.
        class Refusing(FakeReader):
            n = 0
            def at(self, command, ps):
                if command.startswith('AT+CGACT=1') and self.n < 7:
                    self.n += 1
                    raise OSError('AT+CGACT failed (result 0: +CME ERROR: 5847)')
                return super().at(command, ps)
        now, dead = [100.0], []
        data = modemd.DataCalls(2, sleep=lambda t: None)
        service = modemd.DataService(None, data, clock=lambda: now[0], on_dead=dead.append,
                                     works=lambda interface, servers: False)
        interface, cid, settings = data.connect(Refusing(), 'wholesale', 'ipv4v6')
        self.assertEqual(data.refusals, 7)
        service.check_data_path(cid, interface, {'dns6': ['fd00:976a::9']})
        self.assertEqual(len(dead), 1)
        self.assertIn('ccmni2 carries no traffic (7 refusals', dead[0])
        now[0] += 60                                                          # still dead after the cycle
        service.check_data_path(cid, interface, {'dns6': ['fd00:976a::9']})
        self.assertEqual(len(dead), 1)                                        # not again within 10 minutes
        now[0] += modemd.DATA_REBIND_EVERY
        service.check_data_path(cid, interface, {'dns6': ['fd00:976a::9']})
        self.assertEqual(len(dead), 2)

    def test_working_or_released_path_is_left_alone(self):
        dead = []
        data = modemd.DataCalls(2)
        interface, cid, settings = data.connect(FakeReader(), 'wholesale', 'ipv4v6')
        modemd.DataService(None, data, on_dead=dead.append, works=lambda i, s: True).check_data_path(cid, interface, settings)
        modemd.DataService(None, data, on_dead=dead.append, works=lambda i, s: None).check_data_path(cid, interface, settings)
        data.disconnect(FakeReader(), cid)                                    # released meanwhile
        modemd.DataService(None, data, on_dead=dead.append, works=lambda i, s: False).check_data_path(cid, interface, settings)
        self.assertEqual(dead, [])

    def test_cgact_retried_while_ims_sets_up(self):
        class Busy(FakeReader):
            n = 0
            def at(self, command, ps):
                if command.startswith('AT+CGACT=1') and self.n < 2:
                    self.n += 1
                    raise OSError('AT+CGACT failed (result 0: +CME ERROR: 5847)')
                return super().at(command, ps)
        waits = []
        self.assertEqual(modemd.DataCalls(2, sleep=waits.append).connect(Busy(), 'wholesale', 'ipv4v6'), ('ccmni2', 2, {'dns6': []}))
        self.assertEqual(waits, [5, 5])
        class Broken(FakeReader):
            def at(self, command, ps):
                if command.startswith('AT+CGACT'):
                    raise OSError('AT+CGACT failed (result 0: ERROR)')
                return 'OK'
        with self.assertRaises(OSError):
            modemd.DataCalls(2, sleep=waits.append).connect(Broken(), 'wholesale', 'ipv4v6')


class IndicationsTests(unittest.TestCase):
    def test_submitted_at_command_runs_on_the_reader_and_returns_text(self):
        a, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(peer.close)
        p = patch.object(modemd, 'log', lambda *x: None);p.start();self.addCleanup(p.stop)
        reader = modemd.Indications([modemd.Registration(2)], port='unused')
        def modem():
            while True:
                try:
                    d = peer.recv(4096)
                except OSError:
                    return
                if not d:
                    return
                _, _, ps, _, msg_id, txid, _ = struct.unpack_from('<IIBBHHH', d)
                if msg_id == 0x0003:
                    peer.send(ind(0x0004, [(0, bytes(4)), (0x0100, b'\r\n+CGACT: 2,1\r\n\r\nOK\r\n\0')], ps, txid))
                else:
                    peer.send(ind(msg_id + 1, [(0, bytes(4))], ps, txid))
        threading.Thread(target=modem, daemon=True).start()
        start_reader(self, reader, a)
        self.assertIn('+CGACT: 2,1', reader.submit(lambda r: r.at('AT+CGACT?', 2), timeout=5))
        with self.assertRaises(ZeroDivisionError):
            reader.submit(lambda r: 1 / 0, timeout=5)

    def test_modem_command_gets_a_response_with_its_transaction_id(self):
        a, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(peer.close)
        logs = []
        p = patch.object(modemd, 'log', lambda *x: logs.append(x));p.start();self.addCleanup(p.stop)
        reader = modemd.Indications([modemd.Registration(2)], port='unused')
        got = []
        def modem():
            sent_cmd = False
            while True:
                try:
                    d = peer.recv(4096)
                except OSError:
                    return
                if not d:
                    return
                _, _, ps, _, msg_id, txid, _ = struct.unpack_from('<IIBBHHH', d)
                if msg_id == 0x8902:
                    got.append((ps, txid, d[16:]));return
                peer.send(ind(msg_id + 1, [(0, bytes(4))], ps, txid))
                if msg_id == 0x0307 and not sent_cmd:
                    sent_cmd = True
                    peer.send(ind(0x8901, [(0x0100, struct.pack('<I', 2)), (0x0103, struct.pack('<I', 9)),
                                           (0x0104, struct.pack('<I', 20))], 0x22, 0x1234))
        worker = threading.Thread(target=modem, daemon=True);worker.start()
        start_reader(self, reader, a)
        worker.join(5)
        self.assertFalse(worker.is_alive())
        (ps, txid, body), = got
        self.assertEqual((ps, txid), (0x22, 0x1234))   # answered on the command's own protocol byte
        self.assertEqual(modemd.mipc_tlvs(b'\0' * 16 + body),
                         {0x0100: b'\x01', 0x0101: bytes(4), 0x0102: struct.pack('<I', 2),
                          0x0103: struct.pack('<I', 9)})
    def test_registers_watched_indications_then_answers_ims_pdn_request(self):
        a, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(peer.close)
        logs = []
        p = patch.object(modemd, 'log', lambda *x: logs.append(x));p.start();self.addCleanup(p.stop)
        reader = modemd.Indications([modemd.Registration(2)], port='unused')
        seen = []
        def modem():
            while True:
                try:
                    d = peer.recv(4096)
                except OSError:
                    return
                if not d:
                    return
                _, _, ps, _, msg_id, txid, _ = struct.unpack_from('<IIBBHHH', d)
                seen.append((ps, msg_id, bytes(d[16:24])))
                if msg_id == 0x0307 and d[20:22] == struct.pack('<H', 0x4a05):
                    peer.send(ind(0x4a03, [(0x0107, b'\x02')]))   # arrives before the confirmation
                peer.send(ind(msg_id + 1, [(0x0126, b'\x01'), (0, bytes(4))] if msg_id == 0x0201
                              else [(0, bytes(4))], ps, txid))
                if msg_id == 0x0201:
                    return
        worker = threading.Thread(target=modem, daemon=True);worker.start()
        start_reader(self, reader, a)
        worker.join(5)
        self.assertFalse(worker.is_alive())
        registered = [struct.unpack_from('<H', v, 4)[0] for ps, m, v in seen if m == 0x0307]
        self.assertEqual((registered, {ps for ps, m, _ in seen if m == 0x0307}), (list(modemd.WATCHED), {2}))
        self.assertEqual([(ps, struct.unpack_from('<H', v, 4)[0]) for ps, m, v in seen if m == 0x030b],
                         [(0xff, 0x8901)])
        self.assertEqual(seen[-1][1], 0x0201)
        end = time.monotonic() + 2       # the reader logs the confirmation after the modem answered
        while ('MIPC ps2', 'DATA_ACT_CALL ims', '->', 0) not in logs and time.monotonic() < end:
            time.sleep(0.01)
        self.assertIn(('MIPC ps2', 'DATA_ACT_CALL ims', '->', 0), logs)

if __name__=='__main__':
    unittest.main()
