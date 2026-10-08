"""Single-owner nonblocking JSON channel over established mutually pinned TLS.

Call protocol owners supply admission and frame validation. No polling while
idle, no credentials, device discovery, native commands or durable queues.
"""
import json
import queue
import select
import socket
import ssl
import struct
import threading
import time
from . import transport


class PinnedJSONChannel:
    def __init__(self,stream,peer,*,check,receive,failed):
        transport.authenticate(stream,peer)
        self.stream,self.check,self.receive,self.failed=stream,check,receive,failed
        self.closed=threading.Event();self.queue=queue.Queue(maxsize=36)
        self._allowed()
        self.wake_read,self.wake_write=socket.socketpair()
        self.wake_read.setblocking(False);self.wake_write.setblocking(False)
        self.worker=threading.Thread(target=self._run,name='connect-call-channel',daemon=True)

    def _allowed(self):
        if self.closed.is_set():raise PermissionError('call channel closed')
        self.check()

    def start(self):
        self._allowed();self.stream.setblocking(False);self.worker.start()

    def send(self,value):
        self._allowed();raw=transport.encode(value)
        if len(raw)>20476:raise ValueError('call frame bound')
        self.queue.put_nowait(raw)
        try:self.wake_write.send(b'w')
        except BlockingIOError:pass

    def _run(self):
        # Exactly one thread owns TLS I/O. A socketpair wakes queued writes;
        # idle has no polling, while partial reads and writes have deadlines.
        incoming=bytearray();outgoing=b'';read_deadline=write_deadline=None
        send_wants_read=False;read_wants_write=False
        try:
            while not self.closed.is_set():
                if not outgoing:
                    try:
                        raw=self.queue.get_nowait()
                        outgoing=struct.pack('!I',len(raw))+raw;write_deadline=time.monotonic()+5
                    except queue.Empty:pass
                now=time.monotonic();deadlines=[d for d in (read_deadline,write_deadline) if d is not None]
                if deadlines and min(deadlines)<=now:raise TimeoutError('audio signaling deadline')
                timeout=max(0,min(deadlines)-now) if deadlines else None
                if self.stream.pending():timeout=0
                readable,writable,_=select.select([self.stream,self.wake_read],
                    [self.stream] if (outgoing and not send_wants_read) or read_wants_write else [],[],timeout)
                if self.wake_read in readable:
                    try:self.wake_read.recv(4096)
                    except BlockingIOError:pass
                if outgoing and (self.stream in writable or send_wants_read and self.stream in readable):
                    self._allowed()
                    try:
                        sent=self.stream.send(outgoing);outgoing=outgoing[sent:]
                        send_wants_read=False
                        if not outgoing:write_deadline=None
                    except ssl.SSLWantReadError:send_wants_read=True
                    except ssl.SSLWantWriteError:send_wants_read=False
                if self.stream in readable or self.stream.pending() or read_wants_write and self.stream in writable:
                    try:
                        data=self.stream.recv(20480-len(incoming));read_wants_write=False
                    except ssl.SSLWantReadError:read_wants_write=False;continue
                    except ssl.SSLWantWriteError:read_wants_write=True;continue
                    if not data:raise EOFError('audio signaling closed')
                    if not incoming:read_deadline=time.monotonic()+5
                    incoming.extend(data)
                    while len(incoming)>=4:
                        size=struct.unpack('!I',incoming[:4])[0]
                        if not 0<size<=20476:raise ValueError('audio signaling frame bound')
                        if len(incoming)<size+4:break
                        value=json.loads(incoming[4:size+4],object_pairs_hook=transport._unique)
                        del incoming[:size+4];self._allowed();self.receive(value)
                        read_deadline=time.monotonic()+5 if incoming else None
        except Exception:self._lost()
        finally:
            self.stream.close();self.wake_read.close();self.wake_write.close()

    def _lost(self):
        if self.closed.is_set():return
        self.close();self.failed()

    def close(self):
        self.closed.set()
        try:self.wake_write.send(b'c')
        except OSError:pass
        if self.worker.ident is None:
            self.stream.close();self.wake_read.close();self.wake_write.close()
