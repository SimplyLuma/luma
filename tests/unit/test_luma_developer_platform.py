# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[2]
SDK = ROOT / "src/luma-platform/sdk"
BROKER = ROOT / "src/luma-platform/broker"
sys.path.insert(0, str(SDK))
sys.path.insert(0, str(BROKER))

from luma_sdk.broker import BrokerPolicy, Grant, Scope  # noqa: E402
from luma_sdk.contracts import Action, LiveCategory, LiveExtension, Privacy, Risk, SemanticObject  # noqa: E402
from luma_sdk.cli import _semantic_scope_command, build_parser  # noqa: E402
from luma_sdk.semantic import SemanticClient, SemanticClientError, json_parameter  # noqa: E402
from luma_sdk.validation import validate_manifest  # noqa: E402


class ContractTests(unittest.TestCase):
    def test_malformed_contract_fails_closed_without_crashing(self):
        malformed = {
            "schema_version": "0.2",
            "application": [],
            "platform": [],
            "responsive": {"test_widths": 360, "input_modes": 1, "presentation_modes": None},
            "distribution": [],
            "lifecycle": [],
            "conformance": [],
        }
        errors = validate_manifest(malformed, ROOT)
        self.assertIn("application must be a table", errors)
        self.assertIn("responsive.test_widths must be an array", errors)
        self.assertIn("distribution must be a table", errors)
        self.assertIn("lifecycle must be a table", errors)

    def test_semantic_cli_uses_one_verified_identity_across_commands(self):
        args = build_parser().parse_args(
            [
                "semantic",
                "request",
                "org.projectluma.Notes",
                "--scope",
                "observe-public",
                "--duration",
                "600",
            ]
        )
        command = _semantic_scope_command(args, token="deadbeef")
        self.assertIn(
            "--unit=app-gnome-org.projectluma.SemanticInspector-deadbeef.scope",
            command,
        )
        self.assertNotIn("--wait", command)
        self.assertEqual(command[-8:], [
            "/usr/bin/luma",
            "semantic",
            "request",
            "org.projectluma.Notes",
            "--scope",
            "observe-public",
            "--duration",
            "600",
        ])

    def test_semantic_inspector_uses_bounded_tokens_and_json_parameters(self):
        first = SemanticClient._token()
        second = SemanticClient._token()
        self.assertRegex(first, r"^sdk_[0-9a-f]{24}$")
        self.assertNotEqual(first, second)
        self.assertEqual(json_parameter('{"title":"Acceptance"}'), {"title": "Acceptance"})
        with self.assertRaises(SemanticClientError):
            json_parameter("{not-json}")

    def test_destructive_actions_always_require_confirmation(self):
        action = Action("document.delete", "Delete", risk=Risk.DESTRUCTIVE)
        self.assertTrue(action.requires_confirmation)
        policy = BrokerPolicy()
        policy.grant(Grant("agent", "org.projectluma.Notes", frozenset(Scope)))
        self.assertFalse(
            policy.authorize_action(
                "agent",
                "org.projectluma.Notes",
                action.to_dict(),
                locked=False,
                confirmed=False,
            ).allowed
        )
        self.assertTrue(
            policy.authorize_action(
                "agent",
                "org.projectluma.Notes",
                action.to_dict(),
                locked=False,
                confirmed=True,
            ).allowed
        )

    def test_secret_objects_are_never_exported(self):
        root = SemanticObject("secret", "credential", "Password", privacy=Privacy.SECRET)
        policy = BrokerPolicy()
        policy.grant(Grant("agent", "org.example.App", frozenset({Scope.OBSERVE_PRIVATE})))
        decision, visible = policy.observe(
            "agent", "org.example.App", root.to_dict(), locked=False
        )
        self.assertFalse(decision.allowed)
        self.assertIsNone(visible)

    def test_secret_descendants_are_omitted_from_visible_parent(self):
        root = SemanticObject("root", "application", "Example", privacy=Privacy.PUBLIC)
        root.add_child(SemanticObject("credential", "credential", "Saved password", privacy=Privacy.SECRET))
        policy = BrokerPolicy()
        policy.grant(Grant("agent", "org.example.App", frozenset({Scope.OBSERVE_PUBLIC})))
        decision, visible = policy.observe(
            "agent", "org.example.App", root.to_dict(), locked=False
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(visible["children"], [])

    def test_destructive_scope_is_required_in_addition_to_confirmation(self):
        action = Action("document.delete", "Delete", risk=Risk.DESTRUCTIVE)
        policy = BrokerPolicy()
        policy.grant(Grant("agent", "org.example.App", frozenset({Scope.INVOKE_LOW_RISK})))
        self.assertFalse(
            policy.authorize_action(
                "agent",
                "org.example.App",
                action.to_dict(),
                locked=False,
                confirmed=True,
            ).allowed
        )

    def test_live_extension_is_bounded(self):
        activity = LiveExtension(
            "meeting",
            "org.projectluma.Calendar",
            LiveCategory.CALL,
            "Design review",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        for index in range(3):
            activity.add_action(Action(f"call.action-{index}", "Action"))
        with self.assertRaises(ValueError):
            activity.add_action(Action("call.fourth", "Fourth"))

    def test_generated_application_passes_sdk_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = pathlib.Path(directory) / "example"
            environment = {**os.environ, "PYTHONPATH": str(SDK)}
            created = subprocess.run(
                [sys.executable, "-m", "luma_sdk.cli", "new", "Example", "--id", "org.example.Example", "--destination", str(destination)],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            linted = subprocess.run(
                [sys.executable, "-m", "luma_sdk.cli", "lint", str(destination / "luma-app.toml")],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(linted.returncode, 0, linted.stderr)
            self.assertTrue((destination / "flatpak/org.example.Example.json").is_file())
            inspected = subprocess.run(
                [sys.executable, "-m", "luma_sdk.cli", "inspect", str(destination / "luma-app.toml")],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            report = json.loads(inspected.stdout)
            self.assertEqual(report["status"], "Development")
            self.assertFalse(report["native_candidate"])
            self.assertTrue((destination / "data/org.example.Example.desktop").is_file())
            self.assertTrue((destination / "data/org.example.Example.metainfo.xml").is_file())
            self.assertTrue((destination / "data/org.example.Example.svg").is_file())
            self.assertIn(
                "LumaUI.init()",
                (destination / "src/main.py").read_text(encoding="utf-8"),
            )

    def test_native_status_requires_recorded_cross_architecture_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = pathlib.Path(directory) / "example"
            environment = {**os.environ, "PYTHONPATH": str(SDK)}
            subprocess.run(
                [sys.executable, "-m", "luma_sdk.cli", "new", "Example", "--id", "org.example.Example", "--destination", str(destination)],
                cwd=ROOT,
                env=environment,
                check=True,
                capture_output=True,
            )
            initial = subprocess.run(
                [sys.executable, "-m", "luma_sdk.cli", "release-check", str(destination / "luma-app.toml")],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(initial.returncode, 1)
            self.assertIn("architecture has not passed: aarch64", initial.stderr)

            flatpak_path = destination / "flatpak/org.example.Example.json"
            flatpak = json.loads(flatpak_path.read_text(encoding="utf-8"))
            self.assertEqual(flatpak["modules"][0]["subdir"], "src/luma-platform")
            platform_source = flatpak["modules"][0]["sources"][0]
            platform_source.pop("tag")
            platform_source["commit"] = "a" * 40
            flatpak_path.write_text(json.dumps(flatpak), encoding="utf-8")
            (destination / "verification/sbom.spdx.json").write_text("{}", encoding="utf-8")
            (destination / "verification/test-report.json").write_text("{}", encoding="utf-8")
            evidence_path = destination / "verification/luma-native-evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["tested_at"] = "2026-08-27T17:30:00Z"
            evidence["source_revision"] = "b" * 40
            evidence["architectures"] = {"x86_64": True, "aarch64": True}
            evidence["presentations"] = {"desktop": True, "mobile": True}
            evidence["lanes"] = {name: True for name in evidence["lanes"]}
            evidence["artifacts"] = {
                "sbom": "verification/sbom.spdx.json",
                "test_report": "verification/test-report.json",
            }
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            accepted = subprocess.run(
                [sys.executable, "-m", "luma_sdk.cli", "release-check", str(destination / "luma-app.toml")],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)

    def test_contract_accepts_flatpaks_standard_yaml_manifest_form(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = pathlib.Path(directory) / "example"
            environment = {**os.environ, "PYTHONPATH": str(SDK)}
            subprocess.run(
                [sys.executable, "-m", "luma_sdk.cli", "new", "Example", "--id", "org.example.Example", "--destination", str(destination)],
                cwd=ROOT,
                env=environment,
                check=True,
                capture_output=True,
            )
            json_path = destination / "flatpak/org.example.Example.json"
            yaml_path = json_path.with_suffix(".yaml")
            yaml_path.write_text(
                yaml.safe_dump(json.loads(json_path.read_text(encoding="utf-8")), sort_keys=False),
                encoding="utf-8",
            )
            json_path.unlink()
            contract_path = destination / "luma-app.toml"
            contract_path.write_text(
                contract_path.read_text(encoding="utf-8").replace(
                    "flatpak/org.example.Example.json", "flatpak/org.example.Example.yaml"
                ),
                encoding="utf-8",
            )
            linted = subprocess.run(
                [sys.executable, "-m", "luma_sdk.cli", "lint", str(contract_path)],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(linted.returncode, 0, linted.stderr)

    def test_lint_rejects_identity_drift_and_broad_flatpak_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = pathlib.Path(directory) / "example"
            environment = {**os.environ, "PYTHONPATH": str(SDK)}
            subprocess.run(
                [sys.executable, "-m", "luma_sdk.cli", "new", "Example", "--id", "org.example.Example", "--destination", str(destination)],
                cwd=ROOT,
                env=environment,
                check=True,
                capture_output=True,
            )
            flatpak_path = destination / "flatpak/org.example.Example.json"
            flatpak = json.loads(flatpak_path.read_text(encoding="utf-8"))
            flatpak["id"] = "org.example.Impostor"
            flatpak["finish-args"].extend(["--filesystem=host", "--talk-name=org.example.*"])
            flatpak_path.write_text(json.dumps(flatpak), encoding="utf-8")
            rejected = subprocess.run(
                [sys.executable, "-m", "luma_sdk.cli", "lint", str(destination / "luma-app.toml")],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("Flatpak id must match application.id", rejected.stderr)
            self.assertIn("--filesystem=host", rejected.stderr)
            self.assertIn("may not contain a wildcard", rejected.stderr)

    def test_lint_rejects_invalid_flatpak_namespace_conventions(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {**os.environ, "PYTHONPATH": str(SDK)}
            for app_id, message in (
                ("Org.Example.App", "domain components must be lowercase"),
                ("org.example.desktop", "may not end in '.desktop'"),
            ):
                with self.subTest(app_id=app_id):
                    destination = pathlib.Path(directory) / app_id
                    rejected = subprocess.run(
                        [sys.executable, "-m", "luma_sdk.cli", "new", "Example", "--id", app_id, "--destination", str(destination)],
                        cwd=ROOT,
                        env=environment,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(rejected.returncode, 2)
                    self.assertIn(message, rejected.stderr)
                    self.assertFalse(destination.exists())

    def test_contract_schemas_are_well_formed_json(self):
        for schema in (ROOT / "src/luma-platform/schemas").glob("*.json"):
            with self.subTest(schema=schema.name):
                self.assertIsInstance(json.loads(schema.read_text(encoding="utf-8")), dict)

    def test_generated_design_tokens_are_current(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/developer/generate-luma-platform-tokens.py"), "--check"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
