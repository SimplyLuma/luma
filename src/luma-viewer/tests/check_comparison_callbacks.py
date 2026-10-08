# SPDX-License-Identifier: Apache-2.0
"""Actual Swap/Done controls remain safe while comparison decoding is pending."""
import hashlib
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch
import gi
gi.require_version('Gtk','4.0')
from gi.repository import GLib, Gtk
from luma_viewer.application import ViewerApplication
from luma_viewer import formats

def drain(seconds=.1):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
        time.sleep(.005)

def wait(condition):
    deadline=time.monotonic()+20
    while not condition() and time.monotonic()<deadline:drain()
    assert condition(), 'native comparison callback timed out'

def descendants(widget):
    yield widget
    child=widget.get_first_child()
    while child:
        yield from descendants(child)
        child=child.get_next_sibling()

def button(window,name):
    return next(w for w in descendants(window.action_center)
        if isinstance(w,Gtk.Button) and w.get_name()==name)

app=ViewerApplication()
assert app.register(None) and not app.get_is_remote()
app.activate()
window=app.get_windows()[0]
width=int(os.environ['VIEWER_CHECK_WIDTH'])
window.set_default_size(width,740)
wait(lambda:window.loaded)
assert window.fixture
drain(.3)
source_path=window.facts.path
before=hashlib.sha256(Path(source_path).read_bytes()).hexdigest()
paths=[str(window.recents.samples[key]['path']) for key in ('v6','v7')]
original_loader=formats.load_image
started=threading.Event();release=threading.Event()
def delayed(path):
    started.set()
    assert release.wait(10), 'test failed to release decoder'
    return original_loader(path)
with patch('luma_viewer.formats.load_image',side_effect=delayed):
    try:
        window._begin_compare(paths)
        wait(started.is_set)
        button(window,'vw-action-swap').emit('clicked')
        assert window.compare==list(reversed(paths))
    finally:release.set()
    wait(lambda:len(window.compare_pixbufs)==2)
    assert window.compare==list(reversed(paths))
    for image in window.compare_pixbufs.values():
        assert (image.get_width(),image.get_height())==(1180,740)
print('ACTUAL SWAP BEFORE DECODE / BOTH NATIVE FILER IMAGES',width,'PASS',flush=True)
button(window,'vw-action-done').emit('clicked')
assert window.compare is None
started=threading.Event();release=threading.Event();finished=threading.Event()
def late_failure(_path):
    started.set()
    assert release.wait(10), 'test failed to release decoder'
    finished.set()
    raise OSError('isolated delayed comparison failure')
with patch('luma_viewer.formats.load_image',side_effect=late_failure):
    try:
        window._begin_compare(paths)
        wait(started.is_set)
        button(window,'vw-action-done').emit('clicked')
        assert window.compare is None
    finally:release.set()
    wait(finished.is_set)
    drain(.3)
assert window.loaded and window.facts.path==source_path
assert window.marks_area.get_parent() is not None
assert not window.comparison_selection and not window.compare_pixbufs
assert hashlib.sha256(Path(source_path).read_bytes()).hexdigest()==before
print('ACTUAL DONE BEFORE LATE ERROR / DOCUMENT AND ORIGINAL PRESERVED',width,'PASS',flush=True)
window.destroy()
drain()
assert not app.get_windows()
app.quit()
print('CLOSED COMPARISON',width,'PASS',flush=True)
