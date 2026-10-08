# SPDX-License-Identifier: Apache-2.0
"""Unit tests for scripts/depot/sign-catalog.py.

Signing itself is exercised with a stand-in minisign on PATH that records its
arguments; the real tool is covered on the build host by the runbook check.
"""

import importlib.util
import json
import os
import stat
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("sign_catalog", ROOT / "scripts/depot/sign-catalog.py")
sign_catalog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sign_catalog)


def app(**overrides):
    entry = {
        "id": "canvas", "app_id": "org.projectluma.Canvas", "name": "Canvas",
        "tier": "luma", "visibility": "public", "summary": "Design pages.",
        "backend": "flatpak", "repository": "luma", "source_id": "org.projectluma.Canvas",
        "branch": "stable", "homepage": "https://simplyluma.com/apps/canvas",
        "icon": {"url": "https://dl.simplyluma.com/media/org.projectluma.Canvas/icon-256.png", "sha256": "0" * 64,
                 "width": 256, "height": 256},
        "description": "Canvas lays out pages, posters and social graphics with the Luma kit's own type and colour.",
        "screenshots": [{"url": "https://dl.simplyluma.com/media/org.projectluma.Canvas/shot-1.webp",
                         "sha256": "1" * 64, "width": 1600, "height": 1000}],
        "permissions": [{"key": "files.portal", "level": "standard"}],
        "sign_in": "none", "qualification": "admitted",
    }
    entry.update(overrides)
    return entry


def catalog(*apps, collections=None):
    return json.dumps({
        "schema_version": 4,
        "generated_at": "2026-09-30T18:00:00Z",
        "collections": collections if collections is not None else [
            {"id": "creative", "name": "Creative", "applications": ["canvas"]}],
        "applications": list(apps),
    }).encode()


class Schema4Tests(unittest.TestCase):
    def test_valid_catalog(self):
        document = sign_catalog.validate_schema4(catalog(app()))
        self.assertEqual(document["applications"][0]["id"], "canvas")

    def test_explicit_nightly_branch_and_unknown_branch(self):
        document = sign_catalog.validate_schema4(catalog(app(branch="nightly")))
        self.assertEqual(document["applications"][0]["branch"], "nightly")
        self.refused(catalog(app(branch="nightly/../../stable")), "branch must be")

    def refused(self, content, fragment):
        with self.assertRaises(sign_catalog.CatalogRefused) as caught:
            sign_catalog.validate_schema4(content)
        self.assertIn(fragment, str(caught.exception))

    def test_private_and_withdrawn_entries_are_never_published(self):
        self.refused(catalog(app(visibility="private")), "must never be published")
        self.refused(catalog(app(visibility="withdrawn")), "must never be published")

    def test_unknown_tier_repository_and_permission(self):
        self.refused(catalog(app(tier="gold")), "unknown tier")
        self.refused(catalog(app(repository="elsewhere")), "luma or flathub")
        self.refused(catalog(app(permissions=[{"key": "files.everything", "level": "high"}])), "unknown key")
        self.refused(catalog(app(permissions=[{"key": "network", "level": "extreme"}])), "unknown level")

    def test_luma_tier_must_be_on_the_luma_remote(self):
        self.refused(catalog(app(repository="flathub")), "hosted on the Luma remote")
        sign_catalog.validate_schema4(catalog(app(tier="listed", repository="flathub")))

    def test_urls_must_be_https(self):
        self.refused(catalog(app(homepage="http://simplyluma.com/apps/canvas")), "https")
        self.refused(catalog(app(icon={"url": "ftp://x"})), "https")

    def test_structure_and_bounds(self):
        self.refused(b"not json", "not JSON")
        self.refused(json.dumps({"schema_version": 3}).encode(), "schema_version is not 4")
        self.refused(catalog(app(), app()), "duplicate id")
        self.refused(catalog(app(id="Canvas App")), "not a slug")
        self.refused(catalog(app(app_id="Canvas")), "valid app_id")
        big = json.dumps({"schema_version": 4, "generated_at": "2026-09-30T18:00:00Z",
                          "applications": [app(id=f"a{i}") for i in range(5001)]}).encode()
        self.refused(big, "5,000")

    def test_collection_may_name_private_entries(self):
        document = sign_catalog.validate_schema4(
            catalog(app(), collections=[{"id": "office", "applications": ["write", "canvas"]}]))
        self.assertEqual(len(document["collections"]), 1)

    def test_an_icon_without_a_digest_is_refused(self):
        """Nothing would check its bytes, so Depot would never draw it."""
        self.refused(catalog(app(icon={"url": "https://dl.simplyluma.com/media/a.png"})),
                     "no usable sha256")
        self.refused(catalog(app(icon={"url": "https://dl.simplyluma.com/media/a.png",
                                       "sha256": "0" * 63})), "no usable sha256")
        self.refused(catalog(app(icon={"url": "https://dl.simplyluma.com/media/a.png",
                                       "sha256": "A" * 64})), "no usable sha256")


