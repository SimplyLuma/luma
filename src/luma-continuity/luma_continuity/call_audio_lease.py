"""Scoped native IMS PCM lease. No network, mic, or arbitrary D-Bus forwarding.

A dedicated system-bus connection binds one authorization attempt and lease.
Closing it cancels native pending consent through the service's owner-loss path.
All lifecycle methods run on the owning GLib context.
"""
import os
import socket
import time

BUS='net.catcrafts.IMS1'
PATH='/net/catcrafts/IMS1/RemoteAudio'
INTERFACE='net.catcrafts.IMS1.RemoteAudio'
FORMAT={'format':'S16LE','rate':16000,'channels':1,'frame_bytes':640,'lease_ms':5000}


class NativeAudioLease:
    def __init__(self,*,authorized,current_call,dispatch,connection_factory=None,now=time.monotonic):
        from gi.repository import Gio,GLib
        self.Gio,self.GLib=Gio,GLib
        self.authorized,self.current_call,self.dispatch=authorized,current_call,dispatch
        self.connection_factory=connection_factory or self._connect
        self.now=now;self.closed=False;self.connection=None;self.owner=None
        self.token=None;self.native_generation=None;self.call_id=None
        self.uplink=self.downlink=None;self.timer=None;self.subscription=None
        self.pending=Gio.Cancellable();self.renewing=False;self.deadline=0
        self.ready=lambda *_:None;self.failed=lambda:None

    def _connect(self):
        address=self.Gio.dbus_address_get_for_bus_sync(self.Gio.BusType.SYSTEM,None)
        return self.Gio.DBusConnection.new_for_address_sync(address,
            self.Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT|self.Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,None,None)

    def valid(self):
        return not self.closed and self.authorized() and self.current_call()==self.call_id

    def start(self,call_id,ready,failed):
        if self.connection or self.closed:raise RuntimeError('one native lease per session')
        if not isinstance(call_id,str) or not call_id or len(call_id)>256:raise ValueError('native call required')
        self.call_id,self.ready,self.failed=call_id,ready,failed
        if not self.valid():self.close();failed();return
        try:
            self.connection=self.connection_factory()
            self.connection.connect('closed',lambda *_:self.dispatch(self._lost))
            self.subscription=self.connection.signal_subscribe('org.freedesktop.DBus','org.freedesktop.DBus',
                'NameOwnerChanged','/org/freedesktop/DBus',BUS,self.Gio.DBusSignalFlags.NONE,
                lambda *_:self._lost())
            self.connection.call('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','GetNameOwner',
                self.GLib.Variant('(s)',(BUS,)),self.GLib.VariantType.new('(s)'),self.Gio.DBusCallFlags.NONE,
                2000,self.pending,self._owner_ready)
        except Exception:self._lost()

    def _owner_ready(self,connection,result):
        try:
            owner=connection.call_finish(result).unpack()[0]
            if not self.valid():self._lost();return
            self.owner=owner
            connection.call(owner,PATH,INTERFACE,'GetGeneration',self.GLib.Variant('(s)',(self.call_id,)),
                self.GLib.VariantType.new('(s)'),self.Gio.DBusCallFlags.NONE,2000,self.pending,self._generation_ready)
        except Exception:self._lost()

    def _generation_ready(self,connection,result):
        try:
            generation=connection.call_finish(result).unpack()[0]
            if not self.valid() or not isinstance(generation,str) or not 0<len(generation)<=256:
                self._lost();return
            self.native_generation=generation
            connection.call_with_unix_fd_list(self.owner,PATH,INTERFACE,'Acquire',
                self.GLib.Variant('(s)',(generation,)),self.GLib.VariantType.new('(shha{sv})'),
                self.Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION,125000,None,self.pending,self._acquired)
        except Exception:self._lost()

    def _acquired(self,connection,result):
        descriptors=[]
        try:
            response,fd_list=connection.call_with_unix_fd_list_finish(result)
            token,up,down,metadata=response.unpack()
            if (not self.valid() or not isinstance(token,str) or not 0<len(token)<=256
                    or metadata!=FORMAT or up==down or fd_list is None or fd_list.get_length()!=2):
                raise PermissionError('native lease changed')
            for index in (up,down):
                descriptor=fd_list.get(index);descriptors.append(descriptor)
                os.set_inheritable(descriptor,False)
            self.uplink=socket.socket(fileno=descriptors[0]);descriptors.pop(0)
            self.downlink=socket.socket(fileno=descriptors[0]);descriptors.pop(0)
            for stream in (self.uplink,self.downlink):
                if stream.getsockopt(socket.SOL_SOCKET,socket.SO_TYPE)!=socket.SOCK_DGRAM:
                    raise ValueError('PCM datagram required')
                stream.setblocking(False)
            self.token=token;self.deadline=self.now()+4
            self.timer=self.GLib.timeout_add(1500,self._renew)
            self.ready(self)
        except Exception:self._lost()
        finally:
            for descriptor in descriptors:os.close(descriptor)

    def _renew(self):
        if not self.valid() or self.now()>=self.deadline:self._lost();return False
        if self.renewing:return True
        self.renewing=True
        self.connection.call(self.owner,PATH,INTERFACE,'Renew',self.GLib.Variant('(s)',(self.token,)),
            None,self.Gio.DBusCallFlags.NONE,1500,self.pending,self._renewed)
        return True

    def _renewed(self,connection,result):
        self.renewing=False
        try:
            connection.call_finish(result)
            if not self.valid() or self.now()>=self.deadline:self._lost();return
            self.deadline=self.now()+4
        except Exception:self._lost()

    def set_muted(self,muted):
        if type(muted) is not bool or not self.valid() or not self.token:
            raise PermissionError('active audio lease required')
        def done(connection,result):
            try:connection.call_finish(result)
            except Exception:self._lost()
        self.connection.call(self.owner,PATH,INTERFACE,'SetMuted',self.GLib.Variant('(sb)',(self.token,muted)),
            None,self.Gio.DBusCallFlags.NONE,1500,self.pending,done)

    def _lost(self):
        # Content-free stage evidence: never include credentials, SDP, PCM or exception text.
        if not self.closed:
            import logging, sys
            caller=sys._getframe(1).f_code.co_name
            error=sys.exc_info()[0]
            logging.getLogger(__name__).warning('Call audio stopped: component=%s stage=%s exception=%s',
                __name__,caller,error.__name__ if error else 'none')
        if self.closed:return False
        self.close();self.failed();return False

    def close(self):
        if self.closed:return
        self.closed=True;self.pending.cancel()
        if self.timer is not None:self.GLib.source_remove(self.timer);self.timer=None
        for stream in (self.uplink,self.downlink):
            if stream:stream.close()
        self.uplink=self.downlink=None
        if self.connection:
            if self.subscription:self.connection.signal_unsubscribe(self.subscription)
            # The unique bus owner disappearing releases this exact lease and
            # cancels pending consent, including a late Acquire response.
            self.connection.close(None,None,None)
        self.token=None
