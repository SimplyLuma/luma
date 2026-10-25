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
original = EnginePipe.request
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
              parameter_keys=sorted((params or {}).keys())))
    response = original(self, method, params, session)
    def completed(future):
        document = dict(token=token, method=method, stage='response', elapsed=time.monotonic()-start)
        try:
            value = future.result()
            document['result_keys'] = sorted(value.keys()) if isinstance(value, dict) else type(value).__name__
        except Exception as error:
            document['error_type'] = type(error).__name__
            document['error'] = str(error)[:512]
        emit(document)
    response.add_done_callback(completed)
    return response

EnginePipe.request = request
sys.argv[0] = str(host)
runpy.run_path(str(host), run_name='__main__')
