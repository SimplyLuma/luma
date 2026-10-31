# SPDX-License-Identifier: Apache-2.0
"""Exercise graphics closure validation against real ELF dependency/version records."""
import importlib.util
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('tester_linux_graphics', REPO/'scripts/tester/linux_graphics.py')
graphics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(graphics)


@unittest.skipUnless(platform.system() == 'Linux' and shutil.which('gcc') and shutil.which('readelf')
                     and Path('/lib64/ld-linux-x86-64.so.2').is_file(), 'Linux ELF build tools required')
class GraphicsClosureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bundle = Path(self.temp.name)/'app'
        self.ui = self.bundle/'usr/lib64';self.ui.mkdir(parents=True)
        self.tools = self.bundle/'tools/lib';self.tools.mkdir(parents=True)
        shutil.copy2('/lib64/ld-linux-x86-64.so.2', self.ui/'ld-linux-x86-64.so.2')
        self.names = ['libEGL_mesa.so.0', 'libGLX_mesa.so.0', 'libGL.so.1', 'libGLESv2.so.2',
                      'libgbm.so.1', 'libgallium-test.so']
        for name in self.names:self.library(self.tools/name)

    def library(self, path, code='int probe(void) { return 1; }', flags=()):
        source=Path(self.temp.name)/'fixture.c';source.write_text(code)
        subprocess.run(['gcc','-shared','-fPIC','-nostdlib',str(source),'-Wl,-soname,'+path.name,
                        '-o',str(path),*flags],check=True,capture_output=True)

    def test_stages_real_elf_graphics_without_replacing_ui_runtime(self):
        marker=self.ui/'libgtk-4.so.1';marker.write_bytes(b'qualified custom GTK')
        libc=self.ui/'libc.so.6';libc.write_bytes(b'qualified UI libc')
        linked=graphics.stage_ui_graphics(self.bundle)
        self.assertEqual(set(linked),set(self.names))
        self.assertTrue(all((self.ui/n).resolve()==self.tools/n for n in self.names))
        self.assertEqual(marker.read_bytes(),b'qualified custom GTK')
        self.assertEqual(libc.read_bytes(),b'qualified UI libc')

    def test_missing_dynamic_driver_is_a_build_failure(self):
        (self.tools/'libGLX_mesa.so.0').unlink()
        with self.assertRaisesRegex(RuntimeError,'Missing private graphics dependency'):
            graphics.stage_ui_graphics(self.bundle)

    def test_external_driver_link_is_refused(self):
        external=Path(self.temp.name)/'external.so';self.library(external)
        (self.tools/'libgallium-test.so').unlink()
        (self.tools/'libgallium-test.so').symlink_to(external)
        with self.assertRaisesRegex(RuntimeError,'escapes'):
            graphics.stage_ui_graphics(self.bundle)

    def test_driver_symbol_version_incompatible_with_ui_is_rejected(self):
        script=Path(self.temp.name)/'versions.map'
        for version, directory in [('TEST_DRIVER_1',self.ui),('TEST_DRIVER_2',self.tools)]:
            script.write_text(version+' { global: future; };')
            self.library(directory/'libcompat.so','int future(void) { return 2; }',
                         ['-Wl,--version-script='+str(script)])
        self.library(self.tools/'libEGL_mesa.so.0',
                     'extern int future(void); int probe(void) { return future(); }',
                     ['-L'+str(self.tools),'-l:libcompat.so'])
        with self.assertRaisesRegex(RuntimeError,'does not match the UI runtime'):
            graphics.stage_ui_graphics(self.bundle)
