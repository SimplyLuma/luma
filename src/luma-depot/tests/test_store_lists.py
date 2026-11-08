# SPDX-License-Identifier: Apache-2.0
"""Store lists hold Depot's listings only (model/live.py, model/rules.py).

The fixture is a Luma computer as the installer reports it: the signed catalogue's entries plus every
launcher installed here, Android apps from the Android runtime and developer launchers among them.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE.parent), str(ROOT / "src/luma-platform/appkit"), str(ROOT / "src/luma-installer")]

from luma_installer.depot_inventory import visible_applications  # noqa: E402

from luma_depot.model import catalogue_v1, rules  # noqa: E402
from luma_depot.model.live import world_from  # noqa: E402
from luma_depot.providers import App, Catalogue, Category, InstalledApp  # noqa: E402

HOME = str(Path.home())


class Launcher:
    """A desktop entry as GAppInfo shows it."""

    def __init__(self, desktop_id, name, show=True, **keys):
        self.desktop_id, self.name, self.show, self.keys = desktop_id, name, show, keys

    def get_id(self): return self.desktop_id
    def get_name(self): return self.name
    def get_display_name(self): return self.name
    def should_show(self): return self.show
    def get_string(self, key): return self.keys.get(key)


def android(package, name):
    return Launcher(f"waydroid.{package}.desktop", name, Exec=f"waydroid app launch {package}")


LAUNCHERS = (
    android("com.gramgames.tenten", "1010!"), android("com.miniclip.eightballpool", "8 Ball Pool"),
    android("com.rovio.angrybirds", "Angry Birds"), android("com.apple.android.music", "Apple Music"),
    android("com.cartoonnetwork.cardwars", "Card Wars"), android("com.openai.chatgpt", "ChatGPT"),
    android("com.google.android.calculator", "Calculator"),
    Launcher("android-native-preview-setup.desktop", "Android Native Preview Setup", Terminal="true",
             Exec=f"{HOME}/Luma/android-preview/setup.sh"),
    Launcher("android-preview-apks.desktop", "Android Preview APKs", Exec=f"xdg-open {HOME}/Android Preview APKs"),
    Launcher("preview-1010.desktop", "1010", Exec=f"{HOME}/.local/bin/preview-1010.sh"),
    Launcher("apk-folder.desktop", "APK folder", Exec=f"nautilus {HOME}/apks"),
    Launcher("helper.desktop", "Marked helper", **{"X-Luma-Depot-Hidden": "true", "Exec": "/usr/bin/helper"}),
    Launcher("org.projectluma.AndroidSettings.desktop", "Android Applications", show=False,
             Exec="luma-android-settings"),
    Launcher("org.gimp.GIMP.desktop", "GNU Image Manipulation Program", **{"X-Flatpak": "org.gimp.GIMP"}),
    Launcher("org.example.Tarball.desktop", "Tarball IDE", Exec=f"{HOME}/.local/share/Tarball/bin/ide.sh"),
)

#: The signed v1 catalogue: what the Hub publishes (ids, slugs, categories, curated collections).
DOCUMENT = {
    "apps": [
        {"id": "org.gimp.GIMP", "slug": "gimp", "kind": "app", "name": "GIMP", "summary": "Edit pictures",
         "cat": "create", "dev": "GIMP team", "updated": "2026-10-30", "version": "3.0"},
        {"id": "org.blender.Blender", "slug": "blender", "kind": "app", "name": "Blender", "summary": "3D",
         "cat": "create", "dev": "Blender Foundation", "updated": "2026-11-02", "version": "4.5"},
        {"id": "com.spotify.Client", "slug": "spotify", "kind": "app", "name": "Spotify", "summary": "Music",
         "cat": "media", "dev": "Spotify", "updated": "2026-10-01", "version": "1.2"},
        {"id": "org.projectluma.Write", "slug": "write", "kind": "app", "name": "Write", "summary": "Documents",
         "cat": "work", "first_party": True, "dev": "Luma"},
        {"id": "org.example.Unfiled", "slug": "unfiled", "kind": "app", "name": "Unfiled", "summary": "No category",
         "dev": "Example", "updated": "2026-11-05", "version": "1.0"},
        {"id": "com.valvesoftware.Steam", "slug": "steam", "kind": "app", "name": "Steam", "summary": "Games",
         "cat": "play", "dev": "Valve"},
    ],
    "collections": [{"id": "office", "name": "Office", "applications": ["write", "grid"]}],
}


def installer_catalogue(launchers=LAUNCHERS) -> tuple[Catalogue, dict]:
    """The provider catalogue as native.NativeCatalogue builds it: every visible launcher (app_for_installed)
    plus the installer's signed entries, which take over their own installed launcher."""
    records = visible_applications(launchers)
    local = [App(r.desktop_id, r.name, r.provider, description=r.management, installable=False,
                 removable=r.can_review_removal, availability=r.management)
             for r in records if r.desktop_id != "org.gimp.GIMP.desktop"]
    listed = [App("catalog:gimp", "GIMP", "Edit pictures", categories=("Create",), slug="gimp",
                  flatpak_id="org.gimp.GIMP", tier="listed"),
              App("catalog:blender", "Blender", "3D", categories=("Create",), slug="blender",
                  flatpak_id="org.blender.Blender", tier="listed"),
              App("catalog:spotify", "Spotify", "Music", categories=("Media",), slug="spotify",
                  flatpak_id="com.spotify.Client", tier="listed"),
              App("catalog:retired", "Retired", "Gone from the Hub", categories=("Tools",), slug="retired",
                  flatpak_id="org.example.Retired", tier="listed")]
    catalogue = Catalogue(apps=tuple(local + listed), categories=(Category("Create", "Create", "palette"),))
    installed = {app.app_id: InstalledApp(app.app_id, "", 0, app=app) for app in local}
    installed["catalog:gimp"] = InstalledApp("catalog:gimp", "3.0", 1)
    return catalogue, installed


