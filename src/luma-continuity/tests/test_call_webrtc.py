"""Real loopback DTLS-SRTP/Opus with synthetic PCM; no device or microphone."""
import math
import struct
import time
import unittest
import gi
gi.require_version('Gst','1.0')
from gi.repository import GLib
from luma_continuity.call_webrtc import WebRTCAudio


class WebRTCAudioTests(unittest.TestCase):
    def test_real_bidirectional_encrypted_pcm_and_teardown(self):
        received=[[],[]];failures=[];allowed=[True];engines=[]
        def deliver(other,message):
            try:engines[other].receive(message)
            except Exception as error:failures.append(type(error).__name__)
        for i in range(2):
            engines.append(WebRTCAudio(authorized=lambda:allowed[0],dispatch=GLib.idle_add,
                signal=lambda message,i=i:deliver(1-i,message),pcm=lambda frame,i=i:received[i].append(frame),
                failed=lambda:failures.append('closed')))
        context=GLib.MainContext.default();offset=[0]
        def tick():
            if not allowed[0]:return False
            frame=struct.pack('<320h',*(int(4000*math.sin(2*math.pi*440*(offset[0]+n)/16000)) for n in range(320)))
            offset[0]+=320
            for engine in engines:
                try:engine.push(frame)
                except Exception as error:failures.append(type(error).__name__);return False
            return True
        timer=None
        try:
            engines[1].start(offer=False);engines[0].start(offer=True)
            timer=GLib.timeout_add(20,tick)
            deadline=time.monotonic()+15
            while min(map(len,received))<5 and not failures:
                if time.monotonic()>deadline:raise AssertionError('WebRTC PCM timed out')
                while context.pending():context.iteration(False)
                time.sleep(.003)
            self.assertEqual(failures,[])
            self.assertTrue(all(any(any(frame) for frame in side) for side in received))
            self.assertTrue(all(engine.rtc.get_property('connection-state').value_nick=='connected' for engine in engines))
            allowed[0]=False
            for engine in engines:engine.close()
            self.assertTrue(all(engine.pipeline is None for engine in engines))
            with self.assertRaises(PermissionError):engines[0].push(b'\0'*640)
        finally:
            if timer is not None:GLib.source_remove(timer)
            for engine in engines:engine.close()

    def test_no_audio_pipeline_without_permission(self):
        engine=WebRTCAudio(authorized=lambda:False,signal=lambda *_:None,pcm=lambda *_:None,
                          failed=lambda:None,dispatch=GLib.idle_add)
        with self.assertRaises(PermissionError):engine.start(offer=True)
        self.assertIsNone(engine.pipeline)


if __name__=='__main__':unittest.main()
