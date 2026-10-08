# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from luma_viewer.application import ViewerWindow


class ExportCompletionTests(unittest.TestCase):
    def test_current_save_toasts_only_after_success(self):
        source=SimpleNamespace(path=Path('/old.pdf'),name='old.pdf')
        history=SimpleNamespace(saved=[])
        window=SimpleNamespace(fixture=False,saving=False,loaded=True,marks=[object()],
            load_token=7,history=history,facts=source,source_bytes=b'PDF',pixbuf=None,
            adjustments={},crop=None,rotation=0,flip=False,form_values={},
            sessions={},host=Mock(),_change_snapshot=lambda:{},
            _refresh_actions=Mock(),_error=Mock(),_edits_changed=Mock(),
            status_detail=Mock(),last_saved='')
        with (patch('luma_viewer.application.threading.Thread') as thread,
              patch('luma_viewer.application.save_pdf'),
              patch('luma_viewer.application.GLib.idle_add') as idle,
              patch('luma_viewer.application.Toast.show') as toast):
            ViewerWindow._export_to(window,Path('/copy.pdf'))
            toast.assert_not_called()
            thread.call_args.kwargs['target']()
            idle.call_args.args[0](None)
            toast.assert_called_once_with(window.host,'Saved copy: copy.pdf')
            self.assertEqual(window.last_saved,'/copy.pdf')
        window.saving=False
        with (patch('luma_viewer.application.threading.Thread') as thread,
              patch('luma_viewer.application.save_pdf',side_effect=OSError('disk full')),
              patch('luma_viewer.application.GLib.idle_add') as idle,
              patch('luma_viewer.application.Toast.show') as toast):
            ViewerWindow._export_to(window,Path('/failed.pdf'))
            thread.call_args.kwargs['target']()
            idle.call_args.args[0]('disk full')
            toast.assert_not_called()
            window._error.assert_called()

    def test_switching_files_during_export_preserves_the_current_session(self):
        self._complete_after_switch(False)

    def test_returning_to_the_exported_file_updates_its_saved_changes(self):
        self._complete_after_switch(True)

    def _complete_after_switch(self, return_to_source):
        source=SimpleNamespace(path=Path('/old.pdf'),name='old.pdf')
        history=SimpleNamespace(saved=[])
        marks=[object()];snapshot={'form_values':{'tenant':'Edited'}}
        window=SimpleNamespace(fixture=False,saving=False,loaded=True,marks=marks,
            load_token=7,history=history,facts=source,source_bytes=b'PDF',pixbuf=None,
            adjustments={},crop=None,rotation=0,flip=False,form_values={},
            sessions={},host=Mock(),_change_snapshot=lambda:snapshot,
            _refresh_actions=Mock(),_error=Mock(),_edits_changed=Mock(),last_saved='')
        after=Mock()
        with (patch('luma_viewer.application.threading.Thread') as thread,
              patch('luma_viewer.application.save_pdf'),
              patch('luma_viewer.application.GLib.idle_add') as idle,
              patch('luma_viewer.application.Toast.show')):
            ViewerWindow._export_to(window,Path('/copy.pdf'),after)
            thread.call_args.kwargs['target']()
            complete=idle.call_args.args[0]
            window.sessions[str(source.path)]=(history,{'saved_changes':{}})
            current=history if return_to_source else SimpleNamespace(saved=['current saved marks'])
            window.saved_changes={}
            window.history=current;window.load_token=8
            window.facts=SimpleNamespace(path=source.path if return_to_source else Path('/current.pdf'))
            complete(None)
        if return_to_source:
            self.assertEqual(window.saved_changes,snapshot)
        else:
            self.assertEqual(current.saved,['current saved marks'])
            self.assertEqual(window.saved_changes,{})
        self.assertEqual(window.last_saved,'')
        self.assertEqual(history.saved,marks)
        self.assertEqual(window.sessions[str(source.path)][1]['saved_changes'],snapshot)
        window._edits_changed.assert_not_called();after.assert_not_called()
        self.assertFalse(window.saving)


if __name__=='__main__':unittest.main()
