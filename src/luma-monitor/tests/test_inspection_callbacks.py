# SPDX-License-Identifier: Apache-2.0
"""Reject completed reads when their selected process identity has expired."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from luma_monitor.application import MonitorWindow


class InspectionCompletionTests(unittest.TestCase):
    key=(3113,12345,'/app/viola')

    def window(self):
        return SimpleNamespace(closed=False,inspection_generation=7,selected='p:3113',
                               live_processes=[SimpleNamespace(pid=3113,start=12345,cgroup='/app/viola')],
                               host=Mock(),_present_sheet=Mock())

    def finish(self,window,error=None):
        MonitorWindow._inspection_ready(window,7,'p:3113','files','Viola',self.key,
                                        ['FD','Type','Object'],[['0','file','/dev/null']],error)

    def test_current_identity_opens_read_result(self):
        window=self.window();self.finish(window)
        window._present_sheet.assert_called_once_with('files','Viola',3113,
                                                      ['FD','Type','Object'],[['0','file','/dev/null']])

    def test_exited_reused_or_moved_process_never_opens_old_result(self):
        for processes in ([],[SimpleNamespace(pid=3113,start=12346,cgroup='/app/viola')],
                          [SimpleNamespace(pid=3113,start=12345,cgroup='/app/other')]):
            with self.subTest(processes=processes):
                window=self.window();window.live_processes=processes;self.finish(window)
                window._present_sheet.assert_not_called()

    def test_closed_superseded_or_deselected_completion_is_ignored(self):
        for field,value in (('closed',True),('inspection_generation',8),('selected',None)):
            with self.subTest(field=field):
                window=self.window();setattr(window,field,value);self.finish(window)
                window._present_sheet.assert_not_called()

    def test_stale_error_does_not_emit_a_toast_for_the_new_process(self):
        window=self.window();window.live_processes=[]
        with patch('luma_monitor.application.Toast.show') as toast:
            self.finish(window,'process exited');toast.assert_not_called()

    def test_current_read_error_remains_visible(self):
        window=self.window()
        with patch('luma_monitor.application.Toast.show') as toast:
            self.finish(window,'permission denied')
            toast.assert_called_once_with(window.host,'Couldn’t inspect process',kind='error')
        window._present_sheet.assert_not_called()


if __name__=='__main__':unittest.main()
