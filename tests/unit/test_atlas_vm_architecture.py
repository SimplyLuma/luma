# SPDX-License-Identifier: Apache-2.0
"""Check the actual disposable VM launch boundary without starting a guest."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/install/atlas-iso/vm-test.sh'


class AtlasVmArchitecture(unittest.TestCase):
    def launch(self, architecture, command):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / 'bin'; binary.mkdir()
            capture = root / 'arguments.json'
            for arch in ('x86_64', 'aarch64'):
                tool = binary / f'qemu-system-{arch}'
                tool.write_text('''#!/usr/bin/python3
import json, os, pathlib, sys
json.dump({'tool': pathlib.Path(sys.argv[0]).name, 'args': sys.argv[1:]},
          open(os.environ['CAPTURE'], 'w'))
args = sys.argv[1:]
pathlib.Path(args[args.index('-pidfile') + 1]).write_text('12345')
''')
                tool.chmod(0o755)
            environment = dict(os.environ, PATH=str(binary) + ':' + os.environ['PATH'],
                               ATLAS_ARCH=architecture, ATLAS_ACCEL='tcg', CAPTURE=str(capture))
            subprocess.run(['bash', str(SCRIPT), command, str(root / 'work'), str(root / 'test.iso')],
                           env=environment, check=True, capture_output=True, text=True)
            return json.loads(capture.read_text())

    def test_arm_uses_aa64_firmware_virtio_and_optical_iso(self):
        result = self.launch('aarch64', 'screen')
        self.assertEqual(result['tool'], 'qemu-system-aarch64')
        args = result['args']
        self.assertIn('virt,gic-version=3,accel=tcg', args)
        self.assertIn('scsi-cd,bus=scsi.0,drive=stick,bootindex=1', args)
        self.assertTrue(any('QEMU_EFI-pflash.raw' in arg for arg in args))
        self.assertIn('virtio-keyboard-pci', args)
        self.assertFalse(any('OVMF' in arg or 'usb-storage' in arg for arg in args))
        installed = self.launch('aarch64', 'installed')['args']
        self.assertFalse(any('stick' in arg for arg in installed))
        self.assertTrue(any('/vars.fd' in arg for arg in installed))

    def test_x86_retains_ovmf_q35_and_removable_usb(self):
        result = self.launch('x86_64', 'screen')
        self.assertEqual(result['tool'], 'qemu-system-x86_64')
        args = result['args']
        self.assertIn('q35,accel=tcg', args)
        self.assertIn('usb-storage,bus=xhci.0,drive=stick,removable=on,bootindex=1', args)
        self.assertTrue(any('OVMF_CODE.fd' in arg for arg in args))
        self.assertFalse(any('scsi-cd' in arg for arg in args))


if __name__ == '__main__':
    unittest.main()
