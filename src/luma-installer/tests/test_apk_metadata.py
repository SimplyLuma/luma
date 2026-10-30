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
