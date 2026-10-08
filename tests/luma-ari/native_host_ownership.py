# SPDX-License-Identifier: Apache-2.0
"""Two real private-bus clients: actual native authentication, approval custody/unicast.

The agent is a no-tool stand-in; this proves transport/custody, not model actions
or a signed Flatpak deployment. Run only as an ordinary fixture user.
"""
import os, threading, time, json
from collections import deque
from types import SimpleNamespace
from gi.repository import Gio, GLib
from ari.service import Daemon, INTERFACE
from ari import BUS_NAME, OBJECT_PATH
from ari.host_limits import Owners, Admission
assert os.getuid()!=0
loop=GLib.MainLoop(); thread=threading.Thread(target=loop.run,daemon=True);thread.start()
bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
d=Daemon.__new__(Daemon);d.admission=Admission();d.owners=Owners();d._events=deque();d._event_lock=threading.Lock();d._event_source=None;d._closed=False
d._model_lock=threading.RLock();d._model_mutation=None;d._mutation_owner=None
d.requests={};d.downloads={};d.connection=bus;d.settings=None
approved=[]
d.agent=SimpleNamespace(approve=lambda key,value: approved.append((key,value)) or True)
registration=bus.register_object(OBJECT_PATH,Gio.DBusNodeInfo.new_for_xml(INTERFACE).interfaces[0],d.handle,None,None)
bus.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','RequestName',GLib.Variant('(su)',(BUS_NAME,0)),None,Gio.DBusCallFlags.NONE,5000,None)
flags=Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT|Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
clients=[Gio.DBusConnection.new_for_address_sync(os.environ['DBUS_SESSION_BUS_ADDRESS'],flags,None,None) for _ in range(2)]
received=[[],[]]
subscriptions=[]
for i,client in enumerate(clients):
 def signal(_c,_s,_p,_f,_n,parameters,index=i):received[index].append(parameters.unpack())
 subscriptions.append(client.signal_subscribe(BUS_NAME,'org.projectluma.Ari1','Event',OBJECT_PATH,None,Gio.DBusSignalFlags.NONE,signal))
owner=clients[0].get_unique_name();d.owners.add('request',owner);d.owners.event('request',{'type':'approval','approval':'approval'})
def call(client,method,args):
 return client.call_sync(BUS_NAME,OBJECT_PATH,'org.projectluma.Ari1',method,args,None,Gio.DBusCallFlags.NONE,5000,None)
try:
 try:call(clients[1],'Approve',GLib.Variant('(sb)',('approval',True)))
 except GLib.Error as error:assert Gio.DBusError.get_remote_error(error)=='org.projectluma.Ari1.Refused',error
 else:raise AssertionError('Foreign client approved another request')
 assert not approved
 assert call(clients[0],'Approve',GLib.Variant('(sb)',('approval',True))).unpack()==(True,)
 assert approved==[('approval',True)]
 d.emit('request',{'type':'text','text':'private answer'})
 deadline=time.monotonic()+5
 while not received[0] and time.monotonic()<deadline:time.sleep(.01)
 assert received[0] and json.loads(received[0][0][1])['text']=='private answer'
 time.sleep(.15);assert not received[1],received
 print('ACTUAL TWO-CLIENT NATIVE AUTH/FOREIGN APPROVAL REFUSAL/OWNER APPROVAL/UNICAST PASS')
finally:
 for c,s in zip(clients,subscriptions):c.signal_unsubscribe(s);c.close_sync(None)
 bus.unregister_object(registration);d.admission.close();loop.quit();thread.join(3)
