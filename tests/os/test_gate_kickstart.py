# SPDX-License-Identifier: Apache-2.0
"""Run the gate kickstart's actual kargs selector for each installed target."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class GateKickstart(unittest.TestCase):
    def test_installed_target_selects_common_and_matching_kernel_arguments(self):
        template = (ROOT / 'tests/os/gate/luma-gate.ks.in').read_text()
        script = re.search(r'kargs=\$\(python3 .*?<<\'PY\'\n(.*?)\nPY\n\)',
                           template, re.S).group(1)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / '10-common.toml').write_text('kargs = ["quiet"]\n')
            (directory / '20-arm.toml').write_text(
                'match-architectures = ["aarch64"]\nkargs = ["arm-only"]\n')
            (directory / '30-x86.toml').write_text(
                'match-architectures = ["x86_64"]\nkargs = ["x86-only"]\n')
            for arch, argument in (('aarch64', 'arm-only'), ('x86_64', 'x86-only')):
                with self.subTest(architecture=arch):
                    result = subprocess.run([sys.executable, '-', temporary],
                                            input=script.replace('@ARCH@', arch),
                                            text=True, capture_output=True, check=True)
                    self.assertEqual(result.stdout.strip(), 'quiet ' + argument)
