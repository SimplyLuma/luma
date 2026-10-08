# SPDX-License-Identifier: Apache-2.0
"""A completed live force request updates the UI once and retires its callback."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from luma_monitor.application import MonitorWindow


class ImmediateThread:
    def __init__(self,target,**_kwargs):self.target=target
    def start(self):self.target()


class ForceCallbackTests(unittest.TestCase):
    def window(self):
        return SimpleNamespace(closed=False,selected='a:editor',host=object(),_render_bar=Mock())

    def test_success_deselects_and_shows_one_result(self):
        window=self.window();target={'members':[object()]};callbacks=[]
        with patch('luma_monitor.application.threading.Thread',ImmediateThread), \
             patch('luma_monitor.application.GLib.idle_add',side_effect=lambda callback:callbacks.append(callback)), \
             patch('luma_monitor.integration.force_quit_row',return_value=(1,1)) as force, \
             patch('luma_monitor.application.Toast.show') as toast:
            MonitorWindow._request_force(window,target,'Editor','a:editor')
            force.assert_called_once_with(target)
            self.assertEqual(len(callbacks),1)
            self.assertIs(callbacks[0](),False)
            self.assertIsNone(window.selected)
            window._render_bar.assert_called_once()
            toast.assert_called_once_with(window.host,'Sent force quit to 1 of 1 Editor processes',kind='done')

    def test_failed_validation_keeps_selection_and_shows_error(self):
        window=self.window();callbacks=[]
        with patch('luma_monitor.application.threading.Thread',ImmediateThread), \
             patch('luma_monitor.application.GLib.idle_add',side_effect=lambda callback:callbacks.append(callback)), \
             patch('luma_monitor.integration.force_quit_row',side_effect=ProcessLookupError('Selection expired')), \
             patch('luma_monitor.application.Toast.show') as toast:
            MonitorWindow._request_force(window,{},'Editor','a:editor')
            self.assertIs(callbacks[0](),False)
            self.assertEqual(window.selected,'a:editor')
            window._render_bar.assert_not_called()
            toast.assert_called_once_with(window.host,'Selection expired',kind='error')

    def test_changed_selection_never_starts_request(self):
        window=self.window();window.selected='a:other'
        with patch('luma_monitor.application.threading.Thread') as thread:
            MonitorWindow._request_force(window,{},'Editor','a:editor')
            thread.assert_not_called()


if __name__=='__main__':unittest.main()
