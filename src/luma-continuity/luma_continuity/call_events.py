"""Authenticated call-change hints, with no call data or retained commands.

One subscription per paired listener. A hint only triggers a fresh authorized
snapshot over the existing request transport. No timer polls native call state.
"""
import json
import socket
import struct
import threading
import time
from pathlib import Path
from . import transport
from .local import PairedExchange
from .policy import Journal, Denied
from .call_receiver import CallReceiver


def receive_hint(stream):
    # Idle is unbounded and interruptible by shutdown. Once a frame begins,
    # bound its size and completion time independently of idle duration.
    stream.settimeout(None)
    first=stream.recv(1)
    if not first:raise EOFError('event stream closed')
    stream.settimeout(5)
    deadline=time.monotonic()+5
    size,=struct.unpack('!I',first+transport._read(stream,3,deadline))
    if not 0<size<=1024:raise ValueError('event frame bound')
    value=json.loads(transport._read(stream,size,deadline),object_pairs_hook=transport._unique)
    if not isinstance(value,dict):raise ValueError('event object required')
    return value


def shutdown(stream):
    if stream is None:return
    try:stream.shutdown(socket.SHUT_RDWR)
    except OSError:pass
    stream.close()


class CallSubscription:
    def __init__(self,exchange,changed,on_incoming=lambda:None):
        self.exchange,self.changed,self.on_incoming=exchange,changed,on_incoming
        self.stopped=threading.Event();self.stream=None;self.thread=None

    def start(self):
        if self.thread is not None:raise RuntimeError('subscription already started')
        self.thread=threading.Thread(target=self._run,name='connect-call-events',daemon=True)
        self.thread.start()

    def _run(self):
        import secrets
        e=self.exchange
        request=dict(version=1,account=e.account,epoch=e.epoch,id=secrets.token_hex(16),
                     capability='calls.read',expires=int(time.time())+10,payload={'operation':'subscribe'})
        try:
            with e._admit(request):pass
            tls=transport.context(e.directory/'device.pem',e.directory/'device.key',
                                  e.directory/'peers'/(e.peer+'.pem'),server=False)
            raw=socket.create_connection((e.address,e.port),timeout=5)
            self.stream=raw
            if self.stopped.is_set():return
            stream=tls.wrap_socket(raw,do_handshake_on_connect=False);self.stream=stream
            stream.settimeout(5);stream.do_handshake();transport.authenticate(stream,e.peer)
            with e._admit(request):transport.send(stream,request)
            sequence=-1
            while not self.stopped.is_set():
                event=receive_hint(stream)
                if (set(event)!={'type','account','epoch','sequence','incoming'} or event['type']!='calls-changed'
                        or event['account']!=e.account or event['epoch']!=e.epoch
                        or type(event['incoming']) is not bool or type(event['sequence']) is not int or event['sequence']<=sequence):
                    raise ValueError('invalid call event')
                with e._admit(request):pass
                sequence=event['sequence']
                if not self.stopped.is_set():
                    self.changed(True)
                    if event["incoming"]:self.on_incoming()
        except Exception:pass  # no endpoint, recipient, or private error logging
        finally:
            shutdown(self.stream)
            if not self.stopped.is_set():self.changed(False)

    def stop(self):
        self.stopped.set();shutdown(self.stream)


class CallExchange(PairedExchange):
    def __init__(self,*args,account,**kwargs):
        super().__init__(*args,**kwargs);self.account=account

    def __call__(self,request):
        if request.get('capability') not in {'calls.read','calls.control'} or len(transport.encode(request))>8192:
            raise Denied('call operation required')
        return super().__call__(request)


