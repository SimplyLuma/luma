#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Execute the graph container boundary with synthetic key files and runner.

No keys are generated/imported and no containers or production files change.
Root is required by the real boundary's key ownership checks.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'scripts/os/sign-update-graph.sh'

@unittest.skipUnless(os.getuid()==0,'real key ownership boundary requires root')
class Resources(unittest.TestCase):
    def test_signer_host_imports_leave_sealed_source_unchanged(self):
        prefix=SOURCE.read_text().split('set -euo pipefail',1)[1].splitlines()[1]
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'sealed_module.py').write_text('VALUE=1\n')
            subprocess.run(['bash','-c',prefix+'\ncd "$1"\npython3 -c "import sealed_module; assert sealed_module.VALUE==1"',
                            'test',str(p)],check=True)
            self.assertEqual({x.name for x in p.iterdir()},{'sealed_module.py'})
    def invocation(self,mode):
        text=SOURCE.read_text()
        function=text[text.index('luma_os_graph_tools() {'):text.index('# Validation and the previous-generation')]
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'keys').mkdir(mode=0o700);(p/'empty-hooks').mkdir();(p/'data').mkdir()
            for name in ('luma-update-graph.pub','luma-update-graph.key'):
                (p/'keys'/name).write_text('SYNTHETIC NON-KEY FIXTURE\n');(p/'keys'/name).chmod(0o600)
            setup='''set -euo pipefail
work=$1
minisign_dir=$work/keys
graph_tools_image=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
luma_os_die() { echo "$*" >&2; exit 1; }
luma_os_podman() { printf '%s\\0' "$@" >"$work/args"; }
'''
            subprocess.run(['bash','-c',setup+function+'\nluma_os_graph_tools "$2" "$work/data" minisign -V\n','test',str(p),mode],check=True)
            return (p/'args').read_bytes().decode().split('\0')[:-1]
    def test_signing_has_kdf_headroom(self):
        args=self.invocation('sign')
        self.assertIn('--memory=2g',args);self.assertIn('--memory-swap=2500m',args)
    def test_verification_keeps_lower_budget(self):
        args=self.invocation('verify')
        self.assertIn('--memory=1g',args);self.assertIn('--memory-swap=1250m',args)
    def test_verification_receives_no_private_key(self):
        args=self.invocation('verify')
        self.assertFalse(any('luma-update-graph.key' in x for x in args));self.assertIn('--network=none',args)
    def test_signing_private_key_remains_readonly(self):
        args=self.invocation('sign')
        keys=[x for x in args if 'luma-update-graph.key' in x]
        self.assertEqual(len(keys),1);self.assertTrue(keys[0].endswith(':ro'));self.assertIn('--read-only',args)

if __name__=='__main__':unittest.main()
