"""Valet changes only the Flatpak setting the person chose."""
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch

from luma_installer.errors import InstallerError
from luma_installer.model import PackageReport
from luma_installer.valet_permissions import apply_flatpak_choices, network_declared
from luma_installer import workflow


class ValetPermissionTests(unittest.TestCase):
    def test_declared_network_comes_from_package_metadata(self):
        report = PackageReport(Path("bundle.flatpak"), "flatpak", "App", "", 1, "a" * 64,
                               details={"Context · shared": "ipc;network;"})
        self.assertTrue(network_declared(report))
        self.assertFalse(network_declared(PackageReport(
            Path("bundle.flatpak"), "flatpak", "App", "", 1, "a" * 64,
            details={"Context · shared": "ipc;"})))
        self.assertIsNone(network_declared(PackageReport(
            Path("app.flatpakref"), "flatpakref", "App", "", 1, "a" * 64)))

    def test_backup_precedes_write_and_unrelated_override_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "flatpak/overrides/org.example.App"
            original.parent.mkdir(parents=True)
            original.write_text("[Context]\nfilesystems=xdg-documents;\n", encoding="utf-8")
            backup = root / "luma/installer/permission-backups/org.example.App.json"
            calls = []

            def run(argv, **kwargs):
                self.assertEqual(kwargs["timeout"], 30)
                self.assertTrue(backup.is_file())
                saved = json.loads(backup.read_text(encoding="utf-8"))
                self.assertEqual(saved["original_override"], "[Context]\nfilesystems=xdg-documents;\n")
                self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
                calls.append(argv)
                return SimpleNamespace(returncode=0)

            record = {"format": "flatpak", "flatpak_id": "org.example.App", "installation": "user"}
            apply_flatpak_choices(record, {"network": False}, data_home=root, runner=run)
            self.assertEqual(calls, [["flatpak", "override", "--user", "--unshare=network", "org.example.App"]])
            self.assertEqual(original.read_text(encoding="utf-8"), "[Context]\nfilesystems=xdg-documents;\n")
            original.write_text("[Context]\nfilesystems=xdg-pictures;\n", encoding="utf-8")
            apply_flatpak_choices(record, {"network": True}, data_home=root, runner=run)
            self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["original_override"],
                             "[Context]\nfilesystems=xdg-documents;\n")
            self.assertEqual(calls[-1][3], "--share=network")

    def test_no_choice_does_not_touch_a_store_and_unknown_choice_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = {"format": "flatpak", "flatpak_id": "org.example.App"}
            apply_flatpak_choices(record, {}, data_home=root)
            self.assertEqual(list(root.iterdir()), [])
            with self.assertRaisesRegex(InstallerError, "unsupported"):
                apply_flatpak_choices(record, {"camera": False}, data_home=root)
            with self.assertRaisesRegex(InstallerError, "invalid"):
                apply_flatpak_choices({"format": "flatpak", "flatpak_id": "../outside"},
                                      {"network": False}, data_home=root)
            self.assertEqual(list(root.iterdir()), [])

    def test_workflow_passes_only_explicit_choice_after_install(self):
        report = PackageReport(Path("bundle.flatpak"), "flatpak", "App", "", 1, "a" * 64)
        record = {"format": "flatpak", "flatpak_id": "org.example.App", "application_id": "flatpak-org.example.App",
                  "sha256": "a" * 64}
        with patch("luma_installer.workflow.fingerprint", return_value=(1, "a" * 64)), \
             patch("luma_installer.workflow.backends.install", return_value="App"), \
             patch("luma_installer.workflow.iter_records", return_value=[record]), \
             patch("luma_installer.workflow.write_record") as write, \
             patch("luma_installer.valet_permissions.apply_flatpak_choices") as apply:
            workflow.install(report)
            apply.assert_not_called()
            workflow.install(report, {"network": False})
            apply.assert_called_once_with(record, {"network": False})
            self.assertEqual(write.call_count, 2)


if __name__ == "__main__":
    unittest.main()
