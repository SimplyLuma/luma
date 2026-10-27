# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from luma_tester import updates
from luma_tester.core import TesterError


def artifact(release, platform):
    suffix = '.dmg' if platform.startswith('macos') else '.zip' if platform.startswith('windows') else '.AppImage'
    return dict(kind='tester_app', artifact_id=f'tester/{release}/{platform}',
        file='release'+suffix, url='https://example.invalid/release'+suffix,
        sha256='a'*64, size_bytes=100)


class UpdateTests(unittest.TestCase):
    def test_numeric_order_host_architecture_and_universal_fallback(self):
        items = [artifact('0.8.0','macos-universal'), artifact('0.10.0','macos-arm64'),
                 artifact('1.0.0','windows-x86_64'), artifact('0.9.0','macos-x86_64')]
        self.assertEqual(updates.latest(items, '0.7.2','macos','aarch64'),items[1])
        self.assertEqual(updates.latest(items, '0.7.2','macos','x86_64'),items[3])
        self.assertEqual(updates.latest(items[:1], '0.7.2','macos','x86_64'),items[0])
        self.assertIsNone(updates.latest(items,'1.0.0','macos','aarch64'))
        self.assertIsNone(updates.latest(items,'0.7.2','windows','aarch64'))

    def test_same_version_prefers_native_and_never_installs_a_preview_or_downgrade(self):
        universal,native=artifact('0.8.0','macos-universal'),artifact('0.8.0','macos-arm64')
        self.assertEqual(updates.latest([universal,native],'0.7.2','macos','aarch64'),native)
        self.assertIsNone(updates.latest([native],'0.8.0','macos','aarch64'))
        self.assertIsNone(updates.latest([native],'local-dev','macos','aarch64'))
        self.assertIsNone(updates.latest([artifact('0.9.0-rc1','macos-arm64')],'0.7.2','macos','aarch64'))

    def test_rejects_conflicting_identity_invalid_integrity_and_other_products(self):
        item=artifact('0.8.0','linux-x86_64')
        for change in (dict(version='9.0.0'),dict(platform='windows-x86_64'),
                       dict(sha256='bad'),dict(kind='os_image'),dict(file='run.sh')):
            candidate=dict(item,**change)
            self.assertIsNone(updates.latest([candidate],'0.7.2','linux','x86_64'))
            with self.assertRaises(TesterError):updates.package_identity(candidate)

    def test_stage_uses_resume_downloader_and_never_executes_or_trusts_filename_paths(self):
        item=artifact('0.8.0','linux-x86_64');item['file']='../../outside.AppImage'
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            with patch.object(updates,'download') as download:
                result=updates.stage(item,root)
            self.assertEqual(result,root/'updates/Luma-Tester-0.8.0-linux-x86_64.AppImage')
            self.assertEqual(download.call_args.kwargs['purpose'],'update')
            self.assertEqual(download.call_args.kwargs['experience'],'other')
            self.assertFalse(result.exists())


if __name__=='__main__':unittest.main()
