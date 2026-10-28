# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import tempfile
import shutil
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from luma_tester import image_library
from luma_tester.core import digest,read_manifest,TesterError


class LibraryTests(unittest.TestCase):
    def test_registered_download_survives_without_preferences_and_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);directory=root/'imported-images';directory.mkdir()
            self.assertEqual(image_library.manifest_paths(root),[])
            path=directory/'mobile.json';path.write_text('{}')
            self.assertEqual(image_library.manifest_paths(root),[path])
            self.assertEqual(image_library.manifest_paths(root,[path],directory),[path])

    def mobile_artifact(self):
        return dict(artifact_id='tester/mobile/nightly/test/aarch64',kind='tester_image',
            arch='aarch64',file='mobile.qcow2',url='https://example.invalid/mobile.qcow2',
            sha256='b'*64,size_bytes=100,tester_profile=dict(schema=1,id='mobile-arm-test',
                target='mobile',arch='aarch64',boot='uefi',cloud_init=True,user='luma',build='Test-only Mobile',
                display=dict(width=864,height=1920,scale=2),capabilities=dict(home=False,dynamic_display=False)))

    def test_catalog_includes_virtual_mobile_profiles_but_not_phone_flash_payloads(self):
        item=self.mobile_artifact()
        physical=dict(item,kind='os_image',file='userdata.sparse.zst')
        calls=[]
        def catalog(**filters):calls.append(filters);return [physical,item]
        with tempfile.TemporaryDirectory() as name, patch(
                "luma_tester.image_approval.load", return_value=dict(schema=1,generation=1,images=[])):
            self.assertEqual(image_library.refresh(Path(name),SimpleNamespace(catalog=catalog)),[item])
            self.assertEqual(image_library.cached(Path(name)),[item])
        self.assertEqual(calls,[{}])
        self.assertEqual(image_library.target(item),'mobile')

    def test_virtual_media_requires_matching_architecture_and_a_disk_boot_profile(self):
        item=self.mobile_artifact()
        item['tester_profile']['arch']='x86_64'
        self.assertFalse(image_library.usable(item))
        item=self.mobile_artifact();del item['tester_profile']
        self.assertFalse(image_library.usable(item))
        item=self.mobile_artifact();item['tester_profile'].update(media='iso',cloud_init=False)
        self.assertFalse(image_library.usable(item))

    @unittest.skipUnless(shutil.which('qemu-img'),'qemu-img required')
    def test_mobile_download_registers_verified_standalone_disk_and_authoritative_bytes(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);disk=root/'source.qcow2'
            subprocess.run(['qemu-img','create','-q','-f','qcow2',str(disk),'32M'],check=True)
            item=self.mobile_artifact();item.update(sha256=digest(disk),size_bytes=disk.stat().st_size)
            item['tester_profile']['image']=dict(url='file:///unrelated',sha256='c'*64,bytes=1)
            def fake_download(artifact,path,**kwargs):
                self.assertEqual(kwargs['experience'],'mobile')
                path.parent.mkdir(parents=True);path.write_bytes(disk.read_bytes())
            with patch.object(image_library,'download',side_effect=fake_download):
                manifest=read_manifest(image_library.fetch(item,root/'state','mobile'))
            self.assertEqual(manifest['target'],'mobile')
            self.assertTrue(manifest['cloud_init'])
            self.assertEqual(manifest['image']['sha256'],digest(disk))
            self.assertTrue(manifest['image']['url'].endswith('.qcow2'))
            self.assertEqual(manifest['display']['scale'],2)

    def test_published_iso_is_verified_then_registered_without_becoming_mobile(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);iso=root/'image.iso'
            content=bytearray(32768+2048);content[32768:32775]=b'\x01CD001\x01';iso.write_bytes(content)
            artifact=dict(artifact_id='os/nightly/20261027.2/x86_64',kind='os_image',arch='x86_64',file='image.iso',
                url='https://example.invalid/image.iso',sha256=digest(iso),size_bytes=iso.stat().st_size)
            def fake_download(item,target,**kwargs):
                self.assertEqual(kwargs['experience'],'tablet')
                target.parent.mkdir(parents=True);target.write_bytes(iso.read_bytes());return target
            with patch.object(image_library,'download',side_effect=fake_download):
                path=image_library.fetch(artifact,root/'state','tablet')
            manifest=read_manifest(path)
            self.assertEqual(manifest['target'],'desktop');self.assertFalse(manifest['cloud_init'])
            self.assertEqual(manifest['image']['sha256'],artifact['sha256'])
            client=SimpleNamespace(catalog=lambda **filters:[artifact])
            (root/'state/approved-images.json').write_text(json.dumps(dict(schema=1,generation=100,
                images=[dict(artifact_id=artifact['artifact_id'],sha256=artifact['sha256'])])))
            self.assertEqual(image_library.refresh(root/'state',client),[artifact])
            self.assertEqual(image_library.cached(root/'state'),[artifact])
            for mode in ('desktop', 'tablet', 'mobile'):
                self.assertTrue(image_library.supports(artifact, mode))
                from luma_tester import experiences
                self.assertTrue(experiences.compatible(mode, manifest))
            self.assertFalse(image_library.supports(artifact, 'tv'))

    def test_non_iso_payload_is_not_exposed_as_qemu_installation_media(self):
        item=dict(artifact_id='phone',kind='os_image',arch='aarch64',file='userdata.sparse.zst',
            url='https://example.invalid/image.zst',sha256='a'*64,size_bytes=100)
        self.assertFalse(image_library.usable(item))
        with self.assertRaises(TesterError):image_library.fetch(item,Path('/unused'),'mobile')
