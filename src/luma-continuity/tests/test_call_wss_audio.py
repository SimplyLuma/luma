"""Real native FD/WebRTC/desktop PCM loop with pinned TLS signaling over WSS."""
import asyncio
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import test_call_desktop_integration as media_fixture
from luma_continuity.call_audio_session import CallAudioSession
from luma_continuity.call_relay_channel import RelayJSONChannel


class WSSAudioTests(unittest.TestCase):
    def test_actual_wss_signaling_and_native_desktop_media_roundtrip(self):
        from websockets.asyncio.server import serve
        from websockets.asyncio.client import connect
        with tempfile.TemporaryDirectory(prefix='connect-wss-audio-') as temporary:
            root=Path(temporary);cert=root/'outer.pem';key=root/'outer.key'
            subprocess.run(['openssl','req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256',
                '-nodes','-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost',
                '-keyout',str(key),'-out',str(cert)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            key.chmod(0o600)
            server_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server_context.load_cert_chain(cert,key)
            client_context=ssl.create_default_context(cafile=str(cert))
            holder={};ready=threading.Event();errors=[]
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
                        holder.update(loop=asyncio.get_running_loop(),stop=stop,port=service.sockets[0].getsockname()[1])
                        ready.set();await stop.wait()
                try:asyncio.run(run())
                except Exception as error:errors.append(type(error).__name__);ready.set()
            worker=threading.Thread(target=server);worker.start()
            self.assertTrue(ready.wait(5));self.assertEqual(errors,[])
            try:
                async def websocket():
                    return await connect('wss://localhost:'+str(holder['port']),ssl=client_context,proxy=None,
                        max_size=65536,max_queue=4,compression=None,open_timeout=5,close_timeout=1)
                def session(_stream,directory,peer,**kwargs):
                    incoming=kwargs['incoming']
                    return CallAudioSession(None,directory,peer,**kwargs,
                        channel_factory=lambda **callbacks:RelayJSONChannel(directory,peer,server=incoming,
                            websocket_factory=websocket,**callbacks))
                with patch.object(media_fixture,'CallAudioSession',session):
                    fixture=media_fixture.DesktopCallIntegrationTests()
                    fixture.test_native_to_desktop_roundtrip_and_revocation()
            finally:
                holder['loop'].call_soon_threadsafe(holder['stop'].set);worker.join(5)
                self.assertFalse(worker.is_alive())