class CallListener:
    """Two bounded network workers: one event subscription, one command.

    The native adapter runs under one lock and per-operation grant transaction.
    Native bus-owner replacement tears down subscriptions and invalidates all
    tokens, including generations; it never retargets an admitted command.
    """
    def __init__(self,directory,peer,*,address,port,authorized,adapter_factory):
        import ipaddress
        endpoint=ipaddress.ip_address(address)
        if endpoint.is_unspecified or endpoint.is_multicast or type(port) is not int or not 1024<=port<=65535:
            raise ValueError('explicit unprivileged unicast endpoint required')
        self.directory,self.peer=Path(directory),peer
        self.address,self.port=address,port
        self.authorized,self.adapter_factory=authorized,adapter_factory
        self.lock=threading.RLock();self.event=threading.Event();self.stopped=threading.Event()
        self.streams=set();self.guard=threading.Lock();self.slots=threading.BoundedSemaphore(2)
        self.subscription=False;self.listener=None;self.thread=None;self.adapter=None
        self.sequence=0;self.owner_generation=0

    def changed(self,*_):
        with self.lock:self.sequence+=1
        self.event.set()

    def owner_changed(self,*_):
        with self.lock:
            self.owner_generation+=1
            if self.adapter:self.adapter.invalidate()
        with self.guard:streams=list(self.streams)
        for stream in streams:shutdown(stream)
        self.event.set()

    def start(self):
        if self.thread is not None:raise RuntimeError('listener already started')
        if not self.authorized():raise PermissionError('call sharing unavailable')
        self.tls=transport.context(self.directory/'device.pem',self.directory/'device.key',
                                  self.directory/'peers'/(self.peer+'.pem'),server=True)
        listener=socket.socket(socket.AF_INET6 if ':' in self.address else socket.AF_INET)
        listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:listener.bind((self.address,self.port));listener.listen(2)
        except BaseException:listener.close();raise
        self.listener=listener
        self.adapter,self.cleanup=self.adapter_factory(self.changed,self.owner_changed)
        self.thread=threading.Thread(target=self._run,name='connect-call-listener',daemon=True)
        self.thread.start()

    def _run(self):
        try:
            while not self.stopped.is_set():
                try:raw,_=self.listener.accept()
                except OSError:break
                if not self.slots.acquire(blocking=False):raw.close();continue
                with self.guard:self.streams.add(raw)
                threading.Thread(target=self._serve,args=(raw,),name='connect-call-peer',daemon=True).start()
        finally:shutdown(self.listener)

    def _serve(self,raw):
        stream=raw;journal=None;subscribed=False
        try:
            stream=self.tls.wrap_socket(raw,server_side=True,do_handshake_on_connect=False)
            with self.guard:self.streams.discard(raw);self.streams.add(stream)
            stream.settimeout(5);stream.do_handshake();transport.authenticate(stream,self.peer)
            request=transport.receive(stream)
            journal=Journal(self.directory/'continuity.db')
            receiver=CallReceiver(journal,self.adapter,authorized=lambda:not self.stopped.is_set() and self.authorized())
            with self.lock:
                if self.stopped.is_set():return
                owner_generation=self.owner_generation
                if request.get('payload')=={'operation':'subscribe'}:
                    # Admit the exact subscription envelope via the same read
                    # capability boundary without minting control tokens.
                    if request.get('capability')!='calls.read':raise Denied('read subscription required')
                    probe=CallReceiver(journal,lambda *_:{},authorized=self.authorized)
                    probe.handle(self.peer,request)
                    if self.subscription:raise Denied('subscription already active')
                    self.subscription=True;subscribed=True
                else:
                    transport.send(stream,receiver.handle(self.peer,request));return
            disconnected=threading.Event()
            def watch_close():
                try:
                    # No client frames are allowed after subscribing.
                    stream.settimeout(None);stream.recv(1)
                except OSError:pass
                finally:disconnected.set();self.event.set()
            threading.Thread(target=watch_close,name='connect-call-disconnect',daemon=True).start()
            sequence=-1
            while not self.stopped.is_set() and not disconnected.is_set():
                with self.lock:
                    if owner_generation!=self.owner_generation:return
                    if not self.authorized():return
                    # Recheck current grants for every publication; expiration
                    # was admission-only, this is a new event, not a replay.
                    request['expires']=int(time.time())+10
                    probe.handle(self.peer,request)
                    current=self.sequence
                    incoming=self.adapter.incoming()
                    self.event.clear()
                if current!=sequence:
                    deadline=threading.Timer(5,lambda:shutdown(stream));deadline.daemon=True;deadline.start()
                    try:transport.send(stream,dict(type='calls-changed',account=request['account'],
                                               epoch=request['epoch'],sequence=current,incoming=incoming))
                    finally:deadline.cancel()
                    sequence=current
                self.event.wait()
        except Exception:pass
        finally:
            if subscribed:
                with self.lock:self.subscription=False
            if journal:journal.close()
            with self.guard:self.streams.discard(stream);self.streams.discard(raw)
            shutdown(stream);self.slots.release()

    @property
    def running(self):return bool(self.thread and self.thread.is_alive() and not self.stopped.is_set())

    def stop(self):
        self.stopped.set();self.event.set();shutdown(self.listener)
        with self.guard:streams=list(self.streams)
        for stream in streams:shutdown(stream)
        with self.lock:
            if self.adapter:self.adapter.invalidate()
        if hasattr(self,'cleanup'):self.cleanup()
