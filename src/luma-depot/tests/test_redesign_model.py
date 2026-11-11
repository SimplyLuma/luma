# SPDX-License-Identifier: Apache-2.0
"""The redesign's view-model rules (handoff 15.5), against the approved mockup's data."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE.parent), str(ROOT / "src/luma-platform/appkit"), str(ROOT / "src/luma-installer")]

from luma_depot.model import rules  # noqa: E402
from luma_depot.model.world import PendingUpdate, load_fixture  # noqa: E402

# A checkout keeps the approved sample at the root; the package's source tarball beside its tests.
FIXTURE = next((path for path in (ROOT / "tests/fixtures/depot-redesign/world.json",
                                  HERE / "fixtures/depot-redesign/world.json") if path.is_file()),
               ROOT / "tests/fixtures/depot-redesign/world.json")


class BarStates(unittest.TestCase):
    """Every row of 8.7: main action (key), secondary, Install on…, Share, Uninstall."""

    def setUp(self) -> None:
        self.world = load_fixture(FIXTURE)

    def keys(self, kind: str, item_id: str, world=None) -> tuple[str, ...]:
        world = world or self.world
        return rules.bar_for(world.item(kind, item_id), world).keys

    def test_installed_here_no_update(self) -> None:
        self.assertEqual(self.keys("app", "tide"), ("open", "install-on", "share", "uninstall"))

    def test_update_waiting_here_takes_the_key(self) -> None:
        plan = rules.bar_for(self.world.item("app", "discord"), self.world)
        self.assertEqual(plan.keys, ("update", "open", "install-on", "share", "uninstall"))
        self.assertEqual(plan.key_button.tooltip, "1.0.158 → 1.0.159 · 96 MB")
        self.assertEqual(plan.buttons[1].variant, "fill")

    def test_update_waiting_only_elsewhere(self) -> None:
        world = replace(self.world, pending=(PendingUpdate("firefox", ("x1", "fp6"), "1", "2", "9 MB"),))
        world = world.with_installed("xps", "firefox", True)
        plan = rules.bar_for(world.item("app", "firefox"), world)
        self.assertEqual(plan.keys, ("open", "update-on", "install-on", "share", "uninstall"))
        self.assertEqual(plan.buttons[1].label, "Update on 2")
        self.assertEqual(plan.buttons[1].tooltip, "ThinkPad X1, Fairphone 6")

    def test_not_installed_can_run_here(self) -> None:
        self.assertEqual(self.keys("app", "steam"), ("install", "install-on", "share"))

    def test_not_installed_update_waiting_elsewhere(self) -> None:
        self.assertEqual(self.keys("app", "krita"), ("install", "update-on", "install-on", "share"))

    def test_cannot_run_here_is_a_quiet_label(self) -> None:
        plan = rules.bar_for(self.world.item("app", "camera"), self.world)
        self.assertEqual(plan.buttons[0].key, "none")
        self.assertEqual(plan.buttons[0].label, "No desktop version")
        self.assertIsNone(plan.key_button)

    def test_part_of_luma_is_open_and_share(self) -> None:
        self.assertEqual(self.keys("app", "filer"), ("open", "share"))

    def test_look(self) -> None:
        in_use = rules.bar_for(self.world.item("look", "prism"), self.world)
        self.assertEqual(in_use.keys, ("open", "try", "install-on", "share", "uninstall"))
        self.assertEqual(in_use.key_button.label, "In use")
        self.assertEqual(self.keys("look", "theme-95"), ("install", "try", "install-on", "share"))

    def test_tweak_says_installed_never_turn_on(self) -> None:
        plan = rules.bar_for(self.world.item("tweak", "battery-care"), self.world)
        self.assertEqual(plan.key_button.label, "Installed")
        self.assertNotIn("Turn", " ".join(b.label for b in plan.buttons))

    def test_install_on_only_when_another_device_can_run_it(self) -> None:
        world = replace(self.world, devices=(self.world.device("xps"),))
        self.assertNotIn("install-on", self.keys("app", "steam", world))

    def test_one_key_per_bar(self) -> None:
        for kind in ("app", "look", "tweak"):
            for item in self.world.items(kind):
                variants = [b.variant for b in rules.bar_for(item, self.world).buttons]
                self.assertLessEqual(variants.count("key"), 1, item.id)
                self.assertNotEqual(rules.bar_for(item, self.world).buttons[-1].key, "install", item.id)


class Today(unittest.TestCase):
    def setUp(self) -> None:
        self.world = load_fixture(FIXTURE)

    def test_popular_follows_the_catalogue_order(self) -> None:
        self.assertEqual([it.id for it in rules.today_popular(self.world)],
                         ["blender", "obsidian", "spotify", "signal", "vlc", "steam"])

    def test_new_and_updated_is_third_party_excludes_popular_by_date(self) -> None:
        fresh = rules.today_fresh(self.world)
        popular = {it.id for it in rules.today_popular(self.world)}
        self.assertTrue(all(it.seal != "luma" for it in fresh))
        self.assertFalse(popular & {it.id for it in fresh})
        dates = [rules.sort_date(it.app.updated) for it in fresh]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertEqual(fresh[0].id, "discord")

    def test_recently_updated_rows_say_the_day(self) -> None:
        recent = rules.recently_updated(self.world)
        self.assertEqual([it.id for it in recent][:3], ["discord", "spotify", "obsidian"])
        self.assertEqual(rules.short_date(recent[0].app.updated), "Oct 20")


class AppsNarrowing(unittest.TestCase):
    def setUp(self) -> None:
        self.world = load_fixture(FIXTURE)

    def test_lines(self) -> None:
        n = rules.Narrowing(works=["phone"])
        self.assertEqual(rules.narrowed_line(self.world, n), ("Apps", "That also work on phone"))
        n.works.append("tv")
        self.assertEqual(rules.narrowed_line(self.world, n)[1], "That also work on phone and TV")
        self.assertEqual(rules.narrowed_line(self.world, rules.Narrowing(seals=["community"]))[1],
                         "Narrowed by your filters")
        self.assertEqual(rules.narrowed_line(self.world, rules.Narrowing(category="create")),
                         ("Create", "Make pictures, films and music"))

    def test_filters_need_every_ticked_form(self) -> None:
        both = rules.narrowed(self.world, rules.Narrowing(works=["phone", "tv"]))
        self.assertTrue(all("phone" in it.runs and "tv" in it.runs for it in both))
        self.assertEqual(rules.filter_count(rules.Narrowing(works=["phone"], seals=["luma"])), 2)

    def test_category_is_most_popular_first(self) -> None:
        create = rules.narrowed(self.world, rules.Narrowing(category="create"))
        counts = [it.app.rating_count for it in create]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertTrue(all(it.category == "create" for it in create))


class Updates(unittest.TestCase):
    def setUp(self) -> None:
        self.world = load_fixture(FIXTURE)

    def test_summary_line(self) -> None:
        self.assertEqual(rules.updates_lead(self.world, system_waiting=True, checked="6 hours ago"),
                         "5 updates · 1.0 GB · checked 6 hours ago")
        empty = replace(self.world, pending=())
        self.assertEqual(rules.updates_lead(empty, system_waiting=False, checked="just now"),
                         "Everything is up to date · checked just now")

    def test_channel_words_join_and_leave(self) -> None:
        join = rules.channel_decision("stable", "beta", "nightly October 22")
        self.assertEqual((join.title, join.action, join.toast), ("Join Beta?", "Join Beta", "You joined Beta"))
        self.assertEqual(join.body, "This computer will get Beta builds about once a week, before they reach Stable.")
        nightly = rules.channel_decision("beta", "nightly", "")
        self.assertIn("Nightly builds get less testing, so things can break.", nightly.body)
        leave = rules.channel_decision("nightly", "stable", "nightly October 22")
        self.assertEqual((leave.title, leave.action), ("Leave Nightly?", "Switch to Stable"))
        self.assertEqual(leave.body, "This computer stays on nightly October 22 until Stable catches up, then gets "
                                     "Stable releases only.")
        self.assertEqual(leave.toast, "This computer now gets Stable updates")

    def test_devices_line(self) -> None:
        line = rules.devices_line(self.world, ("xps", "x1", "fp6", "tv", "home"))
        self.assertEqual(line, "On this computer, ThinkPad X1, Fairphone 6, Living room and Kitchen")


class LiveWorld(unittest.TestCase):
    """The real catalogue and installed apps in the redesign's shape (model/live.py)."""

    def test_world_from_catalogue_and_installed(self) -> None:
        from luma_depot.model.live import world_from
        from luma_depot.providers import App, Catalogue, Category, InstalledApp
        apps = (App("org.example.Paint", "Paint", "Draw things", categories=("Create",), tier="verified",
                    developer="Example", developer_verified="organization", download_bytes=38_000_000),
                App("org.projectluma.Tide", "Tide", "Music", categories=("Media",), tier="luma", developer="Luma"),
                App("org.example.Hidden", "Hidden", "x", unlisted=True))
        catalogue = Catalogue(apps=apps, categories=(Category("Create", "Create", "palette"),))
        installed = {"org.example.Paint": InstalledApp("org.example.Paint", "1.0", 1, update_version="1.1",
                                                       update_bytes=96_000_000, update_summary="Faster brushes")}
        world = world_from(catalogue, installed)
        self.assertEqual(len(world.devices), 1)
        self.assertEqual([a.app_id for a in world.apps], ["org.example.Paint", "org.projectluma.Tide"])
        paint = world.item("app", "org.example.Paint")
        self.assertEqual((paint.category, paint.app.package_kind, paint.app.size_label), ("create", "flatpak", "38 MB"))
        self.assertEqual(paint.seal, "verified")
        self.assertEqual(world.pending[0].to_version, "1.1")
        self.assertEqual(world.pending[0].size, "96 MB")
        self.assertEqual(world.update_count(), 1)
        self.assertEqual(rules.bar_for(paint, world).keys, ("update", "open", "share", "uninstall"))
        self.assertEqual(world.looks, ())  # no Looks are invented


