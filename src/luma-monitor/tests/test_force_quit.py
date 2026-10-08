# SPDX-License-Identifier: Apache-2.0
"""Force quit must target only the exact scratch processes selected by Monitor."""
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from luma_monitor.integration import force_quit_processes,force_quit_row
from luma_monitor.model import stat_record


class ForceQuitTests(unittest.TestCase):
    def setUp(self):
        self.children=[]

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:child.terminate()
            child.wait(timeout=5)

    def child(self):
        child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
        self.children.append(child)
        root=Path('/proc')/str(child.pid)
        record=stat_record((root/'stat').read_text())
        cgroup=next(line.split(':',2)[2] for line in (root/'cgroup').read_text().splitlines() if line.startswith('0::'))
        return SimpleNamespace(pid=child.pid,start=record['start'],uid=os.getuid(),cgroup=cgroup)

    def test_pidfd_kills_only_the_selected_scratch_child(self):
        target=self.child();other=self.child()
        self.assertEqual(force_quit_row({'background':False,'members':[target]}),(1,1))
        self.assertEqual(self.children[0].wait(timeout=5),-9)
        self.assertIsNone(self.children[1].poll())

    def test_stale_start_group_and_uid_are_rejected_before_any_signal(self):
        other=self.child()
        for wrong in (SimpleNamespace(**{**vars(other),'start':other.start+1}),
                      SimpleNamespace(**{**vars(other),'cgroup':other.cgroup+'/elsewhere'})):
            with self.subTest(wrong=wrong):
                with patch('luma_monitor.integration.signal.pidfd_send_signal') as send:
                    self.assertEqual(force_quit_processes((wrong,)),(0,1))
                    send.assert_not_called()
                self.assertIsNone(self.children[0].poll())
        with patch('luma_monitor.integration.signal.pidfd_send_signal') as send:
            with self.assertRaises(PermissionError):
                force_quit_processes((SimpleNamespace(**{**vars(other),'uid':other.uid+1}),))
            send.assert_not_called()
        self.assertIsNone(self.children[0].poll())

    def test_large_app_uses_only_one_pidfd_at_a_time(self):
        members=[SimpleNamespace(pid=10000+i,start=1,uid=os.getuid(),cgroup='/test') for i in range(1035)]
        opened=0;maximum=0
        def open_fd(*_args):
            nonlocal opened,maximum
            opened+=1;maximum=max(maximum,opened)
            return 99
        def close_fd(_descriptor):
            nonlocal opened
            opened-=1
        with patch('luma_monitor.integration.os.pidfd_open',side_effect=open_fd), \
             patch('luma_monitor.integration.os.close',side_effect=close_fd), \
             patch('luma_monitor.integration.Path.stat',return_value=SimpleNamespace(st_uid=os.getuid())), \
             patch('luma_monitor.integration._same_process'), \
             patch('luma_monitor.integration.signal.pidfd_send_signal') as send:
            self.assertEqual(force_quit_processes(members),(1035,1035))
        self.assertEqual(maximum,1)
        self.assertEqual(opened,0)
        self.assertEqual(send.call_count,1035)

    def test_background_duplicate_and_self_are_refused(self):
        target=self.child()
        with self.assertRaises(ValueError):force_quit_row({'background':True,'members':[target]})
        with self.assertRaises(ValueError):force_quit_processes((target,target))
        with self.assertRaises(PermissionError):
            force_quit_processes((SimpleNamespace(**{**vars(target),'pid':os.getpid()}),))
        self.assertIsNone(self.children[0].poll())


if __name__=='__main__':unittest.main()
