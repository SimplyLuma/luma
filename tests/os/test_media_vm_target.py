#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the real UI verifier's ISO target parsing and VM launch arguments.

No VM is started: virt-install is a recording function, while the actual shell
selection and maintained target helper execute unchanged.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

class MediaVM(unittest.TestCase):
    def launch(self, record, host='x86_64'):
        script = (ROOT/'scripts/os/verify-media-ui.sh').read_text()
        selection = script.split('[ -f "$iso" ]', 1)[1].split('\nluma_os_require_root', 1)[0]
        invocation = script.split('virt-install --connect', 1)[1].split('\nstarted=$SECONDS', 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            iso = Path(tmp)/'fixture.iso'; iso.touch()
            Path(str(iso)+'.release').write_text(record)
            harness = '''set -euo pipefail
. "$1/scripts/os/lib/common.sh"
. "$1/scripts/os/lib/gate-vm.sh"
iso=$2; fixture_host=$3
uname() { printf '%s\\n' "$fixture_host"; }
''' + '[ -f "$iso" ]' + selection + '''
vm=$(dirname "$iso"); out=$vm; domain=fixture-only
luma_os_log() { :; }
virt-install() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@" >"$vm/arguments.json"; }
''' + 'virt-install --connect' + invocation + '\ncat "$vm/arguments.json"\n'
            env = {k:v for k,v in os.environ.items() if k not in
                   ('LUMA_TARGET_ARCHITECTURE','LUMA_OS_PROFILE','LUMA_OS_GATE_EMULATOR',
                    'LUMA_OS_GATE_UEFI_CODE','LUMA_OS_GATE_UEFI_VARS')}
            result = subprocess.run(['bash','-c',harness,'media-fixture',str(ROOT),str(iso),host],
                                    text=True,capture_output=True,env=env)
            return result

    def test_arm_iso_on_x86_uses_arm_tcg(self):
        result = self.launch('architecture=aarch64\nchannel=nightly\nref=luma/1/aarch64/nightly\n')
        self.assertEqual(result.returncode,0,result.stderr)
        args = json.loads(result.stdout)
        for flag,value in (('--arch','aarch64'),('--virt-type','qemu'),('--cpu','max'),('--machine','virt'),('--boot','uefi')):
            self.assertEqual(args[args.index(flag)+1],value)
        self.assertIn('--cdrom',args)
        self.assertEqual(args[args.index('--network')+1],'none')

    def test_retained_x86_record_without_architecture_is_supported(self):
        result = self.launch('channel=nightly\nref=luma/1/x86_64/nightly\n')
        self.assertEqual(result.returncode,0,result.stderr)
        args = json.loads(result.stdout)
        self.assertEqual(args[args.index('--arch')+1],'x86_64')
        self.assertEqual(args[args.index('--machine')+1],'q35')

    def test_explicit_target_must_match_payload(self):
        result = self.launch('architecture=aarch64\nchannel=nightly\nref=luma/1/x86_64/nightly\n')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('does not match its payload ref',result.stderr)

    def test_unknown_target_refused(self):
        result = self.launch('architecture=riscv64\nchannel=nightly\nref=luma/1/riscv64/nightly\n')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('invalid desktop target',result.stderr)

    def test_published_manifest_uses_target_namespace(self):
        script=(ROOT/'scripts/os/verify-media.sh').read_text()
        selection=script.split('# Select the metadata namespace',1)[1].split('\nvolid=',1)[0]
        manifest_line=next(line.strip() for line in script.splitlines() if line.strip().startswith('media_manifest='))
        for arch,namespace in (('aarch64','aarch64/'),('x86_64','')):
            shell='''set -euo pipefail
. "$1/scripts/os/lib/common.sh"
MEDIA_ARCHITECTURE=$2; MEDIA_REF=luma/1/$2/nightly
MEDIA_CHANNEL=nightly; MEDIA_VERSION=fixture-version
LUMA_OS_ROOT=/fixture-only
# Select the metadata namespace'''+selection+'\n'+manifest_line+'\nprintf "%s\\n" "$media_manifest"\n'
            result=subprocess.run(['bash','-c',shell,'manifest-fixture',str(ROOT),arch],capture_output=True,text=True,
                env={**os.environ,'LUMA_OS_PROFILE':'full','LUMA_TARGET_ARCHITECTURE':'x86_64'})
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),'/fixture-only/publish/public-repo/luma/releases/'+namespace+'fixture-version/manifest.json')

if __name__=='__main__': unittest.main()
