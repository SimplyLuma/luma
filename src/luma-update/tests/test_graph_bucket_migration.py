# SPDX-License-Identifier: Apache-2.0
"""Retained administrator configuration cannot keep official clients in another bucket."""
from dataclasses import replace
import unittest

import fakes
from luma_update.config import Settings, load_settings
from luma_update.engine import Engine
from luma_update.hardware import FP6, TITAN2E

BASE = "https://dl.simplyluma.com/os/graph/"

class ExistingGraphBuckets(unittest.TestCase):
    def test_existing_etc_overrides_migrate_for_all_four_profiles(self):
        cases = (("x86_64", "", "", b""),
                 ("aarch64", "", "", b""),
                 ("aarch64", FP6, "fairphone-fp6", b"fairphone,fp6\0"),
                 ("aarch64", TITAN2E, "unihertz-titan2e", b"unihertz,titan2-elite\0"))
        for arch, target, slug, compatible in cases:
            for legacy in (BASE + "{channel}.json", BASE + "{arch}/{channel}.json"):
                with self.subTest(arch=arch, target=target, legacy=legacy):
                    rig = fakes.Rig()
                    self.addCleanup(rig.close)
                    rig.paths.vendor_config.parent.mkdir(parents=True, exist_ok=True)
                    rig.paths.vendor_config.write_text("[update]\ngraph_url = " + Settings().graph_url + "\n")
                    rig.paths.admin_config.parent.mkdir(parents=True, exist_ok=True)
                    text = "[update]\ngraph_url = " + legacy + "\nhardware_target = " + target + "\n"
                    if target:
                        text += "ref_template = luma/1/{arch}/" + slug + "/{channel}\n"
                        path = rig.paths.device_tree_compatible
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(compatible)
                    rig.paths.admin_config.write_text(text)
                    rig.settings = load_settings(rig.paths)
                    rig.backend = fakes.FakeRpmOstree(origin="luma:" + rig.settings.ref("stable", arch))
                    doc = fakes.graph_doc([fakes.release("1.0.0", 1), fakes.release("1.0.1", 2)], arch=arch)
                    if target:
                        doc.update(schema_version=2, hardware_target=target)
                    rig.publish(doc)
                    engine = Engine(rig.paths, rig.settings, rig.backend, rig.probes, rig.http,
                                    arch=arch, clock=lambda: rig.now, boot_id=lambda: "fixture", greenboot_installed=True)
                    self.assertEqual(engine.check().release.commit, fakes.commit(2))
                    expected = BASE + arch + ("/" + slug if slug else "") + "/stable.json"
                    self.assertEqual(rig.http.gets, [expected, expected + ".minisig"])
                    self.assertEqual(engine.status().graph_url, expected)
                    self.assertEqual(rig.paths.admin_config.read_text(), text)

    def test_explicit_wrong_architecture_device_unknown_or_extra_bucket_refuses(self):
        cases = (("aarch64", "", "x86_64/{channel}.json"),
                 ("x86_64", "", "aarch64/{channel}.json"),
                 ("aarch64", FP6, "aarch64/unihertz-titan2e/{channel}.json"),
                 ("aarch64", "", "aarch64/fairphone-fp6/{channel}.json"),
                 ("aarch64", TITAN2E, "aarch64/unknown/{channel}.json"),
                 ("aarch64", TITAN2E, "aarch64/unihertz-titan2e/extra/{channel}.json"))
        for arch, target, path in cases:
            with self.subTest(arch=arch, target=target, path=path), self.assertRaises(ValueError):
                Settings(graph_url=BASE + path, hardware_target=target).graph("stable", arch)

    def test_wrong_official_feed_is_unmanaged_before_network_or_staging(self):
        rig = fakes.Rig()
        self.addCleanup(rig.close)
        rig.settings = replace(rig.settings, graph_url=BASE + "aarch64/{channel}.json")
        engine = rig.engine()
        self.assertIsNone(engine.check())
        self.assertEqual(engine.status().last_error_class, "unmanaged")
        self.assertEqual(rig.http.gets, [])
        self.assertEqual(rig.backend.calls, [])

    def test_custom_https_and_explicit_test_http_urls_are_unchanged(self):
        for url in ("https://mirror.example.test/custom/{channel}.json?qa=1", "http://127.0.0.1:1234/fixture/{channel}.json"):
            settings = Settings(graph_url=url, hardware_target=FP6, allow_insecure_urls=True)
            self.assertEqual(settings.graph("nightly", "aarch64"), url.format(channel="nightly"))

    def test_official_channel_and_query_use_machine_identity(self):
        self.assertEqual(Settings(graph_url=BASE + "stable.json?qa=1").graph("nightly", "x86_64"),
                         BASE + "x86_64/nightly.json?qa=1")
        for origin in ("http://dl.simplyluma.com", "https://dl.simplyluma.com:444", "https://user@dl.simplyluma.com"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                Settings(graph_url=origin + "/os/graph/{channel}.json").graph("stable", "x86_64")
