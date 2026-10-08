# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('remote_generator', Path(__file__).parents[1]/'generate-remote-files.py')
generator = importlib.util.module_from_spec(spec); spec.loader.exec_module(generator)

class RemoteSizes(unittest.TestCase):
    def test_actual_small_application_values_are_not_byte_swapped(self):
        for value in (366592, 208213, 0, (1<<64)-1):
            with patch.object(generator,'ostree',return_value=f'uint64 {value}\n'):
                self.assertEqual(generator._flatpak_size(Path('/repo'),'a'*64,'xa.installed-size'),value)

    def test_wrong_types_negative_trailing_fields_and_overflow_refuse_catalogue(self):
        for value in ('366592','int64 366592','uint64 -1','uint64 3 trailing','uint64 18446744073709551616','uint64 3\nuint64 4'):
            with patch.object(generator,'ostree',return_value=value):
                with self.assertRaises(ValueError): generator._flatpak_size(Path('/repo'),'a'*64,'xa.download-size')

    def test_missing_optional_metadata_remains_absent(self):
        with patch.object(generator,'ostree',side_effect=subprocess.CalledProcessError(1,['ostree'])):
            self.assertIsNone(generator._flatpak_size(Path('/repo'),'a'*64,'xa.installed-size'))

if __name__=='__main__': unittest.main()
