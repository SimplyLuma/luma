import base64
from contextlib import contextmanager
import sys
import types
import unittest
from unittest.mock import patch
from luma_installer.rpm_metadata import read_identity

class Entry:
    def __init__(self, path, data, size=None, regular=True):
        self.pathname=path; self.data=data; self.size=len(data) if size is None else size; self.isfile=regular
    def get_blocks(self):
        yield self.data

class MetadataTests(unittest.TestCase):
    def read(self, entries, desktops='/usr/share/applications/example.desktop'):
        @contextmanager
        def reader(_path):
            yield iter(entries)
        with patch.dict(sys.modules, {'libarchive':types.SimpleNamespace(file_reader=reader)}):
            return read_identity('fixture.rpm', desktops)
    def test_hidden_alias_does_not_obscure_visible_application(self):
        result=self.read([Entry('/usr/share/applications/example.desktop',b'[Desktop Entry]\nName=Example\nExec=/usr/bin/example\nIcon=example\n'),
            Entry('/usr/share/applications/alias.desktop',b'[Desktop Entry]\nName=Example\nNoDisplay=true\n')],
            ['/usr/share/applications/example.desktop','/usr/share/applications/alias.desktop'])
        self.assertEqual(result['entry']['name'],'Example')
    def test_distinct_visible_applications_remain_ambiguous(self):
        result=self.read([Entry('/usr/share/applications/example.desktop',b'[Desktop Entry]\nName=One\n'),
            Entry('/usr/share/applications/other.desktop',b'[Desktop Entry]\nName=Two\n')],
            ['/usr/share/applications/example.desktop','/usr/share/applications/other.desktop'])
        self.assertEqual(result['entry'],{})
    def test_opt_logo_requires_matching_archive_executable(self):
        link=Entry('/usr/bin/example',b'',regular=False)
        link.issym=True;link.linkpath='/opt/example/browser'
        result=self.read([link,Entry('/opt/example/product_logo_256.png',b'right'),
            Entry('/opt/other/product_logo_512.png',b'wrong'),
            Entry('/usr/share/applications/example.desktop',b'[Desktop Entry]\nName=Example\nExec=/usr/bin/example %U\nIcon=example\n')])
        self.assertEqual(base64.b64decode(result['artwork']),b'right')
    def test_artwork_before_launcher_and_largest_raster(self):
        result=self.read([Entry('./usr/share/icons/hicolor/48x48/apps/example.png',b'small'),
            Entry('./usr/share/icons/hicolor/256x256/apps/example.png',b'large'),
            Entry('./usr/share/applications/example.desktop',b'[Desktop Entry]\nName=Example\nIcon=example\n')])
        self.assertEqual(result['entry']['name'],'Example')
        self.assertEqual(base64.b64decode(result['artwork']),b'large')
    def test_symlink_and_traversal_are_not_artwork(self):
        result=self.read([Entry('./usr/share/icons/../example.png',b'bad'),
            Entry('./usr/share/icons/example.png',b'target',regular=False),
            Entry('./usr/share/applications/example.desktop',b'[Desktop Entry]\nName=Example\nIcon=example\n')])
        self.assertEqual(result['artwork'],'')
    def test_declared_size_cannot_hide_oversized_data(self):
        with self.assertRaises(ValueError):
            self.read([Entry('./usr/share/icons/example.png',b'oversized',size=1)])
    def test_truncated_member_rejected(self):
        with self.assertRaises(ValueError):
            self.read([Entry('./usr/share/icons/example.png',b'x',size=2)])

class CacheTests(unittest.TestCase):
    def test_exact_installed_receipt_avoids_payload_scan(self):
        import tempfile
        from pathlib import Path
        from luma_installer.facts import rpm_identity
        from luma_installer.model import PackageReport
        with tempfile.TemporaryDirectory() as root:
            icon = Path(root) / 'app.png'
            icon.write_bytes(b'icon')
            report = PackageReport(Path(root) / 'app.rpm', 'rpm', 'slug', '', 1, 'a'*64)
            record = dict(format='rpm', sha256='a'*64, name='Actual App',
                          icon=str(icon), launcher_metadata_version=3)
            with patch('luma_installer.desktop.iter_records', return_value=[record]), \
                    patch('luma_installer.facts.subprocess.run') as worker:
                self.assertEqual(rpm_identity(report, ['app.desktop']),
                                 ({'name': 'Actual App'}, str(icon)))
                worker.assert_not_called()

    def test_changed_package_cannot_publish_cached_identity(self):
        import json
        import os
        import tempfile
        from pathlib import Path
        from luma_installer.facts import rpm_identity
        from luma_installer.model import PackageReport
        from luma_installer.errors import InstallerError
        report=PackageReport(Path('/tmp/example.rpm'),'rpm','Example','',1,'a'*64)
        worker=types.SimpleNamespace(returncode=0,stdout=json.dumps({'entry':{'name':'Wrong'},'suffix':'','artwork':''}))
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,{'XDG_CACHE_HOME':root}), \
                patch('luma_installer.facts.subprocess.run',return_value=worker), \
                patch('luma_installer.safety.fingerprint',return_value=(1,'b'*64)):
            with self.assertRaises(InstallerError):
                rpm_identity(report,'/usr/share/applications/example.desktop')
            self.assertEqual(list(Path(root).rglob('*.json')),[])