def live(document=DOCUMENT, **changes):
    catalogue, installed = installer_catalogue()
    if changes.get("downloads"):
        document = dict(document, apps=[dict(e, downloads=changes["downloads"].get(e["id"], 0))
                                         for e in document["apps"]])
    listings = catalogue_v1.parse(document) if document else None
    return world_from(catalogue, installed, listings=listings,
                      curated=catalogue_v1.curated(document) if document else ())


ANDROID = {"1010!", "8 Ball Pool", "Angry Birds", "Apple Music", "Card Wars", "ChatGPT", "Calculator"}
HELPERS = {"Android Native Preview Setup", "Android Preview APKs", "1010", "APK folder", "Marked helper",
           "Android Applications"}


class Launchers(unittest.TestCase):
    """Launchers that aren't apps never reach Depot (luma_installer.depot_inventory)."""

    def test_helpers_are_left_out_and_apps_kept(self) -> None:
        names = {r.name for r in visible_applications(LAUNCHERS)}
        self.assertFalse(names & HELPERS)
        self.assertTrue(ANDROID <= names)
        self.assertIn("Tarball IDE", names)  # a script in an app's own folder is still an app

    def test_a_package_owned_launcher_is_never_second_guessed(self) -> None:
        owned = Launcher("org.example.Tool.desktop", "Tool", **{"X-Flatpak": "org.example.Tool",
                                                                 "Exec": "/usr/bin/flatpak run --command=sh x"})
        self.assertEqual([r.name for r in visible_applications([owned])], ["Tool"])


