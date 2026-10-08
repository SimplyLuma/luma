# SPDX-License-Identifier: Apache-2.0
"""Explicit native deadline qualification on an owned private D-Bus.

Run with native Gio and dbus-daemon as an ordinary user. This uses no Android
runtime, Shell windows, credentials or existing session bus. The fresh query
is a real delayed subprocess; both client and broker are maintained code.
The private Shell receiver tests reply lifetime only, not window authority.
"""
import concurrent.futures
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gi.repository import Gio, GLib
from luma_android import activation, activation_budget as budget
from luma_android.live_registry import bounded_output, parse_live_packages
from luma_android.service import Service, RUNTIME_XML

assert os.getuid() != 0, 'The genuine native-caller contract refuses root'
assert shutil.which('dbus-daemon'), 'Native private-bus prerequisite missing'
assert len(sys.argv) == 2
output = Path(sys.argv[1])
assert not output.exists()
events = []
unresponsive = False
held_invocations = []
loop = GLib.MainLoop()
thread = threading.Thread(target=loop.run)
private_bus = Gio.TestDBus.new(Gio.TestDBusFlags.NONE)
private_bus.up()
connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
assert connection.get_unique_name()

class DelayedEngine:
    def live_launchable_packages(self):
        events.append({'event':'real-registry-start','time':time.monotonic()})
        text = bounded_output([sys.executable, '-B', '-I', '-c',
            'import time; time.sleep(16); print("Name: Example\\npackageName: org.example.App\\ncategories:")'])
        packages = parse_live_packages(text)
        events.append({'event':'real-registry-terminal','time':time.monotonic()})
        return packages

service = Service.__new__(Service)
service.engine = DelayedEngine()
service.activation_slots = threading.BoundedSemaphore(2)
service.activation_workers = concurrent.futures.ThreadPoolExecutor(max_workers=2)

def runtime_call(conn, sender, path, interface, method, parameters, invocation):
    if unresponsive:
        events.append({'event':'intentionally-unanswered-request','time':time.monotonic()})
        held_invocations.append(invocation)
        return
    assert interface == activation.ANDROID_NAME and method in ('ActivateExisting','CloseExisting')
    service.request_activation(conn, sender, parameters.unpack()[0], invocation,
                               close=method == 'CloseExisting')

shell_xml = '''<node><interface name="org.gnome.Shell.Introspect">
<method name="ActivateAndroidApplicationForCaller"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="b" direction="out"/></method>
<method name="CloseAndroidApplicationForCaller"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="b" direction="out"/></method>
</interface></node>'''

def shell_call(conn, sender, path, interface, method, parameters, invocation):
    assert sender == connection.get_unique_name()
    assert parameters.unpack() == ('org.example.App', connection.get_unique_name())
    events.append({'event':'real-shell-rpc','method':method,'time':time.monotonic()})
    invocation.return_value(GLib.Variant('(b)', (True,)))

registrations = []
for xml, path, handler in ((RUNTIME_XML, activation.ANDROID_PATH, runtime_call),
                           (shell_xml, activation.SHELL_PATH, shell_call)):
    node = Gio.DBusNodeInfo.new_for_xml(xml)
    registrations.append(connection.register_object(path, node.interfaces[0], handler, None, None))
for name in (activation.ANDROID_NAME, activation.SHELL_NAME):
    result = connection.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus',
        'org.freedesktop.DBus','RequestName',GLib.Variant('(su)',(name,0)),
        GLib.VariantType.new('(u)'),Gio.DBusCallFlags.NONE,1000,None)
    assert result.unpack()[0] == 1
thread.start()
row = {'result':'INCOMPLETE','scope':'Native private-bus activation timing; no real Android/Shell frame qualification',
       'uid':os.getuid(),'declared_work_ms':budget.EXISTING_WORK_TIMEOUT_MS,
       'declared_client_ms':budget.EXISTING_CLIENT_TIMEOUT_MS,'observations':[]}
def wait_idle(seconds):
    deadline = time.monotonic()+seconds
    while service.activation_slots._value != 2 and time.monotonic()<deadline:
        time.sleep(.01)
    assert service.activation_slots._value == 2

try:
    before = time.monotonic()
    with mock.patch.object(activation, 'EXISTING_CLIENT_TIMEOUT_MS', 15000):
        old = activation.request_existing_restore('org.example.App')
    elapsed = time.monotonic()-before
    assert old is False and 14 <= elapsed <= 18, (old,elapsed)
    row['observations'].append({'case':'original15s-deadline','result':'EXPECTED-RED',
                                'elapsed_seconds':elapsed})
    # The old request must finish and release its admission before its successor.
    wait_idle(5)
    assert sum(e['event']=='real-registry-terminal' for e in events) == 1

    for close in (False, True):
        before = time.monotonic()
        current = activation.request_existing_restore('org.example.App', close=close)
        elapsed = time.monotonic()-before
        assert current is True and 16 <= elapsed < 25, (current,elapsed)
        wait_idle(1)
        row['observations'].append({'case':'close' if close else 'restore','result':'PASS',
                                   'elapsed_seconds':elapsed})
    assert sum(e['event']=='real-registry-terminal' for e in events) == 3
    assert sum(e['event']=='real-shell-rpc' for e in events) == 3

    unresponsive = True
    before = time.monotonic()
    current = activation.request_existing_restore('org.example.App')
    elapsed = time.monotonic()-before
    assert current is False and 44 <= elapsed <= 49, (current,elapsed)
    row['observations'].append({'case':'unresponsive-native-bus','result':'EXPECTED-FAILURE',
                               'elapsed_seconds':elapsed})
    row['result'] = 'PASS'
finally:
    service.activation_workers.shutdown(wait=True)
    for invocation in held_invocations:
        invocation.return_dbus_error('org.projectluma.Test.Closed', 'Private fixture closed.')
    held_invocations.clear()
    for registration in registrations:
        connection.unregister_object(registration)
    connection.close_sync(None)
    loop.quit()
    thread.join(timeout=2)
    assert not thread.is_alive()
    del connection
    private_bus.down()
    row['events'] = events
    row['source_files'] = {name:hashlib.sha256(Path(activation.__file__).with_name(name).read_bytes()).hexdigest()
                          for name in ('activation.py','activation_budget.py','live_registry.py','service.py')}
    with output.open('x') as f:
        json.dump(row,f,indent=2)
        f.flush()
        os.fsync(f.fileno())
print('Native delayed restore/close, original-deadline negative and bounded no-reply control PASS')
