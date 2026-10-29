# SPDX-License-Identifier: Apache-2.0
"""Check that live gate classification rejects stale/unavailable evidence."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("native_guard_qualification", Path(__file__).with_name("native-guard-qualification.py"))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.run_id = "5d779372-0738-439b-b033-c944d410ed37"
        self.records = [dict(event=event, value=value, guard_run=self.run_id, uid=10135, pid=2150)
                        for event, value in (("native-clipboard-before", "empty"),
                                             ("native-clipboard-write", "request-returned"),
                                             ("native-clipboard-after", "empty"),
                                             ("native-task-name", "empty"))]
        self.controls = {phase: {service: dict(uid=1000, service=service, state=state)
                                for service, state in (("clipboard", "android-fixture"), ("task", "fixture-label"))}
                         for phase in ("before", "after")}

    def verdict(self):
        return gate.assess(self.records, self.run_id, 10135, 2150, self.controls)

    def test_known_denied_reads_and_unchanged_write(self):
        self.assertEqual(self.verdict(), "passed")

    def test_observed_old_image_disclosure_and_write_is_failure(self):
        self.records[0]["value"] = "android-fixture"
        self.records[2]["value"] = "forbidden-write-fixture"
        self.records[3]["value"] = "fixture-label"
        self.controls["after"]["clipboard"]["state"] = "forbidden-write-fixture"
        self.assertEqual(self.verdict(), "failed")

    def test_empty_or_unavailable_trusted_control_never_passes(self):
        original = copy.deepcopy(self.controls)
        for phase in ("before", "after"):
            for service in ("clipboard", "task"):
                for state in ("empty", "unavailable", "other-content"):
                    self.controls = copy.deepcopy(original)
                    self.controls[phase][service]["state"] = state
                    self.assertEqual(self.verdict(), "inconclusive")

    def test_stale_run_and_wrong_process_never_pass(self):
        for key, value in (("guard_run", "previous-run"), ("uid", 1000), ("pid", 2149)):
            original = self.records[0][key]
            self.records[0][key] = value
            self.assertEqual(self.verdict(), "inconclusive")
            self.records[0][key] = original

    def test_unavailable_raw_transport_is_not_denial(self):
        self.records[0]["value"] = "unavailable"
        self.assertEqual(self.verdict(), "inconclusive")

    def test_missing_and_duplicate_receipts_are_inconclusive(self):
        self.records.append(copy.deepcopy(self.records[0]))
        self.assertEqual(self.verdict(), "inconclusive")
        self.records = self.records[:3]
        self.assertEqual(self.verdict(), "inconclusive")

    def test_task_name_disclosure_fails_even_if_clipboard_is_denied(self):
        self.records[3]["value"] = "fixture-label"
        self.assertEqual(self.verdict(), "failed")

    def test_unchanged_empty_reads_cannot_hide_admitted_setter(self):
        self.controls["after"]["clipboard"]["state"] = "forbidden-write-fixture"
        self.assertEqual(self.verdict(), "failed")


class ControlEvidenceTests(unittest.TestCase):
    def test_unavailable_post_control_state_is_retained(self):
        output = '{"uid":1000,"service":"clipboard","state":"empty"}'
        with patch.object(gate.subprocess, "run", return_value=SimpleNamespace(stdout=output, returncode=1)):
            record = gate.trusted("clipboard", observe_after=True)
        self.assertEqual(record["state"], "empty")
        self.assertEqual(record["returncode"], 1)

    def test_wrong_control_identity_is_rejected(self):
        output = '{"uid":10126,"service":"clipboard","state":"android-fixture"}'
        with patch.object(gate.subprocess, "run", return_value=SimpleNamespace(stdout=output, returncode=0)):
            with self.assertRaises(RuntimeError):
                gate.trusted("clipboard")


class InstalledPathTests(unittest.TestCase):
    def test_randomized_android_package_directory(self):
        path = "/data/app/~~Abc_012+==/org.projectluma.androidtest-Abc_12==/base.apk"
        self.assertEqual(gate.installed_apk_path("package:" + path + "\n"), path)

    def test_old_package_directory(self):
        path = "/data/app/org.projectluma.androidtest-1/base.apk"
        self.assertEqual(gate.installed_apk_path("package:" + path), path)

    def test_paths_outside_package_root_and_multiple_payloads_rejected(self):
        for value in ("package:/data/local/tmp/base.apk", "package:/data/app/../private/base.apk",
                      "package:/data/app/./base.apk", "package:/data/app/x/base.apk\npackage:/data/app/x/split.apk",
                      "package:/data/app/x/base.apk;echo", "", "package:/data/app/x/base.apk\nextra"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                gate.installed_apk_path(value)


if __name__ == "__main__":
    unittest.main()