class SignedCatalogue(unittest.TestCase):
    """The v1 catalogue is read only once the pinned key's signature holds (model/catalogue_v1.py)."""

    def test_pinned_key_refuses_another_signer(self) -> None:
        import json as _json
        sys.path.insert(0, str(ROOT / "src/luma-installer/tests"))
        try:
            import minisign_signer
        except ImportError:
            self.skipTest("the test signer is not here")
        from luma_depot.model import catalogue_v1
        from luma_installer.depot_signature import SignatureError
        document = _json.dumps({"items": []}).encode()
        with self.assertRaises(SignatureError):
            catalogue_v1.verify(document, minisign_signer.signature_file(document).encode()
                                if isinstance(minisign_signer.signature_file(document), str)
                                else minisign_signer.signature_file(document))

    def test_parse_listing_kinds_and_permissions(self) -> None:
        from luma_depot.model import catalogue_v1
        apps, looks, tweaks, _story = catalogue_v1.parse({"items": [
            {"id": "org.example.Paint", "kind": "app", "name": "Paint", "summary": "Draw", "category": "Create",
             "developer": {"name": "Example", "verified": True, "site": "example.org"},
             "permissions": [{"word": "Files", "reason": "Opens your pictures"}, {"word": "Sound", "reason": "x"}],
             "releases": [{"version": "2.0", "date": "Nov 7, 2026", "ref": "app/org.example.Paint/x86_64/stable",
                           "notes": ["Faster brushes"]}], "runs": ["desktop"]},
            {"id": "ember", "kind": "look", "name": "Ember", "type": "Wallpaper", "developer": {"name": "Luma"}},
            {"id": "battery-care", "kind": "tweak", "name": "Battery Care", "glyph": "power"},
            {"name": "no id"}]})
        self.assertEqual([a.app_id for a in apps], ["org.example.Paint"])
        paint = apps[0]
        self.assertEqual((paint.category_key, paint.developer, paint.developer_verified), ("create", "Example", "organization"))
        self.assertEqual(paint.version, "2.0")
        self.assertEqual(paint.reach, (("folder-open", "Files", "Opens your pictures"),))
        self.assertEqual((looks[0].kind, looks[0].seal), ("Wallpaper", "luma"))
        self.assertEqual(tweaks[0].icon, "power")

    def test_listing_only_apps_are_never_installed_from_a_ref(self) -> None:
        from luma_depot.model.live import world_from
        from luma_depot.providers import App
        listing = App("org.example.New", "New", "x")
        world = world_from(None, {}, listings=([listing], [], [], {}))
        item = world.item("app", "org.example.New")
        self.assertEqual(rules.bar_for(item, world).buttons[0].label, "Not available yet")


