# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from luma_tester import provision
from luma_tester.core import TesterError


class ProvisionTests(unittest.TestCase):
    def fixture(self, root):
        return SimpleNamespace(directory=root,root=root,
            manifest=dict(media='iso', arch='aarch64',image=dict(sha256='a'*64)))

    def test_only_approved_exact_architecture_and_bytes_are_automated(self):
        session=self.fixture(Path('/unused'))
        policy=dict(images=[dict(artifact_id='os/nightly/new/aarch64',sha256='a'*64)])
        with patch('luma_tester.image_approval.load',return_value=policy):
            self.assertTrue(provision.approved(session))
            session.manifest['arch']='x86_64'
            self.assertFalse(provision.approved(session))
            session.manifest['arch']='aarch64';session.manifest['image']['sha256']='b'*64
            self.assertFalse(provision.approved(session))

    def test_ready_requires_completed_receipt_matching_bytes_and_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);session=self.fixture(root)
            (root/'disk.qcow2').write_bytes(b'existing user data')
            self.assertFalse(provision.ready(session))
            (root/'setup.json').write_text(json.dumps(dict(phase='ready',sha256='a'*64)))
            self.assertFalse(provision.ready(session))
            (root/'workspace.qcow2').touch()
            self.assertTrue(provision.ready(session))
            session.manifest['image']['sha256']='b'*64
            self.assertFalse(provision.ready(session))
            self.assertEqual((root/'disk.qcow2').read_bytes(),b'existing user data')

    def test_setup_refuses_to_replace_a_workspace_without_a_valid_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);session=self.fixture(root)
            (root/'workspace.qcow2').write_bytes(b'valuable work')
            with patch.object(provision,'boot_files',return_value=(root,'LUMA')):
                with self.assertRaisesRegex(TesterError,'will not be overwritten'):
                    provision.prepare(session,None,{})
            self.assertEqual((root/'workspace.qcow2').read_bytes(),b'valuable work')

    def test_kickstart_limits_partitioning_to_private_disk_and_retains_media_trust(self):
        for arch,console in [('aarch64','ttyAMA0'),('x86_64','ttyS0')]:
            text=provision.kickstart(arch)
            self.assertIn('ignoredisk --only-use=vda',text)
            self.assertIn('clearpart --all --initlabel --disklabel=gpt --drives=vda',text)
            self.assertIn('%include /usr/share/anaconda/interactive-defaults.ks',text)
            self.assertNotIn('--nogpg',text)
            self.assertNotIn('sshkey',text)
            self.assertIn('AutomaticLogin=luma',text)
            self.assertIn('> /dev/'+console,text)

    def test_private_control_uses_only_valid_public_key(self):
        text=provision.kickstart('aarch64','ssh-ed25519 AAAA1234')
        self.assertIn('sshkey --username=luma "ssh-ed25519 AAAA1234"',text)
        self.assertIn('services --enabled=sshd',text)
        for key in ('ssh-ed25519 AAAA\nreboot','not-a-key','ssh-rsa AAAA'):
            with self.assertRaises(TesterError):provision.kickstart('x86_64',key)


if __name__=='__main__':unittest.main()
