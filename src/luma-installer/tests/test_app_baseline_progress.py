# SPDX-License-Identifier: Apache-2.0
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest

from luma_installer import app_baseline_progress as progress


class Progress(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'run/status.json'
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, wait=60):
        return progress.Progress(self.path, wait=wait, notify=self.sent.append, clock=lambda: 1.0)

    def test_finished_handoff_reports_ready_once_and_publishes_readable_status(self):
        run = self.make()
        run.arm()
        run.begin()
        self.assertEqual(progress.read(self.path)['state'], progress.INSTALLING)
        run.step(2, 5)
        run.finish()
        run.release()
        doc = progress.read(self.path)
        self.assertEqual((doc['state'], doc['apps_ready'], doc['apps_total']), (progress.DONE, 2, 5))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.sent.count('READY=1'), 1)
        self.assertFalse(run.timer.is_alive())

    def test_login_wait_releases_the_display_manager_while_import_continues(self):
        run = self.make(wait=0.01)
        run.arm()
        run.begin(3)
        run.timer.join(5)
        self.assertEqual(self.sent, ['READY=1'])
        self.assertEqual(progress.read(self.path)['state'], progress.INSTALLING)
        run.step(1, 3)
        run.finish()
        self.assertEqual(self.sent.count('READY=1'), 1)

    def test_failure_is_published_without_ready_and_kill_is_reported(self):
        run = self.make()
        run.arm()
        run.begin(3)
        run.finish(ok=False)
        self.assertEqual(progress.read(self.path)['state'], progress.FAILED)
        self.assertNotIn('READY=1', self.sent)
        run = self.make()
        run.begin(3)
        progress.stopped(self.path)
        self.assertEqual(progress.read(self.path)['state'], progress.FAILED)
        done = self.make()
        done.begin(1)
        done.finish()
        progress.stopped(self.path)
        self.assertEqual(progress.read(self.path)['state'], progress.DONE)

    def test_completed_baseline_writes_no_status(self):
        run = self.make()
        run.arm()
        run.finish()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.sent, ['READY=1'])

    def test_notice_says_setting_up_then_ready_in_place(self):
        run = self.make()
        run.begin(4)
        run.step(1, 4)
        calls = []

        def send(replaces, summary, body):
            calls.append((replaces, summary, body))
            return 7

        polls = iter([lambda: run.step(3, 4), run.finish])

        def sleep(_):
            next(polls)()

        progress.notice(self.path, send=send, sleep=sleep)
        self.assertEqual(calls[0][:2], (0, 'Setting up your apps'))
        self.assertIn('1 of 4', calls[0][2])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][:2], (7, 'Your apps are ready'))

    def test_notice_is_silent_when_nothing_is_installing(self):
        calls = []
        progress.notice(self.path, send=lambda *a: calls.append(a), sleep=lambda _: None)
        run = self.make()
        run.begin(1)
        run.finish()
        progress.notice(self.path, send=lambda *a: calls.append(a), sleep=lambda _: None)
        self.path.write_text('not json')
        progress.notice(self.path, send=lambda *a: calls.append(a), sleep=lambda _: None)
        self.assertEqual(calls, [])

    def test_sd_notify_uses_the_service_socket(self):
        address = os.path.join(self.tmp.name, 'notify')
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
            server.bind(address)
            self.assertTrue(progress.sd_notify('READY=1', {'NOTIFY_SOCKET': address}))
            self.assertEqual(server.recv(64), b'READY=1')
        self.assertFalse(progress.sd_notify('READY=1', {}))


if __name__ == '__main__':
    unittest.main()
