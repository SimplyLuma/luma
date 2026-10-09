# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the Luma OS release pipeline helpers (no root, no network).

Run: python3 -m unittest discover -s tests/os -p 'test_*.py'
"""

import copy
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "os", "lib"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "update"))

import delta_path  # noqa: E402
import migration_plan  # noqa: E402
import release_identity  # noqa: E402
import update_graph  # noqa: E402
import versions  # noqa: E402

A = "a" * 64
B = "b" * 64
C = "c" * 64
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def release(version, commit, **overrides):
    entry = {
        "version": version,
        "commit": commit,
        "released_at": "2026-10-05T12:00:00Z",
        "rollout": {"start_at": "2026-10-05T12:00:00Z", "start_percentage": 0.05, "duration_minutes": 4320},
        "paused": False,
        "deadend": False,
        "deadend_reason": None,
        "barrier": False,
        "importance": "normal",
        "notes_url": "https://simplyluma.com/releases/" + version,
        "summary": "Faster Alt+Tab.",
        "download_bytes_estimate": 214000000,
    }
    entry.update(overrides)
    return entry


def graph(*releases, generated="2026-10-05T11:59:00Z"):
    return {"schema_version": 1, "channel": "stable", "arch": "x86_64", "generated_at": generated, "releases": list(releases)}


class UpdateGraphTests(unittest.TestCase):
    def check(self, document, releases=None, previous=None):
        return update_graph.validate(document, "stable", "x86_64", releases, previous, 14, now=NOW)

    def test_adr_example_is_valid(self):
        self.assertEqual(self.check(graph(release("1.0.0", A), release("1.0.1", B)), {A: "1.0.0", B: "1.0.1"}), [])

    def test_unpublished_commit_is_rejected(self):
        problems = self.check(graph(release("1.0.1", B)), {A: "1.0.0"})
        self.assertTrue(any("not published" in p for p in problems), problems)

    def test_version_must_match_published_commit(self):
        problems = self.check(graph(release("1.0.2", A)), {A: "1.0.0"})
        self.assertTrue(any("published as 1.0.0" in p for p in problems), problems)

    def test_wrong_channel_and_arch(self):
        document = graph(release("1.0.0", A))
        document["channel"] = "beta"
        document["arch"] = "aarch64"
        problems = self.check(document)
        self.assertTrue(any("channel" in p for p in problems))
        self.assertTrue(any("arch" in p for p in problems))

    def test_stale_and_future_graphs(self):
        self.assertTrue(any("older than" in p for p in self.check(graph(generated="2026-09-01T00:00:00Z"))))
        self.assertTrue(any("future" in p for p in self.check(graph(generated="2026-10-06T00:00:00Z"))))

    def test_unknown_keys_and_types(self):
        bad = release("1.0.0", A, paused="no", importance="urgent", extra=1)
        bad["rollout"] = {"start_at": "2026-10-05T12:00:00Z", "start_percentage": 2, "duration_minutes": -1}
        problems = self.check(graph(bad))
        for needle in ("paused", "importance", "unknown keys", "start_percentage", "duration_minutes"):
            self.assertTrue(any(needle in p for p in problems), (needle, problems))

    def test_deadend_needs_reason_and_rollback_rules(self):
        problems = self.check(graph(release("1.0.0", A, deadend=True)))
        self.assertTrue(any("deadend_reason" in p for p in problems))
        problems = self.check(graph(release("0.9.0", B), release("1.0.0", A, rollback_to={"version": "0.9.0", "commit": B})), {A: "1.0.0", B: "0.9.0"})
        self.assertTrue(any("only valid on a deadend" in p for p in problems), problems)
        ok = release("1.0.1", B, deadend=True, deadend_reason="Breaks Wi-Fi", rollback_to={"version": "1.0.0", "commit": A})
        self.assertEqual(self.check(graph(release("1.0.0", A), ok), {A: "1.0.0", B: "1.0.1"}), [])
        bare = release("1.0.1", B, deadend=True, deadend_reason="Breaks Wi-Fi", rollback_to=A)
        self.assertTrue(any("rollback_to must be" in p for p in self.check(graph(release("1.0.0", A), bare), {A: "1.0.0", B: "1.0.1"})))
        newer = release("1.0.0", A, deadend=True, deadend_reason="x", rollback_to={"version": "1.0.1", "commit": B})
        self.assertTrue(any("older release" in p for p in self.check(graph(newer, release("1.0.1", B)), {A: "1.0.0", B: "1.0.1"})))
        unlisted = release("1.0.1", B, deadend=True, deadend_reason="x", rollback_to={"version": "1.0.0", "commit": C})
        self.assertTrue(any("listed in this graph" in p for p in self.check(graph(release("1.0.0", A), unlisted), {A: "1.0.0", B: "1.0.1", C: "1.0.0"})))

    def test_order_and_duplicates(self):
        problems = self.check(graph(release("1.0.1", B), release("1.0.0", A)))
        self.assertTrue(any("oldest version first" in p for p in problems))
        problems = self.check(graph(release("1.0.0", A), release("1.0.0", B)))
        self.assertTrue(any("appears twice" in p for p in problems))

    def test_previous_graph_rules(self):
        previous = graph(release("1.0.0", A), generated="2026-10-05T11:59:00Z")
        problems = self.check(graph(release("1.0.1", B), generated="2026-10-05T11:59:00Z"), {A: "1.0.0", B: "1.0.1"}, previous)
        self.assertTrue(any("newer than the last signed graph" in p for p in problems))
        self.assertTrue(any("drops published releases" in p for p in problems))

    def test_display_name_is_optional_and_checked(self):
        self.assertEqual(self.check(graph(release("1.0.0", A, display_name="Luma (Version 1, Prairie)"))), [])
        for bad in ("Fedora 44", "Luma\n1", 7, "Luma " + "x" * 200):
            problems = self.check(graph(release("1.0.0", A, display_name=bad)))
            self.assertTrue(any("display_name" in p for p in problems), bad)

    def test_https_notes_only(self):
        problems = self.check(graph(release("1.0.0", A, notes_url="http://example.com")))
        self.assertTrue(any("notes_url" in p for p in problems))


class VersionTests(unittest.TestCase):
    def test_ordering(self):
        ordered = ["1.0.0-beta.2", "1.0.0-beta.10", "1.0.0", "1.0.1", "1.1.0"]
        shuffled = list(reversed(ordered))
        import functools
        self.assertEqual(sorted(shuffled, key=functools.cmp_to_key(versions.compare)), ordered)
        self.assertGreater(versions.compare("1.0.0-nightly.20261002.1", "1.0.0-nightly.20261001.9"), 0)
        self.assertGreater(versions.compare("1.0.0-nightly.20261001.10", "1.0.0-nightly.20261001.9"), 0)
        with self.assertRaises(ValueError):
            versions.parse("v1")


def identity(**overrides):
    base = release_identity.load(os.path.join(ROOT, "config", "os", "release.env"))
    fields = {**base.__dict__, "codename": "prairie", "stage": "beta", "stage_number": "0"}
    fields.update(overrides)
    result = release_identity.Identity(**fields)
    release_identity.validate(result)
    return result


class ReleaseIdentityTests(unittest.TestCase):
    def fields(self, ident, channel, build="20261001.1", day="2026-09-30"):
        text = release_identity.render_os_release(ident, channel, build, day, "2026-10-01")
        return text, dict(line.split("=", 1) for line in text.splitlines())

    def test_contract_advances_installed_prairie_beta_1(self):
        ident = release_identity.load(os.path.join(ROOT, "config", "os", "release.env"))
        self.assertEqual(release_identity.display_name(ident, "beta"), "Luma (Prairie, Beta 1.1)")
        next_version = release_identity.machine_version(ident, "beta", "20261009.1")
        self.assertEqual(next_version, "1.0.0-beta.1.1")
        self.assertGreater(versions.compare(next_version, "1.0.0-beta.1"), 0)
        self.assertEqual(release_identity.display_name(ident, "nightly", "2026-09-16"),
                         "Luma (Prairie, Beta 1.1, Nightly 20260916)")

    def test_owner_names(self):
        self.assertEqual(release_identity.display_name(identity(), "nightly", "2026-09-16"),
                         "Luma (Prairie, Beta 0, Nightly 20260916)")
        self.assertEqual(release_identity.display_name(identity(stage_number="1"), "beta"), "Luma (Prairie, Beta 1)")
        self.assertEqual(release_identity.display_name(identity(stage_number="1.1"), "beta"), "Luma (Prairie, Beta 1.1)")
        self.assertEqual(release_identity.display_name(identity(stage_number="2"), "beta"), "Luma (Prairie, Beta 2)")
        final = identity(stage="final", stage_number="")
        self.assertEqual(release_identity.display_name(final, "stable"), "Luma (Version 1, Prairie)")
        self.assertEqual(release_identity.display_name(final, "nightly", "20261102"),
                         "Luma (Version 1, Prairie, Nightly 20261102)")

    def test_impossible_releases_are_refused(self):
        for ident, channel in ((identity(), "beta"), (identity(stage_number="1"), "stable"),
                               (identity(stage="final", stage_number=""), "beta")):
            with self.assertRaises(ValueError):
                release_identity.display_name(ident, channel, "2026-09-16")
        with self.assertRaises(ValueError):
            release_identity.display_name(identity(), "nightly")
        with self.assertRaises(ValueError):
            identity(version_id="1.0")

    def test_machine_versions_are_unchanged_and_ordered(self):
        nightly = release_identity.machine_version(identity(), "nightly", "20260917.5")
        self.assertEqual(nightly, "1.0.0-nightly.20260917.5")
        beta1 = release_identity.machine_version(identity(stage_number="1"), "beta", "20260930.1")
        beta11 = release_identity.machine_version(identity(stage_number="1.1"), "beta", "20261005.1")
        beta2 = release_identity.machine_version(identity(stage_number="2"), "beta", "20261010.1")
        final = release_identity.machine_version(identity(stage="final", stage_number=""), "stable", "20261101.1")
        self.assertEqual((beta1, beta11, final), ("1.0.0-beta.1", "1.0.0-beta.1.1", "1.0.0"))
        import functools
        ordered = [beta1, beta11, beta2, final]
        self.assertEqual(sorted(reversed(ordered), key=functools.cmp_to_key(versions.compare)), ordered)
        # The nightly the Dell runs (.9 of the 16th) and the Sept 16 nightly
        # order below every nightly built after this change.
        for installed in ("1.0.0-nightly.20260916.9", "1.0.0-nightly.20260916.4", "1.0.0-nightly.20260917.3"):
            self.assertGreater(versions.compare("1.0.0-nightly.20260918.1", installed), 0)

    def test_nightly_date(self):
        # 20260917.3 was built on the evening of the 16th (Central).
        self.assertEqual(release_identity.nightly_date("20260917.3", "2026-09-17T02:30:00Z"), "2026-09-16")
        self.assertEqual(release_identity.nightly_date("20260917.1", "2026-09-18T05:00:00Z"), "2026-09-17")

    def test_os_release_nightly(self):
        text, fields = self.fields(identity(), "nightly")
        self.assertEqual(fields["NAME"], "Luma")
        self.assertEqual(fields["ID"], "luma")
        self.assertEqual(fields["ID_LIKE"], "fedora")
        self.assertEqual(fields["VERSION_ID"], "1")
        self.assertEqual(fields["VERSION_CODENAME"], "prairie")
        self.assertEqual(fields["VERSION"], '"Prairie, Beta 0, Nightly 20260930"')
        self.assertEqual(fields["PRETTY_NAME"], '"Luma (Prairie, Beta 0, Nightly 20260930)"')
        self.assertEqual(fields["RELEASE_TYPE"], "development")
        self.assertEqual((fields["BUILD_ID"], fields["IMAGE_VERSION"]), ("20261001.1", "20261001.1"))
        self.assertEqual(fields["PLATFORM_ID"], "platform:f44")
        self.assertEqual(fields["LOGO"], "luma-logo")
        self.assertEqual(fields["DEFAULT_HOSTNAME"], "luma")
        self.assertEqual(fields["CPE_NAME"], "cpe:/o:projectluma:luma:1")
        self.assertEqual((fields["LUMA_RELEASE_CHANNEL"], fields["LUMA_NIGHTLY_DATE"]), ("nightly", "20260930"))
        self.assertNotIn("1.0", fields["PRETTY_NAME"] + fields["VERSION"])
        # Nothing a person sees may name Fedora (ADR-040); ID_LIKE, PLATFORM_ID
        # and LUMA_FEDORA_RELEASE are the technical exceptions.
        visible = {k: v for k, v in fields.items() if k not in ("ID_LIKE", "PLATFORM_ID", "LUMA_FEDORA_RELEASE")}
        self.assertFalse([k for k, v in visible.items() if "fedora" in v.lower() or "silverblue" in v.lower()])
        with self.assertRaises(ValueError):
            release_identity.render_os_release(identity(), "nightly", "x", "2026-09-30", "2026-10-01")

    def test_package_os_release_names_the_stage_and_no_build(self):
        fields = dict(release_identity.package_os_release_fields(identity()))
        self.assertEqual(fields["PRETTY_NAME"], "Luma (Prairie, Beta 0)")
        self.assertEqual((fields["VERSION_ID"], fields["VERSION_CODENAME"], fields["LOGO"]), ("1", "prairie", "luma-logo"))
        for key in ("BUILD_ID", "IMAGE_VERSION", "LUMA_RELEASE_CHANNEL", "LUMA_NIGHTLY_DATE"):
            self.assertNotIn(key, fields)
        final = dict(release_identity.package_os_release_fields(identity(stage="final", stage_number="")))
        self.assertEqual(final["PRETTY_NAME"], "Luma (Version 1, Prairie)")

    def test_os_release_beta_and_final(self):
        text, fields = self.fields(identity(stage_number="1"), "beta")
        self.assertEqual(fields["PRETTY_NAME"], '"Luma (Prairie, Beta 1)"')
        self.assertNotIn("LUMA_NIGHTLY_DATE", fields)
        text, fields = self.fields(identity(stage="final", stage_number=""), "stable")
        self.assertEqual(fields["PRETTY_NAME"], '"Luma (Version 1, Prairie)"')
        self.assertEqual(fields["VERSION"], '"Version 1, Prairie"')
        self.assertEqual(fields["RELEASE_TYPE"], "stable")
        self.assertNotIn("LUMA_RELEASE_STAGE_NUMBER", fields)


class GraphFromReleasesTests(unittest.TestCase):
    def publish(self, repo, version, build_id, os_release=None, nightly_date=None):
        import tempfile  # noqa: F401
        release_dir = os.path.join(repo, "luma", "releases", version)
        os.makedirs(release_dir)
        manifest = {"version": version, "build_id": build_id, "channel": "nightly", "commit": A,
                    "published_utc": "2026-09-17T06:00:00Z"}
        with open(os.path.join(release_dir, "manifest.json"), "w") as stream:
            json.dump(manifest, stream)
        if os_release is not None:
            with open(os.path.join(release_dir, "os-release"), "w") as stream:
                stream.write(os_release)
        if nightly_date is not None:
            with open(os.path.join(release_dir, "notes.json"), "w") as stream:
                json.dump({"nightly_date": nightly_date}, stream)
        return manifest

    def test_display_names(self):
        import tempfile
        import graph_from_releases
        with tempfile.TemporaryDirectory() as repo:
            new = self.publish(repo, "1.0.0-nightly.20260918.1", "20260918.1", release_identity.render_os_release(
                identity(), "nightly", "20260918.1", "2026-09-17", "2026-09-18"))
            old = self.publish(repo, "1.0.0-nightly.20260917.3", "20260917.3",
                               'NAME=Luma\nPRETTY_NAME="Luma 1.0"\n', nightly_date="2026-09-16")
            bare = self.publish(repo, "1.0.0-nightly.20260916.9", "20260916.9")
            self.assertEqual(graph_from_releases.display_name_of(repo, new), "Luma (Prairie, Beta 0, Nightly 20260917)")
            self.assertEqual(graph_from_releases.display_name_of(repo, old), "Luma (Prairie, Beta 0, Nightly 20260916)")
            self.assertEqual(graph_from_releases.display_name_of(repo, bare), "Luma (Prairie, Beta 0, Nightly 20260916)")


class DeltaPathTests(unittest.TestCase):
    def test_layout(self):
        commit = "0" * 63 + "1"
        self.assertEqual(delta_path.path("", commit), "AA/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAE")
        self.assertTrue(delta_path.path(commit, commit).startswith("AA/"))
        self.assertIn("-", delta_path.path(commit, commit))
        self.assertNotIn("/", delta_path.path("f" * 64, "f" * 64).split("/", 1)[1])


class MigrationPlanTests(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(os.path.dirname(__file__), "fixtures", "parity-machine-status.json"), encoding="utf-8") as stream:
            self.status = json.load(stream)
        self.target = {
            "gnome-shell": ("0", "50.3", "1.luma.99.surfacepreview20260914.78.fc44"),
            "prairie-core-apps": ("0", "0.1.0", "1.luma.74.connect.28~preview.20260914.1.fc44"),
            "luma-continuity": ("0", "0.1.0", "0.54.experiment.fc44"),
            "gnome-shell-extension-appindicator": ("0", "60", "1.fc44"),
            "luma-vitals": ("0", "0.1.0", "1.luma.3.fc44"),
            "gnome-tour": ("0", "50.0", "2.fc44"),
        }

    def test_plan(self):
        plan = migration_plan.plan(self.status, self.target)
        self.assertTrue(plan["reset_overrides"])
        self.assertEqual(plan["downgrades"], [])
        self.assertIn("gnome-shell-extension-appindicator", plan["uninstall"])
        self.assertIn("luma-vitals-0.1.0-1.luma.3.fc44.noarch", plan["uninstall"])
        self.assertIn("libretro-bsnes-mercury", plan["keep_layers"])
        self.assertIn("openemu-linux-0.1.0-1.luma.2.fc44.x86_64", plan["keep_layers"])
        self.assertEqual(plan["reapply_removals"], ["gnome-tour"])

    def test_newer_local_build_is_a_downgrade(self):
        target = copy.deepcopy(self.target)
        target["prairie-core-apps"] = ("0", "0.1.0", "1.luma.74.connect.27~preview.20260914.1.fc44")
        plan = migration_plan.plan(self.status, target)
        self.assertEqual([d["package"] for d in plan["downgrades"]], ["prairie-core-apps"])

    def test_pkglist_sssss(self):
        text = "@a(sssss) [('bash', '0', '5.3', '1.fc44', 'x86_64'), ('ImageMagick', '1', '7.1', '1.fc44', 'x86_64')]"
        parsed = migration_plan.parse_pkglist(text)
        self.assertEqual(parsed["bash"], ("0", "5.3", "1.fc44"))
        self.assertEqual(parsed["ImageMagick"][0], "1")

    def test_pkglist_byteswapped_epoch(self):
        text = "[('ImageMagick', 72057594037927936, '7.1', '1.fc44', 'x86_64')]"
        self.assertEqual(migration_plan.parse_pkglist(text)["ImageMagick"][0], "1")

    def test_pkglist_writer_type(self):
        import tempfile, importlib.util
        spec = importlib.util.spec_from_file_location("ostree_metadata", os.path.join(ROOT, "scripts/os/lib/ostree_metadata.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.NamedTemporaryFile("w", suffix=".tsv") as inventory:
            inventory.write("bash\t0\t5.3\t1.fc44\tx86_64\nImageMagick\t1\t7.1\t1.fc44\tx86_64\n")
            inventory.flush()
            text = module.pkglist(inventory.name)
        self.assertTrue(text.startswith("@a(sssss) [('ImageMagick', '1', "))

    def test_pkglist_gvariant(self):
        text = "@a(stsss) [('bash', uint64 0, '5.3', '1.fc44', 'x86_64'), ('it\\'s', uint64 2, '1', '1', 'noarch')]"
        parsed = migration_plan.parse_pkglist(text)
        self.assertEqual(parsed["bash"], ("0", "5.3", "1.fc44"))
        self.assertEqual(parsed["it\\'s"][0], "2")

    def test_rpmvercmp(self):
        cmp = migration_plan.rpmvercmp
        self.assertEqual(cmp("1.luma.17.preview1.fc44", "1.fc44"), 1)
        self.assertEqual(cmp("1.0~rc1", "1.0"), -1)
        self.assertEqual(cmp("1.0^git1", "1.0"), 1)
        self.assertEqual(cmp("0.54.experiment.fc44", "0.52.experiment.fc44"), 1)


if __name__ == "__main__":
    unittest.main()
