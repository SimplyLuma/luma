# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from luma_viewer.application import ViewerWindow


class OpenInTests(unittest.TestCase):
    def window(self):
        return SimpleNamespace(facts=SimpleNamespace(path=Path('/original.pdf')),
            document=object(), fixture=False, marks=(), adjustments={}, crop=None,
            form_values={}, rotation=0, flip=False, host=Mock())

    def test_changed_documents_do_not_launch_the_original(self):
        for change in (dict(marks=('mark',)), dict(adjustments={'exp': 10}),
                       dict(crop=(0, 0, 10, 10)), dict(form_values={'tenant': 'Edited'}),
                       dict(rotation=90), dict(flip=True)):
            with self.subTest(change=change):
                window=self.window()
                for key,value in change.items():setattr(window,key,value)
                with (patch('luma_viewer.composition.OpenInMenu') as menu,
                      patch('luma_viewer.composition.Toast.show') as toast):
                    ViewerWindow._open_in(window,Mock())
                    callbacks=menu.call_args.kwargs
                    callbacks['on_open'](Mock());callbacks['on_other']()
                    self.assertEqual(toast.call_count,2)
                    self.assertIn('Save a copy first',toast.call_args.args[1])

    def test_unchanged_documents_keep_the_native_launch_route(self):
        with patch('luma_viewer.composition.OpenInMenu') as menu:
            ViewerWindow._open_in(self.window(),Mock())
            self.assertEqual(menu.call_args.args[0],'/original.pdf')
            self.assertIsNone(menu.call_args.kwargs['on_open'])
            self.assertIsNone(menu.call_args.kwargs['on_other'])

    def test_audio_and_epub_use_their_actual_type(self):
        for path in ('/song.flac', '/book.epub'):
            window = self.window()
            window.facts.path = Path(path)
            window.document = None
            with patch('luma_viewer.composition.OpenInMenu') as menu:
                ViewerWindow._open_in(window, Mock())
                self.assertIsNone(menu.call_args.kwargs['content_type'])


if __name__=='__main__':unittest.main()