class StoreLists(unittest.TestCase):
    def setUp(self) -> None:
        self.world = live()

    def names(self, items) -> set[str]:
        return {it.name for it in items}

    def test_store_lists_hold_listings_only(self) -> None:
        world = self.world
        lists = (rules.today_popular(world), rules.today_fresh(world), rules.top_apps(world),
                 rules.recently_updated(world), rules.narrowed(world, rules.Narrowing()), rules.listed_apps(world))
        for items in lists:
            self.assertFalse(self.names(items) & (ANDROID | HELPERS), items)
            self.assertTrue(all(world.listed(it.id) for it in items))
        self.assertNotIn("Retired", self.names(rules.listed_apps(world)))  # not on the Hub: not listed
        self.assertEqual(self.names(rules.listed_apps(world)),
                         {"GIMP", "Blender", "Spotify", "Write", "Unfiled", "Steam"})

    def test_android_apps_stay_in_the_library(self) -> None:
        world = self.world
        mine = {it.name for it in world.items("app") if world.on_devices(it)}
        self.assertTrue(ANDROID <= mine)
        self.assertFalse(mine & HELPERS)
        self.assertEqual(world.item("app", "waydroid.com.openai.chatgpt.desktop").category, "")

    def test_without_install_counts_nothing_is_ranked(self) -> None:
        world = self.world
        self.assertFalse(world.popular_measured)
        self.assertEqual(rules.popular_title(world, "Popular"), "Explore apps")
        self.assertEqual(rules.popular_title(world, "Top apps"), "Explore apps")
        # the Hub's collections first (Write, office), then the old window's Explore picks, then the rest by name
        self.assertEqual([world.app(i).name for i in world.popular], ["Write", "Blender", "Spotify", "Steam", "GIMP",
                                                                        "Unfiled"])

    def test_real_install_counts_rank(self) -> None:
        counts = {"org.gimp.GIMP": 900, "org.blender.Blender": 4000, "com.spotify.Client": 50,
                  "org.projectluma.Write": 70, "org.example.Unfiled": 10, "com.valvesoftware.Steam": 3000}
        world = live(downloads=counts)
        self.assertTrue(world.popular_measured)
        self.assertEqual(rules.popular_title(world, "Popular"), "Popular")
        self.assertEqual([it.name for it in rules.today_popular(world)],
                         ["Blender", "Steam", "GIMP", "Write", "Spotify", "Unfiled"])

    def test_a_few_counts_are_not_a_ranking(self) -> None:
        world = live(downloads={"org.gimp.GIMP": 5})
        self.assertFalse(world.popular_measured)
        self.assertEqual(world.app(world.popular[0]).name, "GIMP")

    def test_no_category_is_not_tools(self) -> None:
        world = self.world
        unfiled = world.item("app", "org.example.Unfiled")
        self.assertEqual(unfiled.category, "")
        self.assertEqual(rules.category_name(world, unfiled), "")
        for category in world.categories:
            shelf = rules.narrowed(world, rules.Narrowing(category.key))
            self.assertNotIn("Unfiled", self.names(shelf))
        self.assertEqual(self.names(rules.narrowed(world, rules.Narrowing("tools"))), set())
        self.assertEqual(world.item("app", "catalog:gimp").category, "create")

    def test_until_the_listings_arrive_the_installer_catalogue_stands_in(self) -> None:
        world = live(document=None)
        self.assertEqual(self.names(rules.listed_apps(world)), {"GIMP", "Blender", "Spotify", "Retired"})
        self.assertFalse(self.names(rules.listed_apps(world)) & (ANDROID | HELPERS))

    def test_curated_order_names_listings_by_id_or_slug(self) -> None:
        document = {"apps": [{"id": "a.A", "slug": "a"}, {"id": "b.B", "featured": True}, {"id": "c.C"}],
                    "collections": [{"applications": ["a", "c.C", "missing", "b.B"]}]}
        self.assertEqual(catalogue_v1.curated(document), ("b.B", "a.A", "c.C"))

    def test_fixture_world_is_unchanged(self) -> None:
        from luma_depot.model.world import load_fixture
        fixture = next((p for p in (ROOT / "tests/fixtures/depot-redesign/world.json",
                                    HERE / "fixtures/depot-redesign/world.json") if p.is_file()), None)
        if fixture is None:
            self.skipTest("the approved sample is not here")
        world = load_fixture(fixture)
        self.assertTrue(world.popular_measured)
        self.assertEqual(rules.popular_title(world, "Top apps"), "Top apps")
        self.assertEqual(len(rules.listed_apps(world)), len(world.apps))


if __name__ == "__main__":
    unittest.main()
