# SPDX-License-Identifier: Apache-2.0
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
from ari.tools import processes


class Processes(unittest.TestCase):
    def test_real_disposable_process_preview_end_and_exit_verification(self):
        child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
        try:
            found=processes.find_processes({'query':str(child.pid)})['data']['processes']
            self.assertEqual(len(found),1)
            args={'process':found[0]['process'],'action':'end'}
            self.assertTrue(processes.control_process({**args,'preview':True})['ok'])
            self.assertIsNone(child.poll())
            result=processes.control_process(args)
            self.assertTrue(result['data']['exited'])
            child.wait(timeout=5)
            with self.assertRaises(ValueError):processes.control_process(args)
        finally:
            if child.poll() is None:child.kill();child.wait()

    def test_changed_identity_and_own_ancestors_cannot_be_signalled(self):
        self.assertEqual(processes.find_processes({'query':str(os.getpid())})['data']['processes'],[])
        child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
        try:
            key=processes.find_processes({'query':str(child.pid)})['data']['processes'][0]['process']
            processes._targets[key]['start']='different'
            with patch.object(processes.signal,'pidfd_send_signal') as send:
                with self.assertRaises(ValueError):processes.control_process({'process':key,'action':'kill'})
                send.assert_not_called()
        finally:child.kill();child.wait()
