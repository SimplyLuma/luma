# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from luma_tester.rfb import native_library


class NativeLibraryTests(unittest.TestCase):
    def test_relocated_mac_bundle_without_host_library_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            contents = Path(directory) / 'Luma Tester.app/Contents'
            (contents / 'Frameworks').mkdir(parents=True)
            with patch('luma_tester.rfb.sys.platform', 'darwin'), patch(
                'luma_tester.rfb.sys.executable', str(contents / 'MacOS/python3')
            ), patch('luma_tester.rfb.ctypes.util.find_library', return_value=None):
                for name in ('gvnc-1.0', 'gobject-2.0'):
                    library = contents / 'Frameworks' / f'lib{name}.0.dylib'
                    library.touch()
                    self.assertEqual(native_library(name, 'unused.so'), str(library))
