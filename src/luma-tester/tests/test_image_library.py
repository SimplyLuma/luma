# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from luma_tester import image_library
from luma_tester.core import digest,read_manifest,TesterError


class LibraryTests(unittest.TestCase):
    def test_published_iso_is_verified_then_registered_without_becoming_mobile(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);iso=root/'image.iso'
            content=bytearray(32768+2048);content[32768:32775]=b'\x01CD001\x01';iso.write_bytes(content)
            artifact=dict(artifact_id='os/nightly/test/x86_64',kind='os_image',arch='x86_64',file='image.iso',
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
            self.assertEqual(image_library.refresh(root/'state',client),[artifact])
            self.assertEqual(image_library.cached(root/'state'),[artifact])

    def test_non_iso_payload_is_not_exposed_as_qemu_installation_media(self):
        item=dict(artifact_id='phone',kind='os_image',arch='aarch64',file='userdata.sparse.zst',
            url='https://example.invalid/image.zst',sha256='a'*64,size_bytes=100)
        self.assertFalse(image_library.usable(item))
        with self.assertRaises(TesterError):image_library.fetch(item,Path('/unused'),'mobile')
