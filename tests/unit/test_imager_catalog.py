# SPDX-License-Identifier: Apache-2.0
"""tools/build-imager-catalog.py builds a valid, reproducible catalog and rejects each broken rule."""
import importlib.util
import json
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("build_imager_catalog", ROOT / "tools" / "build-imager-catalog.py")
catalog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog)

STAMP = "2026-10-23T03:00:00Z"
SCHEMA = json.loads((ROOT / "imager" / "catalog.schema.json").read_text())
FP6 = tomllib.loads((ROOT / "devices" / "fairphone" / "fp6" / "device.toml").read_text())


def offline(_url):
    return None


class ImagerCatalog(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for name in ("devices", "imager"):
            shutil.copytree(ROOT / name, self.root / name, ignore=shutil.ignore_patterns("__pycache__"))
        self.fp6 = self.root / "devices" / "fairphone" / "fp6" / "device.toml"

    def tearDown(self):
        self.temporary.cleanup()

    def build(self, head=offline, get_json=offline):
        return catalog.build(self.root, head=head, get_json=get_json, generated_utc=STAMP)

    def edit(self, path, old, new):
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1))

    def phone(self, document, key):
        return next(p for p in document["phones"] if p["id"] == key)

    def assertRejected(self, needle, document=None):
        errors = catalog.check(document or self.build(), SCHEMA)
        self.assertTrue(any(needle in e for e in errors), f"{needle!r} not in {errors}")

    def test_real_sources_are_valid(self):
        document = catalog.build(ROOT, head=offline, get_json=offline, generated_utc=STAMP)
        self.assertEqual(catalog.check(document, SCHEMA), [])
        self.assertEqual(catalog.subset_validate(document, SCHEMA, SCHEMA), [])

    def test_output_is_reproducible(self):
        self.assertEqual(catalog.render(self.build()), catalog.render(self.build()))
        self.assertEqual(self.build()["generated_utc"], STAMP)

    def test_fp6_comes_from_its_device_toml(self):
        phone = self.phone(self.build(), "fairphone-fp6")
        self.assertEqual(phone["never_write"], FP6["install"]["preserve"])
        self.assertIn("modemst1", phone["never_write"])
        self.assertEqual((phone["status"], phone["method"], phone["fastboot_product"]),
                         ("available", "fastboot_bundle", "FP6"))
        self.assertEqual(phone["release"]["name"], FP6["releases"][-1]["name"])
        files = {f["role"]: f for f in phone["release"]["files"]}
        self.assertEqual(files["boot"]["sha256"], FP6["releases"][-1]["boot_image_sha256"])
        self.assertEqual(files["system"]["size_bytes"], FP6["releases"][-1]["system_image_size_bytes"])
        self.assertIn("checksums", files)

    def test_fp6_first_boot_is_fastboot_boot(self):
        bodies = " ".join(s["body"] for s in self.phone(self.build(), "fairphone-fp6")["guide"])
        self.assertIn("fastboot boot", bodies)
        self.assertIn("never fastboot reboot", bodies)
        self.assertIn("never writes modemst1", bodies)

    def test_overlays_list_phones_from_other_branches(self):
        document = self.build()
        titan = self.phone(document, "unihertz-titan2elite")
        self.assertEqual((titan["status"], titan["method"], titan["release"]), ("coming_soon", "sd_card", None))
        self.assertIn("nvdata", titan["never_write"])
        tokay = self.phone(document, "google-tokay")
        self.assertEqual((tokay["status"], tokay["method"]), ("coming_soon", "none"))

    def test_overlay_merges_over_device_toml(self):
        (self.root / "imager" / "phones" / "fairphone-fp6.toml").write_text(
            'vendor = "fairphone"\ncodename = "fp6"\nname = "FP6 overlay"\n[imager]\nsummary = "Changed."\n')
        phone = self.phone(self.build(), "fairphone-fp6")
        self.assertEqual((phone["name"], phone["summary"]), ("FP6 overlay", "Changed."))
        self.assertEqual(phone["method"], "fastboot_bundle")
        self.assertEqual(phone["never_write"], FP6["install"]["preserve"])

    def test_overlay_name_must_match(self):
        shutil.move(self.root / "imager" / "phones" / "google-tokay.toml", self.root / "imager" / "phones" / "pixel.toml")
        with self.assertRaisesRegex(catalog.CatalogError, "must be named google-tokay.toml"):
            self.build()

    def test_sizes_come_from_head(self):
        sizes = {r["boot_image"]: r["boot_image_size_bytes"] for r in FP6["releases"]}
        phone = self.phone(self.build(head=lambda url: sizes.get(url)), "fairphone-fp6")
        self.assertEqual(phone["release"]["files"][0]["size_bytes"], FP6["releases"][-1]["boot_image_size_bytes"])

    def test_size_that_disagrees_with_the_source_is_refused(self):
        with self.assertRaisesRegex(catalog.CatalogError, "differs from boot_image_size_bytes"):
            self.build(head=lambda url: 5 if url.endswith("boot.bin") else None)

    def test_size_without_head_or_source_is_refused(self):
        self.edit(self.fp6, "boot_image_size_bytes = ", "# boot_image_size_bytes = ")
        with self.assertRaisesRegex(catalog.CatalogError, "size unknown"):
            self.build()
        boot = self.phone(self.build(head=lambda url: 7 if url.endswith("boot.bin") else None), "fairphone-fp6")
        self.assertEqual(boot["release"]["files"][0]["size_bytes"], 7)

    def test_arm_follows_the_nightly_index(self):
        def arch(index):
            document = self.build(get_json=lambda url: index)
            return {a["id"]: a["status"] for a in document["desktop"]["architectures"]}["aarch64"]
        self.assertEqual(arch(None), "coming_soon")
        self.assertEqual(arch({"entries": [{"arch": "x86_64"}]}), "coming_soon")
        self.assertEqual(arch({"entries": [{"arch": "x86_64"}, {"arch": "aarch64"}]}), "available")
        self.assertEqual(arch({"entries": [], "latest_by_arch": {"aarch64": {}}}), "available")

    def test_mods(self):
        mods = {m["id"]: m for m in self.build()["mods"]}
        self.assertEqual(mods["surface"]["suggest_when"], {"dmi_sys_vendor": "Microsoft Corporation"})
        self.assertEqual((mods["surface"]["kind"], mods["surface"]["arch"]), ("derived_channel", ["x86_64"]))
        self.assertEqual(mods["apple-silicon"]["note"], "Installs from macOS")
        self.assertEqual({m["status"] for m in mods.values()}, {"coming_soon"})

    def test_unknown_status_is_refused(self):
        self.edit(self.root / "imager" / "mods" / "surface.toml", 'status = "coming_soon"', 'status = "later"')
        self.assertRejected("is not one of")

    def test_unknown_mod_key_is_refused(self):
        self.edit(self.root / "imager" / "mods" / "surface.toml", 'kind =', 'colour = "blue"\nkind =')
        with self.assertRaisesRegex(catalog.CatalogError, "unknown keys"):
            self.build()

    def test_available_phone_needs_a_release(self):
        self.edit(self.root / "imager" / "phones" / "google-tokay.toml", 'status = "coming_soon"', 'status = "available"')
        self.assertRejected("an available phone needs a release")
        self.assertRejected("an available phone needs an install method")

    def test_one_default_channel(self):
        self.edit(self.root / "imager" / "catalog.toml", 'name = "Beta"', 'name = "Beta"\ndefault = true')
        self.assertRejected("exactly one channel must be the default")

    def test_no_exclamation_marks(self):
        self.edit(self.root / "imager" / "mods" / "surface.toml", 'channel."', 'channel!"')
        self.assertRejected("exclamation marks")

    def test_subset_validator(self):
        document = self.build()
        document["phones"][0]["colour"] = "green"
        document["phones"][0]["release"]["files"][0]["sha256"] = "nope"
        document["mods"][0]["note"] = 5
        errors = catalog.subset_validate(document, SCHEMA, SCHEMA)
        self.assertTrue(any("additional property 'colour'" in e for e in errors), errors)
        self.assertTrue(any("matches none of the allowed forms" in e for e in errors), errors)
        self.assertTrue(any("is not of type string or null" in e for e in errors), errors)

    def test_size_cap(self):
        document = self.build()
        document["desktop"]["guide"] = [{"title": "x", "body": "y" * 2000}] * 600
        self.assertRejected("larger than", document)


if __name__ == "__main__":
    unittest.main()
