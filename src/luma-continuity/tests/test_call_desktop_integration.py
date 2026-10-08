"""Synthetic native FD -> encrypted media -> private desktop graph -> native FD."""
import math
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from gi.repository import GLib
import test_call_audio_lease as lease_fixture
import test_call_signaling as signal_fixture
from luma_continuity.call_audio_session import CallAudioSession
from luma_continuity.call_audio_lease import NativeAudioLease


class DesktopCallIntegrationTests(unittest.TestCase):
    def test_native_to_desktop_roundtrip_and_revocation(self):
        self.assertFalse(Path('/dev/snd').exists())
        fixture=lease_fixture.AudioLeaseTests();fixture.setUp()
        signaling=signal_fixture.AudioSignalTests();signaling.setUp()
        sessions=[];timer=None;processes=[];failures=[];uplink=[]
        context=GLib.MainContext.default()
        def wait(predicate):
            deadline=time.monotonic()+15
            while not predicate():
                if time.monotonic()>deadline:raise AssertionError('desktop encrypted PCM timeout')
                for _ in range(64):
                    if not context.pending():break
                    context.iteration(False)
                time.sleep(.003)
        with tempfile.TemporaryDirectory(prefix='connect-pw-combined-') as root,patch.dict(os.environ,
                {'XDG_RUNTIME_DIR':root,'PIPEWIRE_RUNTIME_DIR':root,'PIPEWIRE_REMOTE':'pipewire-0','PULSE_SERVER':f'unix:{root}/pulse/native'}):
            with open(Path(root)/'graph.log','w') as log:
                try:
                    processes.append(subprocess.Popen(['pipewire'],stdout=log,stderr=log))
                    wait(lambda:(Path(root)/'pipewire-0').exists())
                    processes.append(subprocess.Popen(['pipewire-pulse'],stdout=log,stderr=log))
                    wait(lambda:(Path(root)/'pulse'/'native').exists())
                    processes.append(subprocess.Popen(['pw-loopback','-n','fixture','-c','1','-m','MONO',
                        '-i','node.name=fixture-input media.class=Audio/Sink node.autoconnect=false',
                        '-o','node.name=fixture-output media.class=Audio/Source node.autoconnect=false'],stdout=log,stderr=log))
                    processes.append(subprocess.Popen(['wireplumber'],stdout=log,stderr=log))
                    fixture.native_up.setblocking(False)
                    for i in range(2):
                        sessions.append(CallAudioSession(signaling.streams[i],signaling.dirs[i],signaling.pins[1-i],
                            account='account',epoch='e'*32,call='c'*32,session='a'*32,incoming=bool(i),
                            current_call=lambda:'c'*32,consented=lambda:fixture.allowed,dispatch=GLib.idle_add,
                            failed=lambda:failures.append('session'),
                            native_current_call=(lambda:'native-call') if i==0 else None,
                            lease_factory=lambda **kwargs:NativeAudioLease(**kwargs,connection_factory=fixture.connect)))
                    # The native service owns the incoming audio grant. Give
                    # that endpoint the receiver identity and peer the requester.
                    sessions[0].binding['incoming']=False
                    sessions[1].binding['incoming']=True
                    sessions[1].start(offer=False);sessions[0].start(offer=True)
                    wait(lambda:all(s.ready for s in sessions) or failures)
                    frame=struct.pack('<320h',*(int(4000*math.sin(2*math.pi*440*n/16000)) for n in range(320)))
                    def tick():
                        if not fixture.allowed:return False
                        fixture.native_down.send(frame)
                        while True:
                            try:uplink.append(fixture.native_up.recv(641))
                            except BlockingIOError:break
                        return True
                    timer=GLib.timeout_add(20,tick)
                    wait(lambda:(len(uplink)>=5 and any(any(f) for f in uplink)) or failures)
                    self.assertEqual(failures,[])
                    self.assertTrue(all(len(f)==640 for f in uplink))
                    fixture.allowed=False
                    for session in sessions:session.changed()
                    self.assertTrue(all(s.closed for s in sessions))
                    self.assertTrue(sessions[0].lease.closed)
                    self.assertIsNone(sessions[1].engine.pipeline)
                finally:
                    if timer is not None:
                        source=context.find_source_by_id(timer)
                        if source:source.destroy()
                    for session in sessions:session.close()
                    for session in sessions:
                        if session.signals and session.signals.worker.ident is not None:session.signals.worker.join(2)
                    self.assertTrue(signaling.doCleanups())
                    for process in reversed(processes):
                        process.terminate()
                        try:process.wait(timeout=3)
                        except subprocess.TimeoutExpired:process.kill();process.wait()
                    fixture.tearDown()
