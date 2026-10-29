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
    def test_glslang_minimum_from_the_actual_mesa_declaration(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            mesa = source / 'external/mesa'
            mesa.mkdir(parents=True)
            (mesa / 'meson.build').write_text("_glslang_preamble_version = '12.2'\n")
            for selected in ('11.8.0', '12.1.0', '12.2.0', '16.6.0'):
                output = f'Glslang Version: 11:{selected}\nSPIR-V Version 0x00010600\n'
                with patch.dict(os.environ, PYTHONPATH='/synthetic/modules'), \
                        patch.object(module.subprocess, 'check_output', return_value=output) as call:
                    if selected in ('11.8.0', '12.1.0'):
                        with self.assertRaisesRegex(RuntimeError, 'requires >= 12.2'):
                            module.verify_glslang(source, '/synthetic/bin')
                    else:
                        self.assertEqual(module.verify_glslang(source, '/synthetic/bin'), selected)
                    self.assertEqual(call.call_args.args[0], ['glslangValidator', '--version'])
                    self.assertNotIn('PYTHONPATH', call.call_args.kwargs['env'])

    def test_missing_glslang_requirement_or_unparseable_version_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            mesa = source / 'external/mesa'
            mesa.mkdir(parents=True)
            definition = mesa / 'meson.build'
            definition.write_text('unknown requirement')
            with self.assertRaisesRegex(RuntimeError, 'glslang requirement'):
                module.verify_glslang(source, os.defpath)
            definition.write_text("_glslang_preamble_version = '12.2'")
            with patch.object(module.subprocess, 'check_output', return_value='unknown'):
                with self.assertRaisesRegex(RuntimeError, 'selected glslang version'):
                    module.verify_glslang(source, os.defpath)

    def test_selected_host_version_and_mesa_path_precedence(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            mesa = source / 'external/mesa'
            mesa.mkdir(parents=True)
            (mesa / 'meson.build').write_text("project('mesa', meson_version : '>= 1.4.0')\n")
            for selected in ('0.61.2', '1.3.99', '1.4.0', '1.9.2'):
                with patch.dict(os.environ, PYTHONPATH='/synthetic/new-meson/modules'), \
                        patch.object(module.subprocess, 'check_output', return_value=selected+'\n') as call:
                    if selected in ('0.61.2', '1.3.99'):
                        with self.assertRaisesRegex(RuntimeError, 'requires >= 1.4.0'):
                            module.verify(source, '/synthetic/new-meson/bin')
                    else:
                        self.assertEqual(module.verify(source, '/synthetic/new-meson/bin'), selected)
                    self.assertEqual(call.call_args.kwargs['env']['PATH'], '/usr/bin:/usr/local/bin:/synthetic/new-meson/bin')
                    self.assertNotIn('PYTHONPATH', call.call_args.kwargs['env'])

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
