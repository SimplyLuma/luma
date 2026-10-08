"""Real TLS SSE cancellation retains its fd until the I/O owner exits."""
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import httpx
from luma_continuity.bootstrap import create_identity
from luma_continuity import transport
from luma_continuity.account import AccountAPI,AccountConfig
from luma_continuity.call_relay_api import CallRelayAPI


class EventCancellationTLSTests(unittest.TestCase):
    def test_cancel_blocked_tls_keeps_descriptor_and_closes_on_owner(self):
        release=threading.Event();observed=threading.Event();errors=[];sockets=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                self.wfile.write(b'event: snapshot\nid: first\ndata: {}\n\n');self.wfile.flush()
                release.wait(3)
                try:self.wfile.write(b': keepalive\n\n');self.wfile.flush()
                except OSError:pass
            def log_message(self,*args):pass
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);left=root/'left';right=root/'right'
            create_identity(left);create_identity(right)
            client_tls=transport.context(left/'device.pem',left/'device.key',right/'device.pem',server=False)
            server_tls=transport.context(right/'device.pem',right/'device.key',left/'device.pem',server=True)
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
            server.socket=server_tls.wrap_socket(server.socket,server_side=True)
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            client=httpx.Client(verify=client_tls,trust_env=False,event_hooks={'response':[
                lambda response:sockets.append(response.extensions['network_stream'].get_extra_info('socket'))]})
            api=AccountAPI(AccountConfig('https://identity.example',f'https://127.0.0.1:{server.server_port}','native'),client)
            relay=CallRelayAPI(api,'11111111-1111-4111-8111-111111111111',bearer=lambda:'synthetic',authorized=lambda:True)
            def consume():
                try:
                    for event in relay.events():observed.set()
                except Exception as error:errors.append(type(error).__name__)
            reader=threading.Thread(target=consume);reader.start()
            try:
                self.assertTrue(observed.wait(3))
                original=sockets[0].fileno();self.assertGreaterEqual(original,0)
                relay.cancel_events()
                self.assertEqual(sockets[0].fileno(),original)
                canary=root/'canary';baseline=b'CANARY-'*256
                fd=os.open(canary,os.O_CREAT|os.O_RDWR,0o600)
                try:
                    self.assertNotEqual(fd,original);os.write(fd,baseline);os.lseek(fd,5,os.SEEK_SET)
                    release.set();reader.join(3);self.assertFalse(reader.is_alive())
                finally:os.close(fd)
                self.assertEqual(canary.read_bytes(),baseline);self.assertEqual(errors,[])
                self.assertEqual(sockets[0].fileno(),-1)
            finally:
                release.set();reader.join(4);client.close();server.shutdown();server.server_close();worker.join(2)
