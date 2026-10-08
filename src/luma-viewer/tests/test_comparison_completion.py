# SPDX-License-Identifier: Apache-2.0
"""Deferred comparison work belongs to the actual comparison session."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from luma_viewer.composition import ViewerUI


class ComparisonCompletionTests(unittest.TestCase):
    paths = ['/first.png', '/second.png']

    def pending(self, failed=False):
        window = SimpleNamespace(load_token=7, _refresh_files=Mock(),
            _refresh_actions=Mock(), _processing_changed=Mock(), _render_failed=Mock())
        with (patch('threading.Thread') as thread,
                patch('luma_viewer.formats.load_image', side_effect=OSError('decode failed') if failed else None,
                    return_value=(object(), None)),
                patch('luma_viewer.composition.GLib.idle_add') as idle):
            ViewerUI._begin_compare(window, self.paths)
            thread.call_args.kwargs['target']()
            callback, *arguments = idle.call_args.args
        return window, lambda: callback(*arguments)

    def test_swap_before_load_finishes_still_displays_both_images(self):
        window, complete = self.pending()
        window.compare.reverse()
        complete()
        self.assertEqual(window.compare, list(reversed(self.paths)))
        self.assertEqual(set(window.compare_pixbufs), set(self.paths))
        window._processing_changed.assert_called_once()

    def test_restarting_same_comparison_does_not_adopt_old_completion(self):
        window, complete = self.pending()
        window.compare = list(self.paths)
        window.compare_pixbufs = {'new session': object()}
        complete()
        self.assertEqual(set(window.compare_pixbufs), {'new session'})
        window._processing_changed.assert_not_called()

    def test_late_error_after_done_does_not_replace_current_document(self):
        window, complete = self.pending(failed=True)
        window.compare = None
        complete()
        window._render_failed.assert_not_called()

    def test_current_decode_failure_remains_visible(self):
        window, complete = self.pending(failed=True)
        complete()
        window._render_failed.assert_called_once_with(7, 'decode failed')

    def test_changed_document_ignores_old_completion(self):
        window, complete = self.pending()
        window.load_token += 1
        complete()
        self.assertEqual(window.compare_pixbufs, {})
        window._processing_changed.assert_not_called()


if __name__ == '__main__':
    unittest.main()
