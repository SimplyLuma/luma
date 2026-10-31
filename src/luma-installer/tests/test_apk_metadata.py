import base64
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile
from luma_installer.apk_metadata import read_identity, identity_for_package, metadata_for_package

class ApkIdentityTests(unittest.TestCase):
    def fixture(self, root):
        path=Path(root)/'versioned-name.apk'
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('AndroidManifest.xml',b'manifest')
            z.writestr('resources.arsc',b'resources')
            z.writestr('res/ap/a.png',b'\x89PNG\r\n\x1a\nartwork')
        return path

    def test_resource_reference_finds_obfuscated_artwork(self):
        with tempfile.TemporaryDirectory() as root:
            path=self.fixture(root)
            result=types.SimpleNamespace(stdout=json.dumps({'label':'TikTok','icon':'res/ap/a.png'}))
            with patch('luma_installer.apk_metadata.subprocess.run',return_value=result):
                value=read_identity(path)
            self.assertEqual(value['label'],'TikTok')
            self.assertEqual(base64.b64decode(value['artwork']),b'\x89PNG\r\n\x1a\nartwork')

    def test_control_characters_and_external_artwork_are_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            path=self.fixture(root)
            for icon in ('/etc/passwd','../secret','missing.png'):
                result=types.SimpleNamespace(stdout=json.dumps({'label':'App\nInjected','icon':icon}))
                with patch('luma_installer.apk_metadata.subprocess.run',return_value=result):
                    self.assertEqual(read_identity(path),{'label':'','artwork':''})

    def test_changed_package_cannot_publish_identity_cache(self):
        with tempfile.TemporaryDirectory() as root:
            path=self.fixture(root)
            result=types.SimpleNamespace(stdout=json.dumps({'label':'TikTok','artwork':''}))
            with patch.dict('os.environ',{'XDG_CACHE_HOME':root}), \
                 patch('luma_installer.apk_metadata.subprocess.run',return_value=result), \
                 patch('luma_installer.safety.fingerprint',return_value=(1,'b'*64)):
                with self.assertRaises(ValueError):
                    identity_for_package(path,'a'*64)
            self.assertEqual(list(Path(root).rglob('*.json')),[])

    def test_declared_version_and_package_are_read_without_execution(self):
        with tempfile.TemporaryDirectory() as root:
            path=self.fixture(root)
            result=types.SimpleNamespace(stdout=json.dumps({'label':'F-Droid','icon':'res/ap/a.png',
                                                            'version':'2.0.2','package':'org.fdroid.fdroid'}))
            with patch('luma_installer.apk_metadata.subprocess.run',return_value=result):
                value=read_identity(path)
            self.assertEqual(value['version'],'2.0.2')
            self.assertEqual(value['package'],'org.fdroid.fdroid')

    def test_invalid_declared_fields_are_not_presented_as_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            path=self.fixture(root)
            for version,package in [('2.0\nInjected','../other'), ('x'*257,'bad package')]:
                result=types.SimpleNamespace(stdout=json.dumps({'label':'App','version':version,'package':package}))
                with patch('luma_installer.apk_metadata.subprocess.run',return_value=result):
                    value=read_identity(path)
                self.assertNotIn('version',value)
                self.assertNotIn('package',value)

    def test_old_artwork_only_cache_does_not_hide_new_version(self):
        from luma_installer.safety import fingerprint
        with tempfile.TemporaryDirectory() as root:
            path=self.fixture(root);digest=fingerprint(path)[1]
            cache=Path(root)/'luma/installer/artwork';cache.mkdir(parents=True)
            (cache/(digest+'-apk-v1.json')).write_text(json.dumps({'label':'Old cached name','artwork':''}))
            result=types.SimpleNamespace(stdout=json.dumps({'label':'F-Droid','artwork':'',
                                                            'version':'2.0.2','package':'org.fdroid.fdroid'}))
            with patch.dict('os.environ',{'XDG_CACHE_HOME':root}), \
                 patch('luma_installer.apk_metadata.subprocess.run',return_value=result) as worker:
                value=metadata_for_package(path,digest)
            self.assertEqual(value['version'],'2.0.2')
            self.assertEqual(value['label'],'F-Droid')
            worker.assert_called_once()

    def test_split_archive_reads_base_identity_and_artwork(self):
        with tempfile.TemporaryDirectory() as root:
            base=self.fixture(root);bundle=Path(root)/'download.apkm'
            with zipfile.ZipFile(bundle,'w') as z:
                z.write(base,'base.apk');z.writestr('split_config.x86_64.apk',b'not selected')
            result=types.SimpleNamespace(stdout=json.dumps({'label':'App','package':'org.example.app','icon':'res/ap/a.png'}))
            with patch('luma_installer.apk_metadata.subprocess.run',return_value=result):
                value=read_identity(bundle)
            self.assertEqual(value['package'],'org.example.app')
            self.assertTrue(value['artwork'])

    def test_ambiguous_bundle_never_guesses_an_identity(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'ambiguous.apkm'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('first/base.apk',b'a');z.writestr('second/base.apk',b'b')
            with patch('luma_installer.apk_metadata.subprocess.run') as worker:
                with self.assertRaises(ValueError):read_identity(path)
                worker.assert_not_called()

    def test_webp_launcher_artwork_is_preserved_as_a_raster(self):
        with tempfile.TemporaryDirectory() as root:
            path=self.fixture(root);raw=b'RIFF'+b'\x04\x00\x00\x00'+b'WEBP'
            with zipfile.ZipFile(path,'a') as z:z.writestr('res/icon.webp',raw)
            result=types.SimpleNamespace(stdout=json.dumps({'label':'App','icon':'res/icon.webp'}))
            with patch('luma_installer.apk_metadata.subprocess.run',return_value=result):
                self.assertEqual(base64.b64decode(read_identity(path)['artwork']),raw)

    def test_apkx_uses_the_validated_split_archive_install_path(self):
        from luma_android.apk import inspect_android_package
        from luma_installer.model import kind_for_path
        with tempfile.TemporaryDirectory() as root:
            base=self.fixture(root);bundle=Path(root)/'download.APKX'
            with zipfile.ZipFile(bundle,'w') as z:z.write(base,'base.apk')
            value=inspect_android_package(bundle,64*1024*1024)
            self.assertEqual(kind_for_path(bundle),'android')
            self.assertEqual(value.package_format,'apkx')
            self.assertEqual(value.apk_entries,('base.apk',))
