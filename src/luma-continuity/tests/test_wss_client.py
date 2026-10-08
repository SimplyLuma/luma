"""Real maintained WebSocket client over disposable localhost TLS."""
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from luma_continuity.relay import connect_wss

class WSSClientTests(unittest.TestCase):
    def test_tls_carrier_headers_and_hostname_validation(self):
        from websockets.sync.server import serve
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cert=root/'server.pem';key=root/'server.key'
            subprocess.run(['openssl','req','-new','-x509','-newkey','rsa:2048','-nodes',
                '-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost',
                '-addext','subjectAltName=DNS:localhost'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
            received=[]
            def echo(connection):
                received.append(dict(connection.request.headers))
                connection.send(connection.recv())
            server=serve(echo,'127.0.0.1',0,ssl=context,open_timeout=10)
            worker=threading.Thread(target=server.serve_forever);worker.start()
            port=server.socket.getsockname()[1]
            try:
                # Test-only CA injection. Production load_default_certs still
                # uses system trust; hostname verification is never disabled.
                with patch.object(ssl.SSLContext,'load_default_certs',lambda ctx:ctx.load_verify_locations(cert)):
                    with connect_wss(f'wss://localhost:{port}/v1/relay/synthetic','synthetic-ticket') as connection:
                        connection.send(b'synthetic encrypted carrier payload')
                        self.assertEqual(connection.recv(),b'synthetic encrypted carrier payload')
                    with self.assertRaises(ssl.SSLCertVerificationError):
                        connect_wss(f'wss://127.0.0.1:{port}/v1/relay/synthetic','synthetic-ticket')
                self.assertEqual(received[0]['x-luma-relay-ticket'],'synthetic-ticket')
                self.assertNotIn('origin',received[0]);self.assertNotIn('authorization',received[0])
            finally:server.shutdown();worker.join(5)
            self.assertFalse(worker.is_alive())

    def test_plaintext_or_query_carrier_rejected(self):
        for url in ('ws://localhost/relay','wss://localhost/relay?ticket=secret'):
            with self.assertRaises(ValueError):connect_wss(url,'fixture')
