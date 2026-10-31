#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the real curl verifier across origin HTTP and media HTTPS hosts."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import shlex
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
OBJECT = 'os/repo/objects/ab/' + 'c' * 62 + '.commit'

class OriginRedirectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='luma-origin-redirect-')
        cls.work = Path(cls.temp.name)
        cls.cert, cls.key = cls.work/'cert.pem', cls.work/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes',
                        '-keyout',str(cls.key),'-out',str(cls.cert),'-days','1',
                        '-subj','/CN=media.simplyluma.com',
                        '-addext','subjectAltName=DNS:media.simplyluma.com'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        source = (ROOT/'scripts/os/lib/sync-origin.sh').read_text()
        cls.functions = source[source.index('http_request() {'):source.index('# verify_repo SOURCE')]

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def exercise(self, method='GET', missing=False, destination=None, path=OBJECT):
        requests=[]
        body=b'authenticated immutable fixture bytes\n'
        class Media(BaseHTTPRequestHandler):
            def respond(self, head=False):
                requests.append(('media', self.command, self.headers.get('Host')))
                # The origin's custom virtual-host header must not reach R2.
                code=421 if self.headers.get('Host')!='media.simplyluma.com' else (404 if missing else 200)
                self.send_response(code);self.send_header("Content-Length",str(len(body)));self.end_headers()
                if not head:self.wfile.write(body)
            def do_GET(self):self.respond()
            def do_HEAD(self):self.respond(True)
            def log_message(self,*args):pass
        class Origin(BaseHTTPRequestHandler):
            def request(self):
                requests.append(('origin', self.command, self.headers.get('Host')))
                if self.headers.get('Host')!='fixture.invalid':
                    self.send_response(421);self.end_headers();return
                self.send_response(302)
                self.send_header('Location',destination or 'https://media.simplyluma.com/'+path)
                self.end_headers()
            do_GET=request
            do_HEAD=request
            def log_message(self,*args):pass
        media=ThreadingHTTPServer(('127.0.0.1',0),Media)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(self.cert,self.key)
        media.socket=context.wrap_socket(media.socket,server_side=True)
        origin=ThreadingHTTPServer(('127.0.0.1',0),Origin)
        servers=[media,origin];threads=[]
        for server in servers:
            t=threading.Thread(target=server.serve_forever,daemon=True);t.start();threads.append(t)
        with tempfile.TemporaryDirectory(dir=self.work) as folder:
            work=Path(folder);bin_dir=work/'bin';bin_dir.mkdir()
            curl=bin_dir/'curl'
            # Route only the official media TLS name to the local test server.
            # curl itself still performs the real HTTP requests, TLS validation,
            # status checks and Host selection used by the shipping verifier.
            curl.write_text('#!/bin/sh\nexec '+shlex.join([shutil.which('curl'),
                '--noproxy','*','--cacert',str(self.cert),'--connect-to',
                f'media.simplyluma.com:443:127.0.0.1:{media.server_port}'])+' "$@"\n')
            curl.chmod(0o755)
            output=work/'output'
            env={**os.environ,'PATH':str(bin_dir)+':'+os.environ['PATH'],
                 'DL_HTTP_ORIGIN':f'http://127.0.0.1:{origin.server_port}',
                 'DL_HOST_HEADER':'fixture.invalid'}
            try:
                result=subprocess.run(['bash','-c','set -euo pipefail\n'+self.functions+
                    '\nhttp_request "$1" "$2" "$3"','test',path,str(output),method],
                    env=env,capture_output=True,text=True,timeout=15)
                actual=output.read_bytes() if output.exists() else b''
            finally:
                for server in servers:server.shutdown();server.server_close()
                for t in threads:t.join(timeout=5)
        return result,actual,requests,body

    def test_real_tls_object_get_preserves_origin_host_and_uses_media_host(self):
        result,actual,requests,body=self.exercise()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(actual,body)
        self.assertEqual(requests,[('origin','GET','fixture.invalid'),('media','GET','media.simplyluma.com')])

    def test_head_requires_final_media_success(self):
        result,_,requests,_=self.exercise(method='HEAD',missing=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(requests,[('origin','HEAD','fixture.invalid'),('media','HEAD','media.simplyluma.com')])

    def test_redirect_to_unapproved_host_is_rejected(self):
        result,_,requests,_=self.exercise(destination='https://unapproved.invalid/'+OBJECT)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(len(requests),1)

    def test_private_preview_redirect_is_rejected_without_logging_credential(self):
        credential='private-device-token-123456789'
        result,_,requests,_=self.exercise(path='os/preview/'+credential+'/repo/objects/ab/commit')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(len(requests),1)
        self.assertNotIn(credential,result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
