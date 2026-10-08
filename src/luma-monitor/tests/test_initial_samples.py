# SPDX-License-Identifier: Apache-2.0
"""Real proc fixtures must publish activity before optional services finish."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from luma_monitor.application import MonitorWindow
import test_model as model


class InitialSampleTests(unittest.TestCase):
    def test_real_process_and_cpu_samples_precede_optional_service_read(self):
        files = model.Samples()
        files.setUp()
        queued = []
        self.addCleanup(files.tearDown)
        window = SimpleNamespace(fixture=False, closed=False, working=False,
            generation=0, catalog=SimpleNamespace(refresh_running=Mock(), close=Mock()),
            sampler=files.sampler, _sampled=Mock())

        def advance(_seconds):
            files.write(files.proc / 'stat', 'cpu 50 0 20 130 0 0 0 0\ncpu0 50 0 20 130 0 0 0 0\n')
            files.write(files.proc / '42/stat', model.process_stat(42, 30))

        def optional_read():
            # This is where a slow UPower activation previously held all
            # process and CPU data out of the UI. Both real samples are queued.
            self.assertEqual(len(queued), 2)
            self.assertEqual(len(queued[0][2]['processes']), 2)
            self.assertIsNone(queued[0][2]['cpu'])
            self.assertIsNotNone(queued[1][2]['cpu'])
            self.assertTrue(window.working)
            return None

        class Worker:
            def __init__(self, *, target, **_kwargs): self.target = target
            def start(self): self.target()

        with patch('luma_monitor.application.threading.Thread', Worker), \
             patch('luma_monitor.application.GLib.idle_add', side_effect=lambda *args: queued.append(args)), \
             patch('luma_monitor.application.time.sleep', side_effect=advance), \
             patch('luma_monitor.integration.battery_info', side_effect=optional_read), \
             patch('luma_monitor.integration.performance_profile', return_value=None):
            MonitorWindow._sample(window)
        self.assertEqual(len(queued), 3)
        self.assertFalse(queued[0][-1])
        self.assertFalse(queued[1][-1])
        # Full proportional-memory facts still reach the normal final callback.
        self.assertEqual(queued[2][2]['rows'][0]['memory'], 200 * 1024)
        self.assertIsNone(queued[2][3])


if __name__ == '__main__': unittest.main()
