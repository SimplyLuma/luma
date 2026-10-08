# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the image icon-reference check (no root, no network).

Run: python3 -m unittest discover -s tests/os -p 'test_*.py'
"""

import io
import os
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "os" / "lib"))

import icon_references  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "icon-references" / "app.gresource"
SVG = '<svg xmlns="http://www.w3.org/2000/svg"/>'


def elf_with_section(name: bytes, payload: bytes) -> bytes:
    """A minimal 64-bit little-endian ELF whose only named section is NAME."""
    strtab = b"\0" + name + b"\0.shstrtab\0"
    data_at = 64
    strtab_at = data_at + len(payload)
    shoff = strtab_at + len(strtab)
    header = b"\x7fELF" + bytes([2, 1, 1]) + bytes(9)
    header += struct.pack("<HHIQQQIHHHHHH", 1, 62, 1, 0, 0, shoff, 0, 64, 0, 0, 64, 3, 2)
    null = bytes(64)
    section = struct.pack("<IIQQQQIIQQ", 1, 1, 0, 0, data_at, len(payload), 0, 0, 1, 0)
    names = struct.pack("<IIQQQQIIQQ", 1 + len(name) + 1, 3, 0, 0, strtab_at, len(strtab), 0, 0, 1, 0)
    return header + payload + strtab + null + section + names


class IconReferenceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "image"
        self.source = Path(self.temporary.name) / "source"
        icons = self.root / "usr/share/icons"
        self.write(icons / "Prairie/index.theme", "[Icon Theme]\nName=Prairie\nInherits=Adwaita,hicolor\n")
        self.write(icons / "Prairie/symbolic/actions/go-next-symbolic.svg", SVG)
        self.write(icons / "Adwaita/index.theme", "[Icon Theme]\nName=Adwaita\n")
        self.write(icons / "Adwaita/symbolic/actions/edit-copy-symbolic.svg", SVG)
        self.write(icons / "hicolor/index.theme", "[Icon Theme]\nName=hicolor\n")
        self.write(icons / "hicolor/scalable/apps/org.example.App.svg", SVG)
        self.write(icons / "hicolor/16x16/status/legacy.symbolic.png", "png")
        # Installed but not in the desktop's theme chain: never drawn.
        self.write(icons / "breeze/actions/16/breeze-only-symbolic.svg", SVG)
        # An app that adds its own data directory to the theme search path.
        self.write(self.root / "usr/share/example/icons/private-symbolic.svg", SVG)
        (self.root / "usr/bin").mkdir(parents=True)
        (self.root / "usr/bin/example").write_bytes(elf_with_section(b".gresource.example", FIXTURE.read_bytes()))

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def run_check(self, *extra: str) -> tuple[int, str]:
        output = io.StringIO()
        with redirect_stdout(output):
            code = icon_references.main(["--root", str(self.root), "--source", str(self.source), *extra])
        return code, output.getvalue()

    def test_gvdb_matches_glib_compile_resources(self):
        keys = icon_references.gvdb_keys(FIXTURE.read_bytes())
        self.assertIn("/org/example/app/icons/scalable/actions/app-only-symbolic.svg", keys)
        self.assertEqual(icon_references.resource_icons(FIXTURE.read_bytes()), ["app-only-symbolic"])

    def test_elf_gresource_section(self):
        blobs = icon_references.elf_resources((self.root / "usr/bin/example").read_bytes())
        self.assertEqual(blobs, [FIXTURE.read_bytes()])

    def test_providers_follow_the_theme_chain(self):
        names = icon_references.collect_providers(self.root, "Prairie").names
        for name in ("go-next-symbolic", "edit-copy-symbolic", "org.example.App", "legacy-symbolic",
                     "private-symbolic", "app-only-symbolic"):
            self.assertIn(name, names)
        self.assertNotIn("breeze-only-symbolic", names)

    def test_every_source_kind_is_read(self):
        self.write(self.source / "app/window.ui",
                   '<object class="GtkButton"><property name="icon-name">go-next-symbolic</property>'
                   '</object><item><attribute name="icon">ui-menu-symbolic</attribute></item>')
        self.write(self.source / "app/window.py",
                   'b.set_icon_name("py-missing-symbolic")\nImage(icon_name="org.example.App")\n'
                   'x = f"luma-{kind}-symbolic"\nnames = ("device.icon-name", "api.bluez5.address")\n'
                   '__all__ = ("icon_name",\n    "is_listening")\n')
        self.write(self.source / "app/panel.js", "new St.Icon({icon_name: 'js-missing-symbolic'});\n")
        self.write(self.source / "app/view.c",
                   'gtk_button_set_icon_name (GTK_BUTTON (b), "c-missing");\n'
                   'g_object_set (image, "icon-name", "edit-copy-symbolic", NULL);\n')
        self.write(self.source / "app/style.css", 'image { -gtk-icon-source: -gtk-icontheme("css-missing-symbolic"); }\n')
        self.write(self.source / "app/org.example.App.desktop.in", "[Desktop Entry]\nIcon=desktop-missing\n")
        self.write(self.root / "usr/share/applications/org.example.App.desktop", "[Desktop Entry]\n")
        self.write(self.source / "app/tests/test_window.py", 'set_icon_name("test-only-symbolic")\n')
        code, output = self.run_check()
        self.assertEqual(code, 1)
        missing = {line.split()[2].rstrip(":") for line in output.splitlines()
                   if line.startswith("FAIL icon ") and not line.startswith("FAIL icon references")}
        self.assertEqual(missing, {"ui-menu-symbolic", "py-missing-symbolic", "js-missing-symbolic",
                                   "c-missing", "css-missing-symbolic", "desktop-missing"})
        self.assertIn("window.py:1", output)

    def test_patch_added_lines_only(self):
        patches = Path(self.temporary.name) / "patches"
        self.write(patches / "shell/0001-example.patch",
                   "--- a/js/ui/panel.js\n+++ b/js/ui/panel.js\n@@ -10,2 +10,3 @@\n"
                   " const a = 'removed-context-symbolic';\n"
                   "-const b = 'old-missing-symbolic';\n"
                   "+const b = 'new-missing-symbolic';\n"
                   "+const c = 'go-next-symbolic';\n"
                   "--- a/data/image.png\n+++ b/data/image.png\n")
        output = io.StringIO()
        with redirect_stdout(output):
            code = icon_references.main(["--root", str(self.root), "--patches", str(patches)])
        self.assertEqual(code, 1)
        self.assertIn("FAIL icon new-missing-symbolic", output.getvalue())
        self.assertIn("(js/ui/panel.js):11", output.getvalue())
        self.assertNotIn("old-missing", output.getvalue())
        self.assertNotIn("removed-context", output.getvalue())

    def test_allowlist_needs_a_reason_and_matches_paths(self):
        self.write(self.source / "a/window.py", 'AppWindow(icon_name="org.example.Gone")\n')
        allow = Path(self.temporary.name) / "allow.txt"
        allow.write_text("org.example.Gone  */b/*  # a different file\n")
        self.assertEqual(self.run_check("--allowlist", str(allow))[0], 1)
        allow.write_text("org.example.Gone  */a/window.py  # AppWindow discards icon_name\n")
        code, output = self.run_check("--allowlist", str(allow))
        self.assertEqual(code, 0, output)
        self.assertTrue(output.startswith("PASS icon references"))
        allow.write_text("org.example.Gone\n")
        with self.assertRaises(SystemExit):
            self.run_check("--allowlist", str(allow))

    def test_installed_first_party_code(self):
        self.write(self.root / "usr/lib/python3.14/site-packages/luma_example/window.py",
                   'Image(icon_name="installed-missing-symbolic")\n')
        self.write(self.root / "usr/share/applications/org.projectluma.Example.desktop", "Icon=launcher-missing\n")
        self.write(self.root / "usr/share/applications/org.thirdparty.App.desktop", "Icon=third-party-missing\n")
        code, output = self.run_check("--installed")
        self.assertEqual(code, 1)
        self.assertIn("installed-missing-symbolic", output)
        self.assertIn("launcher-missing", output)
        self.assertNotIn("third-party-missing", output)

    def test_overlay_of_candidate_packages(self):
        self.write(self.source / "app/window.py", 'b.set_icon_name("candidate-symbolic")\n')
        overlay = Path(self.temporary.name) / "candidate"
        self.assertEqual(self.run_check()[0], 1)
        # An extracted package with no index.theme still lands in the chain's directories.
        self.write(overlay / "usr/share/icons/hicolor/scalable/actions/candidate-symbolic.svg", SVG)
        self.assertEqual(self.run_check("--overlay", str(overlay))[0], 0)

    def test_components_missing_from_the_image_are_not_judged(self):
        self.write(self.source / "withdrawn/data/org.example.Gone.desktop", "Icon=org.example.Gone\n")
        self.write(self.source / "withdrawn/app.py", 'set_icon_name("gone-only-symbolic")\n')
        self.write(self.source / "library/lib.c", 'gtk_image_new_from_icon_name ("library-missing-symbolic");\n')
        code, output = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("library-missing-symbolic", output)
        self.assertNotIn("gone-only-symbolic", output)
        self.assertIn("not in the image, not checked: withdrawn", output)
        self.write(self.root / "usr/share/applications/org.example.Gone.desktop", "Icon=org.example.Gone\n")
        self.assertIn("gone-only-symbolic", self.run_check()[1])

    def nested_apps(self):
        for name in ("Included", "Excluded"):
            component = self.source / "external" / name.lower()
            self.write(component / "packaging/app.spec", f"Name: example-{name.lower()}\n")
            self.write(component / f"data/org.example.{name}.desktop.in", f"Icon=org.example.{name}\n")
        self.write(self.source / "external/included/window.py", 'set_icon_name("included-only-symbolic")\n')
        self.write(self.source / "external/excluded/window.py", 'set_icon_name("excluded-only-symbolic")\n')
        self.write(self.root / "usr/share/applications/org.example.Included.desktop", "Icon=org.example.Included\n")
        self.write(self.root / "usr/share/icons/hicolor/scalable/apps/org.example.Included.svg", SVG)
        self.write(self.root / "usr/share/included/icons/included-only-symbolic.svg", SVG)

    def test_nested_package_scope_excludes_only_uninstalled_apps(self):
        self.nested_apps()
        code, output = self.run_check("--installed")
        self.assertEqual(code, 0, output)
        self.assertIn("external/excluded", output)
        self.assertNotIn("FAIL icon excluded-only-symbolic", output)
        # An installed app's missing private resource must still fail.
        (self.root / "usr/share/included/icons/included-only-symbolic.svg").unlink()
        code, output = self.run_check("--installed")
        self.assertEqual(code, 1, output)
        self.assertIn("FAIL icon included-only-symbolic", output)

    def test_nested_package_scope_keeps_shared_and_patch_checks(self):
        self.nested_apps()
        self.write(self.source / "external/shared/library.py", 'set_icon_name("shared-missing-symbolic")\n')
        patches = Path(self.temporary.name) / "patches"
        self.write(patches / "shell/0001-icon.patch",
                   "--- a/panel.py\n+++ b/panel.py\n@@ -1 +1 @@\n+set_icon_name('patch-missing-symbolic')\n")
        code, output = self.run_check("--installed", "--patches", str(patches))
        self.assertEqual(code, 1, output)
        self.assertIn("FAIL icon shared-missing-symbolic", output)
        self.assertIn("FAIL icon patch-missing-symbolic", output)
        self.assertNotIn("FAIL icon excluded-only-symbolic", output)

    def test_nested_package_overlay_activates_candidate_app_checks(self):
        self.nested_apps()
        overlay = Path(self.temporary.name) / "candidate"
        self.write(overlay / "usr/share/applications/org.example.Excluded.desktop", "Icon=org.example.Excluded\n")
        code, output = self.run_check("--overlay", str(overlay))
        self.assertEqual(code, 1, output)
        self.assertIn("FAIL icon excluded-only-symbolic", output)

    def test_bad_root(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(icon_references.main(["--root", os.devnull]), 2)


if __name__ == "__main__":
    unittest.main()
