# SPDX-License-Identifier: Apache-2.0
"""The new data-removal option cannot delete before a complete backup."""

from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from luma_depot.removal_data import backup_private_data, private_data_path, remove_backed_up_data


class RemovalDataTest(unittest.TestCase):
    def test_provider_reports_partial_success_after_flatpak_removal(self):
        from luma_depot import native
        from luma_depot.providers import Result

        provider = native.NativeInstallation()
        entry = SimpleNamespace(source_id="org.example.App", name="Example")
        ref = Mock()
        ref.get_name.return_value = "org.example.App"
        ref.get_appdata_version.return_value = "1.0"
        transaction = Mock()
        backup = Path("/tmp/depot-test-backup")
        results = []
        order = []

        def run_synchronously(work, callback, _cancellable=None):
            callback(Result(value=work()))

        def backed_up(*_args):
            order.append("backup")
            return backup

        def removed_data(*_args):
            order.append("data cleanup")
            raise OSError("private data is busy")

        transaction.run.side_effect = lambda _cancellable: order.append("flatpak removal")
        with (patch.object(provider, "_channel_entry", return_value=None),
              patch.object(provider, "_entry", return_value=entry),
              patch.object(provider, "_event") as event,
              patch.object(native, "installed_flatpak_refs", return_value={entry.source_id: (Mock(), ref)}),
              patch.object(native, "run_async", side_effect=run_synchronously),
              patch("luma_installer.depot_flatpak.removal_transaction", return_value=transaction),
              patch("luma_depot.removal_data.backup_private_data", side_effect=backed_up),
              patch("luma_depot.removal_data.remove_backed_up_data", side_effect=removed_data)):
            provider.remove(entry.source_id, keep_data=False, callback=results.append)

        self.assertEqual(order, ["backup", "flatpak removal", "data cleanup"])
        self.assertTrue(results[0].ok)
        self.assertEqual(results[0].value["backup"], str(backup))
        self.assertIn("private data is busy", results[0].value["data_removal_error"])
        event.assert_called_once_with(entry, "remove", "1.0")

    def test_backup_precedes_opt_in_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            data = home / ".var/app/org.example.App"
            data.mkdir(parents=True)
            (data / "notes.txt").write_text("keep this", encoding="utf-8")

            backup = backup_private_data("org.example.App", home)
            self.assertEqual((backup / "notes.txt").read_text(encoding="utf-8"), "keep this")
            self.assertTrue(data.exists())
            remove_backed_up_data("org.example.App", home, backup)
            self.assertFalse(data.exists())
            self.assertEqual((backup / "notes.txt").read_text(encoding="utf-8"), "keep this")

    def test_missing_backup_never_removes_data(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            data = home / ".var/app/org.example.App"
            data.mkdir(parents=True)
            with self.assertRaises(ValueError):
                remove_backed_up_data("org.example.App", home, home / "missing")
            self.assertTrue(data.exists())

    def test_rejects_link_and_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root = home / ".var/app"
            root.mkdir(parents=True)
            (root / "org.example.Link").symlink_to(home)
            with self.assertRaises(ValueError):
                private_data_path("org.example.Link", home)
            with self.assertRaises(ValueError):
                private_data_path("../outside", home)
            root.rename(home / ".var/old-app")
            root.symlink_to(home / ".var/old-app")
            with self.assertRaises(ValueError):
                private_data_path("org.example.App", home)

    def test_rejects_backup_directory_link(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / ".var/app/org.example.App"
            source.mkdir(parents=True)
            (source / "notes.txt").write_text("keep this", encoding="utf-8")
            backup_root = home / ".local/share/luma/depot/backups"
            backup_root.parent.mkdir(parents=True)
            backup_root.symlink_to(home / "outside")
            with self.assertRaises(ValueError):
                backup_private_data("org.example.App", home)
            self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
