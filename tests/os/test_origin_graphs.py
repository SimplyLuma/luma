#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run the origin verifier against actual nested graph files over HTTP."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]

class OriginGraphsTests(unittest.TestCase):
    def exercise(self, tamper=False):
        with tempfile.TemporaryDirectory(prefix='luma-origin-graphs-') as folder:
            work = Path(folder)
            generation = work / 'generation'
            paths = ['nightly.json', 'nightly.json.minisig',
                     'x86_64/nightly.json', 'x86_64/nightly.json.minisig',
                     'aarch64/nightly.json', 'aarch64/nightly.json.minisig',
                     'aarch64/fairphone-fp6/nightly.json',
                     'aarch64/unihertz-titan2e/nightly.json.minisig']
            for name in paths:
                file = generation / 'os/graph' / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('signed fixture ' + name + '\n')
            origin = work / 'origin'
            shutil.copytree(generation, origin)
            if tamper:
                (origin / 'os/graph/aarch64/fairphone-fp6/nightly.json').write_text('different\n')
            bin_dir = work / 'bin'
            bin_dir.mkdir()
            # Only the transport is inert; the complete maintained HTTP verifier
            # runs against separately staged real files, including failure cases.
            transport = bin_dir / 'rsync'
            transport.write_text('#!/bin/sh\nexit 0\n')
            transport.chmod(0o755)
            requests = []
            class Handler(SimpleHTTPRequestHandler):
                def do_GET(self):
                    requests.append(self.path)
                    super().do_GET()
                def log_message(self, *args):
                    pass
            server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(origin)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = {**os.environ, 'PATH': str(bin_dir) + ':' + os.environ['PATH'],
                       'DL_RSYNC_DEST': 'unused', 'DL_SSH_KEY': '/unused',
                       'KNOWN_HOSTS': '/unused', 'DL_HOST_HEADER': 'fixture.invalid',
                       'DL_HTTP_ORIGIN': f'http://127.0.0.1:{server.server_port}'}
                result = subprocess.run(['bash', str(ROOT / 'scripts/os/lib/sync-origin.sh'),
                    str(generation), '1', '0', 'graph', ''], env=env,
                    capture_output=True, text=True, timeout=30)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
            self.assertEqual(set(requests), {'/os/graph/' + path for path in paths})
            return result

    def test_verifies_legacy_architecture_and_device_graphs(self):
        result = self.exercise()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_changed_nested_device_graph_is_rejected(self):
        result = self.exercise(tamper=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('aarch64/fairphone-fp6/nightly.json: served copy differs', result.stdout)

if __name__ == '__main__':
    unittest.main()
