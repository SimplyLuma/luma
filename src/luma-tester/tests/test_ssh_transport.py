# SPDX-License-Identifier: Apache-2.0
import subprocess
import sys
import unittest
from unittest.mock import patch

from luma_tester import host


class WindowsSSHTransportTests(unittest.TestCase):
    def execute(self, code, **kwargs):
        with patch.object(host, 'windows', return_value=True):
            return host.run_ssh([sys.executable, '-c', code], **kwargs)

    def test_binary_upload_and_both_output_streams(self):
        data = bytes(range(256)) * 2048
        result = self.execute('import sys; b=sys.stdin.buffer.read(); sys.stdout.buffer.write(b); sys.stderr.write("diagnostic")',
                              input=data, capture_output=True, check=True, timeout=10)
        self.assertEqual(result.stdout, data)
        self.assertEqual(result.stderr, b'diagnostic')

    def test_text_output_and_failed_status_preserve_diagnostics(self):
        with self.assertRaises(subprocess.CalledProcessError) as failure:
            self.execute('import sys; print("out"); print("reason",file=sys.stderr); sys.exit(23)',
                         capture_output=True, text=True, check=True, timeout=10)
        self.assertEqual(failure.exception.returncode, 23)
        self.assertEqual(failure.exception.stdout, 'out\n')
        self.assertEqual(failure.exception.stderr, 'reason\n')

    def test_timeout_kills_child_and_retains_partial_output(self):
        with self.assertRaises(subprocess.TimeoutExpired) as failure:
            self.execute('import time; print("started",flush=True); time.sleep(30)',
                         capture_output=True, text=True, timeout=.5)
        self.assertEqual(failure.exception.stdout, 'started\n')

    def test_merged_output_and_no_anonymous_pipe_handles(self):
        original = subprocess.run
        calls = []
        def execute(*args, **kwargs):
            calls.append(kwargs)
            return original(*args, **kwargs)
        with patch.object(subprocess, 'run', side_effect=execute):
            result = self.execute('import sys; print("ok"); print("diagnostic",file=sys.stderr)',
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=10)
        self.assertIn('ok\n', result.stdout)
        self.assertIn('diagnostic\n', result.stdout)
        self.assertIsNone(result.stderr)
        self.assertTrue(hasattr(calls[0]['stdout'], 'fileno'))
        self.assertNotEqual(calls[0]['stdin'], subprocess.PIPE)

    def test_posix_keeps_standard_subprocess_behavior(self):
        with patch.object(host, 'windows', return_value=False), patch.object(subprocess, 'run') as run:
            host.run_ssh(['ssh', 'host', 'true'], capture_output=True, timeout=7)
        run.assert_called_once_with(['ssh', 'host', 'true'], capture_output=True, timeout=7)

    def test_windows_upload_rejects_untrusted_guest_paths(self):
        with patch.object(host, 'run_ssh', return_value=subprocess.CompletedProcess([], 0, stdout='/tmp/file; do-something')) as run:
            with self.assertRaisesRegex(RuntimeError, 'private input file'):
                host._ssh_upload_input(['ssh', '-p', '2222', 'luma@127.0.0.1', 'command'], b'secret', {'timeout': 5})
        self.assertEqual(run.call_count, 1)

    def test_windows_upload_preserves_binary_data_and_cleans_guest_file(self):
        from pathlib import Path
        payload = bytes(range(256)) * 2048
        calls = []
        def execute(argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[0].endswith('sftp.exe'):
                batch = Path(argv[argv.index('-b') + 1])
                self.assertEqual((batch.parent / 'input').read_bytes(), payload)
                self.assertIn('-P', argv)
                self.assertNotIn('-p', argv)
            return subprocess.CompletedProcess(argv, 0, stdout='/tmp/try-luma-input.a1b2c3d4e5\n', stderr='')
        with patch.object(host, 'run_ssh', side_effect=execute), patch('shutil.which', return_value='/private/ssh.exe'), patch('pathlib.Path.is_file', return_value=True):
            host._ssh_upload_input(['ssh', '-F', 'nul', '-p', '2222', 'luma@127.0.0.1', 'command'], payload, {'timeout': 5})
        self.assertEqual(calls[2][0][-1], '(command) < /tmp/try-luma-input.a1b2c3d4e5')
        self.assertEqual(calls[3][0][-1], 'rm -f -- /tmp/try-luma-input.a1b2c3d4e5')
        self.assertLess(calls[2][1]['timeout'], 5)
