import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from luma_relay.engine import WineEngine
from luma_relay.errors import RelayError


class RemovalTests(unittest.TestCase):
    def remove_in(self, base, keep=True, fail=False):
        apps = base / 'apps'; apps.mkdir(exist_ok=True)
        root = apps / 'example-app'; root.mkdir(exist_ok=True)
        (root / 'settings').write_text('preserve me')
        desktop = base / 'launchers'; desktop.mkdir(exist_ok=True)
        launcher = desktop / 'org.projectluma.Relay.Windows.example-app.desktop'
        launcher.touch()
        engine = object.__new__(WineEngine)
        def stop(*args, **kwargs):
            self.assertTrue(root.exists())
            if fail: raise RelayError('runtime unavailable')
            return subprocess.CompletedProcess([], 0)
        with patch('luma_relay.engine.read_manifest', return_value={'name':'Example'}), \
             patch('luma_relay.engine.capsule_root', return_value=root), \
             patch('luma_relay.engine.windows_apps_root', return_value=apps), \
             patch('luma_relay.engine.applications_directory', return_value=desktop), \
             patch.object(engine, '_run', side_effect=stop):
            engine.remove('example-app', keep_data=keep)
        self.assertFalse(root.exists())
        self.assertFalse(launcher.exists())
        return base / 'retained/example-app'

    def test_retention_preserves_unknown_data_without_active_launcher(self):
        with tempfile.TemporaryDirectory() as temporary:
            retained = self.remove_in(Path(temporary))
            self.assertEqual((retained / 'settings').read_text(), 'preserve me')

    def test_existing_retained_environment_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            retained = base / 'retained/example-app'; retained.mkdir(parents=True)
            (retained / 'old').write_text('old data')
            with self.assertRaisesRegex(RelayError, 'nothing was overwritten'):
                self.remove_in(base)
            self.assertEqual((retained / 'old').read_text(), 'old data')
            self.assertTrue((base / 'apps/example-app/settings').exists())
            self.assertTrue(next((base / 'launchers').glob('*.desktop')).exists())

    def test_explicit_data_removal_works_without_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertFalse(self.remove_in(Path(temporary), keep=False, fail=True).exists())

if __name__ == '__main__': unittest.main()
