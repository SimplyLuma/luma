# SPDX-License-Identifier: Apache-2.0
"""The Studio sample and the list logic used by Depot's LumaUI port."""

from pathlib import Path
import tempfile
import unittest

from luma_depot.v70_data import installed_sorted, load_fixture, search


FIXTURE = next(
    candidate for candidate in (
        Path(__file__).resolve().parents[0] / "fixtures/depot-v70.json",
        Path(__file__).resolve().parents[3] / "tests/fixtures/depot-v70.json",
    ) if candidate.is_file()
)


class V70DataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.apps = load_fixture(FIXTURE)

    def test_fixture_matches_studio_sample(self):
        self.assertEqual(len(self.apps), 36)
        self.assertEqual(sum(app.installed for app in self.apps), 18)
        self.assertEqual(self.apps[0].id, "tide")
        self.assertEqual(self.apps[-1].id, "discord")
        self.assertEqual(next(app for app in self.apps if app.id == "discord").update, "1.0.160")

    def test_fixture_load_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "fixture.json"
            original = FIXTURE.read_bytes()
            copy.write_bytes(original)
            load_fixture(copy)
            self.assertEqual(copy.read_bytes(), original)
            self.assertEqual(set(Path(directory).iterdir()), {copy})

    def test_search_matches_v70_name_tagline_and_category(self):
        self.assertEqual({app.id for app in search(self.apps, "music")},
                         {"tide", "sessions", "spotify"})
        self.assertEqual(search(self.apps, "no such app"), ())

    def test_installed_sort_and_kinds(self):
        names = [app.name for app in installed_sorted(self.apps)]
        self.assertEqual(names, sorted(names, key=str.casefold))
        self.assertEqual(installed_sorted(self.apps, "size")[0].name, "Steam")
        self.assertEqual(next(app for app in self.apps if app.id == "filer").kind_label, "Part of Luma")
        self.assertEqual(next(app for app in self.apps if app.id == "discord").kind_label, "Universal .deb")


if __name__ == "__main__":
    unittest.main()
