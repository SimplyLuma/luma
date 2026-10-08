# SPDX-License-Identifier: Apache-2.0
"""Applications that come as a .tar.gz, a .zip or an unpacked folder."""
import io
import pathlib
import struct
import sys
import tarfile
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src/luma-installer"))

from luma_installer import archive_app  # noqa: E402
from luma_installer.errors import InstallerError  # noqa: E402
from luma_installer.model import kind_for_path  # noqa: E402

ELF_X86_64 = b"\x7fELF\x02\x01\x01" + b"\x00" * 9 + struct.pack("<HH", 2, 62) + b"\x00" * 100


def tar_with(path, entries):
    with tarfile.open(path, "w:gz") as archive:
        for name, data, mode, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                archive.addfile(info)
            elif kind == "link":
                info.type, info.linkname = tarfile.SYMTYPE, data
                archive.addfile(info)
            else:
                info.size, info.mode = len(data), mode
                archive.addfile(info, io.BytesIO(data))


class PortableApplications(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_archives_and_folders_are_portable_applications(self):
        for name in ("Charlie-1.0.1-Linux-x64.tar.gz", "tool.tgz", "Game.tar.xz", "App.zip"):
            self.assertEqual(kind_for_path(pathlib.Path(name)), "portable")
        self.assertEqual(kind_for_path(self.root), "portable")
        self.assertEqual(kind_for_path(pathlib.Path("notes.gz")), None)

    def test_a_flutter_release_finds_its_program_icon_and_kind(self):
        path = self.root / "Charlie-1.0.1-Linux-x64.tar.gz"
        tar_with(path, [
            ("Charlie-1.0.1-Linux-x64", b"", 0, "dir"),
            ("Charlie-1.0.1-Linux-x64/charlie", ELF_X86_64, 0o755, "file"),
            ("Charlie-1.0.1-Linux-x64/lib/libapp.so", ELF_X86_64, 0o755, "file"),
            ("Charlie-1.0.1-Linux-x64/data/flutter_assets/assets/icon/icon.png", b"\x89PNG" + b"0" * 64, 0o644, "file"),
        ])
        layout = archive_app.analyse(path)
        self.assertEqual((layout.name, layout.executable, layout.architecture, layout.toolkit),
                         ("Charlie", "charlie", "x86_64", "flutter"))
        self.assertEqual(layout.icon, "data/flutter_assets/assets/icon/icon.png")
        destination = self.root / "app"
        archive_app.extract(path, destination, layout)
        self.assertTrue((destination / "charlie").stat().st_mode & 0o100)
        self.assertTrue((destination / "lib/libapp.so").is_file())

    def test_a_launcher_inside_the_archive_names_the_application(self):
        path = self.root / "thing.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Thing/thing.desktop", "[Desktop Entry]\nName=Thing Studio\nExec=bin/thing-bin %U\nIcon=thing\n")
            info = zipfile.ZipInfo("Thing/bin/thing-bin"); info.external_attr = (0o100755 << 16)
            archive.writestr(info, ELF_X86_64)
            archive.writestr("Thing/share/thing.png", b"\x89PNG")
        layout = archive_app.analyse(path)
        self.assertEqual((layout.name, layout.executable, layout.icon), ("Thing Studio", "bin/thing-bin", "share/thing.png"))

    def test_members_that_leave_the_folder_are_refused(self):
        for entries in (
            [("../evil", b"x", 0o644, "file")],
            [("/etc/evil", b"x", 0o644, "file")],
            [("App/app", ELF_X86_64, 0o755, "file"), ("App/escape", "../../etc/passwd", 0, "link")],
            [("App/app", ELF_X86_64, 0o755, "file"), ("App/abs", "/etc/passwd", 0, "link")],
        ):
            path = self.root / "bad.tar.gz"
            tar_with(path, entries)
            with self.assertRaises(InstallerError):
                archive_app.analyse(path)

    def test_an_archive_without_a_program_says_so(self):
        path = self.root / "photos.tar.gz"
        tar_with(path, [("photos/a.png", b"\x89PNG", 0o644, "file")])
        with self.assertRaisesRegex(InstallerError, "does not contain a Linux application"):
            archive_app.analyse(path)

    def test_an_unpacked_folder_is_reviewed_as_one_fingerprinted_file(self):
        import os
        from luma_installer.inspectors import pack_folder
        folder = self.root / "Charlie-1.0.1-Linux-x64"
        (folder / "lib").mkdir(parents=True)
        (folder / "charlie").write_bytes(ELF_X86_64); (folder / "charlie").chmod(0o755)
        (folder / "lib/libapp.so").write_bytes(ELF_X86_64)
        os.environ["XDG_CACHE_HOME"] = str(self.root / "cache")
        first = pack_folder(folder).read_bytes()
        second = pack_folder(folder).read_bytes()
        self.assertEqual(first, second)
        layout = archive_app.analyse(pack_folder(folder))
        self.assertEqual((layout.top, layout.executable), ("Charlie-1.0.1-Linux-x64", "charlie"))


if __name__ == "__main__":
    unittest.main()
