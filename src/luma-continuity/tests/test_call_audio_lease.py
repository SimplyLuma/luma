"""Actual private GIO FD transfer, synthetic native service, no modem or audio."""
import socket
import time
import unittest
import gi
gi.require_version('Gio','2.0')
from gi.repository import Gio,GLib
from luma_continuity.call_audio_lease import NativeAudioLease,BUS,PATH,INTERFACE,FORMAT


class AudioLeaseTests(unittest.TestCase):
    def setUp(self):
        self.test_bus=Gio.TestDBus.new(Gio.TestDBusFlags.NONE);self.test_bus.up()
        address=self.test_bus.get_bus_address()
        self.connect=lambda:Gio.DBusConnection.new_for_address_sync(address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT|Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,None,None)
        self.service=self.connect()
        self.service.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','RequestName',
            GLib.Variant('(su)',(BUS,0)),None,Gio.DBusCallFlags.NONE,1000,None)
        xml='''<node><interface name="net.catcrafts.IMS1.RemoteAudio">
        <method name="GetGeneration"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
        <method name="Acquire"><arg type="s" direction="in"/><arg type="s" direction="out"/><arg type="h" direction="out"/><arg type="h" direction="out"/><arg type="a{sv}" direction="out"/></method>
        <method name="Renew"><arg type="s" direction="in"/></method>
        <method name="SetMuted"><arg type="s" direction="in"/><arg type="b" direction="in"/></method>
        </interface></node>'''
        self.methods=[];self.allowed=True;self.ready=[];self.failed=[];self.hold=False;self.pending=None;self.invalid=False
        self.up,self.native_up=socket.socketpair(type=socket.SOCK_DGRAM)
        self.down,self.native_down=socket.socketpair(type=socket.SOCK_DGRAM)
        def call(_c,_sender,_p,_i,method,params,invocation):
            self.methods.append(method)
            if method=='GetGeneration':invocation.return_value(GLib.Variant('(s)',('native-generation',)))
            elif method=='Acquire':
                self.pending=invocation
                if not self.hold:self.complete()
            else:invocation.return_value(GLib.Variant('()',()))
        self.registration=self.service.register_object(PATH,Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0],call,None,None)
        self.lease=NativeAudioLease(authorized=lambda:self.allowed,current_call=lambda:'native-call',
            dispatch=GLib.idle_add,connection_factory=self.connect)

    def complete(self):
        descriptors=Gio.UnixFDList.new();up=descriptors.append(self.up.fileno());down=descriptors.append(self.down.fileno())
        metadata={k:GLib.Variant('s' if isinstance(v,str) else 'u',v) for k,v in FORMAT.items()}
        if self.invalid:metadata['rate']=GLib.Variant('u',48000)
        self.pending.return_value_with_unix_fd_list(GLib.Variant('(shha{sv})',('lease-token',up,down,metadata)),descriptors)
        self.pending=None

    def wait(self,predicate):
        deadline=time.monotonic()+4;context=GLib.MainContext.default()
        while not predicate():
            if time.monotonic()>deadline:raise AssertionError('private audio lease timeout')
            while context.pending():context.iteration(False)
            time.sleep(.002)

    def start(self):self.lease.start('native-call',self.ready.append,lambda:self.failed.append(True))

    def tearDown(self):
        self.lease.close()
        if self.pending:self.complete()
        self.service.unregister_object(self.registration);self.service.close_sync(None)
        for stream in (self.up,self.down,self.native_up,self.native_down):stream.close()
        context=GLib.MainContext.default()
        for _ in range(20):
            while context.pending():context.iteration(False)
            time.sleep(.002)
        self.lease.connection=None;self.service=None
        self.test_bus.down()

    def test_directions_and_renewal_use_same_unique_owner(self):
        self.start();self.wait(lambda:self.ready)
        self.lease.uplink.send(b'u'*640);self.assertEqual(self.native_up.recv(640),b'u'*640)
        self.native_down.send(b'd'*640);self.assertEqual(self.lease.downlink.recv(640),b'd'*640)
        self.lease._renew();self.wait(lambda:not self.lease.renewing)
        self.assertIn('Renew',self.methods)
        self.lease.set_muted(True);self.wait(lambda:'SetMuted' in self.methods)

    def test_revocation_closes_fds_and_caller_connection(self):
        self.start();self.wait(lambda:self.ready)
        stream=self.lease.uplink;self.allowed=False;self.lease._renew()
        self.assertEqual(stream.fileno(),-1);self.assertTrue(self.lease.closed);self.assertEqual(self.failed,[True])

    def test_cancelled_acquisition_cannot_publish_late_fds(self):
        self.hold=True;self.start();self.wait(lambda:self.pending is not None)
        self.lease.close();self.complete()
        self.assertEqual(self.ready,[]);self.assertTrue(self.lease.closed)

    def test_wrong_pcm_geometry_fails_closed(self):
        self.invalid=True;self.start();self.wait(lambda:self.failed)
        self.assertEqual(self.ready,[]);self.assertIsNone(self.lease.uplink)


if __name__=='__main__':unittest.main()
