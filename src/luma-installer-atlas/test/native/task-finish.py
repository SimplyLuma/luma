# Copyright (C) 2026 Project Luma contributors
# SPDX-License-Identifier: LGPL-2.1-or-later
# Harmless native Anaconda44 diagnostic: fixture threads only, no installation.
import json
import threading
import time
from gi.repository import GLib
from pyanaconda.core.threads import thread_manager
from pyanaconda.modules.common.errors.installation import PayloadInstallationError
from pyanaconda.modules.common.task.task import Task

MESSAGE='Atlas native diagnostic fixture: Failed to pull payload: No space left on device'
class NativeFailure(Task):
    @property
    def name(self): return 'Atlas harmless Task.Finish error fixture'
    def run(self): raise PayloadInstallationError(MESSAGE)
class NativeSuccess(Task):
    @property
    def name(self): return 'Atlas harmless Task.Finish success fixture'
    def run(self): return None
results=[]
for cls in (NativeFailure, NativeSuccess):
    task=cls(); interface=task.for_publication()
    stopped=threading.Event(); task.stopped_signal.connect(stopped.set)
    interface.Start()
    context=GLib.MainContext.default(); deadline=time.monotonic()+10
    while not stopped.is_set() and time.monotonic()<deadline:
        while context.pending(): context.iteration(False)
        time.sleep(0.01)
    assert stopped.is_set(), "fixture thread failed to stop"
    assert not interface.IsRunning
    try:
        interface.Finish()
    except PayloadInstallationError as error:
        assert cls is NativeFailure and str(error)==MESSAGE
        results.append({'case':'failed','exception_class':type(error).__name__,'message':str(error),'result':'pass'})
    else:
        assert cls is NativeSuccess
        results.append({'case':'succeeded','result':'pass'})
    # Native44 removes the error after its first Finish. The frontend therefore
    # retains its sanitized failure in session storage across page reloads.
    interface.Finish()
print(json.dumps({'native_Task_Finish':results,'repeated_Finish_consumes_error':True},indent=2))