class IconCoverageTests(unittest.TestCase):
    """The artwork Depot's users already have may not quietly disappear."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.published = Path(self.tmp.name) / "catalog-4.json"

    def document(self, *apps):
        return json.loads(catalog(*apps))

    def publish(self, *apps):
        self.published.write_bytes(catalog(*apps))

    def test_coverage_is_counted_over_public_entries(self):
        covered, total, without = sign_catalog.icon_coverage(self.document(
            app(), app(id="write", app_id="org.projectluma.Write", icon=None),
            app(id="hidden", app_id="org.projectluma.Hidden", visibility="unlisted", icon=None)))
        self.assertEqual((covered, total), (1, 2))
        self.assertEqual(without, ["write"])

    def test_nothing_published_yet_is_not_a_regression(self):
        sign_catalog.refuse_icon_regression(self.document(app(icon=None)), self.published)

    def test_holding_steady_is_allowed(self):
        self.publish(app())
        sign_catalog.refuse_icon_regression(self.document(app()), self.published)

    def test_gaining_icons_is_allowed(self):
        self.publish(app(icon=None))
        sign_catalog.refuse_icon_regression(self.document(app()), self.published)

    def test_losing_an_icon_is_refused(self):
        self.publish(app())
        with self.assertRaises(sign_catalog.CatalogRefused) as caught:
            sign_catalog.refuse_icon_regression(self.document(app(icon=None)), self.published)
        self.assertIn("icon coverage fell from 1 to 0", str(caught.exception))

    def test_an_unreadable_published_copy_does_not_block_publishing(self):
        self.published.write_bytes(b"not json")
        sign_catalog.refuse_icon_regression(self.document(app()), self.published)


class JobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.bin = base / "bin"
        self.bin.mkdir()
        fake = self.bin / "minisign"
        fake.write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import sys
            args = sys.argv[1:]
            if "-S" in args:
                open(args[args.index("-x") + 1], "w").write("untrusted comment: test\\nsig\\n")
            elif "-V" in args:
                pass
            """))
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bin}{os.pathsep}{self.old_path}"
        self.source = base / "source"
        self.source.mkdir()
        (self.source / "catalog-4.json").write_bytes(catalog(app()))
        (self.source / "catalog-3.json").write_bytes((ROOT / "src/luma-installer/data/depot-catalog.json").read_bytes())
        self.site = base / "site"
        self.args = ["--token-file", str(base / "missing-token"), "--key", str(base / "k"),
                     "--public-key", str(base / "k.pub"), "--site", str(self.site)]

    def tearDown(self):
        os.environ["PATH"] = self.old_path
        self.tmp.cleanup()

    def test_not_configured_without_token(self):
        self.assertEqual(sign_catalog.main(self.args), 2)

    def test_group_readable_token_is_refused(self):
        token = Path(self.tmp.name, "token")
        token.write_text("secret")
        token.chmod(0o640)
        args = list(self.args)
        args[1] = str(token)
        self.assertEqual(sign_catalog.main(args), 1)

    def test_signs_writes_and_skips_unchanged(self):
        self.assertEqual(sign_catalog.main(self.args + ["--from-dir", str(self.source)]), 0)
        for name in ("catalog-4.json", "catalog-4.json.minisig", "catalog-3.json", "catalog-3.json.minisig"):
            self.assertTrue((self.site / "catalog" / name).is_file(), name)
        self.assertEqual((self.site / "catalog/catalog-4.json").read_bytes(),
                         (self.source / "catalog-4.json").read_bytes())
        marker = self.site / "catalog/catalog-4.json.minisig"
        before = marker.stat().st_mtime_ns
        self.assertEqual(sign_catalog.main(self.args + ["--from-dir", str(self.source)]), 0)
        self.assertEqual(marker.stat().st_mtime_ns, before)

    def test_refused_catalog_publishes_nothing(self):
        (self.source / "catalog-4.json").write_bytes(catalog(app(visibility="private")))
        self.assertEqual(sign_catalog.main(self.args + ["--from-dir", str(self.source)]), 1)
        self.assertFalse((self.site / "catalog").exists())



