# SPDX-License-Identifier: Apache-2.0
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import fakes
from fakes import NOW, graph_doc
from luma_update.config import Paths, Settings
from luma_update.graph import GraphError, check_freshness, parse_graph
from luma_update.hardware import FP6, TITAN2E, require_commit_target, require_device_target
from luma_update.engine import Engine, UpdateError


class HardwareGraph(unittest.TestCase):
    def check(self, doc, expected):
        check_freshness(parse_graph(json.dumps(doc).encode()), channel="stable",
                        arch="aarch64", now=NOW, last_generated_at=None,
                        hardware_target=expected)

    def test_fp6_rejects_same_arch_legacy_desktop(self):
        with self.assertRaises(GraphError):
            self.check(graph_doc([], arch="aarch64"), FP6)

    def test_fp6_graph_rejected_by_desktop_and_other_board(self):
        doc = graph_doc([], arch="aarch64")
        doc.update(schema_version=2, hardware_target=FP6)
        self.check(doc, FP6)
        for expected in ("", "fairphone,fp5", "generic"):
            with self.subTest(expected=expected), self.assertRaises(GraphError):
                self.check(doc, expected)

    def test_target_requires_explicit_new_schema(self):
        doc = graph_doc([], arch="aarch64")
        doc["hardware_target"] = FP6
        with self.assertRaises(GraphError):
            self.check(doc, FP6)
        doc.update(schema_version=2)
        for invalid in (None, False, "", "../fp6", "fairphone,fp6\0"):
            doc["hardware_target"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(GraphError):
                self.check(doc, FP6)


class DeviceIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = Paths(Path(self.tmp.name))
        self.paths.device_tree_compatible.parent.mkdir(parents=True)

    def test_fp6_requires_target_and_actual_arm_architecture(self):
        self.paths.device_tree_compatible.write_bytes(b"fairphone,fp6\0qcom,milos\0")
        require_device_target(self.paths, FP6, "aarch64")
        for target, arch in (("", "aarch64"), (FP6, "x86_64"), ("other", "aarch64")):
            with self.subTest(target=target, arch=arch), self.assertRaises(ValueError):
                require_device_target(self.paths, target, arch)

    def test_titan_requires_its_target_and_arm_architecture(self):
        self.paths.device_tree_compatible.write_bytes(b"unihertz,titan2-elite\0mediatek,mt6878\0")
        require_device_target(self.paths, TITAN2E, "aarch64")
        for target, arch in (("", "aarch64"), (TITAN2E, "x86_64"), (FP6, "aarch64"),
                             ("unihertz,titan2-elite", "aarch64")):
            with self.subTest(target=target, arch=arch), self.assertRaises(ValueError):
                require_device_target(self.paths, target, arch)

    def test_titan_target_string_is_not_its_board_compatible(self):
        self.paths.device_tree_compatible.write_bytes(b"unihertz,titan2e\0mediatek,mt6878\0")
        with self.assertRaises(ValueError):
            require_device_target(self.paths, TITAN2E, "aarch64")

    def test_unknown_device_is_refused_even_when_the_tree_names_it(self):
        self.paths.device_tree_compatible.write_bytes(b"acme,phone\0")
        for arch in ("aarch64", "x86_64"):
            with self.subTest(arch=arch), self.assertRaises(ValueError):
                require_device_target(self.paths, "acme,phone", arch)

    def test_config_cannot_invent_phone_identity(self):
        for data in (b"fairphone,fp60\0", b"other,fairphone,fp6\0", b"qcom,milos\0"):
            self.paths.device_tree_compatible.write_bytes(data)
            with self.subTest(data=data), self.assertRaises(ValueError):
                require_device_target(self.paths, FP6, "aarch64")

    def test_absent_identity_only_allows_legacy_generic(self):
        require_device_target(self.paths, "", "x86_64")
        with self.assertRaises(ValueError):
            require_device_target(self.paths, FP6, "aarch64")

    def test_unreadable_or_malformed_identity_fails_closed(self):
        self.paths.device_tree_compatible.mkdir()
        with self.assertRaises(ValueError):
            require_device_target(self.paths, "", "aarch64")
        self.paths.device_tree_compatible.rmdir()
        for data in (b"", b"\xff\0", b"x" * 65537, b"fairphone,fp6",
                     b"\0fairphone,fp6\0", b"fairphone,fp6\0\0qcom,milos\0"):
            self.paths.device_tree_compatible.write_bytes(data)
            with self.assertRaises(ValueError):
                require_device_target(self.paths, "", "aarch64")


class SignedHardwareSelection(unittest.TestCase):
    def test_matching_signed_fp6_graph_can_be_selected(self):
        rig = fakes.Rig()
        self.addCleanup(rig.close)
        rig.settings = replace(rig.settings, hardware_target=FP6,
                               ref_template="luma/1/{arch}/fairphone-fp6/{channel}")
        rig.backend = fakes.FakeRpmOstree(origin="luma:luma/1/aarch64/fairphone-fp6/stable")
        path = rig.paths.device_tree_compatible
        path.parent.mkdir(parents=True)
        path.write_bytes(b"fairphone,fp6\0qcom,milos\0")
        doc = graph_doc([fakes.release("1.0.0", 1), fakes.release("1.0.1", 2)], arch="aarch64")
        doc.update(schema_version=2, hardware_target=FP6)
        rig.publish(doc)
        engine = Engine(rig.paths, rig.settings, rig.backend, rig.probes, rig.http,
                        clock=lambda: rig.now, arch="aarch64",
                        boot_id=lambda: "test-boot", greenboot_installed=True)
        self.assertEqual(engine.check().release.commit, fakes.commit(2))
        self.assertEqual(rig.backend.calls, [])  # Selection does not stage a deployment.

    def test_valid_signature_does_not_authorize_wrong_hardware(self):
        for schema, target in ((1, None), (2, "generic"), (2, "fairphone,fp5")):
            with self.subTest(schema=schema, target=target):
                rig = fakes.Rig()
                self.addCleanup(rig.close)
                rig.settings = replace(rig.settings, hardware_target=FP6,
                                       ref_template="luma/1/{arch}/fairphone-fp6/{channel}")
                rig.backend = fakes.FakeRpmOstree(origin="luma:luma/1/aarch64/fairphone-fp6/stable")
                path = rig.paths.device_tree_compatible
                path.parent.mkdir(parents=True)
                path.write_bytes(b"fairphone,fp6\0qcom,milos\0")
                doc = graph_doc([fakes.release("1.0.0", 1), fakes.release("1.0.1", 2)],
                                arch="aarch64")
                doc["schema_version"] = schema
                if target is not None:
                    doc["hardware_target"] = target
                rig.publish(doc)
                engine = Engine(rig.paths, rig.settings, rig.backend, rig.probes, rig.http,
                                clock=lambda: rig.now, arch="aarch64",
                                boot_id=lambda: "test-boot", greenboot_installed=True)
                self.assertIsNone(engine.check())
                self.assertEqual(engine.status().available_version, "")
                self.assertFalse(engine.download(user_initiated=True))
                self.assertEqual(rig.backend.calls, [])


class CommitAdmission(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.addCleanup(self.rig.close)
        self.rig.settings = replace(self.rig.settings, hardware_target=FP6,
                                    ref_template="luma/1/{arch}/fairphone-fp6/{channel}")
        self.rig.backend = fakes.FakeRpmOstree(origin="luma:luma/1/aarch64/fairphone-fp6/stable")
        self.path = self.rig.paths.device_tree_compatible
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"fairphone,fp6\0qcom,milos\0")
        doc = graph_doc([fakes.release("1.0.0", 1), fakes.release("1.0.1", 2)], arch="aarch64")
        doc.update(schema_version=2, hardware_target=FP6)
        self.rig.publish(doc)
        self.engine = Engine(self.rig.paths, self.rig.settings, self.rig.backend,
                             self.rig.probes, self.rig.http, clock=lambda: self.rig.now,
                             arch="aarch64", boot_id=lambda: "test-boot", greenboot_installed=True)
        self.metadata = {"org.projectluma.hardware-target": FP6,
                         "org.projectluma.architecture": "aarch64"}

    def test_signed_graph_cannot_stage_commit_without_hardware_metadata(self):
        self.assertFalse(self.engine.download(user_initiated=True))
        self.assertEqual([c[0] for c in self.rig.backend.calls], ["download", "commit-metadata"])
        self.assertFalse(any(d.staged for d in self.rig.backend.list))

    def test_signed_graph_cannot_stage_commit_for_other_architecture(self):
        self.metadata["org.projectluma.architecture"] = "x86_64"
        self.rig.backend.commit_metadata[fakes.commit(2)] = self.metadata
        self.assertFalse(self.engine.download(user_initiated=True))
        self.assertFalse(any(d.staged for d in self.rig.backend.list))

    def test_exact_commit_is_checked_between_download_and_stage(self):
        self.rig.backend.commit_metadata[fakes.commit(2)] = self.metadata
        self.assertTrue(self.engine.download(user_initiated=True))
        self.assertEqual([c[0] for c in self.rig.backend.calls],
                         ["download", "commit-metadata", "update"])
        self.assertTrue(all(c[1] == fakes.commit(2) for c in self.rig.backend.calls))

    def test_device_identity_is_rechecked_after_download(self):
        self.rig.backend.commit_metadata[fakes.commit(2)] = self.metadata
        self.rig.backend.during_download = lambda: self.path.write_bytes(b"other,board\0")
        self.assertFalse(self.engine.download(user_initiated=True))
        self.assertFalse(any(d.staged for d in self.rig.backend.list))

    def test_wrong_board_commit_cannot_stage(self):
        self.metadata["org.projectluma.hardware-target"] = "fairphone,fp5"
        self.rig.backend.commit_metadata[fakes.commit(2)] = self.metadata
        self.assertFalse(self.engine.download(user_initiated=True))
        self.assertFalse(any(d.staged for d in self.rig.backend.list))

    def test_apply_rechecks_the_pending_commit_before_restart(self):
        self.rig.backend.commit_metadata[fakes.commit(2)] = self.metadata
        self.assertTrue(self.engine.download(user_initiated=True))
        self.rig.backend.commit_metadata[fakes.commit(2)] = {}
        restarted = []
        with self.assertRaises(UpdateError):
            self.engine.apply(lambda: restarted.append(True))
        self.assertEqual(restarted, [])

    def test_rollback_refuses_previous_commit_for_another_target(self):
        previous = fakes.FakeDeployment(fakes.commit(0), "0.9.0",
                                        "luma:luma/1/aarch64/fairphone-fp6/stable")
        self.rig.backend.list.append(previous)
        self.rig.backend.commit_metadata[fakes.commit(0)] = {
            **self.metadata, "org.projectluma.hardware-target": "generic"}
        with self.assertRaises(UpdateError):
            self.engine.rollback()
        self.assertFalse(any(c[0] == "rollback" for c in self.rig.backend.calls))

    def test_matching_rollback_remains_available(self):
        previous = fakes.FakeDeployment(fakes.commit(0), "0.9.0",
                                        "luma:luma/1/aarch64/fairphone-fp6/stable")
        self.rig.backend.list.append(previous)
        self.rig.backend.commit_metadata[fakes.commit(0)] = self.metadata
        self.engine.rollback()
        self.assertTrue(any(c[0] == "rollback" for c in self.rig.backend.calls))


class ArchitectureWithoutTarget(unittest.TestCase):
    def test_wrong_arch_graph_is_refused(self):
        graph = parse_graph(json.dumps(graph_doc([], arch="aarch64")).encode())
        with self.assertRaises(GraphError):
            check_freshness(graph, channel="stable", arch="x86_64", now=NOW, last_generated_at=None)

    def test_generic_desktop_refuses_phone_or_malformed_commit_target(self):
        for target in ("fairphone,fp6", "unihertz,titan2e", [], None, False):
            with self.subTest(target=target), self.assertRaises(ValueError):
                require_commit_target({"org.projectluma.architecture": "aarch64",
                                       "org.projectluma.hardware-target": target}, "", "aarch64")
        require_commit_target({"org.projectluma.hardware-target": ""}, "", "aarch64")

    def test_generic_commit_architecture_must_match(self):
        require_commit_target({"org.projectluma.architecture": "x86_64"}, "", "x86_64")
        require_commit_target({}, "", "x86_64")  # generic commits from before the key
        for value in ("aarch64", "", None, b"x86_64"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                require_commit_target({"org.projectluma.architecture": value}, "", "x86_64")

    def test_generic_install_does_not_keep_a_wrong_arch_commit_staged(self):
        rig = fakes.Rig()
        self.addCleanup(rig.close)
        rig.publish(graph_doc([fakes.release("1.0.0", 1), fakes.release("1.0.1", 2)]))
        rig.backend.commit_metadata[fakes.commit(2)] = {"org.projectluma.architecture": "aarch64"}
        engine = rig.engine()
        self.assertFalse(engine.download(user_initiated=True))
        self.assertIn(("commit-metadata", fakes.commit(2)), rig.backend.calls)
        self.assertEqual(rig.backend.calls[-1], ("cleanup",))
        self.assertFalse(any(d.staged for d in rig.backend.list))
        self.assertEqual(engine.status().last_error_class, "hardware-target")

    def test_generic_metadata_read_failure_cleans_staged_commit(self):
        rig = fakes.Rig()
        self.addCleanup(rig.close)
        rig.publish(graph_doc([fakes.release("1.0.0", 1), fakes.release("1.0.1", 2)]))
        def unreadable(_revision):
            raise RuntimeError("immutable commit metadata unreadable")
        rig.backend.commit_target_metadata = unreadable
        self.assertFalse(rig.engine().download(user_initiated=True))
        self.assertEqual(rig.backend.calls[-1], ("cleanup",))
        self.assertFalse(any(d.staged for d in rig.backend.list))

    def test_generic_install_stages_a_matching_commit(self):
        rig = fakes.Rig()
        self.addCleanup(rig.close)
        rig.publish(graph_doc([fakes.release("1.0.0", 1), fakes.release("1.0.1", 2)]))
        rig.backend.commit_metadata[fakes.commit(2)] = {"org.projectluma.architecture": "x86_64"}
        self.assertTrue(rig.engine().download(user_initiated=True))
        self.assertTrue(any(d.staged for d in rig.backend.list))


class ArchQualifiedGraphUrl(unittest.TestCase):
    def test_default_and_shipped_graph_url_name_the_architecture(self):
        conf = Path(__file__).resolve().parents[1] / "data/config/update.conf"
        self.assertIn("graph_url = https://dl.simplyluma.com/os/graph/{arch}/{channel}.json",
                      conf.read_text().splitlines())
        self.assertEqual(Settings().graph_url, "https://dl.simplyluma.com/os/graph/{arch}/{channel}.json")

    def test_engine_fetches_the_graph_for_its_own_architecture(self):
        rig = fakes.Rig()
        self.addCleanup(rig.close)
        rig.publish(graph_doc([fakes.release("1.0.0", 1)]))
        rig.engine().check()
        self.assertIn("https://dl.simplyluma.com/os/graph/x86_64/stable.json", rig.http.gets)
        self.assertFalse(any("/graph/stable.json" in url or "aarch64" in url for url in rig.http.gets))

    def test_titan_is_managed_and_fetches_its_arm_graph(self):
        rig = fakes.Rig()
        self.addCleanup(rig.close)
        rig.settings = replace(rig.settings, hardware_target=TITAN2E,
                               ref_template="luma/1/{arch}/unihertz-titan2e/{channel}")
        rig.backend = fakes.FakeRpmOstree(origin="luma:luma/1/aarch64/unihertz-titan2e/stable")
        path = rig.paths.device_tree_compatible
        path.parent.mkdir(parents=True)
        path.write_bytes(b"unihertz,titan2-elite\0mediatek,mt6878\0")
        doc = graph_doc([fakes.release("1.0.0", 1), fakes.release("1.0.1", 2)], arch="aarch64")
        doc.update(schema_version=2, hardware_target=TITAN2E)
        rig.publish(doc)
        engine = Engine(rig.paths, rig.settings, rig.backend, rig.probes, rig.http,
                        clock=lambda: rig.now, arch="aarch64",
                        boot_id=lambda: "test-boot", greenboot_installed=True)
        self.assertEqual(engine.check().release.commit, fakes.commit(2))
        self.assertNotEqual(engine.status().state, "unmanaged")
        self.assertIn("https://dl.simplyluma.com/os/graph/aarch64/unihertz-titan2e/stable.json", rig.http.gets)

