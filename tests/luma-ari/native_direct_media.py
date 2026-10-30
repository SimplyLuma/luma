# SPDX-License-Identifier: Apache-2.0
"""Isolated server check: actual Tide app, D-Bus, MCP, queue and decoded PCM.

A generated silent WAV and GStreamer's fakesink replace speakers. No hosted
model, personal music, account or credentials are used. The production app,
library, controller, decoder and Ari policy are exercised without UI clicks.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import wave

out = Path(os.environ['DEV_ARTIFACT_DIR'])
assert os.environ.get('ARI_TIDE_ISOLATED') == '1'

if '--app' in sys.argv:
    import gi
    gi.require_foreign('cairo')
    from gi.repository import GLib
    from unittest.mock import patch
    from luma_tide.application import TideApplication
    from luma_tide.playback import GStreamerEngine
    def engine():
        value = GStreamerEngine(audio_sink='fakesink')
        value._audio_sink.set_property('sync', True)
        value._audio_sink.set_property('signal-handoffs', True)
        def decoded(_sink, buffer, _pad):
            if not (out/'decoded-pcm').exists():
                (out/'decoded-pcm').write_text(str(buffer.get_size()))
        value._audio_sink.connect('handoff', decoded)
        return value
    app = TideApplication()
    def ready():
        if app.window and app.window.get_mapped() and not app._syncing:
            def snapshot(value):
                (out/'snapshot.json').write_text(json.dumps({'state':value.state.value,
                    'title':value.track.title if value.track else None,'error':value.error}))
            app.controller.subscribe(snapshot)
            (out/'ready').write_text('ready')
            return False
        return True
    GLib.timeout_add(50, ready)
    with patch('luma_tide.application.GStreamerEngine', engine):
        sys.exit(app.run([]))

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib
from unittest.mock import Mock
from ari.agent import Agent, Tools, Turn
from ari.policy import Policy
from ari.store import Store
from ari.tools import desktop, media
from luma_tide.model import LibraryStore
from luma_tide.metadata import default_reader

music = out/'music'; music.mkdir()
audio = music/'Have a Cigar.wav'
with wave.open(str(audio), 'wb') as stream:
    stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(8000)
    stream.writeframes(b'\0\0'*8000*20)
library = LibraryStore()
source = library.add_source('Fixture music', 'local-folder', music, local=True)
library.upsert_copy(source.id, default_reader().read(audio))
library.close()

applications = Path(os.environ['XDG_DATA_HOME'])/'applications'; applications.mkdir(parents=True)
entry = shlex.join([sys.executable, str(Path(__file__).resolve()), '--app'])
(applications/'org.projectluma.Tide.desktop').write_text(
    '[Desktop Entry]\nType=Application\nName=Tide\nExec='+entry+'\nDBusActivatable=false\n')

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
def owns():
    return bus.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus',
        'NameHasOwner', GLib.Variant('(s)',('org.projectluma.Tide',)), None,
        Gio.DBusCallFlags.NONE,1000,None).unpack()[0]
def wait(check, timeout=8):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if check(): return
        time.sleep(.03)
    raise AssertionError('Native application did not reach the expected state')

tools = Tools({name:[sys.executable, str(Path(module.__file__))]
               for name,module in (('desktop', desktop),('media', media))})
tools.start()
process = None
log = (out/'app.log').open('w')
timings = []
try:
    for mode in ('system','all','never'):
        for marker in ('ready','decoded-pcm'): (out/marker).unlink(missing_ok=True)
        process = subprocess.Popen([sys.executable, __file__, '--app'], stdout=log, stderr=log)
        wait(lambda: (out/'ready').exists())
        policy = Policy(); policy.approval_mode=mode
        store = Store(':memory:')
        provider = Mock(side_effect=AssertionError('No hosted inference is required'))
        agent = Agent(store, tools, policy, model_provider=provider)
        approvals=[]
        def emit(event):
            if event.get('type')=='approval':
                approvals.append(event)
                assert agent.approve(event['approval'], True)
        for prompt in ('Play Have a Cigar on Tide', 'Close Tide'):
            conversation = store.new_conversation()
            started=time.monotonic()
            agent.answer(Turn(conversation, prompt, emit))
            elapsed=time.monotonic()-started
            steps=store.steps(conversation)
            assert len(steps)==1, (prompt, store.messages(conversation))
            if prompt.startswith('Play'):
                assert steps[0]['tool']=='play_track' and steps[0]['summary'].startswith('Playing'), steps
                wait(lambda:(out/'decoded-pcm').exists(),2)
                assert int((out/'decoded-pcm').read_text())>0
            else:
                assert steps[0]['summary']=='Closed Tide.', steps
                wait(lambda: not owns())
            timings.append(dict(mode=mode,action=steps[0]['tool'],seconds=round(elapsed,3)))
        provider.assert_not_called()
        assert len(approvals)==(2 if mode=='all' else 0), approvals
        assert process.wait(timeout=5)==0
        store.db.close()
    # Cold launch uses the ordinary launcher and the same permissions,
    # then directly plays. There is still no model roundtrip or UI script.
    (out/'decoded-pcm').unlink(missing_ok=True)
    store=Store(':memory:'); policy=Policy()
    agent=Agent(store,tools,policy,model_provider=None)
    conversation=store.new_conversation(); started=time.monotonic()
    agent.answer(Turn(conversation,'Play Have a Cigar on Tide',lambda event:None))
    steps=store.steps(conversation)
    assert [row['tool'] for row in steps]==['open_app','play_track'], (steps,store.messages(conversation))
    wait(lambda:(out/'decoded-pcm').exists(),2)
    timings.append(dict(mode='system',action='cold_launch_and_play',seconds=round(time.monotonic()-started,3)))
    agent.answer(Turn(store.new_conversation(),'Close Tide',lambda event:None))
    wait(lambda:not owns())
    store.db.close()
    (out/'timings.json').write_text(json.dumps(timings,indent=2))
    print('PASS actual Tide application/controller, decoded PCM, real MCP/Agent, 3 permission modes, verified close and cold launch; zero model calls')
    print(json.dumps(timings))
finally:
    tools.stop()
    if process and process.poll() is None:
        process.terminate(); process.wait(timeout=5)
    log.close()
