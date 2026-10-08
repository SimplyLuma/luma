"""Real synthetic WebRTC survives bounded live-audio scheduling bursts."""
import time
import unittest
from gi.repository import GLib
from luma_continuity.call_webrtc import WebRTCAudio

class BackpressureTests(unittest.TestCase):
    def test_pcm_burst_drops_backlog_without_ending_authorized_session(self):
        engines=[];failures=[];received=[0,0]
        def frame(index,_data):received[index]+=1
        for i in range(2):
            engines.append(WebRTCAudio(authorized=lambda:True,dispatch=GLib.idle_add,
                signal=lambda value,i=i:engines[1-i].receive(value),
                pcm=lambda value,i=i:frame(i,value),failed=lambda:failures.append('closed')))
        context=GLib.MainContext.default()
        def pump(seconds):
            deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                while context.pending():context.iteration(False)
                time.sleep(.002)
        try:
            engines[1].start(offer=False);engines[0].start(offer=True)
            deadline=time.monotonic()+15
            while not all(e.rtc.get_property('connection-state').value_nick=='connected' for e in engines):
                self.assertFalse(failures);self.assertLess(time.monotonic(),deadline);pump(.02)
            for _ in range(64):
                for engine in engines:engine.push(bytes(640))
            pump(.2)
            for _ in range(30):
                for engine in engines:engine.push(bytes(640))
                pump(.02)
            self.assertFalse(failures)
            self.assertTrue(all(n>0 for n in received))
            self.assertTrue(all(not e.closed for e in engines))
        finally:
            for engine in engines:engine.close()

class TimelineTests(unittest.TestCase):
    def test_consent_wait_jitter_stall_and_burst_keep_live_timestamps_bounded(self):
        import threading
        from types import SimpleNamespace
        from gi.repository import Gst
        Gst.init(None)
        now=[10_000_000_000];written=[];allowed=[True]
        engine=WebRTCAudio.__new__(WebRTCAudio)
        engine.Gst=Gst;engine.lock=threading.RLock();engine.closed=False;engine.started=True
        engine.authorized=lambda:allowed[0];engine.samples=0;engine.next_pts=None;engine.endpoint=None
        engine.rtc=SimpleNamespace(get_property=lambda _:SimpleNamespace(value_nick='connected'))
        engine.pipeline=SimpleNamespace(get_clock=lambda:SimpleNamespace(get_time=lambda:now[0]),get_base_time=lambda:0)
        engine.source=SimpleNamespace(emit=lambda _,b:(written.append((b.pts,b.duration)),Gst.FlowReturn.OK)[1])
        engine.push(bytes(640));now[0]+=5_000_000_000;engine.push(bytes(640))
        self.assertEqual(written[-1][0],now[0])
        anchor=written[-1][0]
        for index,delay in enumerate((22,18,24,16),1):
            now[0]+=delay*1_000_000;engine.push(bytes(640))
            self.assertEqual(written[-1],(anchor+index*20_000_000,20_000_000))
        now[0]+=300_000_000;engine.push(bytes(640))
        self.assertEqual(written[-1][0],now[0])
        before=len(written)
        for _ in range(100):engine.push(bytes(640))
        self.assertLessEqual(len(written)-before,11)
        self.assertLessEqual(written[-1][0],now[0]+200_000_000)
        now[0]+=500_000_000;engine.push(bytes(640))
        self.assertEqual(written[-1][0],now[0])
        allowed[0]=False
        with self.assertRaises(PermissionError):engine.push(bytes(640))
