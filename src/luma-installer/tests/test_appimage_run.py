import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from luma_installer import appimage_run, backends, capsule_runtime
from luma_installer.desktop import write_launcher


class OwnBusNameTests(unittest.TestCase):
    def payload(self, *names):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name in names:
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text("")
        return root

    def test_appimage_owns_its_desktop_id(self):
        root = self.payload("io.github.nmcmil.Tiles.desktop", "AppRun")
        record = {"format": "appimage", "root": str(root)}
        self.assertEqual(capsule_runtime.own_bus_names(record), ["io.github.nmcmil.Tiles"])
        self.assertEqual(capsule_runtime.own_bus_arguments(record),
                         ["--own=io.github.nmcmil.Tiles", "--own=io.github.nmcmil.Tiles.*"])

    def test_appstream_id_and_window_class_count(self):
        root = self.payload("tool.desktop", "usr/share/metainfo/org.example.Tool.metainfo.xml")
        record = {"format": "portable", "root": str(root), "startup_wm_class": "org.example.Tool"}
        self.assertEqual(capsule_runtime.own_bus_names(record), ["org.example.Tool"])

    def test_package_capsule_uses_its_launcher_name(self):
        record = {"format": "deb", "desktop_path": "/usr/share/applications/com.example.Editor.desktop"}
        self.assertEqual(capsule_runtime.own_bus_names(record), ["com.example.Editor"])

    def test_session_services_are_never_granted(self):
        root = self.payload("org.freedesktop.Notifications.desktop", "org.gnome.Shell.desktop",
                            "io.luma.Valet.desktop", "org.projectluma.Depot.desktop", "code.desktop")
        self.assertEqual(capsule_runtime.own_bus_names({"format": "appimage", "root": str(root)}), [])


class OpenRunsAppImageTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.home)}))
        self.enterContext(mock.patch("luma_installer.desktop.refresh_icon_cache"))

    def file(self, name, header):
        path = self.home / name
        path.write_bytes(header + b"\0" * 64)
        return path

    def test_marker_or_name_makes_an_appimage(self):
        self.assertTrue(appimage_run.is_appimage(self.file("Tiles", b"\x7fELF\x02\x01\x01\x00AI\x02")))
        self.assertTrue(appimage_run.is_appimage(self.file("Tool.AppImage", b"#!/bin/sh\n")))
        self.assertFalse(appimage_run.is_appimage(self.file("tool", b"\x7fELF\x02\x01\x01\x00\x00\x00\x00")))
        self.assertFalse(appimage_run.is_appimage(self.home / "missing.AppImage"))

    def test_other_files_go_to_valet(self):
        package = self.file("tool.rpm", b"\xed\xab\xee\xdb")
        with mock.patch.object(appimage_run, "run") as run:
            self.assertIsNone(appimage_run.main_for_open([str(package)]))
            self.assertIsNone(appimage_run.main_for_open(["--remove", "x"]))
            run.assert_not_called()

    def test_appimage_runs_without_a_window(self):
        image = self.file("Tiles.AppImage", b"\x7fELF")
        with mock.patch.object(appimage_run, "run", return_value=0) as run:
            self.assertEqual(appimage_run.main_for_open([str(image)]), 0)
        run.assert_called_once()

    def test_unreadable_appimage_falls_back_to_valet(self):
        image = self.file("Broken.AppImage", b"not an elf")
        self.assertIsNone(appimage_run.run(image))

    def test_hidden_launcher_until_kept(self):
        records = self.home / "luma/installer/applications"
        records.mkdir(parents=True)
        record = {"application_id": "appimage-tiles-aaaa", "format": "appimage", "name": "Tiles",
                  "comment": "Slide and merge tiles", "icon": "tiles", "mime_types": ["application/x-tiles"],
                  "kept": False}
        (records / "appimage-tiles-aaaa.json").write_text(json.dumps(record))
        with mock.patch("luma_installer.desktop.register_handlers") as handlers:
            launcher = write_launcher("appimage-tiles-aaaa", "Tiles", "", "tiles",
                                      mime_types=["application/x-tiles"], hidden=True)
            self.assertIn("NoDisplay=true", launcher.read_text())
            self.assertNotIn("MimeType=", launcher.read_text())
            handlers.assert_called_once_with(launcher, [])
            kept = backends.keep_appimage(record)
        self.assertTrue(kept["kept"])
        text = launcher.read_text()
        self.assertNotIn("NoDisplay", text)
        self.assertIn("MimeType=application/x-tiles;", text)
        self.assertTrue(json.loads((records / "appimage-tiles-aaaa.json").read_text())["kept"])


if __name__ == "__main__":
    unittest.main()
