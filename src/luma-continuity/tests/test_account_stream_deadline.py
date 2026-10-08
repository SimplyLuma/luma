"""Exercise the elapsed body budget with actual TLS and a trickling peer."""
from datetime import datetime,timedelta,timezone
import ipaddress
from pathlib import Path
import ssl,tempfile,threading,time,unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from luma_continuity.account import AccountAPI,AccountConfig,AccountError


class StreamDeadlineTests(unittest.TestCase):
    def test_real_tls_slow_body_stops_within_budget_plus_read_timeout(self):
        stop=threading.Event();encodings=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                encodings.append(self.headers.get('Accept-Encoding'))
                self.send_response(200);self.send_header('Content-Length','1000');self.end_headers()
                try:
                    while not stop.is_set():
                        self.wfile.write(b' ');self.wfile.flush()
                        if stop.wait(.5):break
                except (BrokenPipeError,ConnectionResetError,ssl.SSLError):pass
            def log_message(self,*args):pass
        with tempfile.TemporaryDirectory() as directory:
            key=ec.generate_private_key(ec.SECP256R1());name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
            now=datetime.now(timezone.utc)
            cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
                .not_valid_after(now+timedelta(hours=1))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
                .sign(key,hashes.SHA256()))
            pem=cert.public_bytes(serialization.Encoding.PEM);p=Path(directory)
            (p/'cert.pem').write_bytes(pem)
            (p/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            (p/'key.pem').chmod(0o600)
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
            tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(p/'cert.pem',p/'key.pem')
            server.socket=tls.wrap_socket(server.socket,server_side=True)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            trust=ssl.create_default_context(cadata=pem.decode())
            client=httpx.Client(verify=trust,timeout=5,trust_env=False)
            api=AccountAPI(AccountConfig('https://identity.example',f'https://127.0.0.1:{server.server_port}','native'),client)
            started=time.monotonic()
            try:
                with self.assertRaises(AccountError) as caught:api.request('GET','/v1/me','synthetic')
                self.assertEqual(caught.exception.code,'service_unavailable')
                elapsed=time.monotonic()-started
                self.assertGreaterEqual(elapsed,9.9);self.assertLess(elapsed,16)
                self.assertEqual(encodings,['identity'])
            finally:
                client.close();stop.set();server.shutdown();server.server_close();thread.join(2)

    def test_compressed_body_is_rejected_without_decompression(self):
        client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,
            headers={'Content-Encoding':'gzip'},stream=httpx.ByteStream(b'synthetic invalid compressed data'))))
        self.addCleanup(client.close)
        api=AccountAPI(AccountConfig('https://identity.example','https://api.example','native'),client)
        with self.assertRaises(AccountError) as caught:api.request('GET','/v1/me','synthetic')
        self.assertEqual(caught.exception.code,'invalid_server_response')
