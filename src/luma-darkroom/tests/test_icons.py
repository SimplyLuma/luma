# SPDX-License-Identifier: Apache-2.0
"""Every icon Darkroom names is one Adwaita 50 or Darkroom itself ships."""
from __future__ import annotations

import re
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path


ROOT = Path(__file__).parents[1]
GLYPHS = ROOT / "data" / "icons" / "hicolor" / "scalable" / "actions"
SPEC_CANDIDATES = (ROOT / "luma-darkroom.spec", ROOT.parents[1] / "packaging" / "rpm" / "luma-darkroom.spec")
SVG = "{http://www.w3.org/2000/svg}"

# Names Darkroom uses from adwaita-icon-theme 50.0 (Fedora 44), each confirmed
# to exist there. transform-crop, draw-brush, brush and color-gradient are not
# in it, which is why Darkroom ships its own crop, heal, brush and gradient.
ADWAITA_50 = {
    "changes-prevent-symbolic", "color-select-symbolic", "dialog-warning-symbolic",
    "document-open-symbolic", "document-properties-symbolic", "edit-clear-symbolic", "edit-copy-symbolic", "edit-redo-symbolic",
    "edit-undo-symbolic", "folder-symbolic", "folder-open-symbolic", "go-next-symbolic", "go-previous-symbolic", "go-down-symbolic", "go-up-symbolic", "image-x-generic-symbolic",
    "insert-text-symbolic", "list-add-symbolic", "media-record-symbolic", "object-select-symbolic",
    "pan-down-symbolic", "user-trash-symbolic", "view-dual-symbolic", "view-list-symbolic", "zoom-in-symbolic",
    "zoom-out-symbolic",
}


def referenced_icons() -> set[str]:
    source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "luma_darkroom").glob("*.py"))
    return set(re.findall(r"[\"']([a-z0-9][a-z0-9.-]*-symbolic)[\"']", source))


def shipped_glyphs() -> set[str]:
    return {path.stem for path in GLYPHS.glob("*.svg")}


class IconContractTest(unittest.TestCase):
    def test_every_referenced_icon_resolves(self) -> None:
        names = referenced_icons()
        unknown = sorted(names - ADWAITA_50 - shipped_glyphs())
        self.assertEqual(unknown, [], "icons neither Adwaita 50 nor Darkroom provides")
        self.assertIn("luma-darkroom-crop-symbolic", names)

    def test_every_shipped_glyph_is_used_and_packaged(self) -> None:
        spec = next((path for path in SPEC_CANDIDATES if path.is_file()), None)
        glyphs = shipped_glyphs()
        self.assertEqual(glyphs, {"luma-darkroom-crop-symbolic", "luma-darkroom-heal-symbolic",
                                  "luma-darkroom-brush-symbolic", "luma-darkroom-linear-gradient-symbolic"})
        self.assertEqual(sorted(glyphs - referenced_icons()), [])
        if spec is None:
            self.skipTest("the package spec is not beside this source tree")
        files = spec.read_text(encoding="utf-8").split("\n%files", 1)[1]
        for name in glyphs:
            self.assertIn(f"%{{_datadir}}/icons/hicolor/scalable/actions/{name}.svg", files)
        self.assertIn("%license LICENSE.md data/icons/LUCIDE-LICENSE.txt", files)

    def test_glyphs_follow_the_symbolic_conventions_and_name_their_source(self) -> None:
        for path in sorted(GLYPHS.glob("*.svg")):
            with self.subTest(path.name):
                text = path.read_text(encoding="utf-8")
                self.assertRegex(text.splitlines()[0],
                                 r"^<!-- (Lucide geometry \(lucide-static 1\.46\.0 icons/[a-z-]+\.svg\), ISC;"
                                 r"|Project Luma geometry in Lucide's 24px stroke conventions, Apache-2\.0:)")
                root = ElementTree.fromstring(text)
                self.assertEqual(root.get("viewBox"), "0 0 24 24")
                shapes = [element for element in root.iter() if element.tag != f"{SVG}svg"]
                self.assertTrue(shapes)
                for shape in shapes:
                    self.assertIn("foreground-stroke", shape.get("class", "").split())
                    self.assertIn("transparent-fill", shape.get("class", "").split())
                    self.assertEqual(shape.get("stroke-width"), "2")

    def test_lucide_notice_is_the_upstream_isc_license(self) -> None:
        notice = (ROOT / "data" / "icons" / "LUCIDE-LICENSE.txt").read_text(encoding="utf-8")
        self.assertTrue(notice.startswith("ISC License\n\nCopyright (c) 2026 Lucide Icons and Contributors"))

    def test_adwaita_names_exist_in_the_installed_theme(self) -> None:
        theme = Path("/usr/share/icons/Adwaita")
        if not (theme / "index.theme").is_file():
            self.skipTest("adwaita-icon-theme is not installed")
        installed = {path.stem for path in theme.rglob("*-symbolic.svg")}
        self.assertEqual(sorted(ADWAITA_50 - installed), [])


if __name__ == "__main__":
    unittest.main()
