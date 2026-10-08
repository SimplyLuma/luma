from pathlib import Path
import os, tempfile, unittest
from unittest.mock import patch
from luma_android.engine import WaydroidEngine, CommandResult
from luma_android.errors import RuntimeUnavailableError

class InstalledRegistry(unittest.TestCase):
    def test_framework_package_and_installed_apps_are_accepted(self):
        engine=WaydroidEngine()
        with patch.object(engine,'_run_as_android_package_manager',return_value=CommandResult(
                'package:android\npackage:org.example.App\n','',0)):
            self.assertEqual(engine.installed_packages(),{'android','org.example.App'})

    def test_empty_registry_never_authorizes_launcher_deletion(self):
        engine=WaydroidEngine()
        with patch.object(engine,'_run_as_android_package_manager',return_value=CommandResult('','',0)):
            with self.assertRaises(RuntimeUnavailableError):engine.installed_packages()

    def test_retained_data_is_not_an_installed_launcher(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ,{'XDG_DATA_HOME':temporary}):
            apps=Path(temporary)/'applications';apps.mkdir()
            removed=apps/'waydroid.org.example.Removed.desktop';removed.write_text('[Desktop Entry]\nName=Removed\n')
            native=apps/'native.desktop';native.write_text('[Desktop Entry]\nName=Native\n')
            engine=WaydroidEngine()
            with patch.object(engine,'_installed_packages_if_available',return_value={'android','org.example.Kept'}):
                engine.synchronize_launchers()
            self.assertFalse(removed.exists());self.assertTrue(native.exists())

if __name__=='__main__':unittest.main()
