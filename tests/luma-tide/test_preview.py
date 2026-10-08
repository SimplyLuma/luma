# SPDX-License-Identifier: Apache-2.0
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from luma_tide import preview
from luma_tide.identity import PREVIEW_APP_ID


class PreviewLaunchTests(unittest.TestCase):
    def test_native_entry_passes_preview_identity_to_the_application(self):
        launch = Mock(return_value=0)
        with patch.dict(os.environ, {}, clear=True), patch.dict(sys.modules, {
            'luma_tide.application': SimpleNamespace(main=launch),
        }):
            self.assertEqual(preview.main(['tide-preview']), 0)
        launch.assert_called_once_with(['tide-preview'], application_id=PREVIEW_APP_ID)

    def test_fixture_entry_cannot_start_the_real_application(self):
        launch = Mock(return_value=0)
        real = Mock(side_effect=AssertionError('fixture must not start the real library'))
        with patch.dict(os.environ, {'LUMA_TIDE_FIXTURE': '/fixture/library.json'}, clear=True), \
             patch.dict(sys.modules, {
                 'luma_tide.fixture_application': SimpleNamespace(main=launch),
                 'luma_tide.application': SimpleNamespace(main=real),
             }):
            self.assertEqual(preview.main([]), 0)
        launch.assert_called_once_with([])
        real.assert_not_called()
