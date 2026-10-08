"""Single asyncio owner for native mutual TLS over an opaque async WebSocket.

The outer authenticated WSS factory belongs to the account/session owner. This
adapter retains native certificate/ALPN pinning and bounded volatile framing;
it cannot create a session, retrieve credentials, retry or grant capabilities.
"""
import asyncio
import json
import logging
from pathlib import Path
import queue
import ssl
import struct
import threading
import time
from . import transport


class RelayJSONChannel:
    def __init__(self,directory,peer,*,server,websocket_factory,check,receive,failed,ready=lambda:None):
        self.directory,self.peer=Path(directory),peer
        self.server,self.factory=server,websocket_factory
        self.check,self.receive,self.failed,self.ready=check,receive,failed,ready
        self.closed=threading.Event();self.queue=queue.Queue(maxsize=36)
        self.loop=self.task=self.wake=None
        self.phase='admission'
        self.worker=threading.Thread(target=self._run,name='connect-call-relay',daemon=True)
        self._allowed()

    def _allowed(self):
        if self.closed.is_set():raise PermissionError('call relay closed')
        self.check()

    def start(self):self._allowed();self.worker.start()

    def send(self,value):
        self._allowed();raw=transport.encode(value)
        if len(raw)>20476:raise ValueError('call frame bound')
        self.queue.put_nowait(struct.pack('!I',len(raw))+raw)
        if self.loop and self.wake:self.loop.call_soon_threadsafe(self.wake.set)

    def _run(self):
        try:asyncio.run(self._main())
        except BaseException as error:
            if not self.closed.is_set():
                logging.getLogger(__name__).warning('Calls channel failed at %s (%s; status=%s; retry_after=%s)',self.phase,type(error).__name__,getattr(error,'status',None),getattr(error,'retry_after',None))
                self.closed.set();self.failed()

    async def _main(self):
        self.loop=asyncio.get_running_loop();self.task=asyncio.current_task();self.wake=asyncio.Event()
        self._allowed();self.phase='carrier';ws=await asyncio.wait_for(self.factory(),20)
        rx=tx=None
        try:
            self.phase='mutual_tls'
            context=transport.context(self.directory/'device.pem',self.directory/'device.key',
                self.directory/'peers'/(self.peer+'.pem'),server=self.server)
            incoming,outgoing=ssl.MemoryBIO(),ssl.MemoryBIO()
            tls=context.wrap_bio(incoming,outgoing,server_side=self.server)
            async def flush():
                while outgoing.pending:
                    self._allowed();await asyncio.wait_for(ws.send(outgoing.read(65536)),5)
            def feed(data):
                if not isinstance(data,bytes) or not 0<len(data)<=65536:raise ValueError('invalid opaque relay frame')
                incoming.write(data)
            async def handshake():
                while True:
                    self._allowed()
                    try:tls.do_handshake();await flush();break
                    except ssl.SSLWantReadError:
                        await flush();feed(await ws.recv())
                transport.authenticate(tls,self.peer);self._allowed()
            await asyncio.wait_for(handshake(),20)
            self.ready();self.phase='frames'
            async def next_output():
                while True:
                    self._allowed()
                    try:return self.queue.get_nowait()
                    except queue.Empty:
                        self.wake.clear()
                        if not self.queue.empty():continue
                        await self.wake.wait()
            rx=asyncio.create_task(ws.recv());tx=asyncio.create_task(next_output())
            buffer=bytearray();plain=b'';read_deadline=write_deadline=None
            while not self.closed.is_set():
                deadlines=[d for d in (read_deadline,write_deadline) if d is not None]
                timeout=max(0,min(deadlines)-time.monotonic()) if deadlines else None
                tasks={rx}|({tx} if tx else set())
                done,_=await asyncio.wait(tasks,timeout=timeout,return_when=asyncio.FIRST_COMPLETED)
                if not done:raise TimeoutError('call relay frame deadline')
                self._allowed()
                if rx in done:
                    feed(rx.result());rx=asyncio.create_task(ws.recv())
                    while True:
                        try:data=tls.read(20480-len(buffer))
                        except ssl.SSLWantReadError:break
                        if not data:raise EOFError('native call TLS closed')
                        if not buffer:read_deadline=time.monotonic()+5
                        buffer.extend(data)
                        while len(buffer)>=4:
                            size=struct.unpack('!I',buffer[:4])[0]
                            if not 0<size<=20476:raise ValueError('call relay frame bound')
                            if len(buffer)<size+4:break
                            value=json.loads(buffer[4:size+4],object_pairs_hook=transport._unique)
                            del buffer[:size+4];self._allowed();self.receive(value)
                            read_deadline=time.monotonic()+5 if buffer else None
                if tx in done:
                    plain=tx.result();tx=None;write_deadline=time.monotonic()+5
                while plain:
                    self._allowed()
                    try:plain=plain[tls.write(plain):]
                    except (ssl.SSLWantReadError,ssl.SSLWantWriteError):break
                await flush()
                if not plain and tx is None:
                    write_deadline=None;tx=asyncio.create_task(next_output())
        finally:
            for task in (rx,tx):
                if task:task.cancel()
            await asyncio.gather(*(t for t in (rx,tx) if t),return_exceptions=True)
            try:await asyncio.wait_for(ws.close(),3)
            except BaseException:pass
            self.loop=self.task=self.wake=None

    def close(self):
        self.closed.set()
        if self.loop and self.task:
            try:self.loop.call_soon_threadsafe(self.task.cancel)
            except RuntimeError:pass
