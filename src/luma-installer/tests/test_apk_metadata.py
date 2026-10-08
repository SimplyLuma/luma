import base64
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile
from luma_installer.apk_metadata import read_identity, identity_for_package

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
