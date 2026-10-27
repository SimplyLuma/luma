# SPDX-License-Identifier: Apache-2.0
import json
from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from luma_tester import image_approval,image_library
from luma_tester.core import TesterError


def artifact(build,arch='x86_64'):
    return dict(artifact_id=f'os/nightly/{build}/{arch}',kind='os_image',arch=arch,
        file='luma.iso',url='https://example.invalid/luma.iso',sha256='a'*64,size_bytes=100)


class ApprovalTests(unittest.TestCase):
    def test_remote_approval_is_persisted_and_an_older_generation_cannot_restore_history(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            new=dict(schema=1,generation=100,images=[dict(artifact_id='os/nightly/new/x86_64',sha256='a'*64)])
            def response(document,url=image_approval.URL):
                body=json.dumps(document).encode()
                return SimpleNamespace(open=lambda *args,**kwargs:nullcontext(SimpleNamespace(url=url,read=lambda limit:body)))
            with patch.object(image_approval,'build_opener',return_value=response(new)):
                self.assertEqual(image_approval.load(root,refresh=True),new)
            old=dict(schema=1,generation=99,images=[dict(artifact_id='os/nightly/old/x86_64',sha256='a'*64)])
            with patch.object(image_approval,'build_opener',return_value=response(old)):
                self.assertEqual(image_approval.load(root,refresh=True),new)
            with patch.object(image_approval,'build_opener',return_value=response(dict(new,generation=101),url='https://other.invalid/policy')):
                self.assertEqual(image_approval.load(root,refresh=True),new)

    def test_only_current_pair_is_visible_even_with_cached_old_catalog(self):
        current=artifact('20261027.2');arm=artifact('20261027.1','aarch64')
        old=artifact('20261026.9');future=artifact('20261028.1')
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);items=[old,future,current,arm]
            (root/'approved-images.json').write_text(json.dumps(dict(schema=1,generation=100,
                images=[dict(artifact_id=current['artifact_id'],sha256=current['sha256']),
                        dict(artifact_id=arm['artifact_id'],sha256=arm['sha256'])])))
            (root/'download-catalog.json').write_text(json.dumps(items))
            self.assertEqual(image_library.cached(root),[current,arm])
            self.assertEqual(image_library.refresh(root,SimpleNamespace(catalog=lambda:items)),[current,arm])

    def test_explicit_new_generation_can_replace_pair_but_never_expand_history(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            new=dict(schema=1,generation=100,images=[dict(artifact_id='os/nightly/20261028.1/x86_64',sha256='a'*64)])
            (root/'approved-images.json').write_text(json.dumps(new))
            policy=image_approval.load(root)
            self.assertTrue(image_approval.allows(artifact('20261028.1'),policy))
            self.assertFalse(image_approval.allows(artifact('20261027.2'),policy))
            self.assertFalse(image_approval.allows(dict(artifact('20261028.1'),sha256='b'*64),policy))
            with self.assertRaises(TesterError):
                image_approval.validate(dict(new,images=new['images']+[dict(artifact_id='os/nightly/old/x86_64',sha256='a'*64)]))

    def test_failed_or_rolled_back_channel_retains_cached_approval(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);new=dict(schema=1,generation=100,images=[])
            (root/'approved-images.json').write_text(json.dumps(new))
            with patch.object(image_approval,'build_opener',side_effect=OSError('offline')):
                self.assertEqual(image_approval.load(root,refresh=True),new)
            # A damaged cache fails back to the embedded curated pair, never all media.
            (root/'approved-images.json').write_text('broken')
            self.assertFalse(image_approval.allows(artifact('20261021.1'),image_approval.load(root)))
