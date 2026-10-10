# SPDX-License-Identifier: Apache-2.0
"""Qualify the installed user service with actual signed Flatpak clients.

Run as a non-root test user on an isolated installed Luma guest with seeded
Monitor and Clock applications. This deliberately uses systemd/D-Bus activation
and production authentication; direct Broker unit tests cannot cover namespaces.
Only aggregate measurements and authorization outcomes leave the test.
"""
import json
import os
import subprocess

assert os.getuid()!=0, 'Run as the isolated guest test user, not root'


def run(arguments):
    result=subprocess.run(arguments,capture_output=True,text=True,timeout=120)
    assert result.returncode==0, result.stderr
    return json.loads(result.stdout)


samples=run(['flatpak','run','--system','--command=python3','io.luma.Monitor','-c','''
import json,time
from luma_monitor.host_sampler import HostSampler
s=HostSampler();results=[]
for memory in (False,False,True):
    snapshot=s.sample(memory=memory)
    results.append({'memory':memory,'process_count':len(snapshot['processes']),
                    'measured_cpu':isinstance(snapshot['cpu'],(int,float)),
                    'memory_present':'MemTotal' in snapshot['memory']})
    time.sleep(.2)
print(json.dumps(results))
'''])
assert len(samples)==3 and all(row['process_count']>0 for row in samples), samples
assert samples[1]['measured_cpu'] and samples[2]['measured_cpu'], samples
assert samples[2]['memory_present'], samples

refusal='''
import json
from gi.repository import Gio,GLib
bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
try:
    bus.call_sync('org.projectluma.MonitorHost1','/org/projectluma/MonitorHost1',
                  'org.projectluma.MonitorHost1','Sample',GLib.Variant('(b)',(False,)),
                  GLib.VariantType.new('(s)'),Gio.DBusCallFlags.NONE,15000,None)
except GLib.Error as error:
    name=Gio.DBusError.get_remote_error(error)
    assert name=='org.projectluma.MonitorHost1.Refused',name
    print(json.dumps({'refused':True}))
else:
    raise AssertionError('An unauthorized caller received host activity')
'''
# Clock is genuinely signed and receives explicit transport permission for this
# negative probe. Its different application identity must still be refused.
wrong_app=run(['flatpak','run','--system','--talk-name=org.projectluma.MonitorHost1',
               '--command=python3','org.projectluma.Clock','-c',refusal])
unsigned=run(['python3','-c',refusal])
assert wrong_app['refused'] and unsigned['refused']
print(json.dumps({'signed_monitor_samples':samples,'signed_wrong_app':wrong_app,
                  'unsigned_native':unsigned,'production_authentication':True}))
