# SPDX-License-Identifier: Apache-2.0
"""The undeclared-library check follows the launcher's program (tree_check.py)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for "-m unittest tests.test_tree_check" as well as discover
from closure_fixtures import write_elf  # noqa: E402
from luma_installer import bus_names, tree_check

CACHE = """3 libs found in cache `/etc/ld.so.cache'
\tlibc.so.6 (libc6,x86-64) => /lib/x86_64-linux-gnu/libc.so.6
\tlibgtk-3.so.0 (libc6,x86-64) => /lib/x86_64-linux-gnu/libgtk-3.so.0
\tlibold.so.1 (libc6) => /lib/i386-linux-gnu/libold.so.1
"""


class LibraryCheck(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.shipped: list[str] = []

    def elf(self, path: str, soname: str = "", needed: tuple[str, ...] = (), executable: bool = False) -> None:
        target = self.root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        write_elf(target, soname, needed)
        if executable:
            target.chmod(0o755)
        self.shipped.append(path)

    def script(self, path: str, text: str) -> None:
        target = self.root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        target.chmod(0o755)
        self.shipped.append(path)

    def link(self, path: str, target: str) -> None:
        (self.root / path.lstrip("/")).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, self.root / path.lstrip("/"))

    def check(self, command: str) -> dict:
        programs = tree_check.launcher_programs(self.root, command, self.shipped)
        return tree_check.check(self.root, programs, self.shipped, CACHE)

    def test_a_wrapper_script_is_followed_to_the_program_it_names(self):
        self.elf("/opt/vendor/app/chrome", needed=("libc.so.6", "libnss3.so"), executable=True)
        self.elf("/opt/vendor/app/chrome_crashpad_handler", needed=("libc.so.6",), executable=True)
        self.script("/opt/vendor/app/vendor-app", '#!/bin/bash\nHERE="$(dirname "$0")"\nexec -a "$0" "$HERE/chrome" "$@"\n')
        self.link("/usr/bin/vendor-app-stable", "/opt/vendor/app/vendor-app")
        result = self.check("/usr/bin/vendor-app-stable %U")
        self.assertEqual(result["programs"], ["/opt/vendor/app/chrome"])
        self.assertEqual(result["missing"], ["libnss3.so"])

    def test_a_theme_shim_for_a_toolkit_the_program_never_loads_is_not_followed(self):
        self.elf("/opt/vendor/app/chrome", needed=("libc.so.6", "libgtk-3.so.0"), executable=True)
        self.elf("/opt/vendor/app/libqt5_shim.so", "libqt5_shim.so", ("libQt5Core.so.5", "libQt5Widgets.so.5", "libc.so.6"))
        self.link("/usr/bin/chrome", "/opt/vendor/app/chrome")
        result = self.check("chrome")
        self.assertEqual((result["missing"], result["plugin_missing"]), ([], []))

    def test_a_bundled_toolkit_is_found_by_name_and_its_plugins_are_checked(self):
        self.elf("/opt/zoom/zoom", needed=("libc.so.6", "libQt6Core.so.6"), executable=True)
        self.elf("/opt/zoom/Qt/lib/libQt6Core.so.6", "libQt6Core.so.6", ("libc.so.6", "libnss3.so"))
        self.elf("/opt/zoom/Qt/plugins/platforms/libqxcb.so", "libqxcb.so", ("libQt6Core.so.6", "libxcb-cursor.so.0"))
        result = self.check("/opt/zoom/zoom")
        # The bundled Qt is not "missing"; what it needs from the distribution is, and so is what its platform plugin needs.
        self.assertEqual(result["missing"], ["libnss3.so"])
        self.assertEqual(result["plugin_missing"], ["libxcb-cursor.so.0"])

    def test_a_library_of_another_architecture_does_not_satisfy_the_program(self):
        self.elf("/usr/bin/tool", needed=("libc.so.6", "libold.so.1"), executable=True)
        self.assertEqual(self.check("tool")["missing"], ["libold.so.1"])

    def test_a_command_that_resolves_to_nothing_checks_nothing(self):
        self.assertEqual(self.check("absent --flag")["programs"], [])
        self.assertEqual(tree_check.launcher_programs(self.root, "env FOO=1", []), [])

    def test_links_are_followed_inside_the_tree_only(self):
        self.elf("/usr/lib/app/real", needed=("libc.so.6",), executable=True)
        self.link("/usr/bin/app", "../lib/app/real")
        self.link("/usr/bin/escape", "/../../../../etc/passwd")
        self.assertEqual(tree_check.resolve_in(self.root, "/usr/bin/app"), self.root / "usr/lib/app/real")
        self.assertIsNone(tree_check.resolve_in(self.root, "/usr/bin/escape"))


class BusNames(unittest.TestCase):
    def test_declared_names_come_from_launchers_service_files_and_appstream(self):
        files = ["/usr/share/applications/org.vendor.App.desktop", "/usr/share/applications/plain.desktop",
                 "/usr/share/dbus-1/services/whatever.service", "/usr/share/metainfo/vendor.metainfo.xml",
                 "/usr/share/doc/x/other.service"]
        contents = {"/usr/share/dbus-1/services/whatever.service": "[D-BUS Service]\nName=org.vendor.App.Helper\nExec=/usr/bin/x\n",
                    "/usr/share/metainfo/vendor.metainfo.xml": "<component><id>org.vendor.App.desktop</id></component>"}
        names = bus_names.select(bus_names.declared(files, contents.get))
        self.assertEqual(names, ["org.vendor.App", "org.vendor.App.Helper"])

    def test_embedded_names_have_to_carry_the_packages_own_name(self):
        words = bus_names.identity_words("featherpad", "FeatherPad")
        tokens = ["org.featherpad.FeatherPad", "org.freedesktop.DBus", "libfeatherpad.so.1", "www.featherpad.org",
                  "featherpad.desktop", "org.kde.StatusNotifierWatcher", "1.3.featherpad", "org.featherpad.FeatherPad"]
        self.assertEqual(bus_names.embedded(tokens, words), ["org.featherpad.FeatherPad"])

    def test_a_packages_name_is_not_its_packaging(self):
        self.assertEqual(bus_names.identity_words("google-chrome-stable"), {"google", "chrome", "googlechrome"})
        self.assertEqual(bus_names.identity_words("deb-zoom-0123456789ab"), {"zoom"})

    def test_the_sessions_own_names_are_never_granted(self):
        granted = bus_names.select(["org.freedesktop.secrets", "org.gnome.Shell", "org.mpris.MediaPlayer2.x", "io.luma.Valet",
                                    "not a name", "single", "com.example.Fine"])
        self.assertEqual(granted, ["com.example.Fine"])
        self.assertEqual(bus_names.proxy_arguments(granted), ["--own=com.example.Fine", "--own=com.example.Fine.*"])

    def test_the_scan_is_a_fixed_pattern_over_the_packages_own_programs(self):
        command = bus_names.scan_command({"featherpad", "x;rm"}, ["/usr/bin/featherpad"])
        self.assertEqual(command[:7], ["grep", "-a", "-o", "-h", "-i", "-E", "-m"])
        self.assertIn("(featherpad)", command[8])
        self.assertIsNone(bus_names.scan_command({"ab"}, ["/usr/bin/x"]))


if __name__ == "__main__":
    unittest.main()
