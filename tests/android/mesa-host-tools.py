#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the production check, including the actual failed-build version."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('mesa_host', ROOT / 'scripts/android/verify-mesa-host.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MesaHost(unittest.TestCase):
    def test_selected_host_version_and_mesa_path_precedence(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            mesa = source / 'external/mesa'
            mesa.mkdir(parents=True)
            (mesa / 'meson.build').write_text("project('mesa', meson_version : '>= 1.4.0')\n")
            for selected in ('0.61.2', '1.3.99', '1.4.0', '1.9.2'):
                with patch.object(module.subprocess, 'check_output', return_value=selected+'\n') as call:
                    if selected in ('0.61.2', '1.3.99'):
                        with self.assertRaisesRegex(RuntimeError, 'requires >= 1.4.0'):
                            module.verify(source, '/synthetic/new-meson/bin')
                    else:
                        self.assertEqual(module.verify(source, '/synthetic/new-meson/bin'), selected)
                    self.assertEqual(call.call_args.kwargs['env']['PATH'], '/usr/bin:/usr/local/bin:/synthetic/new-meson/bin')

    def test_missing_requirement_or_malformed_version_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            mesa = source / 'external/mesa'
            mesa.mkdir(parents=True)
            definition = mesa / 'meson.build'
            definition.write_text('unknown requirement')
            with self.assertRaisesRegex(RuntimeError, 'requirement'):
                module.verify(source, os.defpath)
            definition.write_text("meson_version : '>= 1.4.0'")
            with patch.object(module.subprocess, 'check_output', return_value='unknown'):
                with self.assertRaisesRegex(RuntimeError, 'selected Meson version'):
                    module.verify(source, os.defpath)


if __name__ == '__main__':
    unittest.main()
