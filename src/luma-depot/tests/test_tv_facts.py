# SPDX-License-Identifier: Apache-2.0
"""Depot on a TV: which apps run on a TV and how they are driven, the catalogue's tv and
keywords fields, search by the names people arrive with, and the runtime wide tile."""

from pathlib import Path
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[3]
for path in ("src/luma-platform/appkit", "src/luma-installer", "src/luma-depot"):
    sys.path.insert(0, str(REPO_ROOT / path))

from luma_depot import tv_facts  # noqa: E402
from luma_depot.tv_facts import AppStreamFacts, TvFacts  # noqa: E402
from luma_installer import depot_catalog  # noqa: E402


class FakeDesktop:
    """The keys of a desktop entry that tv_facts reads."""

    def __init__(self, **keys):
        self.keys = keys

    def has_key(self, key):
        return key in self.keys

    def get_boolean(self, key):
        return self.keys[key] == "true"

    def get_string(self, key):
        return self.keys.get(key)


def meta(**controls):
    return AppStreamFacts({k: frozenset(v) for k, v in controls.items()})


class FromAppStream(unittest.TestCase):
    def test_remote_anywhere_is_the_remote_even_with_a_recommended_pad(self):
        # Kodi recommends every control: it works with the remote.
        facts = tv_facts.from_appstream(meta(recommends={"keyboard", "pointing", "tv-remote", "gamepad"}))
        self.assertEqual(facts, TvFacts(True, tv_facts.REMOTE, "app"))

    def test_required_pad_needs_a_controller(self):
        facts = tv_facts.from_appstream(meta(requires={"gamepad"}, supports={"tv-remote"}))
        self.assertEqual(facts.input, tv_facts.CONTROLLER)

    def test_pad_alone_needs_a_controller(self):
        self.assertEqual(tv_facts.from_appstream(meta(supports={"gamepad"})).input, tv_facts.CONTROLLER)

    def test_keyboard_and_pointer_alone_are_no_tv_facts(self):
        self.assertIsNone(tv_facts.from_appstream(meta(supports={"keyboard", "pointing", "touch"})))
        self.assertIsNone(tv_facts.from_appstream(AppStreamFacts({})))

    def test_a_small_display_limit_keeps_it_off_a_tv(self):
        facts = tv_facts.from_appstream(AppStreamFacts({"supports": frozenset({"tv-remote"})}, display_max=760))
        self.assertFalse(facts.runs)


class Resolve(unittest.TestCase):
    def test_tv_presentation_is_driven_with_the_remote(self):
        info = FakeDesktop(**{"X-Luma-TV": "true", "X-Luma-TV-Presentation": "tv"})
        self.assertEqual(tv_facts.resolve(None, info, AppStreamFacts({})).input, tv_facts.REMOTE)

    def test_opting_out_keeps_it_off_a_tv(self):
        info = FakeDesktop(**{"X-Luma-TV": "false"})
        self.assertFalse(tv_facts.resolve(None, info, meta(supports={"tv-remote"})).runs)

    def test_full_screen_opt_in_without_controls_is_best_with_a_keyboard(self):
        info = FakeDesktop(**{"X-Luma-TV": "true", "X-Luma-TV-Presentation": "fullscreen"})
        self.assertEqual(tv_facts.resolve(None, info, AppStreamFacts({})).input, tv_facts.KEYBOARD)

    def test_the_catalogue_wins(self):
        given = depot_catalog.TvFacts(True, "controller")
        facts = tv_facts.resolve(given, None, meta(supports={"tv-remote"}))
        self.assertEqual((facts.runs, facts.input, facts.source), (True, tv_facts.CONTROLLER, "catalogue"))
        self.assertFalse(tv_facts.resolve(depot_catalog.TvFacts(False), None, meta(supports={"tv-remote"})).runs)

    def test_no_facts_at_all_is_not_listed(self):
        self.assertIsNone(tv_facts.resolve(None, None, AppStreamFacts({})))