class OnATv(unittest.TestCase):
    """Depot on a TV draws the same world, with this TV as its device (tv_views over model.rules)."""

    def test_a_tv_lists_only_what_runs_on_a_tv(self) -> None:
        world = load_fixture(FIXTURE, here="tv")
        self.assertEqual(world.this_device.form, "tv")
        popular = rules.today_popular(world, count=50)
        self.assertTrue(popular)
        self.assertTrue(all("tv" in it.runs for it in popular))
        desktop = load_fixture(FIXTURE)
        self.assertGreater(len(rules.today_popular(desktop, count=50)), len(popular))

    def test_the_catalogue_says_how_an_app_is_driven_on_a_tv(self) -> None:
        from luma_depot.model import catalogue_v1
        apps, _looks, _tweaks, _story = catalogue_v1.parse({"items": [
            {"id": "com.valvesoftware.Steam", "kind": "app", "name": "Steam", "runs": ["desktop", "tablet", "tv"],
             "tv": {"runs": True, "input": "controller", "action": "BigPicture"}},
            {"id": "org.example.Paint", "kind": "app", "name": "Paint", "runs": ["desktop"], "tv": None}]})
        self.assertEqual({a.app_id: a.tv_input for a in apps},
                         {"com.valvesoftware.Steam": "controller", "org.example.Paint": ""})


class World(unittest.TestCase):
    def test_updates_badge_counts_every_device_and_luma(self) -> None:
        self.assertEqual(load_fixture(FIXTURE).update_count(), 11)

    def test_missing_data_stays_missing(self) -> None:
        bitwarden = load_fixture(FIXTURE).item("app", "bitwarden").app
        self.assertEqual(bitwarden.shot_keys, ())
        self.assertEqual(bitwarden.releases[0].notes if bitwarden.releases else (), ())

    def test_counts(self) -> None:
        self.assertEqual([rules.fmt_count(n) for n in (940, 1200, 18000, 48200)], ["940", "1.2k", "18k", "48k"])


if __name__ == "__main__":
    unittest.main()
