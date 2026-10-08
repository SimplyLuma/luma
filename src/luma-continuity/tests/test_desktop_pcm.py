"""Private PipeWire graph only: desktop endpoint inside the WebRTC pipeline, no physical audio device."""
import math
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from gi.repository import GLib,Gio
from luma_continuity.call_webrtc import WebRTCAudio


def pump(seconds=.005):
    context=GLib.MainContext.default()
    for _ in range(64):
        if not context.pending():break
        context.iteration(False)
    time.sleep(seconds)


class DesktopEndpointTests(unittest.TestCase):
    def test_no_capture_without_consent(self):
        engine=WebRTCAudio(authorized=lambda:False,signal=lambda _:None,pcm=lambda _:None,
                           failed=lambda:None,dispatch=GLib.idle_add)
        with self.assertRaises(PermissionError):engine.start(offer=True,endpoint='desktop')
        self.assertIsNone(engine.pipeline)

    def private_graph(self,root,log,*,policy=True):
        processes=[subprocess.Popen(['pipewire'],stdout=log,stderr=log)]
        deadline=time.monotonic()+5
        while not (Path(root)/'pipewire-0').exists():
            if time.monotonic()>deadline:self.fail('private PipeWire startup failed')
            time.sleep(.01)
        # The desktop endpoint uses the session's PulseAudio-compatible server.
        processes.append(subprocess.Popen(['pipewire-pulse'],stdout=log,stderr=log))
        while not (Path(root)/'pulse'/'native').exists():
            if time.monotonic()>deadline+5:self.fail('private pipewire-pulse startup failed')
            time.sleep(.01)
        # This fixture must never discover a physical audio device.
        self.assertFalse(Path('/dev/snd').exists())
        if policy:
            processes.append(subprocess.Popen(['pw-loopback','-n','fixture','-c','1','-m','MONO',
                '-i','node.name=fixture-input media.class=Audio/Sink',
                '-o','node.name=fixture-output media.class=Audio/Source'],stdout=log,stderr=log))
            processes.append(subprocess.Popen(['wireplumber'],stdout=log,stderr=log))
        return processes

    def stop(self,processes):
        for process in reversed(processes):
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait()

    def test_private_virtual_pipewire_roundtrip_mute_and_revocation(self):
        test_bus=Gio.TestDBus.new(Gio.TestDBusFlags.NONE);test_bus.up();self.addCleanup(test_bus.down)
        with tempfile.TemporaryDirectory(prefix='connect-pw-') as root:
            with patch.dict(os.environ,{'XDG_RUNTIME_DIR':root,'PIPEWIRE_RUNTIME_DIR':root,'PIPEWIRE_REMOTE':'pipewire-0','PULSE_SERVER':f'unix:{root}/pulse/native'}):
                log=open(Path(root)/'pipewire.log','w');processes=[];engines=[];timer=None
                try:
                    processes=self.private_graph(root,log)
                    received=[];failures=[];allowed=[True]
                    for index in range(2):
                        engines.append(WebRTCAudio(authorized=lambda:allowed[0],dispatch=GLib.idle_add,
                            signal=lambda value,index=index:engines[1-index].receive(value),
                            pcm=received.append,failed=lambda index=index:failures.append(index)))
                    engines[1].start(offer=False);engines[0].start(offer=True,endpoint='desktop')
                    frame=struct.pack('<320h',*(int(6000*math.sin(2*math.pi*440*n/16000)) for n in range(320)))
                    def tick():
                        if engines[1].closed:return False
                        engines[1].push(frame);return True
                    timer=GLib.timeout_add(20,tick)
                    # Far-end tone -> desktop speaker -> fixture loopback -> desktop mic -> far end.
                    deadline=time.monotonic()+20
                    loud=lambda frames:sum(1 for f in frames if max(abs(v) for v in struct.unpack('<320h',f))>1000)
                    while loud(received)<150 and not failures:
                        if time.monotonic()>deadline:
                            log.flush();raise AssertionError('desktop endpoint loop timeout '+str(len(received))+'\n'+(Path(root)/'pipewire.log').read_text()[-4000:])
                        pump()
                    self.assertEqual(failures,[])
                    # Capture must stay real-time while playback is also running.
                    start=len(received);end=time.monotonic()+3
                    while time.monotonic()<end:pump()
                    self.assertGreaterEqual(len(received)-start,135,'desktop capture fell behind real time')
                    engines[0].set_muted(True);received.clear();end=time.monotonic()+1.5
                    while time.monotonic()<end:pump()
                    tail=received[-20:]
                    self.assertTrue(tail and loud(tail)==0,'muted desktop microphone must send silence')
                    allowed[0]=False;deadline=time.monotonic()+1
                    while engines[0].pipeline is not None:
                        self.assertLess(time.monotonic(),deadline,'revocation must stop desktop devices within the watchdog bound')
                        pump()
                    self.assertIn(0,failures)
                finally:
                    if timer is not None:
                        source=GLib.MainContext.default().find_source_by_id(timer)
                        if source:source.destroy()
                    for engine in engines:engine.close()
                    self.stop(processes);log.close()

    def test_missing_policy_fails_without_blocking_main_context(self):
        with tempfile.TemporaryDirectory(prefix='connect-no-policy-') as root:
            with patch.dict(os.environ,{'XDG_RUNTIME_DIR':root,'PIPEWIRE_RUNTIME_DIR':root,'PIPEWIRE_REMOTE':'pipewire-0','PULSE_SERVER':f'unix:{root}/pulse/native'}):
                log=open(os.devnull,'w');processes=[];engine=None
                try:
                    processes=self.private_graph(root,log,policy=False)
                    failures=[]
                    engine=WebRTCAudio(authorized=lambda:True,signal=lambda _:None,pcm=lambda _:None,
                                       failed=lambda:failures.append(True),dispatch=GLib.idle_add)
                    started=time.monotonic()
                    engine.start(offer=True,endpoint='desktop')
                    self.assertLess(time.monotonic()-started,1,'device negotiation must not block the main context')
                    iterations=0
                    while not failures and time.monotonic()-started<13:pump();iterations+=1
                    self.assertEqual(failures,[True]);self.assertTrue(engine.closed);self.assertIsNone(engine.pipeline)
                    self.assertGreater(iterations,100)
                finally:
                    if engine:engine.close()
                    self.stop(processes);log.close()
                    # A negotiation stuck on the dead graph must end, not leak into later sessions.
                    if engine and engine.state_thread:
                        engine.state_thread.join(120);self.assertFalse(engine.state_thread.is_alive())