class Metainfo(unittest.TestCase):
    def test_reads_controls_length_keywords_and_brand(self):
        with tempfile.NamedTemporaryFile("w", suffix=".metainfo.xml", delete=False) as handle:
            handle.write("""<?xml version="1.0"?><component type="desktop-application"><id>x</id>
<recommends><control>tv-remote</control><control>gamepad</control></recommends>
<requires><display_length compare="ge">360</display_length></requires>
<keywords><keyword>music</keyword><keyword xml:lang="de">Musik</keyword></keywords>
<branding><color type="primary" scheme_preference="light">#ffffff</color>
<color type="primary" scheme_preference="dark">#123456</color></branding>
<categories><category>Game</category></categories></component>""")
        found = tv_facts._read_metainfo(handle.name, 0)
        Path(handle.name).unlink()
        self.assertEqual(found.controls["recommends"], frozenset({"tv-remote", "gamepad"}))
        self.assertEqual(found.display_max, 0)
        self.assertEqual(found.keywords, ("music",))
        self.assertEqual(found.brand, "#123456")
        self.assertEqual(found.categories, ("Game",))


class CatalogueFields(unittest.TestCase):
    def test_tv_and_keywords_are_optional_and_forgiving(self):
        self.assertEqual(depot_catalog._tv_facts({"runs": True, "input": "remote"}), depot_catalog.TvFacts(True, "remote"))
        self.assertEqual(depot_catalog._tv_facts({"runs": True, "input": "joystick"}), depot_catalog.TvFacts(True, ""))
        self.assertIsNone(depot_catalog._tv_facts({"runs": "yes"}))
        self.assertIsNone(depot_catalog._tv_facts(None))
        self.assertEqual(depot_catalog._keywords(["iTunes", "", 3, " Apple Music "]), ("iTunes", "Apple Music"))
        self.assertEqual(depot_catalog._keywords("iTunes"), ())

    def test_the_seed_names_where_people_arrive_from(self):
        catalog = depot_catalog.load_catalog(REPO_ROOT / "src/luma-installer/data/depot-catalog-4.json")
        tide = next(entry for entry in catalog.applications if entry.id == "tide")
        self.assertIn("iTunes", tide.keywords)
        self.assertTrue(all(entry.tv is None for entry in catalog.applications),
                        "no catalogue entry offers a download on a TV yet")


class Search(unittest.TestCase):
    def item(self, name, summary="", keywords=()):
        from luma_depot.providers import App
        return tv_facts.TvApp(App("catalog:" + name.lower(), name, summary), TvFacts(True, "remote"), None,
                              tuple(keywords), "", False, None)

    def test_names_people_arrive_with(self):
        tide = self.item("Tide", "Play music", ("iTunes", "Music"))
        self.assertTrue(tv_facts.matches(tide, "itunes"))
        self.assertTrue(tv_facts.matches(tide, "TIDE"))
        self.assertFalse(tv_facts.matches(tide, "photoshop"))
        self.assertFalse(tv_facts.matches(tide, "  "))


class WideTile(unittest.TestCase):
    def test_composes_like_wide_tile_py(self):
        try:
            from luma_appkit.tv_tiles import compose_wide_tile
        except (ImportError, ValueError) as error:
            self.skipTest(f"needs GTK 4: {error}")
        icon = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024"><defs>'
                '<linearGradient id="pl"/><linearGradient id="rim"/>'
                '<clipPath id="c"><rect x="82" y="82" width="860" height="860" rx="202"/></clipPath></defs>'
                '<g id="plate"><rect fill="url(#pl)"/></g><g id="art" clip-path="url(#c)"><circle r="9"/></g>'
                '<g id="plate-rim"><rect stroke="url(#rim)"/></g></svg>')
        tile = compose_wide_tile(icon)
        self.assertIn('viewBox="0 0 1600 900"', tile)
        self.assertIn('<rect width="1600" height="900" rx="190" fill="url(#pl)"/>', tile)
        self.assertIn('transform="translate(211.2 -138.8) scale(1.15)"', tile)
        self.assertIn('stroke="url(#rim)"', tile)
        self.assertNotIn('clip-path="url(#c)"><circle', tile)
        self.assertIsNone(compose_wide_tile('<svg><defs></defs><g id="art"/></svg>'))


if __name__ == "__main__":
    unittest.main()
