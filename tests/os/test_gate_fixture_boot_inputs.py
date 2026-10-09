#!/usr/bin/python3
"""Run real gate setup helpers against disposable boot and guest filesystems."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]

class BootPolicy(unittest.TestCase):
    def run_policy(self, references, available, theme="desktop-color: \"#000000\"\n"):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        base=Path(self.tmp.name);boot=base/'boot/grub2';boot.mkdir(parents=True)
        source=base/'share';(source/'grub-theme').mkdir(parents=True)
        cfg=boot/'grub.cfg';cfg.write_text('load_env\nblscfg\n')
        (source/'luma.cfg').write_text(''.join('loadfont $prefix/themes/luma/'+name+'\n' for name in references)+'blscfg\n')
        (source/'grub-theme/theme.txt').write_text(theme)
        for name in available:(source/'grub-theme'/name).write_bytes(b'fixture font')
        (base/'ostree-booted').touch();binary=base/'bin';binary.mkdir()
        editor=binary/'grub2-editenv';editor.write_text('#!/bin/sh\nif [ "$2" = set ]; then shift 2; printf "%s\\n" "$@" > "$FIXTURE_ENV"; else cat "$FIXTURE_ENV"; fi\n');editor.chmod(0o755)
        script=(ROOT/'config/boot/apply-grub-policy.sh').read_text().replace('/run/ostree-booted',str(base/'ostree-booted')).replace('/boot/grub2/grub.cfg',str(cfg)).replace('/usr/share/luma/boot',str(source))
        path=base/'apply.sh';path.write_text(script)
        env={**os.environ,'PATH':str(binary)+':'+os.environ['PATH'],'FIXTURE_ENV':str(base/'grubenv')}
        return subprocess.run(['bash',str(path)],env=env,text=True,capture_output=True),boot,cfg
    def test_older_policy_needs_only_its_original_font(self):
        result,boot,cfg=self.run_policy(['prairie.pf2'],['prairie.pf2'])
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((boot/'themes/luma/prairie.pf2').is_file())
        self.assertIn('source $prefix/luma.cfg',cfg.read_text())
    def test_current_policy_keeps_all_referenced_sizes(self):
        names=['prairie.pf2','prairie-12.pf2','prairie-22.pf2']
        result,boot,_=self.run_policy(names,names)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual({p.name for p in (boot/'themes/luma').glob('*.pf2')},set(names))
    def test_missing_referenced_font_fails_before_grub_changes(self):
        result,boot,cfg=self.run_policy(['prairie.pf2','prairie-22.pf2'],['prairie.pf2'])
        self.assertNotEqual(result.returncode,0)
        self.assertIn('requires missing font prairie-22.pf2',result.stderr)
        self.assertEqual(cfg.read_text(),'load_env\nblscfg\n')
        self.assertFalse((boot/'themes').exists())

    def test_theme_referenced_missing_font_still_fails(self):
        result,_,_=self.run_policy(['prairie.pf2'],['prairie.pf2'],
                                  '# asset prairie-12.pf2 required\n')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('requires missing font prairie-12.pf2',result.stderr)
    def test_primary_font_remains_required(self):
        result,_,_=self.run_policy([],[])
        self.assertNotEqual(result.returncode,0)
        self.assertIn('requires missing font prairie.pf2',result.stderr)

class AgentEndpoint(unittest.TestCase):
    def setup_guest(self, preview, mirror_layout):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);root=Path(tmp.name)
        (root/'etc/luma').mkdir(parents=True);(root/'etc/ostree/remotes.d').mkdir(parents=True)
        remote=root/'etc/ostree/remotes.d/luma.conf'
        remote.write_text('[remote "luma"]\nurl='+('mirrorlist=file://'+str(root)+'/etc/luma/update-mirrorlist' if mirror_layout else 'http://192.0.2.1:1234')+'\ngpg-verify=true\ngpg-verify-summary=true\n')
        (root/'etc/luma/update-mirrorlist').write_text('http://192.0.2.1:1234\n')
        key=root/'keys';key.mkdir();(key/'gate.pub').write_text('fixture public key')
        source=(ROOT/'scripts/os/gate.sh').read_text();function=source[source.index('agent_setup() {'):source.index('\nagent_field() {')]
        prefix=r'''set -euo pipefail
fixture=$1
graph_key_dir=$fixture/keys
guest_gateway=192.0.2.1
http_port=5678
run_id=fixture
channel=nightly
luma_os_channel_is_preview() { return 1; }
guest() {
 local cmd=$2
 case "$cmd" in 'rpm -q luma-update'|'systemctl restart luma-updated.service'* ) return 0;; esac
 cmd=${cmd//\/etc\//$fixture\/etc\/}
 cmd=${cmd//systemctl stop luma-updated.timer/systemctl_fixture}
 bash -c "$cmd"
}
systemctl_fixture() { return 0; }
export -f systemctl_fixture
guest_copy() { cp "$2" "$fixture$3"; }
'''
        prefix=prefix.replace('luma_os_channel_is_preview() { return 1; }', 'luma_os_channel_is_preview() { '+('return 0;' if preview else 'return 1;')+' }')
        script=root/'setup.sh';script.write_text(prefix+function+'\nagent_setup fixture\n')
        result=subprocess.run(['bash',str(script),str(root)],text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        return root,remote
    def test_public_retained_mirror_is_current_without_enrollment(self):
        root,remote=self.setup_guest(False,True)
        self.assertEqual((root/'etc/luma/update-mirrorlist').read_text(),'http://192.0.2.1:5678\n')
        self.assertFalse((root/'etc/luma/update-preview-credential').exists())
        self.assertIn('gpg-verify=true',remote.read_text().replace(str(root),''));self.assertIn('gpg-verify-summary=true',remote.read_text().replace(str(root),''))
    def test_public_older_direct_url_becomes_current_mirror(self):
        root,remote=self.setup_guest(False,False)
        self.assertIn('url=mirrorlist=file:///etc/luma/update-mirrorlist',remote.read_text().replace(str(root),''))
        self.assertEqual((root/'etc/luma/update-mirrorlist').read_text(),'http://192.0.2.1:5678\n')
    def test_preview_mirror_layout_keeps_enrollment_contract(self):
        root,_=self.setup_guest(True,True)
        credential=root/'etc/luma/update-preview-credential'
        self.assertEqual(json.loads(credential.read_text())['channels'],['nightly'])
        self.assertEqual(credential.stat().st_mode & 0o777,0o600)
        self.assertEqual((root/'etc/luma/update-mirrorlist').read_text(),'http://192.0.2.1:5678\n')
    def test_preview_older_direct_url_keeps_private_mirror(self):
        root,remote=self.setup_guest(True,False)
        self.assertIn('url=mirrorlist=file:///etc/luma/update-preview-mirrorlist',remote.read_text().replace(str(root),''))
        path=root/'etc/luma/update-preview-mirrorlist'
        self.assertEqual(path.read_text(),'http://192.0.2.1:5678\n')
        self.assertEqual(path.stat().st_mode & 0o777,0o600)

if __name__=='__main__':unittest.main()
