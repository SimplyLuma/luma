# SPDX-License-Identifier: Apache-2.0
"""The current file face keeps application ownership and GIO MIME fallbacks."""
import unittest
from unittest.mock import Mock
from gi.repository import Gio
from luma_appkit import content_file

class FileFaceTests(unittest.TestCase):
    def test_registered_application_icon_owns_the_face(self):
        icon = Gio.ThemedIcon.new("org.projectluma.Write")
        app = Mock()
        app.get_icon.return_value = icon
        self.assertIs(content_file._app_icon(app, "application/pdf"), icon)

    def test_mime_fallback_is_present_for_supported_file_families(self):
        for content_type in ("application/pdf", "image/png", "application/zip", "audio/mpeg"):
            with self.subTest(content_type=content_type):
                icon = content_file._app_icon(None, content_type)
                self.assertIsInstance(icon, Gio.ThemedIcon)
                self.assertTrue(icon.get_names())
                self.assertTrue(any(content_type.replace("/", "-") in name for name in icon.get_names()))
        self.assertIsNone(content_file._app_icon(None, None))
