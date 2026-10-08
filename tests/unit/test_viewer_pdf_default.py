# SPDX-License-Identifier: Apache-2.0
"""Viewer is Luma's PDF default, and GNOME's Document Viewer never ships.

Source gate: every mimeapps list Luma ships must send each PDF type to Viewer,
Viewer must declare those types, org.gnome.Papers must be on the system
Flatpak replacement list and nowhere in an install list, and the once-per-
account migration must follow its rules.
"""

import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEWER = "org.projectluma.Viewer.desktop"
PDF_TYPES = ("application/pdf", "application/x-bzpdf", "application/x-gzpdf",
             "application/x-xzpdf", "application/x-ext-pdf")
SKIP_PARTS = {".git", "build", "artifacts", "tests", "docs", "node_modules"}


def load_migration():
    path = ROOT / "src/luma-desktop-launcher-policy/default-apps/luma-default-apps-migration"
    loader = importlib.machinery.SourceFileLoader("luma_default_apps_migration", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def shipped_mimeapps_lists():
    for path in ROOT.rglob("*mimeapps.list"):
        if not SKIP_PARTS.intersection(path.relative_to(ROOT).parts):
            yield path


def defaults(path):
    values, inside = {}, False
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("["):
            inside = line == "[Default Applications]"
        elif inside and "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


class ShippedDefaultsTests(unittest.TestCase):
    def test_desktop_policy_sends_every_pdf_type_to_viewer(self):
        policy = defaults(ROOT / "src/luma-desktop-launcher-policy/gnome-mimeapps.list")
        for mime in PDF_TYPES:
            self.assertEqual(policy.get(mime, "").split(";")[0], VIEWER, mime)

    def test_no_shipped_list_points_pdf_anywhere_else(self):
        lists = list(shipped_mimeapps_lists())
        self.assertTrue(lists)
        for path in lists:
            for mime, value in defaults(path).items():
                if mime in PDF_TYPES:
                    self.assertEqual(value.split(";")[0], VIEWER, f"{path}: {mime}={value}")

    def test_viewer_declares_every_pdf_type(self):
        entry = (ROOT / "src/luma-viewer/data/org.projectluma.Viewer.desktop").read_text()
        line = next(l for l in entry.splitlines() if l.startswith("MimeType="))
        declared = set(line.split("=", 1)[1].split(";"))
        for mime in PDF_TYPES:
            self.assertIn(mime, declared)


class PapersDoesNotShipTests(unittest.TestCase):
    def test_papers_flatpak_is_replaced_by_viewer(self):
        entries = {}
        for line in (ROOT / "config/desktop/system-flatpak-replacements.txt").read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                app, provider = line.split()
                entries[app] = provider
        self.assertEqual(entries.get("org.gnome.Papers"), "luma-viewer")
        self.assertEqual(entries.get("org.gnome.Evince"), "luma-viewer")

    def test_no_launcher_override_or_install_list_carries_papers(self):
        self.assertFalse((ROOT / "src/luma-desktop-launcher-policy/applications/org.gnome.Papers.desktop").exists())
        for path in [*(ROOT / "config").rglob("*"), *(ROOT / "image").rglob("*")]:
            if not path.is_file() or path.name == "system-flatpak-replacements.txt":
                continue
            try:
                text = path.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            for line in text.splitlines():
                if line.lstrip().startswith("#"):
                    continue
                self.assertNotIn("org.gnome.Papers", line, f"{path}: {line}")
                self.assertNotRegex(line, r"^\s*papers(-[a-z]+)?\s*$",
                                    f"{path} installs a Papers package: {line}")


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.m = load_migration()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.config = root / "config"
        self.data = root / "data"
        apps = self.data / "applications"
        apps.mkdir(parents=True)
        (apps / VIEWER).write_text("[Desktop Entry]\nName=Viewer\nMimeType=application/pdf;\n")
        (apps / "google-chrome.desktop").write_text(
            "[Desktop Entry]\nName=Chrome\nCategories=Network;WebBrowser;\n"
            "MimeType=application/pdf;x-scheme-handler/https;\n")
        (apps / "org.example.Reader.desktop").write_text(
            "[Desktop Entry]\nName=Reader\nCategories=Office;\nMimeType=application/pdf;\n")
        self.dirs = [self.data]

    def tearDown(self):
        self._tmp.cleanup()

    def run_with(self, text, name="mimeapps.list"):
        self.config.mkdir(parents=True, exist_ok=True)
        if text is not None:
            (self.config / name).write_text(text)
        report = self.m.migrate(self.config, self.dirs)
        return report, self.config / name

    def test_papers_evince_missing_and_uninstalled_become_viewer(self):
        report, path = self.run_with(
            "# mine\n[Default Applications]\napplication/pdf=org.gnome.Papers.desktop\n"
            "application/x-bzpdf=org.gnome.Evince.desktop;\n"
            "application/x-gzpdf=com.gone.App.desktop;\n"
            "x-scheme-handler/https=google-chrome.desktop\n\n[Added Associations]\n"
            "application/pdf=org.example.Reader.desktop;\n")
        result = defaults(path)
        for mime in PDF_TYPES:
            self.assertEqual(result[mime], VIEWER + ";", mime)
        self.assertEqual(result["x-scheme-handler/https"], "google-chrome.desktop")
        text = path.read_text()
        self.assertTrue(text.startswith("# mine\n"))
        self.assertIn("[Added Associations]\napplication/pdf=org.example.Reader.desktop;", text)
        self.assertEqual(report["changed"]["application/pdf"]["reason"], "replaced-stock-viewer")
        self.assertEqual(report["changed"]["application/x-gzpdf"]["reason"], "not-installed")
        self.assertEqual(report["changed"]["application/x-xzpdf"]["reason"], "missing")

    def test_browser_takeover_is_reset_and_reported(self):
        report, path = self.run_with("[Default Applications]\napplication/pdf=google-chrome.desktop\n")
        self.assertEqual(defaults(path)["application/pdf"], VIEWER + ";")
        self.assertEqual(report["changed"]["application/pdf"]["reason"], "browser-takeover")

    def test_deliberate_choice_is_kept(self):
        report, path = self.run_with(
            "[Default Applications]\napplication/pdf=org.example.Reader.desktop\n"
            "image/png=org.example.Reader.desktop;\n")
        result = defaults(path)
        self.assertEqual(result["application/pdf"], "org.example.Reader.desktop")
        self.assertEqual(result["image/png"], "org.example.Reader.desktop;")
        self.assertEqual(report["kept"]["application/pdf"]["reason"], "personal-choice")

    def test_first_installed_entry_decides(self):
        report, path = self.run_with(
            "[Default Applications]\napplication/pdf=com.gone.App.desktop;org.example.Reader.desktop;\n")
        self.assertIn("application/pdf", report["kept"])

    def test_no_personal_file_gets_a_default_section(self):
        report, path = self.run_with(None)
        self.assertEqual(defaults(path)["application/pdf"], VIEWER + ";")
        self.assertTrue(path.read_text().startswith("[Default Applications]\n"))

    def test_gnome_specific_list_is_fixed_where_it_lives(self):
        (self.config).mkdir(parents=True)
        (self.config / "mimeapps.list").write_text("[Default Applications]\napplication/pdf=org.example.Reader.desktop\n")
        report, gnome = self.run_with("[Default Applications]\napplication/pdf=org.gnome.Papers.desktop\n",
                                      name="gnome-mimeapps.list")
        self.assertEqual(defaults(gnome)["application/pdf"], VIEWER + ";")
        self.assertEqual(defaults(self.config / "mimeapps.list")["application/pdf"], "org.example.Reader.desktop")


if __name__ == "__main__":
    unittest.main()
