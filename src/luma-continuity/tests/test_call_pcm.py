"""Actual GIO FD lease read and written by the clocked WebRTC pipeline."""
import math
import struct
import threading
import time
import unittest
from gi.repository import GLib
import test_call_audio_lease as lease_fixture
from luma_continuity.call_webrtc import WebRTCAudio


class PCMIntegrationTests(unittest.TestCase):
    def test_native_fd_bidirectional_media_and_permission_loss(self):
        fixture=lease_fixture.AudioLeaseTests();fixture.setUp()
        engines=[];timer=None;received=[];uplink=[];failures=[];stop=threading.Event()
        frame=struct.pack('<320h',*(int(4000*math.sin(2*math.pi*440*n/16000)) for n in range(320)))
        def native_service():
            # The native service's own 20 ms clock, independent of the GLib context.
            next_due=time.monotonic()
            while not stop.is_set():
                try:fixture.native_down.send(frame)
                except OSError:return
                next_due+=.02;time.sleep(max(0,next_due-time.monotonic()))
        service=threading.Thread(target=native_service,daemon=True)
        try:
            fixture.start();fixture.wait(lambda:fixture.ready)
            fixture.native_up.setblocking(False)
            for index in range(2):
                engines.append(WebRTCAudio(authorized=lambda:fixture.allowed,
                    signal=lambda value,index=index:engines[1-index].receive(value),
                    pcm=received.append,failed=lambda index=index:failures.append(index),dispatch=GLib.idle_add))
            engines[1].start(offer=False)
            engines[0].start(offer=True,endpoint=('native',fixture.lease.uplink,fixture.lease.downlink))
            with self.assertRaises(RuntimeError):engines[0].push(frame)  # Python cannot inject into a device endpoint
            service.start()
            def tick():
                engines[1].push(frame)
                while True:
                    try:uplink.append(fixture.native_up.recv(641))
                    except BlockingIOError:break
                return True
            timer=GLib.timeout_add(20,tick)
            context=GLib.MainContext.default();deadline=time.monotonic()+15
            while (sum(any(v) for v in received)<25 or sum(any(v) for v in uplink)<25) and not failures:
                if time.monotonic()>deadline:raise AssertionError(f'leased encrypted PCM timeout rx={len(received)} up={len(uplink)}')
                while context.pending():context.iteration(False)
                time.sleep(.003)
            self.assertEqual(failures,[])
            self.assertTrue(all(len(value)==640 for value in uplink+received))
            fixture.allowed=False
            deadline=time.monotonic()+1
            while engines[0].pipeline is not None:
                self.assertLess(time.monotonic(),deadline,'revocation must stop native media within the watchdog bound')
                while context.pending():context.iteration(False)
                time.sleep(.005)
            self.assertIn(0,failures)
        finally:
            stop.set()
            if timer is not None:
                source=GLib.MainContext.default().find_source_by_id(timer)
                if source:source.destroy()
            for engine in engines:engine.close()
            service.join(1)
            fixture.tearDown()

    def test_stalled_native_reader_does_not_end_authorized_session(self):
        import socket
        up,native_up=socket.socketpair(type=socket.SOCK_DGRAM)
        down,native_down=socket.socketpair(type=socket.SOCK_DGRAM)
        for s in (up,down,native_up,native_down):s.setblocking(False)
        up.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,4096)
        engines=[];failures=[];context=GLib.MainContext.default()
        def pump(seconds):
            end=time.monotonic()+seconds
            while time.monotonic()<end:
                while context.pending():context.iteration(False)
                time.sleep(.002)
        try:
            for index in range(2):
                engines.append(WebRTCAudio(authorized=lambda:True,signal=lambda value,index=index:engines[1-index].receive(value),
                    pcm=lambda _f:None,failed=lambda:failures.append('closed'),dispatch=GLib.idle_add))
            engines[1].start(offer=False);engines[0].start(offer=True,endpoint=('native',up,down))
            deadline=time.monotonic()+30
            # The native service sends call audio from lease start, before ICE completes.
            while engines[0].rtc is not None and engines[0].rtc.get_property('connection-state').value_nick!='connected':
                self.assertLess(time.monotonic(),deadline);native_down.send(bytes(640));pump(.02)
            self.assertEqual(failures,[])
            for _ in range(150):  # three seconds of far-end audio with nobody reading the uplink
                engines[1].push(bytes(640));native_down.send(bytes(640));pump(.02)
            drained=0
            while True:
                try:native_up.recv(641);drained+=1
                except BlockingIOError:break
            for _ in range(50):engines[1].push(bytes(640));native_down.send(bytes(640));pump(.02)
            fresh=0
            while True:
                try:self.assertEqual(len(native_up.recv(641)),640);fresh+=1
                except BlockingIOError:break
            self.assertEqual(failures,[]);self.assertFalse(engines[0].closed)
            self.assertGreater(drained,0);self.assertGreater(fresh,0)
        finally:
            for engine in engines:engine.close()
            for s in (up,down,native_up,native_down):s.close()
