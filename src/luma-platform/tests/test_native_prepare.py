# SPDX-License-Identifier: Apache-2.0
"""Native GTK preparation: no map until activation; reclaim unused UI safely."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

LIBRARY = os.environ.get('LUMA_NATIVE_PREPARE_LIBRARY')
FIXTURE = os.environ.get('LUMA_NATIVE_PREPARE_FIXTURE')


@unittest.skipUnless(LIBRARY and FIXTURE, 'native GTK fixture and library required')
class NativePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('XDG_RUNTIME_DIR'))
        self.root = Path(self.temp.name)
        self.ready = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.ready.settimeout(10)
        self.path = self.root / 'ready.sock'
        self.ready.bind(str(self.path))
        self.record = self.root / 'mapped'
        self.identifier = 'org.projectluma.PrepareTest' + uuid.uuid4().hex
        self.child = None
        self.original_oom = int(Path("/proc/self/oom_score_adj").read_text())

    def tearDown(self):
        if self.child and self.child.poll() is None:
            self.child.terminate()
            self.child.communicate(timeout=5)
        self.ready.close()
        self.temp.cleanup()

    def start(self, memory=False):
        env = dict(os.environ, LD_PRELOAD=LIBRARY, LUMA_APP_PREPARE='1',
            LUMA_APP_PREPARE_LIBRARY=LIBRARY, LUMA_APP_PREPARE_NOTIFY=str(self.path),
            LUMA_PREPARE_TEST_RECORD=str(self.record), LUMA_PREPARE_TEST_ID=self.identifier)
        if memory:
            env['LUMA_PREPARE_TEST_MEMORY'] = '1'
        self.child = subprocess.Popen([sys.executable, '-sP', '-c',
            'import ctypes,sys;raise SystemExit(ctypes.CDLL(sys.argv[1]).luma_prepare_test_main())', FIXTURE],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            message = self.ready.recv(64)
        except TimeoutError:
            if self.child.poll() is not None:
                self.fail(self.child.communicate(timeout=1)[1].decode())
            raise
        self.assertEqual(int(message), self.child.pid)
        self.assertFalse(self.record.exists(), 'preparation mapped a window')

    def assert_activated(self, method="Activate"):
        """Normal Activate and document Open must both end the preparation hold."""
        self.start()
        self.assertEqual(Path(f'/proc/{self.child.pid}/oom_score_adj').read_text().strip(), str(max(self.original_oom, 500)))
        time.sleep(.5)  # allow asynchronous font/display initialization to settle
        before = Path(f'/proc/{self.child.pid}/stat').read_text().split()[13:15]
        time.sleep(.5)
        after = Path(f'/proc/{self.child.pid}/stat').read_text().split()[13:15]
        self.assertLessEqual(sum(map(int, after)) - sum(map(int, before)), 1,
            'unused preparation continuously spends CPU')
        subprocess.run(['gdbus', 'call', '--session', '--dest', self.identifier,
            '--object-path', '/' + self.identifier.replace('.', '/'), '--method',
            'org.freedesktop.Application.' + method,
            *(["['file:///tmp']", '{}'] if method == 'Open' else ['{}'])], check=True, capture_output=True, timeout=5)
        out, error = self.child.communicate(timeout=5)
        self.assertEqual(self.child.returncode, 0, error.decode())
        self.assertEqual(self.record.read_text().split(), ['2' if method == 'Open' else '1', str(self.original_oom), '0', '1'])

    def test_prepared_view_is_reused_without_leaking_preload_to_children(self):
        self.assert_activated()

    def test_open_restores_normal_priority_and_lifecycle(self):
        self.assert_activated('Open')

    def test_low_memory_reclaims_unused_application_without_mapping(self):
        self.start(memory=True)
        _, error = self.child.communicate(timeout=5)
        self.assertEqual(self.child.returncode, 0, error.decode())
        self.assertFalse(self.record.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
