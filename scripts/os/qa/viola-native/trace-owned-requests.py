#!/usr/bin/python3
"""Request timing for the disposable native CI profile; never acceptance."""
import json
import os
from pathlib import Path
import runpy
import sys
import threading
import time

host = Path('/usr/share/viola-browser/luma-host/integrated_window.py')
assert os.getuid() == 1100
assert os.environ.get('HOME') == '/home/luma-gate-browser-opens'
assert '--interactive' in sys.argv and '--start-url' in sys.argv
sys.path.insert(0, str(host.parent))
from engine_pipe import EnginePipe
from frame_receiver import FrameReceiver
from native_visibility import NativeVisibility
original = EnginePipe.request
original_visible = FrameReceiver.set_visible
original_changed = NativeVisibility.changed
lock = threading.Lock()
count = 0

def emit(document):
    print('OWNED_REQUEST_DIAGNOSTIC ' + json.dumps(document, sort_keys=True), file=sys.stderr, flush=True)

def request(self, method, params=None, session=None):
    global count
    with lock:
        count += 1
        token = count
    if token > 128:
        return original(self, method, params, session)
    start = time.monotonic()
    emit(dict(token=token, method=method, session=session, stage='request',
              parameter_keys=sorted((params or {}).keys()),
              geometry=params if method.startswith('Emulation.') else None))
    response = original(self, method, params, session)
    def completed(future):
        document = dict(token=token, method=method, stage='response', elapsed=time.monotonic()-start)
        try:
            value = future.result()
            document['result_keys'] = sorted(value.keys()) if isinstance(value, dict) else type(value).__name__
            if method == 'Target.getTargets':
                document['targets'] = [{key: info.get(key) for key in ('targetId', 'type', 'url', 'attached')} for info in value.get('targetInfos', [])]
            if method == 'Target.attachToTarget':
                document['target_id'] = (params or {}).get('targetId')
                document['session'] = value.get('sessionId')
        except Exception as error:
            document['error_type'] = type(error).__name__
            document['error'] = str(error)[:512]
        emit(document)
    response.add_done_callback(completed)
    return response

def visible(self, window_id, value):
    emit(dict(stage='native-visibility', window_id=window_id, visible=value))
    return original_visible(self, window_id, value)

def changed(self, *args):
    surface = self.window.get_surface()
    emit(dict(stage='native-surface-state', mapped=self.window.get_mapped(),
              state=str(surface.get_state()) if surface else None, active=self.window.is_active()))
    return original_changed(self, *args)

FrameReceiver.set_visible = visible
NativeVisibility.changed = changed
EnginePipe.request = request
sys.argv[0] = str(host)
runpy.run_path(str(host), run_name='__main__')