class QualityGateTests(unittest.TestCase):
    """Every public listing has an icon, words, pictures and a way to install it."""

    def gate(self, *apps, media=None):
        return sign_catalog.quality_gate(json.loads(catalog(*apps)), media)

    def test_complete_listing_passes(self):
        failures, counts, exceptions = self.gate(app())
        self.assertEqual(failures, [])
        self.assertEqual(counts, {"flatpak:luma": 1})
        self.assertEqual(exceptions, [])

    def test_missing_or_small_icon_fails(self):
        entry = app()
        del entry["icon"]
        self.assertIn("canvas: no icon", self.gate(entry)[0])
        small = app(icon={"url": "https://dl.simplyluma.com/x.png", "sha256": "0" * 64, "width": 128, "height": 128})
        self.assertTrue(any("smaller than 256" in line for line in self.gate(small)[0]))

    def test_missing_description_and_screenshots_fail(self):
        failures = self.gate(app(description="Short.", screenshots=[]))[0]
        self.assertIn("canvas: no real description", failures)
        self.assertIn("canvas: no screenshots", failures)

    def test_system_tools_need_no_screenshots(self):
        self.assertEqual(self.gate(app(screenshots=[], system_tool=True))[0], [])

    def test_pending_screenshots_only_for_luma_apps_with_a_reason(self):
        failures, _counts, exceptions = self.gate(app(screenshots=[], screenshots_pending="Needs a camera."))
        self.assertEqual(failures, [])
        self.assertEqual(exceptions, ["canvas: screenshots pending: Needs a camera."])
        listed = app(tier="listed", repository="flathub", screenshots=[], screenshots_pending="Later.")
        self.assertIn("canvas: no screenshots", self.gate(listed)[0])

    def test_unlisted_and_private_are_not_gated(self):
        self.assertEqual(self.gate(app(visibility="unlisted", description=""))[0], [])

    def test_snap_and_repository_paths_count(self):
        snap = app(id="nordvpn", tier="listed", backend="snap", repository="snap-store", source_id="nordvpn",
                   sources={"snap": {"name": "nordvpn", "publisher": "nordvpn"}})
        repo = app(id="mega", tier="listed", backend="rpm", repository="mega", source_id="megasync",
                   sources={"rpm_repository": {"id": "mega", "package": "megasync"}})
        failures, counts, _ = self.gate(snap, repo)
        self.assertEqual(failures, [])
        self.assertEqual(counts, {"snap": 1, "rpm:publisher-repository": 1})

    def test_publisher_only_needs_a_reason(self):
        bare = app(id="expressvpn", tier="listed", backend="download", repository="expressvpn")
        self.assertTrue(any("no automated install path" in line for line in self.gate(bare)[0]))
        documented = dict(bare, channel={"kind": "publisher", "title": "ExpressVPN's website",
                                         "reason": "Only offered from the account page."})
        failures, counts, exceptions = self.gate(documented)
        self.assertEqual(failures, [])
        self.assertEqual(counts, {"publisher-only": 1})
        self.assertEqual(exceptions, ["expressvpn: Only offered from the account page."])

    def test_luma_media_must_be_in_the_site_with_its_digest(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            picture = site / "media/listings/canvas/icon-512-aa.png"
            picture.parent.mkdir(parents=True)
            picture.write_bytes(b"png")
            good = {"url": "https://dl.simplyluma.com/media/listings/canvas/icon-512-aa.png",
                    "sha256": hashlib.sha256(b"png").hexdigest(), "width": 512, "height": 512}
            self.assertEqual(self.gate(app(icon=good), media=site)[0], [])
            wrong = dict(good, sha256="f" * 64)
            self.assertTrue(any("does not match" in line for line in self.gate(app(icon=wrong), media=site)[0]))
            missing = dict(good, url="https://dl.simplyluma.com/media/listings/canvas/gone.png")
            self.assertTrue(any("not in the site" in line for line in self.gate(app(icon=missing), media=site)[0]))

    def test_the_signer_refuses_a_listing_below_the_bar(self):
        with self.assertRaises(sign_catalog.CatalogRefused) as caught:
            sign_catalog.refuse_below_quality(json.loads(catalog(app(screenshots=[]))), None)
        self.assertIn("no screenshots", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
