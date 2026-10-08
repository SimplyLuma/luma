"""Actual strict localhost WSS carrying mutually pinned native TLS signals."""
import asyncio
from pathlib import Path
import ssl
import subprocess
import threading
import unittest
import test_call_signaling as signal_fixture
from luma_continuity.call_relay_channel import RelayJSONChannel
from luma_continuity.call_signaling import PairedAudioSignals


class CallRelayChannelTests(unittest.TestCase):
    def test_strict_wss_inner_pin_and_audio_session_signals(self):
        from websockets.asyncio.server import serve
        from websockets.asyncio.client import connect
        fixture=signal_fixture.AudioSignalTests();fixture.setUp()
        root=Path(fixture.tmp.name);cert=root/'outer.pem';key=root/'outer.key'
        subprocess.run(['openssl','req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256',
            '-nodes','-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost',
            '-keyout',str(key),'-out',str(cert)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        key.chmod(0o600)
        server_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server_context.load_cert_chain(cert,key)
        client_context=ssl.create_default_context(cafile=str(cert))
        ready=threading.Event();holder={};errors=[];channels=[]
        def server():
            async def run():
                peers=[];paired=asyncio.Event();stop=asyncio.Event()
                async def handler(ws):
                    index=len(peers);peers.append(ws)
                    if len(peers)==2:paired.set()
                    await paired.wait()
                    try:
                        async for data in ws:await peers[1-index].send(data)
                    finally:await peers[1-index].close()
                async with serve(handler,'127.0.0.1',0,ssl=server_context,max_size=65536,compression=None) as service:
                    holder.update(loop=asyncio.get_running_loop(),stop=stop,port=service.sockets[0].getsockname()[1]);ready.set()
                    await stop.wait()
            try:asyncio.run(run())
            except Exception as error:errors.append(type(error).__name__);ready.set()
        thread=threading.Thread(target=server);thread.start();self.assertTrue(ready.wait(5));self.assertFalse(errors)
        try:
            async def websocket():
                return await connect('wss://localhost:'+str(holder['port']),ssl=client_context,proxy=None,
                    max_size=65536,max_queue=4,compression=None,open_timeout=5,close_timeout=1)
            for i in range(2):
                factory=lambda i=i,**kw:RelayJSONChannel(fixture.dirs[i],fixture.pins[1-i],server=bool(i),websocket_factory=websocket,**kw)
                channels.append(PairedAudioSignals(None,fixture.dirs[i],fixture.pins[1-i],account='account',epoch='e'*32,
                    call='c'*32,session='a'*32,incoming=bool(i),authorized=lambda:fixture.allowed,
                    receive=fixture.received[i].append,failed=lambda:fixture.failures.append(True),
                    dispatch=fixture.callbacks.put,channel_factory=factory))
            for channel in channels:channel.start()
            channels[0].send({'type':'offer','sdp':'synthetic'});channels[1].send({'type':'answer','sdp':'synthetic'})
            fixture.wait(lambda:all(fixture.received) or fixture.failures)
            self.assertFalse(fixture.failures);self.assertTrue(all(fixture.received))
            fixture.allowed=False
            with self.assertRaises(PermissionError):channels[0].send({'type':'ice','index':0,'candidate':'synthetic'})
        finally:
            for channel in channels:channel.close()
            for channel in channels:channel.worker.join(5);self.assertFalse(channel.worker.is_alive())
            holder['loop'].call_soon_threadsafe(holder['stop'].set);thread.join(5)
            self.assertFalse(thread.is_alive());self.assertTrue(fixture.doCleanups())
