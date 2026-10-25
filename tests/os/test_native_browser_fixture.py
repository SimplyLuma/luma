#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the real HTTP fixture with Chromium-style idle preconnections."""
import hashlib
import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import urlsplit


class PageServerTest(unittest.TestCase):
    def test_idle_preconnection_does_not_block_page_or_dom_report(self):
        fixture = Path(__file__).resolve().parents[2] / 'scripts/os/qa/viola-native'
        html = (fixture / 'page.html').read_bytes()
        with tempfile.TemporaryDirectory(prefix='native-page-test-') as directory:
            out = Path(directory)
            process = subprocess.Popen([
                sys.executable, str(fixture / 'page-server.py'), str(out),
                '--html-sha256', hashlib.sha256(html).hexdigest(),
            ])
            idle = None
            try:
                deadline = time.monotonic() + 5
                while not (out / 'page-server.json').exists():
                    self.assertIsNone(process.poll())
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.02)
                record = json.loads((out / 'page-server.json').read_text())
                url = urlsplit(record['url'])
                idle = socket.create_connection((url.hostname, url.port), timeout=2)
                time.sleep(.1)
                client = http.client.HTTPConnection(url.hostname, url.port, timeout=2)
                client.request('GET', url.path + '?' + url.query)
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), html)
                client.close()
                document = dict(token=record['token'], phase=0, pointers=0, keys=0,
                                last_event='load', href=record['url'], width=640, height=480)
                client = http.client.HTTPConnection(url.hostname, url.port, timeout=2)
                client.request('POST', '/native-event-' + record['token'],
                               json.dumps(document), headers={
                                   'Origin': f'http://{url.hostname}:{url.port}',
                                   'Content-Type': 'application/json',
                               })
                response = client.getresponse()
                self.assertEqual(response.status, 204)
                response.read()
                client.close()
                self.assertEqual(json.loads((out / 'page-phase-0.json').read_text())['phase'], 0)
            finally:
                if idle:
                    idle.close()
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


if __name__ == '__main__':
    unittest.main()
