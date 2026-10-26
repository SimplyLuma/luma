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
from PIL import Image, ImageDraw
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


class SignedBrowserPageServerTest(unittest.TestCase):
    def test_idle_preconnection_does_not_block_signed_browser_page(self):
        source = (Path(__file__).resolve().parents[2] /
                  'tests/os/gate/browser-opens.sh').read_text()
        script = source.split("cat >\"$work/page.py\" <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        with tempfile.TemporaryDirectory(prefix='signed-browser-page-test-') as directory:
            out = Path(directory)
            program = out / 'page.py'
            program.write_text(script)
            process = subprocess.Popen([sys.executable, str(program), str(out)])
            idle = None
            try:
                deadline = time.monotonic() + 5
                while not (out / 'url').exists():
                    self.assertIsNone(process.poll())
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.02)
                url = urlsplit((out / 'url').read_text().strip())
                idle = socket.create_connection((url.hostname, url.port), timeout=2)
                time.sleep(.1)
                client = http.client.HTTPConnection(url.hostname, url.port, timeout=2)
                client.request('GET', url.path)
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn(b'Luma browser gate', response.read())
                client.close()
                self.assertEqual(json.loads((out / 'page-requested').read_text())['path'], url.path)
            finally:
                if idle:
                    idle.close()
                process.terminate()
                process.wait(timeout=3)


class CompositorPixelTest(unittest.TestCase):
    def test_real_png_decoder_requires_all_page_colors(self):
        verifier = Path(__file__).resolve().parents[2] / 'scripts/os/qa/viola-native/verify-pixels.py'
        with tempfile.TemporaryDirectory(prefix='native-pixel-test-') as directory:
            image = Image.new('RGB', (200, 200))
            draw = ImageDraw.Draw(image)
            colors = ((231, 41, 83), (19, 173, 109), (43, 83, 229), (241, 181, 29))
            for index, color in enumerate(colors):
                x, y = (index % 2) * 100, (index // 2) * 100
                draw.rectangle((x, y, x + 99, y + 99), fill=color)
            path = Path(directory) / 'monitor.png'
            image.save(path)
            command = [sys.executable, str(verifier), str(path), '--rectangle', '0', '0', '200', '200', '--phase']
            passed = subprocess.run(command + ['0'], capture_output=True, text=True, timeout=5)
            self.assertEqual(passed.returncode, 0, passed.stderr)
            self.assertEqual(len(json.loads(passed.stdout)['samples']), 20)
            rejected = subprocess.run(command + ['1'], capture_output=True, text=True, timeout=5)
            self.assertEqual(rejected.returncode, 1)
            self.assertFalse(json.loads(rejected.stdout)['ok'])

    def test_png_bounds_checked_before_decode(self):
        verifier = Path(__file__).resolve().parents[2] / 'scripts/os/qa/viola-native/verify-pixels.py'
        with tempfile.TemporaryDirectory(prefix='native-pixel-bounds-') as directory:
            path = Path(directory) / 'monitor.png'
            Image.new('RGB', (100, 100)).save(path)
            data = bytearray(path.read_bytes())
            data[16:24] = (65536).to_bytes(4, 'big') * 2
            path.write_bytes(data)
            result = subprocess.run([sys.executable, str(verifier), str(path), '--rectangle',
                                     '0', '0', '100', '100', '--phase', '0'],
                                    capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('PNG dimensions exceed bound', result.stderr)


if __name__ == '__main__':
    unittest.main()
