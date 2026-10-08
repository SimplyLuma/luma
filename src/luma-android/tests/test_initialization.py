import unittest
import subprocess
from unittest.mock import patch
from luma_android.engine import WaydroidEngine, CommandResult
from luma_android.errors import RuntimeUnavailableError

class Initialization(unittest.TestCase):
    def test_setup_failure_or_cancel_does_not_claim_images_missing_or_retry_session(self):
        engine = WaydroidEngine('waydroid')
        with patch.object(engine, 'status', return_value={'initialized':False}), \
             patch('luma_android.engine._activate_fp6_software_presentation'), \
             patch('luma_android.engine._prepare_interactive_windowing'), \
             patch('luma_android.engine.subprocess.run',
                   side_effect=subprocess.CalledProcessError(1, 'systemctl')) as setup, \
             patch('luma_android.engine.subprocess.Popen') as start:
            with self.assertRaisesRegex(RuntimeUnavailableError, 'administrator approval was cancelled'):
                engine.ensure_ready(multi_window=True, detached=True)
            setup.assert_called_once_with(
                ['/usr/bin/systemctl', 'start', 'luma-android-initialize.service'],
                check=True, capture_output=True, text=True, timeout=210)
            start.assert_not_called()

    def test_setup_success_without_initialized_runtime_cannot_start_session(self):
        engine = WaydroidEngine('waydroid')
        with patch.object(engine, 'status', return_value={'initialized': False}) as status, \
             patch('luma_android.engine._activate_fp6_software_presentation'), \
             patch('luma_android.engine._prepare_interactive_windowing'), \
             patch('luma_android.engine.subprocess.run') as setup, \
             patch('luma_android.engine.subprocess.Popen') as start:
            with self.assertRaisesRegex(RuntimeUnavailableError, 'did not become ready'):
                engine.ensure_ready(multi_window=True, detached=True)
            self.assertEqual(status.call_count, 2)
            setup.assert_called_once()
            start.assert_not_called()

    def test_setup_rechecks_authoritative_status_before_boot_readiness(self):
        engine = WaydroidEngine('waydroid')
        with patch.object(engine, 'status', side_effect=[
                {'initialized': False},
                {'initialized': True, 'session': 'RUNNING', 'container': 'RUNNING'}]), \
             patch('luma_android.engine._activate_fp6_software_presentation'), \
             patch('luma_android.engine._prepare_interactive_windowing'), \
             patch('luma_android.engine._host_booted_in_charger_mode', return_value=False), \
             patch('luma_android.engine.subprocess.run') as setup, \
             patch('luma_android.engine.subprocess.Popen') as start, \
             patch.object(engine, 'wait_for_boot_completion',
                          side_effect=RuntimeUnavailableError('boot readiness sentinel')) as boot:
            with self.assertRaisesRegex(RuntimeUnavailableError, 'boot readiness sentinel'):
                engine.ensure_ready(multi_window=True, detached=True)
            setup.assert_called_once()
            boot.assert_called_once()
            start.assert_not_called()

    def test_upstream_missing_initialization_message(self):
        engine = WaydroidEngine('waydroid')
        with patch.object(engine, '_run', return_value=CommandResult('Waydroid is not initialized, run "waydroid init"\n','',0)), patch.object(engine,'available',return_value=True), patch('luma_android.engine.is_fp6',return_value=False):
            self.assertFalse(engine.status()['initialized'])
