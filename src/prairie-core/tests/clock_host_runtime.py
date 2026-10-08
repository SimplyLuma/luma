# SPDX-License-Identifier: Apache-2.0
"""Real private D-Bus dispatch + native scheduler, with explicit auth stand-ins.

Unsigned caller rejection uses the actual production authenticator. Permitted
operations use a unit-only identity stand-in; signed Flatpak qualification is a
separate required gate, not claimed by this test.
"""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from gi.repository import Gio, GLib
sys.path.insert(0, str(Path(__file__).parent))
from clock_alarms_unit import FakeApplication, FakeRinger, FakeInhibitor, FakeWake
from prairie_apps.clock_backend import ClockStore
from prairie_apps.clock_alarms import AlarmService
from prairie_apps.clock_host import HostInterface, BUS, OBJECT
from luma_installer import app_data_broker

with tempfile.TemporaryDirectory() as root:
    store=ClockStore(Path(root)/'state.json');store.add_world_clock('London','Europe/London')
    service=AlarmService(FakeApplication(),store=store,ringer=FakeRinger(),agent=SimpleNamespace(publish=lambda *_:None),inhibitor=FakeInhibitor(),wake=FakeWake(),sandboxed=False)
    bus=Gio.bus_get_sync(Gio.BusType.SESSION,None);interface=HostInterface(bus,service)
    loop=GLib.MainLoop();thread=threading.Thread(target=loop.run);thread.start()
    client=Gio.DBusConnection.new_for_address_sync(__import__('os').environ['DBUS_SESSION_BUS_ADDRESS'],Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT|Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,None,None)
    def call(method,args=None,result='()'):
        return client.call_sync(BUS,OBJECT,BUS,method,args,GLib.VariantType.new(result),Gio.DBusCallFlags.NONE,5000,None).unpack()
    try:
        time.sleep(.1)
        try:call('GetPlan',result='(s)')
        except GLib.Error as error:assert '.Refused' in str(error)
        else:raise AssertionError('Production auth accepted an unsigned native caller')
        assert store.alarms()==() and store.timers()==()
        with patch.object(app_data_broker,'authenticate',return_value='org.projectluma.Clock'):
            first=json.loads(call('GetPlan',result='(s)')[0]);now=int(time.time())
            plan={'alarms':[dict(uid='a'*32,label='Native',hour=7,minute=30,days=[0,2],enabled=True,sound='Chime',snooze_minutes=9,ring_seconds=60)],'timers':[dict(uid='b'*32,label='Native timer',fires_at=now+3600,total_seconds=3600)]}
            call('SetPlan',GLib.Variant('(ss)',(first['revision'],json.dumps(plan))))
            assert len(store.alarms())==len(store.timers())==1 and service.upcoming and service.timer.deadline is not None
            assert store.world_clocks()[0].label=='London'
            try:call('SetPlan',GLib.Variant('(ss)',(first['revision'],json.dumps({'alarms':[],'timers':[]}))))
            except GLib.Error as error:assert 'changed' in str(error)
            else:raise AssertionError('Stale plan overwrote a new native schedule')
            assert len(store.alarms())==len(store.timers())==1
            current=json.loads(call('GetPlan',result='(s)')[0]);bad=dict(plan,path='/tmp/other')
            try:call('SetPlan',GLib.Variant('(ss)',(current['revision'],json.dumps(bad))))
            except GLib.Error:pass
            else:raise AssertionError('Client-selected path accepted')
            call('Snooze',GLib.Variant('(s)',('a'*32,)));assert 'a'*32 in service.state.snoozes
            call('Stop',GLib.Variant('(s)',('a'*32,)));assert 'a'*32 not in service.state.snoozes
        with patch.object(app_data_broker,'authenticate',return_value='io.luma.Monitor'):
            try:call('GetPlan',result='(s)')
            except GLib.Error:pass
            else:raise AssertionError('Wrong installed app role read alarms')
        print('Clock real DBus/native scheduling + stale/unsigned/wrong-role/path controls PASS; signedclient gate remains separate',flush=True)
    finally:
        service.shutdown();interface.close();loop.quit();thread.join();client.close_sync(None)
