# SPDX-License-Identifier: Apache-2.0
"""The signing-side validator must enforce the same target as the client."""
import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/luma-update/tests"))

from fakes import NOW, graph_doc, iso, release

VALIDATOR = ROOT / "scripts/os/lib/update_graph.py"
spec = importlib.util.spec_from_file_location("target_publisher", VALIDATOR)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class PublisherHardwareTarget(unittest.TestCase):
    def validate(self, doc, target=None, previous=None):
        return publisher.validate(doc, "stable", "aarch64", previous=previous,
                                  hardware_target=target,
                                  now=datetime.fromtimestamp(NOW, timezone.utc))

    def graph(self, target="fairphone,fp6"):
        doc = graph_doc([release("1.0.0", 1)], arch="aarch64")
        doc.update(schema_version=2, hardware_target=target)
        return doc

    def test_correct_target_accepted(self):
        self.assertEqual(self.validate(self.graph(), "fairphone,fp6"), [])

    def test_targeted_graph_cannot_be_published_as_generic(self):
        self.assertTrue(self.validate(self.graph()))

    def test_wrong_board_rejected(self):
        self.assertTrue(self.validate(self.graph("fairphone,fp5"), "fairphone,fp6"))

    def test_legacy_desktop_unchanged(self):
        self.assertEqual(self.validate(graph_doc([], arch="aarch64")), [])

    def test_legacy_desktop_cannot_be_published_for_phone(self):
        self.assertTrue(self.validate(graph_doc([], arch="aarch64"), "fairphone,fp6"))

    def test_previous_graph_cannot_cross_targets(self):
        previous = self.graph()
        previous["generated_at"] = iso(NOW - 7200)
        self.assertEqual(self.validate(self.graph(), "fairphone,fp6", previous), [])
        previous["hardware_target"] = "fairphone,fp5"
        problems = self.validate(self.graph(), "fairphone,fp6", previous)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("target", problems[0])

    def test_invalid_target_configuration_rejected(self):
        for target in ("", "../fp6", False):
            with self.subTest(target=target):
                self.assertTrue(self.validate(self.graph(target), target))
