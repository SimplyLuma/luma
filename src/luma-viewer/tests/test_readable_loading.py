# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from luma_viewer.application import ViewerWindow


class ReadableLoadingTests(unittest.TestCase):
    def load(self,kind):
        facts=SimpleNamespace(kind=kind,path=Path('/fixture/document'))
        window=SimpleNamespace(stage=Mock(),load_token=7,facts=facts,
            _clear_stage=Mock(),_render_text=Mock(),_render_table=Mock())
        return window,facts

    def test_read_is_deferred_and_stale_completion_cannot_replace_new_file(self):
        window,facts=self.load('Text')
        with (patch('luma_viewer.application.Gtk.Spinner'),patch('luma_viewer.application.threading.Thread') as thread,
                patch('luma_viewer.application.formats.read_text',return_value='Old file') as read,
                patch('luma_viewer.application.GLib.idle_add') as idle):
            ViewerWindow._render_readable_async(window,facts,7)
            read.assert_not_called()
            thread.call_args.kwargs['target']()
            read.assert_called_once_with(facts.path)
            window.load_token=8
            self.assertFalse(idle.call_args.args[0]())
            window._clear_stage.assert_not_called();window._render_text.assert_not_called()

    def test_current_table_completion_renders_only_after_read_finishes(self):
        window,facts=self.load('Table');data=(['Name'],[['Parcel']])
        with (patch('luma_viewer.application.Gtk.Spinner'),patch('luma_viewer.application.threading.Thread') as thread,
                patch('luma_viewer.application.formats.read_table',return_value=data),
                patch('luma_viewer.application.GLib.idle_add') as idle):
            ViewerWindow._render_readable_async(window,facts,7)
            thread.call_args.kwargs['target']()
            self.assertFalse(idle.call_args.args[0]())
        window._clear_stage.assert_called_once_with()
        window._render_table.assert_called_once_with(facts,data)
