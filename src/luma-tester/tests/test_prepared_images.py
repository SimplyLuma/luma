# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from luma_tester import experiences, image_approval, image_library
from luma_tester.core import Session, TesterError, validate_manifest


def artifact(arch='x86_64'):
    return dict(artifact_id='tester/preview/20261027/'+arch,kind='tester_image',arch=arch,
        file='try-luma-'+arch+'.qcow2',url='https://example.invalid/'+arch+'.qcow2',
        sha256=('a' if arch=='x86_64' else 'b')*64,size_bytes=100,
        tester_profile=dict(schema=1,id='try-luma-'+arch.replace('_','-'),target='desktop',
            arch=arch,boot='uefi',cloud_init=True,media='disk',guest_protocol='luma-unified-v1',
            user='luma',build='Luma prepared preview'))


class PreparedImageTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('qemu-img'),'qemu-img required')
    def test_saved_installation_reopens_without_redownloading_removed_iso_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);item=artifact()
            manifest=dict(item['tester_profile'],image=dict(url='https://example.invalid/removed.iso',
                sha256='a'*64,bytes=100),media='iso',cloud_init=False)
            manifest.pop('guest_protocol')
            path=root/'manifest.json';path.write_text(json.dumps(manifest))
            session=Session(path,root/'state');session.directory.mkdir(parents=True,exist_ok=True)
            disk=session.directory/'workspace.qcow2'
            subprocess.run(['qemu-img','create','-q','-f','qcow2',str(disk),'32M'],check=True)
            (session.directory/'setup.json').write_text(json.dumps(dict(phase='ready',sha256='a'*64)))
            with patch.object(session,'tools',return_value={}),patch('luma_tester.core.build_opener',side_effect=AssertionError('Must not download')):
                session.prepare(report=lambda message:None)
            self.assertEqual(session.disk,disk)
            self.assertFalse(session.base.exists())

    def test_only_two_architecture_images_support_all_three_interfaces(self):
        items=[artifact(arch) for arch in ('x86_64','aarch64')]
        policy=dict(schema=1,generation=100,images=[],workspaces=items)
        image_approval.validate(policy)
        for item in items:
            profile=image_library.developer_profile(item)
            for target in ('desktop','tablet','mobile'):
                self.assertTrue(image_library.supports(item,target))
                self.assertTrue(experiences.compatible(target,profile))
        with self.assertRaises(TesterError):image_approval.validate(dict(policy,workspaces=[items[0],items[0]]))
        with self.assertRaises(TesterError):image_approval.validate(dict(policy,workspaces=items+[items[0]]))

    def test_prepared_pair_is_available_when_hub_catalog_is_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            items=[artifact(arch) for arch in ('x86_64','aarch64')]
            policy=dict(schema=1,generation=100,images=[],workspaces=items)
            with patch.object(image_approval,'load',return_value=policy):
                actual=image_library.refresh(Path(directory),SimpleNamespace(catalog=lambda: (_ for _ in ()).throw(OSError('offline'))))
            self.assertEqual(actual,items)

    def test_unapproved_old_prepared_images_do_not_reappear(self):
        item=artifact();policy=dict(schema=1,generation=100,images=[],workspaces=[item])
        self.assertTrue(image_library.listed(item,Path('/unused'),policy))
        self.assertFalse(image_library.listed(dict(item,sha256='c'*64),Path('/unused'),policy))

    def test_bootstrap_accepts_public_key_only_for_existing_luma_user(self):
        path=Path(__file__).resolve().parents[3]/'scripts/tester/prepared-firstboot.py'
        spec=importlib.util.spec_from_file_location('prepared_firstboot',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        config=dict(users=[dict(name='luma',ssh_authorized_keys=['ssh-ed25519 AAAA workspace'])],
                    write_files=[dict(path='/etc/luma-tester-target',content='mobile\n')])
        def parse():return module.seed_values('#cloud-config\n'+json.dumps(config))
        self.assertEqual(parse(),('ssh-ed25519 AAAA workspace','mobile'))
        config['users'][0]['name']='root'
        with self.assertRaises(ValueError):parse()
        config['users'][0]['name']='luma';config['users'][0]['ssh_authorized_keys']=['ssh-ed25519 AAAA\nreboot']
        with self.assertRaises(ValueError):parse()
        config['users'][0]['ssh_authorized_keys']=['ssh-ed25519 AAAA']
        config['write_files'][0]['path']='/etc/passwd'
        with self.assertRaises(ValueError):parse()

    def test_windows_remote_python_preserves_multiline_and_quoted_arguments(self):
        import shlex
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);item=artifact()
            manifest=dict(item['tester_profile'],image=dict(url=item['url'],sha256=item['sha256'],bytes=item['size_bytes']))
            path=root/'manifest.json';path.write_text(json.dumps(manifest))
            session=Session(path,root/'state');session.directory.mkdir(parents=True,exist_ok=True)
            (session.directory/'connection.json').write_text(json.dumps(dict(port=2228)))
            script="import sys\nprint(repr(sys.argv[1]))"
            argument="one 'quoted' line\nand another"
            with patch.object(session,'status',return_value='running'),patch('luma_tester.host.windows',return_value=True):
                command=session.ssh(['python3','-c',script,argument])[-1]
            self.assertNotIn('\n',command)
            result=subprocess.check_output(shlex.split(command),text=True)
            self.assertEqual(result,repr(argument)+'\n')
