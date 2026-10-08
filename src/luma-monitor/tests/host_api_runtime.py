# SPDX-License-Identifier: Apache-2.0
"""Private real D-Bus/native proc and pidfd operation unit qualification.

Production auth rejects the unsigned caller. Positive method ownership uses a
unit-only identity stand-in; actual signedclient admission is separately required.
"""
import json
import os
import subprocess
import threading
import time
from unittest.mock import patch
from gi.repository import Gio, GLib
from luma_monitor.host_sampler import Broker
from luma_monitor.host_protocol import BUS, OBJECT, decode
from luma_installer import app_data_broker

broker=Broker();thread=threading.Thread(target=broker.loop.run);thread.start()
client=Gio.DBusConnection.new_for_address_sync(os.environ['DBUS_SESSION_BUS_ADDRESS'],Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT|Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,None,None)
child=None
def call(method,args,result):return client.call_sync(BUS,OBJECT,BUS,method,args,GLib.VariantType.new(result),Gio.DBusCallFlags.NONE,15000,None).unpack()
try:
    time.sleep(.1)
    try:call('Sample',GLib.Variant('(b)',(False,)),'(s)')
    except GLib.Error as error:assert '.Refused' in str(error)
    else:raise AssertionError('Production auth accepted unsigned caller')
    assert broker.snapshot is None
    with patch.object(app_data_broker,'authenticate',return_value='io.luma.Monitor'):
        child=subprocess.Popen(['sleep','30'])
        snapshot=decode(call('Sample',GLib.Variant('(b)',(False,)),'(s)')[0])
        target=next(p for p in snapshot['processes'] if p.pid==child.pid)
        assert target.start>0 and target.uid==os.getuid() and target.cgroup
        facts=json.loads(call('Inspect',GLib.Variant('(uts)',(target.pid,target.start,'properties')),'(s)')[0]);assert any(str(child.pid) in str(v) for k,v in facts)
        for kind,start in [('arbitrary-file',target.start),('properties',target.start+1)]:
            try:call('Inspect',GLib.Variant('(uts)',(target.pid,start,kind)),'(s)')
            except GLib.Error:pass
            else:raise AssertionError('Unknown detail or reused PID accepted')
        own=next(p for p in snapshot['processes'] if p.pid==os.getpid())
        try:call('ForceQuit',GLib.Variant('(a(ut))',([(own.pid,own.start)],)),'(uu)')
        except GLib.Error:pass
        else:raise AssertionError('Host broker killed itself')
        sent,total=call('ForceQuit',GLib.Variant('(a(ut))',([(target.pid,target.start)],)),'(uu)');assert(sent,total)==(1,1)
        assert child.wait(timeout=5)==-9
        try:call('ForceQuit',GLib.Variant('(a(ut))',([(target.pid,target.start+1)],)),'(uu)')
        except GLib.Error:pass
        else:raise AssertionError('Reused PID was signalled')
    with patch.object(app_data_broker,'authenticate',return_value='org.projectluma.Clock'):
        try:call('Sample',GLib.Variant('(b)',(False,)),'(s)')
        except GLib.Error:pass
        else:raise AssertionError('Wrong installed app role sampled host')
    print('Monitor real DBus/native proc+ownedchild pidfd, unsigned/wrong-role/path/PID controls PASS; signedclient gate remains separate',flush=True)
finally:
    if child is not None and child.poll() is None:child.terminate();child.wait(timeout=5)
    broker.loop.quit();thread.join();broker.catalog.close();Gio.bus_unown_name(broker.owner);client.close_sync(None)
