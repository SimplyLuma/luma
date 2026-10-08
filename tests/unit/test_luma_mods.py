#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
MODS_SOURCE = REPO_ROOT / "src/luma-mods"
EXAMPLES = REPO_ROOT / "examples/mods"
BUILTIN_CATALOG = MODS_SOURCE / "catalog"
sys.path.insert(0, str(MODS_SOURCE))

from luma_mods.errors import (  # noqa: E402
    CatalogError,
    LumaModsError,
    ManifestValidationError,
    ResolutionError,
    TrustError,
    StateError,
    TransactionError,
    CatalogUpdateError,
)
from luma_mods.catalog_update import (  # noqa: E402
    CatalogSnapshot,
    TrustedCatalogPlan,
    TufCatalogClient,
)
from luma_mods.catalog_runtime import (  # noqa: E402
    CatalogClientConfig,
    TrustedCatalogRuntime,
)
from luma_mods.catalog_author import assemble as assemble_catalog  # noqa: E402
from luma_mods.catalog_ceremony import prepare as prepare_ceremony  # noqa: E402
from luma_mods.catalog_publish import accept as accept_catalog  # noqa: E402
from luma_mods.author_sandbox import reproducible_build  # noqa: E402
from luma_mods.lifecycle import Authorization, PreferenceLifecycle  # noqa: E402
from luma_mods.inventory import ProbeResult, collect_inventory  # noqa: E402
from luma_mods.image_composition import (  # noqa: E402
    ImageCompositionLock,
    build_image_composition_lock,
    require_install_authorized,
)
from luma_mods.manifest import ManifestLimits, inspect_manifest  # noqa: E402
from luma_mods.profile import FilePreferenceBackend, PreferenceProfile  # noqa: E402
from luma_mods.privileged import (  # noqa: E402
    ArtifactRequest,
    ClosedSystemBackend,
    SystemCompositionRequest,
    build_system_request,
)
from luma_mods.paths import UserPaths  # noqa: E402
from luma_mods.pilot import template as pilot_template, validate as validate_pilot  # noqa: E402
from luma_mods.recovery import recover_pending  # noqa: E402
from luma_mods.resolver import Catalog, HostContext, assess_mod, resolve_mod  # noqa: E402
from luma_mods.runtime import UserRuntime, authorization_for_plan, profile_for  # noqa: E402
from luma_mods.state import StateStore, empty_state  # noqa: E402
from luma_mods.system_backend import ArtifactStore, RpmOstreeBackend  # noqa: E402
from luma_mods.system_catalog import SystemCatalogAdmission  # noqa: E402
from luma_mods.system_coordinator import SystemTransactionCoordinator  # noqa: E402
from luma_mods.system_host import (  # noqa: E402
    SystemHostProfile,
    dmi_hardware_identities,
)
from luma_mods.system_state import SystemStateStore, empty_system_state  # noqa: E402
from luma_mods.system_gate import (  # noqa: E402
    POLKIT_CHECK_AUTHORIZATION_SIGNATURE,
    RECOVERY_HEALTH_UNIT,
    polkit_authorized,
    validate_recovery_record,
)
from luma_mods.trust import TrustPolicy, VerificationResult, verify_inspection  # noqa: E402
from luma_mods.update_policy import evaluate_update  # noqa: E402


def example(name: str = "org.projectluma.mod.green-dock.mod.json") -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def write_manifest(root: Path, value: dict, name: str = "test.mod.json") -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def host(**overrides: object) -> HostContext:
    value: dict[str, object] = {
        "luma_base": "0.1.0",
        "architecture": "x86_64",
        "presentation": "desktop",
        "hardware": [],
        "capabilities": {"org.luma.apps.core": "1.0.0"},
        "installed_mods": {},
        "effect_owners": {},
    }
    value.update(overrides)
    return HostContext.from_dict(value)


class ManifestTests(unittest.TestCase):
    def test_inspects_example_and_produces_two_digests(self) -> None:
        result = inspect_manifest(EXAMPLES / "org.projectluma.mod.green-dock.mod.json")
        self.assertEqual(result.mod.identity.id, "org.projectluma.mod.green-dock")
        self.assertEqual(len(result.source_sha256), 64)
        self.assertEqual(len(result.canonical_sha256), 64)
        self.assertNotEqual(result.source_sha256, result.canonical_sha256)
        self.assertEqual(len(result.signing_sha256), 64)

    def test_canonical_digest_ignores_formatting_and_key_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = example()
            compact = root / "compact.mod.json"
            pretty = root / "pretty.mod.json"
            compact.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
            pretty.write_text(json.dumps(value, indent=4, sort_keys=True), encoding="utf-8")
            self.assertEqual(
                inspect_manifest(compact).canonical_sha256,
                inspect_manifest(pretty).canonical_sha256,
            )

    def test_signing_digest_excludes_detached_signature_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = example()
            unsigned = inspect_manifest(write_manifest(root, value, "unsigned.mod.json"))
            value["signatures"] = [
                {
                    "signer": "org.projectluma",
                    "algorithm": "sigstore-bundle-v0.3",
                    "manifest_digest": f"sha256:{unsigned.signing_sha256}",
                    "bundle": "test.sigstore.json",
                    "bundle_digest": f"sha256:{'0' * 64}",
                }
            ]
            signed = inspect_manifest(write_manifest(root, value, "signed.mod.json"))
            self.assertEqual(unsigned.signing_sha256, signed.signing_sha256)
            self.assertNotEqual(unsigned.canonical_sha256, signed.canonical_sha256)

    def test_rejects_signature_bundle_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            value = example()
            value["signatures"] = [
                {
                    "signer": "org.projectluma",
                    "algorithm": "sigstore-bundle-v0.3",
                    "manifest_digest": f"sha256:{'0' * 64}",
                    "bundle": "../outside.sigstore.json",
                    "bundle_digest": f"sha256:{'0' * 64}",
                }
            ]
            with self.assertRaisesRegex(ManifestValidationError, "sidecar filename"):
                inspect_manifest(write_manifest(Path(directory), value))

    def test_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = write_manifest(root, example(), "real.mod.json")
            link = root / "link.mod.json"
            link.symlink_to(real)
            with self.assertRaisesRegex(ManifestValidationError, "safely"):
                inspect_manifest(link)

    def test_rejects_oversized_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_manifest(Path(directory), example())
            with self.assertRaisesRegex(ManifestValidationError, "larger"):
                inspect_manifest(path, ManifestLimits(max_bytes=64))

    def test_rejects_duplicate_json_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.mod.json"
            path.write_text('{"schema":"org.luma.mod/v0.1","schema":"bad"}', encoding="utf-8")
            with self.assertRaisesRegex(ManifestValidationError, "duplicate field"):
                inspect_manifest(path)

    def test_rejects_unknown_security_relevant_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            value = example()
            value["post_install_command"] = "sudo anything"
            path = write_manifest(Path(directory), value)
            with self.assertRaisesRegex(ManifestValidationError, "unknown field"):
                inspect_manifest(path)

    def test_rejects_deletion_as_implicit_removal_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            value = example()
            value["recovery"]["user_data"] = "delete"
            path = write_manifest(Path(directory), value)
            with self.assertRaisesRegex(ManifestValidationError, "separate transaction"):
                inspect_manifest(path)

    def test_rejects_relative_or_non_normalized_file_effects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, path in enumerate(("usr/share/luma", "/usr/share/../etc/luma")):
                value = example()
                value["effects"]["files"] = [path]
                with self.subTest(path=path), self.assertRaisesRegex(
                    ManifestValidationError, "normalized absolute paths"
                ):
                    inspect_manifest(write_manifest(root, value, f"bad-{index}.mod.json"))

    def test_rejects_ambiguous_effect_domains_and_device_wildcards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            domain = example()
            domain["effects"]["settings"] = ["dock appearance"]
            with self.assertRaisesRegex(ManifestValidationError, "reverse-domain"):
                inspect_manifest(write_manifest(root, domain, "domain.mod.json"))
            device = example()
            device["effects"]["devices"] = ["pci:v00008086*"]
            with self.assertRaisesRegex(ManifestValidationError, "wildcard"):
                inspect_manifest(write_manifest(root, device, "device.mod.json"))

    def test_boot_effect_escalates_to_core_system_and_recovery_reboot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            value = example()
            value["identity"]["kind"] = "hardware"
            value["effects"]["kernel_modules"] = ["surface_aggregator"]
            value["compatibility"]["hardware"] = ["dmi:Microsoft:Surface"]
            value["compatibility"]["kernel_releases"] = ["6.15.10-200.fc44.x86_64"]
            path = write_manifest(Path(directory), value)
            assessment = assess_mod(inspect_manifest(path))
            self.assertEqual(assessment.impact, "core-system")
            self.assertEqual(assessment.activation, "recovery-reboot")

    def test_kernel_effect_requires_exact_hardware_and_kernel_tuple(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            value = example()
            value["identity"]["kind"] = "hardware"
            value["effects"]["kernel_modules"] = ["surface_aggregator"]
            with self.assertRaisesRegex(ManifestValidationError, "kernel_releases"):
                inspect_manifest(write_manifest(Path(directory), value))
            value["compatibility"]["kernel_releases"] = ["6.*"]
            value["compatibility"]["hardware"] = ["dmi:Microsoft:Surface"]
            with self.assertRaisesRegex(ManifestValidationError, "exact kernel"):
                inspect_manifest(write_manifest(Path(directory), value, "wildcard.mod.json"))


class ResolverTests(unittest.TestCase):
    def _kde_host(self, **overrides: object) -> HostContext:
        value = json.loads(
            (EXAMPLES / "pilots" / "host-kde-desktop.json").read_text(encoding="utf-8")
        )
        value.update(overrides)
        return HostContext.from_dict(value)

    def _kde_catalog(self) -> Catalog:
        return Catalog.from_directory(EXAMPLES / "pilots")

    def test_dependency_is_loud_and_included_before_target(self) -> None:
        plan = resolve_mod(
            "org.projectluma.mod.green-dock",
            Catalog.from_directory(EXAMPLES),
            host(),
        )
        self.assertEqual(
            [item.inspection.mod.identity.id for item in plan.mods],
            [
                "org.projectluma.prairie.dock-style-api",
                "org.projectluma.mod.green-dock",
            ],
        )
        self.assertEqual(len(plan.dependencies), 1)
        self.assertEqual(plan.dependencies[0].dependency_name, "Prairie Dock Styling Support")
        self.assertEqual(plan.dependencies[0].dependency_publisher, "Project Luma")
        self.assertIn("bounded and reversible", plan.dependencies[0].reason)
        self.assertFalse(plan.dependencies[0].already_installed)
        self.assertEqual(plan.impact, "appearance")
        self.assertEqual(plan.activation, "live")

    def test_installed_dependency_is_reported_but_not_reinstalled(self) -> None:
        plan = resolve_mod(
            "org.projectluma.mod.green-dock",
            Catalog.from_directory(EXAMPLES),
            host(installed_mods={"org.projectluma.prairie.dock-style-api": "1.0.0"}),
        )
        self.assertEqual([item.inspection.mod.identity.id for item in plan.mods], [
            "org.projectluma.mod.green-dock"
        ])
        self.assertTrue(plan.dependencies[0].already_installed)

    def test_installed_dependency_must_match_catalog_evidence(self) -> None:
        with self.assertRaisesRegex(ResolutionError, "cannot be inferred safely"):
            resolve_mod(
                "org.projectluma.mod.green-dock",
                Catalog.from_directory(EXAMPLES),
                host(installed_mods={"org.projectluma.prairie.dock-style-api": "1.1.0"}),
            )

    def test_missing_dependency_fails_with_exact_id(self) -> None:
        catalog = Catalog([inspect_manifest(EXAMPLES / "org.projectluma.mod.green-dock.mod.json")])
        with self.assertRaisesRegex(
            ResolutionError, "org.projectluma.prairie.dock-style-api"
        ):
            resolve_mod("org.projectluma.mod.green-dock", catalog, host())

    def test_wrong_architecture_is_rejected(self) -> None:
        with self.assertRaisesRegex(ResolutionError, "riscv64"):
            resolve_mod(
                "org.projectluma.mod.green-dock",
                Catalog.from_directory(EXAMPLES),
                host(architecture="riscv64"),
            )

    def test_image_compose_mode_is_explicit_and_phone_candidate_is_scoped(self) -> None:
        catalog = self._kde_catalog()
        phone = "org.projectluma.pilot.hardware.oneplus-cph2653"
        phone_host = HostContext.from_dict(json.loads(
            (EXAMPLES / "pilots" / "host-oneplus-cph2653-image.json").read_text(
                encoding="utf-8"
            )
        ))
        plan = resolve_mod(phone, catalog, phone_host)
        self.assertEqual(plan.target_id, phone)
        self.assertEqual(plan.impact, "hardware")
        with self.assertRaisesRegex(ResolutionError, "install mode running-system"):
            resolve_mod(
                phone,
                catalog,
                HostContext.from_dict({
                    **json.loads(
                        (EXAMPLES / "pilots" / "host-oneplus-cph2653-image.json").read_text(
                            encoding="utf-8"
                        )
                    ),
                    "install_mode": "running-system",
                }),
            )

    def test_kernel_bound_mod_rejects_different_kernel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = example("org.projectluma.prairie.dock-style-api.mod.json")
            value["identity"]["id"] = "org.example.surface-kernel"
            value["identity"]["kind"] = "hardware"
            value["compatibility"]["hardware"] = ["dmi:Microsoft:Surface"]
            value["compatibility"]["kernel_releases"] = ["6.15.10-200.fc44.x86_64"]
            value["effects"]["kernel_modules"] = ["surface_aggregator"]
            write_manifest(root, value)
            with self.assertRaisesRegex(ResolutionError, "does not support kernel"):
                resolve_mod(
                    "org.example.surface-kernel",
                    Catalog.from_directory(root),
                    host(
                        hardware=["dmi:Microsoft:Surface"],
                        kernel_release="6.16.0-0.rc1.fc45.x86_64",
                    ),
                )


    def test_dependency_cycle_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = example("org.projectluma.prairie.dock-style-api.mod.json")
            second = deepcopy(first)
            first["identity"]["id"] = "org.example.first"
            first["identity"]["name"] = "First"
            first["dependencies"] = [
                {"id": "org.example.second", "version": "=1.0.0", "reason": "cycle"}
            ]
            second["identity"]["id"] = "org.example.second"
            second["identity"]["name"] = "Second"
            second["dependencies"] = [
                {"id": "org.example.first", "version": "=1.0.0", "reason": "cycle"}
            ]
            write_manifest(root, first, "first.mod.json")
            write_manifest(root, second, "second.mod.json")
            with self.assertRaisesRegex(ResolutionError, "cycle"):
                resolve_mod("org.example.first", Catalog.from_directory(root), host())

    def test_duplicate_catalog_identity_is_rejected(self) -> None:
        inspection = inspect_manifest(EXAMPLES / "org.projectluma.mod.green-dock.mod.json")
        with self.assertRaisesRegex(CatalogError, "duplicate"):
            Catalog([inspection, inspection])

    def test_missing_required_capability_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = example("org.projectluma.prairie.dock-style-api.mod.json")
            value["identity"]["id"] = "org.example.requires-missing"
            value["dependencies"] = []
            value["capabilities"]["requires"] = [
                {
                    "id": "org.example.missing-api",
                    "version": ">=1.0.0 <2.0.0",
                    "ownership": "shared",
                    "reason": "Required for the example.",
                }
            ]
            write_manifest(root, value)
            with self.assertRaisesRegex(ResolutionError, "org.example.missing-api"):
                resolve_mod("org.example.requires-missing", Catalog.from_directory(root), host())

    def test_composition_identity_is_deterministic(self) -> None:
        catalog = Catalog.from_directory(EXAMPLES)
        first = resolve_mod("org.projectluma.mod.green-dock", catalog, host())
        second = resolve_mod("org.projectluma.mod.green-dock", catalog, host())
        self.assertEqual(first.composition_sha256, second.composition_sha256)

    def test_host_capability_provider_must_match_capability_version(self) -> None:
        with self.assertRaisesRegex(ResolutionError, "does not match capability version"):
            host(
                capabilities={"org.example.session.shell": "1.0.0"},
                capability_providers={
                    "org.example.session.shell": {
                        "provider": "org.example.shell",
                        "version": "2.0.0",
                        "ownership": "exclusive",
                    }
                },
            )

    def test_experience_must_declare_active_provider_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = example(
                "pilots/org.kde.plasma.luma-experience.candidate.mod.json"
            )
            value["capabilities"]["replaces"] = []
            write_manifest(root, value)
            with self.assertRaisesRegex(
                ResolutionError, "without declaring it in capabilities.replaces"
            ):
                resolve_mod(
                    "org.kde.plasma.luma-experience",
                    Catalog.from_directory(root),
                    self._kde_host(),
                )

    def test_unidentified_host_provider_cannot_be_displaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = example("org.projectluma.prairie.dock-style-api.mod.json")
            value["identity"]["id"] = "org.example.new-shell"
            value["identity"]["name"] = "New Shell"
            value["capabilities"]["provides"] = [
                {
                    "id": "org.example.session.shell",
                    "version": "1.0.0",
                    "ownership": "exclusive",
                    "reason": "Provides a replacement shell.",
                }
            ]
            write_manifest(root, value)
            with self.assertRaisesRegex(ResolutionError, "not identified by the host"):
                resolve_mod(
                    "org.example.new-shell",
                    Catalog.from_directory(root),
                    host(capabilities={"org.example.session.shell": "1.0.0"}),
                )

    def test_kde_experience_plans_complete_provider_transition(self) -> None:
        plan = resolve_mod(
            "org.kde.plasma.luma-experience",
            self._kde_catalog(),
            self._kde_host(),
        )
        self.assertEqual(plan.activation, "session-restart")
        self.assertEqual(len(plan.provider_transitions), 9)
        self.assertEqual(
            {transition.previous_provider for transition in plan.provider_transitions},
            {"org.projectluma.prairie.experience"},
        )
        self.assertEqual(
            {transition.next_provider for transition in plan.provider_transitions},
            {"org.kde.plasma.luma-experience"},
        )
        serialized = plan.as_dict()["provider_transitions"]
        self.assertEqual(serialized[0]["capability"], "org.luma.portal.desktop")
        self.assertEqual(serialized[-1]["capability"], "org.luma.session.shell")
        self.assertFalse(plan.as_dict()["mutated_host"])

    def test_incomplete_experience_provider_set_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = example(
                "pilots/org.kde.plasma.luma-experience.candidate.mod.json"
            )
            value["capabilities"]["provides"] = [
                item
                for item in value["capabilities"]["provides"]
                if item["id"] != "org.luma.session.power"
            ]
            value["capabilities"]["replaces"] = [
                item
                for item in value["capabilities"]["replaces"]
                if item["id"] != "org.luma.session.power"
            ]
            write_manifest(root, value)
            with self.assertRaisesRegex(ResolutionError, "incomplete experience provider"):
                resolve_mod(
                    "org.kde.plasma.luma-experience",
                    Catalog.from_directory(root),
                    self._kde_host(),
                )

    def test_provider_selection_changes_composition_identity(self) -> None:
        prairie_host = self._kde_host()
        value = json.loads(
            (EXAMPLES / "pilots" / "host-kde-desktop.json").read_text(encoding="utf-8")
        )
        for provider in value["capability_providers"].values():
            provider["provider"] = "org.example.prairie-preview"
        preview_host = HostContext.from_dict(value)
        prairie = resolve_mod(
            "org.kde.plasma.luma-experience", self._kde_catalog(), prairie_host
        )
        preview = resolve_mod(
            "org.kde.plasma.luma-experience", self._kde_catalog(), preview_host
        )
        self.assertNotEqual(prairie.composition_sha256, preview.composition_sha256)

    def test_shared_capability_providers_can_coexist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = example("org.projectluma.prairie.dock-style-api.mod.json")
            value["identity"]["id"] = "org.example.shared-provider"
            value["identity"]["name"] = "Shared Provider"
            value["capabilities"]["provides"] = [
                {
                    "id": "org.example.shared-capability",
                    "version": "1.0.0",
                    "ownership": "shared",
                    "reason": "Adds another compatible shared provider.",
                }
            ]
            write_manifest(root, value)
            plan = resolve_mod(
                "org.example.shared-provider",
                Catalog.from_directory(root),
                host(
                    capabilities={"org.example.shared-capability": "1.0.0"},
                    capability_providers={
                        "org.example.shared-capability": {
                            "provider": "org.example.existing-provider",
                            "version": "1.0.0",
                            "ownership": "shared",
                        }
                    },
                ),
            )
            self.assertEqual(plan.provider_transitions, ())

    def test_host_owned_setting_collision_is_rejected(self) -> None:
        with self.assertRaisesRegex(ResolutionError, "org.luma.shell.dock.appearance"):
            resolve_mod(
                "org.projectluma.mod.green-dock",
                Catalog.from_directory(EXAMPLES),
                host(
                    effect_owners={
                        "settings": {
                            "org.luma.shell.dock.appearance": "org.example.blue-dock"
                        }
                    }
                ),
            )

    def test_conflicting_boot_argument_values_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = example("org.projectluma.prairie.dock-style-api.mod.json")
            value["identity"]["id"] = "org.example.boot-mod"
            value["identity"]["name"] = "Boot Mod"
            value["identity"]["kind"] = "core-system"
            value["compatibility"]["hardware"] = ["dmi:Example:Machine"]
            value["compatibility"]["kernel_releases"] = ["6.15.10-200.fc44.x86_64"]
            value["effects"]["boot_arguments"] = ["example.mode=new"]
            write_manifest(root, value)
            with self.assertRaisesRegex(ResolutionError, "example.mode=old"):
                resolve_mod(
                    "org.example.boot-mod",
                    Catalog.from_directory(root),
                    host(
                        hardware=["dmi:Example:Machine"],
                        kernel_release="6.15.10-200.fc44.x86_64",
                        effect_owners={
                            "boot_arguments": {
                                "example.mode=old": "org.example.old-boot-mod"
                            }
                        }
                    ),
                )

    def test_parent_file_ownership_conflicts_with_child_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = example("org.projectluma.prairie.dock-style-api.mod.json")
            value["identity"]["id"] = "org.example.file-mod"
            value["effects"]["files"] = ["/usr/share/luma/themes/prairie.css"]
            write_manifest(root, value)
            with self.assertRaisesRegex(ResolutionError, "overlaps"):
                resolve_mod(
                    "org.example.file-mod",
                    Catalog.from_directory(root),
                    host(
                        effect_owners={
                            "files": {"/usr/share/luma": "org.example.base-theme"}
                        }
                    ),
                )

    def test_sibling_file_paths_do_not_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = example("org.projectluma.prairie.dock-style-api.mod.json")
            value["identity"]["id"] = "org.example.file-mod"
            value["effects"]["files"] = ["/usr/share/luma/themes/prairie.css"]
            write_manifest(root, value)
            plan = resolve_mod(
                "org.example.file-mod",
                Catalog.from_directory(root),
                host(
                    effect_owners={
                        "files": {"/usr/share/luma/icons/prairie.svg": "org.example.icons"}
                    }
                ),
            )
            self.assertEqual(plan.target_id, "org.example.file-mod")

    def test_parent_configuration_domain_conflicts_with_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = example("org.projectluma.prairie.dock-style-api.mod.json")
            value["identity"]["id"] = "org.example.config-mod"
            value["effects"]["configuration_domains"] = ["org.luma.shell.dock"]
            write_manifest(root, value)
            with self.assertRaisesRegex(ResolutionError, "org.luma.shell"):
                resolve_mod(
                    "org.example.config-mod",
                    Catalog.from_directory(root),
                    host(
                        effect_owners={
                            "configuration_domains": {
                                "org.luma.shell": "org.example.shell"
                            }
                        }
                    ),
                )

    def test_device_effect_has_one_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = example("org.projectluma.prairie.dock-style-api.mod.json")
            value["identity"]["id"] = "org.example.device-mod"
            value["identity"]["kind"] = "hardware"
            value["effects"]["devices"] = ["pci:v00008086d00009A49"]
            write_manifest(root, value)
            with self.assertRaisesRegex(ResolutionError, "org.example.kernel"):
                resolve_mod(
                    "org.example.device-mod",
                    Catalog.from_directory(root),
                    host(
                        effect_owners={
                            "devices": {
                                "pci:v00008086d00009A49": "org.example.kernel"
                            }
                        }
                    ),
                )


class ImageCompositionTests(unittest.TestCase):
    def _plan(self):
        host_value = json.loads(
            (EXAMPLES / "pilots" / "host-kde-desktop.json").read_text(encoding="utf-8")
        )
        host_value["install_mode"] = "image-compose"
        image_host = HostContext.from_dict(host_value)
        plan = resolve_mod(
            "org.kde.plasma.luma-experience",
            Catalog.from_directory(EXAMPLES / "pilots"),
            image_host,
        )
        return image_host, plan

    def test_review_lock_is_deterministic_and_cannot_install(self) -> None:
        image_host, plan = self._plan()
        first = build_image_composition_lock(plan, image_host)
        second = build_image_composition_lock(plan, image_host)
        self.assertEqual(first.lock_sha256, second.lock_sha256)
        self.assertFalse(first.install_authorized)
        with self.assertRaisesRegex(TransactionError, "review-only"):
            require_install_authorized(first)
        self.assertEqual(ImageCompositionLock.from_dict(first.as_dict()), first)

    def test_accepted_snapshot_and_verified_publisher_authorize_exact_lock(self) -> None:
        image_host, plan = self._plan()
        inspection = plan.mods[0].inspection
        verification = VerificationResult(
            level="luma-verified",
            label="Luma Verified",
            verified=True,
            reason="test fixture",
            publisher_id=inspection.mod.identity.publisher.id,
        )
        receipt = {
            "schema": "org.luma.mod-catalog-publication-receipt/v0.1",
            "repository": "https://mods.projectluma.invalid/staging",
            "snapshot_id": f"sha256:{'a' * 64}",
            "policy_sha256": "b" * 64,
            "request_sha256": "c" * 64,
            "metadata_versions": {"root": 1, "targets": 1, "snapshot": 1, "timestamp": 1},
            "targets": [{
                "path": "mods/kde.mod.json",
                "length": len(inspection.canonical_json),
                "sha256": inspection.source_sha256,
                "role": "targets",
            }],
            "verification": "python-tuf-full-refresh-and-target-verification",
        }
        lock = build_image_composition_lock(
            plan,
            image_host,
            {inspection.mod.identity.id: verification},
            receipt,
        )
        self.assertTrue(lock.install_authorized)
        require_install_authorized(lock)
        serialized = ImageCompositionLock.from_dict(lock.as_dict())
        self.assertFalse(serialized.install_authorized)
        with self.assertRaisesRegex(TransactionError, "review-only"):
            require_install_authorized(serialized)
        tampered = lock.as_dict()
        tampered["base"]["hardware"] = ["dmi:Other:Machine"]
        with self.assertRaisesRegex(TransactionError, "does not match"):
            ImageCompositionLock.from_dict(tampered)

    def test_lock_refuses_a_different_host_than_the_resolved_plan(self) -> None:
        image_host, plan = self._plan()
        different = HostContext.from_dict({
            **json.loads(
                (EXAMPLES / "pilots" / "host-kde-desktop.json").read_text(
                    encoding="utf-8"
                )
            ),
            "install_mode": "image-compose",
            "hardware": ["dmi:Other:Machine"],
        })
        self.assertNotEqual(image_host.hardware, different.hardware)
        with self.assertRaisesRegex(TransactionError, "does not match"):
            build_image_composition_lock(plan, different)


class TrustTests(unittest.TestCase):
    def _policy(self, root: Path) -> TrustPolicy:
        value = {
            "schema": "org.luma.mod-trust-policy/v0.1",
            "publishers": [
                {
                    "id": "org.projectluma",
                    "name": "Project Luma",
                    "level": "luma-core",
                    "identity": "https://github.com/SimplyLuma/luma/.github/workflows/release-mod.yml@refs/heads/main",
                    "issuer": "https://token.actions.githubusercontent.com",
                }
            ],
        }
        path = root / "trust-policy.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return TrustPolicy.from_file(path)

    def _signed_inspection(self, root: Path):
        value = example()
        unsigned_path = write_manifest(root, value, "unsigned.mod.json")
        unsigned = inspect_manifest(unsigned_path)
        bundle = b'{"test-only":"sigstore bundle fixture"}'
        bundle_path = root / "test.sigstore.json"
        bundle_path.write_bytes(bundle)
        value["signatures"] = [
            {
                "signer": "org.projectluma",
                "algorithm": "sigstore-bundle-v0.3",
                "manifest_digest": f"sha256:{unsigned.signing_sha256}",
                "bundle": bundle_path.name,
                "bundle_digest": f"sha256:{hashlib.sha256(bundle).hexdigest()}",
            }
        ]
        return inspect_manifest(write_manifest(root, value, "signed.mod.json")), bundle_path

    def test_unsigned_mod_is_explicitly_local_unverified(self) -> None:
        result = verify_inspection(
            inspect_manifest(EXAMPLES / "org.projectluma.mod.green-dock.mod.json"),
            TrustPolicy.from_file(EXAMPLES / "trust-policy.json"),
        )
        self.assertFalse(result.verified)
        self.assertEqual(result.level, "local-unverified")

    def test_verified_bundle_uses_offline_exact_identity_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inspection, _bundle = self._signed_inspection(root)
            commands: list[list[str]] = []

            def accept(command, **kwargs):
                commands.append(list(command))
                return subprocess.CompletedProcess(command, 0, stdout="OK", stderr="")

            result = verify_inspection(
                inspection,
                self._policy(root),
                sigstore_executable="sigstore-test",
                runner=accept,
            )
            self.assertTrue(result.verified)
            self.assertEqual(result.level, "luma-core")
            self.assertIn("--offline", commands[0])
            self.assertIn(f"sha256:{inspection.signing_sha256}", commands[0])
            self.assertIn("https://token.actions.githubusercontent.com", commands[0])

    def test_wrong_signing_digest_fails_before_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inspection, _bundle = self._signed_inspection(root)
            value = json.loads(inspection.path.read_text(encoding="utf-8"))
            value["signatures"][0]["manifest_digest"] = f"sha256:{'f' * 64}"
            wrong = inspect_manifest(write_manifest(root, value, "wrong.mod.json"))
            with self.assertRaisesRegex(TrustError, "different manifest"):
                verify_inspection(wrong, self._policy(root))

    def test_bundle_digest_mismatch_fails_before_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inspection, bundle = self._signed_inspection(root)
            bundle.write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(TrustError, "bundle digest"):
                verify_inspection(inspection, self._policy(root))

    def test_bundle_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inspection, bundle = self._signed_inspection(root)
            real = root / "real.sigstore.json"
            bundle.rename(real)
            bundle.symlink_to(real)
            with self.assertRaisesRegex(TrustError, "safely"):
                verify_inspection(inspection, self._policy(root))

    def test_sigstore_rejection_is_not_downgraded_to_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inspection, _bundle = self._signed_inspection(root)

            def reject(command, **kwargs):
                return subprocess.CompletedProcess(
                    command, 1, stdout="", stderr="certificate identity mismatch"
                )

            with self.assertRaisesRegex(TrustError, "identity mismatch"):
                verify_inspection(inspection, self._policy(root), runner=reject)


class CliTests(unittest.TestCase):
    def test_plan_output_is_explicit_and_non_mutating(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(MODS_SOURCE)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "luma_mods.cli",
                "plan",
                "org.projectluma.mod.green-dock",
                "--catalog",
                str(EXAMPLES),
                "--host",
                str(EXAMPLES / "host-desktop.json"),
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertFalse(value["mutated_host"])
        self.assertEqual(len(value["dependencies"]), 1)
        self.assertIn("bounded and reversible", value["dependencies"][0]["reason"])
        self.assertEqual(
            value["dependencies"][0]["verification"]["level"],
            "local-unverified",
        )

    def test_cli_runs_complete_confirmed_preference_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = os.environ.copy()
            environment.update({
                "PYTHONPATH": str(MODS_SOURCE),
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CONFIG_HOME": str(root / "config"),
            })
            common = [
                sys.executable,
                "-m",
                "luma_mods.cli",
                "install",
                "org.projectluma.mod.green-dock",
                "--catalog",
                str(BUILTIN_CATALOG / "manifests"),
                "--host",
                str(EXAMPLES / "host-desktop.json"),
                "--profile",
                str(
                    BUILTIN_CATALOG
                    / "profiles/org.projectluma.mod.green-dock.profile.json"
                ),
            ]
            refused = subprocess.run(
                common,
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(refused.returncode, 2)
            self.assertIn("Local / Unverified confirmation required", refused.stderr)

            installed = subprocess.run(
                [*common, "--confirm-unverified"],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertIn("org.projectluma.mod.green-dock", installed.stdout)

            for command in ("disable", "enable", "remove"):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "luma_mods.cli",
                        command,
                        "org.projectluma.mod.green-dock",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            preferences = root / "config/luma/preferences.json"
            self.assertEqual(json.loads(preferences.read_text(encoding="utf-8")), {})


class StateStoreTests(unittest.TestCase):
    def test_initial_state_is_empty_and_update_is_compare_and_swap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state")
            self.assertEqual(store.read(), empty_state())
            updated = store.update(0, lambda value: value["audit"].append({
                "transaction_id": "state-only-test",
                "operation": "enable",
                "target_id": "org.example.test",
                "result": "committed",
                "at": "2026-08-22T00:00:00+00:00",
                "from_generation": 0,
                "to_generation": 1,
            }))
            self.assertEqual(updated["generation"], 1)
            with self.assertRaisesRegex(StateError, "stale"):
                store.update(0, lambda _value: None)

    def test_state_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            state_root.mkdir()
            outside = root / "outside.json"
            outside.write_text(json.dumps(empty_state()), encoding="utf-8")
            (state_root / "state.json").symlink_to(outside)
            with self.assertRaisesRegex(StateError, "safely"):
                StateStore(state_root).read()


class InventoryTests(unittest.TestCase):
    def test_inventory_is_read_only_and_labels_observations_unmanaged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "proc"
            proc.mkdir()
            (proc / "cmdline").write_text("quiet example.mode=test\n", encoding="utf-8")
            (proc / "modules").write_text("surface_aggregator 1 0 - Live 0x0\n", encoding="utf-8")
            os_release = root / "os-release"
            os_release.write_text('ID=luma\nVERSION_ID="0.1"\n', encoding="utf-8")

            def runner(command, _timeout):
                key = tuple(command)
                if key == ("rpm-ostree", "status", "--json"):
                    return ProbeResult(key, 0, json.dumps({"deployments": [{
                        "checksum": "abc123", "booted": True,
                        "requested-packages": ["example-rpm"],
                    }]}), "")
                if key == ("gnome-extensions", "list", "--enabled"):
                    return ProbeResult(key, 0, "example@extension\n", "")
                return ProbeResult(key, 0, "M /etc/example.conf\n", "")

            value = collect_inventory(
                proc_root=proc, os_release_path=os_release, runner=runner
            )
            self.assertFalse(value["mutated_host"])
            identities = {(item["kind"], item["identity"]) for item in value["observations"]}
            self.assertIn(("layered-package", "example-rpm"), identities)
            self.assertIn(("gnome-extension", "example@extension"), identities)
            self.assertIn(("boot-argument", "example.mode=test"), identities)
            self.assertTrue(all(
                item["provenance"] == "unmanaged-or-unknown"
                for item in value["observations"]
            ))

    def test_probe_failures_are_reported_not_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "proc"
            proc.mkdir()

            def unavailable(command, _timeout):
                return ProbeResult(tuple(command), None, "", "", "not-installed")

            value = collect_inventory(
                proc_root=proc,
                os_release_path=root / "missing",
                runner=unavailable,
            )
            self.assertEqual(len(value["probe_failures"]), 3)


class PreferenceLifecycleTests(unittest.TestCase):
    def _profile(self, root: Path) -> PreferenceProfile:
        path = root / "green.profile.json"
        path.write_text(json.dumps({
            "schema": "org.luma.mod-profile/v0.1",
            "mod_id": "org.projectluma.mod.green-dock",
            "version": "1.0.0",
            "values": {
                "org.luma.shell.dock.appearance": {"surface": "green-glow"}
            },
        }), encoding="utf-8")
        return PreferenceProfile.from_file(path)

    def _plan(self):
        return resolve_mod(
            "org.projectluma.mod.green-dock",
            Catalog.from_directory(EXAMPLES),
            host(),
        )

    def _authorization(self) -> Authorization:
        return Authorization(
            trust_levels={},
            confirmed_unverified=frozenset({
                "org.projectluma.mod.green-dock",
                "org.projectluma.prairie.dock-style-api",
            }),
        )

    def test_install_and_remove_restore_exact_prior_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preference_path = root / "preferences.json"
            original = {"org.luma.shell.dock.appearance": {"surface": "prairie-default"}}
            preference_path.write_text(json.dumps(original), encoding="utf-8")
            lifecycle = PreferenceLifecycle(
                StateStore(root / "state"),
                FilePreferenceBackend(
                    preference_path, {"org.luma.shell.dock.appearance"}
                ),
            )
            installed = lifecycle.install(
                self._plan(), self._profile(root), self._authorization(), expected_generation=0
            )
            self.assertEqual(installed["generation"], 1)
            self.assertIn("org.projectluma.mod.green-dock", installed["installed"])
            applied = json.loads(preference_path.read_text(encoding="utf-8"))
            self.assertEqual(applied["org.luma.shell.dock.appearance"]["surface"], "green-glow")

            disabled = lifecycle.set_enabled(
                "org.projectluma.mod.green-dock", False, expected_generation=1
            )
            self.assertFalse(disabled["installed"]["org.projectluma.mod.green-dock"]["enabled"])
            self.assertEqual(json.loads(preference_path.read_text(encoding="utf-8")), original)
            enabled = lifecycle.set_enabled(
                "org.projectluma.mod.green-dock", True, expected_generation=2
            )
            self.assertTrue(enabled["installed"]["org.projectluma.mod.green-dock"]["enabled"])
            self.assertEqual(
                json.loads(preference_path.read_text(encoding="utf-8"))[
                    "org.luma.shell.dock.appearance"
                ]["surface"],
                "green-glow",
            )

            removed = lifecycle.remove(
                "org.projectluma.mod.green-dock", expected_generation=3
            )
            self.assertEqual(removed["generation"], 4)
            self.assertNotIn("org.projectluma.mod.green-dock", removed["installed"])
            self.assertEqual(json.loads(preference_path.read_text(encoding="utf-8")), original)

    def test_dependency_cannot_be_removed_while_referenced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lifecycle = PreferenceLifecycle(
                StateStore(root / "state"),
                FilePreferenceBackend(
                    root / "preferences.json", {"org.luma.shell.dock.appearance"}
                ),
            )
            lifecycle.install(
                self._plan(), self._profile(root), self._authorization(), expected_generation=0
            )
            with self.assertRaisesRegex(TransactionError, "required by"):
                lifecycle.remove(
                    "org.projectluma.prairie.dock-style-api", expected_generation=1
                )

    def test_update_preserves_original_restoration_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preference_path = root / "preferences.json"
            original = {"org.luma.shell.dock.appearance": {"surface": "prairie-default"}}
            preference_path.write_text(json.dumps(original), encoding="utf-8")
            lifecycle = PreferenceLifecycle(
                StateStore(root / "state"),
                FilePreferenceBackend(preference_path, {"org.luma.shell.dock.appearance"}),
            )
            lifecycle.install(
                self._plan(), self._profile(root), self._authorization(), expected_generation=0
            )
            catalog_root = root / "catalog"
            catalog_root.mkdir()
            dependency = example("org.projectluma.prairie.dock-style-api.mod.json")
            write_manifest(catalog_root, dependency, "dependency.mod.json")
            updated_manifest = example()
            updated_manifest["identity"]["version"] = "1.1.0"
            write_manifest(catalog_root, updated_manifest, "updated.mod.json")
            updated_plan = resolve_mod(
                "org.projectluma.mod.green-dock",
                Catalog.from_directory(catalog_root),
                host(installed_mods={"org.projectluma.prairie.dock-style-api": "1.0.0"}),
            )
            updated_profile_path = root / "updated.profile.json"
            updated_profile_path.write_text(json.dumps({
                "schema": "org.luma.mod-profile/v0.1",
                "mod_id": "org.projectluma.mod.green-dock",
                "version": "1.1.0",
                "values": {
                    "org.luma.shell.dock.appearance": {"surface": "green-glow"}
                },
            }), encoding="utf-8")
            updated = lifecycle.update(
                updated_plan,
                PreferenceProfile.from_file(updated_profile_path),
                Authorization({}, frozenset({"org.projectluma.mod.green-dock"})),
                expected_generation=1,
            )
            self.assertEqual(
                updated["installed"]["org.projectluma.mod.green-dock"]["version"], "1.1.0"
            )
            lifecycle.remove("org.projectluma.mod.green-dock", expected_generation=2)
            self.assertEqual(json.loads(preference_path.read_text(encoding="utf-8")), original)

    def test_unreviewed_local_mod_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lifecycle = PreferenceLifecycle(
                StateStore(root / "state"),
                FilePreferenceBackend(
                    root / "preferences.json", {"org.luma.shell.dock.appearance"}
                ),
            )
            with self.assertRaisesRegex(TransactionError, "not been authorized"):
                lifecycle.install(
                    self._plan(), self._profile(root), Authorization({}), expected_generation=0
                )

    def test_backend_failure_recovers_state_and_preferences(self) -> None:
        class FailingBackend(FilePreferenceBackend):
            def apply(self, values):
                super().apply(values)
                raise RuntimeError("simulated crash after write")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preference_path = root / "preferences.json"
            original = {"org.luma.shell.dock.appearance": {"surface": "prairie-default"}}
            preference_path.write_text(json.dumps(original), encoding="utf-8")
            store = StateStore(root / "state")
            lifecycle = PreferenceLifecycle(
                store,
                FailingBackend(preference_path, {"org.luma.shell.dock.appearance"}),
            )
            with self.assertRaisesRegex(TransactionError, "backend failed"):
                lifecycle.install(
                    self._plan(), self._profile(root), self._authorization(), expected_generation=0
                )
            recovered = store.read()
            self.assertIsNone(recovered["pending_transaction"])
            self.assertEqual(recovered["generation"], 1)
            self.assertEqual(recovered["installed"], {})
            self.assertEqual(recovered["audit"][-1]["result"], "recovered")
            self.assertEqual(json.loads(preference_path.read_text(encoding="utf-8")), original)

    def test_session_recovery_repairs_hard_interruption(self) -> None:
        class InterruptedBackend(FilePreferenceBackend):
            def apply(self, values):
                super().apply(values)
                raise KeyboardInterrupt("simulated process death")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preference_path = root / "preferences.json"
            original = {"org.luma.shell.dock.appearance": {"surface": "prairie-default"}}
            preference_path.write_text(json.dumps(original), encoding="utf-8")
            state_root = root / "state"
            lifecycle = PreferenceLifecycle(
                StateStore(state_root),
                InterruptedBackend(preference_path, {"org.luma.shell.dock.appearance"}),
            )
            with self.assertRaises(KeyboardInterrupt):
                lifecycle.install(
                    self._plan(), self._profile(root), self._authorization(), expected_generation=0
                )
            self.assertIsNotNone(StateStore(state_root).read()["pending_transaction"])
            self.assertTrue(recover_pending(
                state_root=state_root,
                preference_file=preference_path,
                supported_domains={"org.luma.shell.dock.appearance"},
            ))
            recovered = StateStore(state_root).read()
            self.assertIsNone(recovered["pending_transaction"])
            self.assertEqual(recovered["audit"][-1]["result"], "recovered")
            self.assertEqual(json.loads(preference_path.read_text(encoding="utf-8")), original)

    def test_profile_must_match_declared_settings_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "wrong.profile.json"
            path.write_text(json.dumps({
                "schema": "org.luma.mod-profile/v0.1",
                "mod_id": "org.projectluma.mod.green-dock",
                "version": "1.0.0",
                "values": {"org.luma.shell.dock.unclaimed": True},
            }), encoding="utf-8")
            lifecycle = PreferenceLifecycle(
                StateStore(root / "state"),
                FilePreferenceBackend(root / "preferences.json", {
                    "org.luma.shell.dock.appearance", "org.luma.shell.dock.unclaimed"
                }),
            )
            with self.assertRaisesRegex(TransactionError, "exactly match"):
                lifecycle.install(
                    self._plan(), PreferenceProfile.from_file(path),
                    self._authorization(), expected_generation=0,
                )

    def test_registered_preference_rejects_stylesheet_escape_hatches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = FilePreferenceBackend(
                root / "preferences.json", {"org.luma.shell.dock.appearance"}
            )
            with self.assertRaisesRegex(TransactionError, "surface only"):
                backend.apply({
                    "org.luma.shell.dock.appearance": {
                        "surface": "green-glow",
                        "css": "#dash { background: red; }",
                    }
                })

    def test_registered_preference_rejects_unbounded_dock_properties(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = FilePreferenceBackend(
                root / "preferences.json", {"org.luma.shell.dock.appearance"}
            )
            with self.assertRaisesRegex(TransactionError, "surface only"):
                backend.apply({
                    "org.luma.shell.dock.appearance": {
                        "surface": "green-glow",
                        "intensity": 0.9,
                    }
                })


class CatalogClientConfigTests(unittest.TestCase):
    def test_accepts_only_absolute_trust_files_and_clean_https_bases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "catalog-client.json"
            path.write_text(json.dumps({
                "schema": "org.luma.mod-catalog-client/v0.1",
                "bootstrap_root": str(root / "root.json"),
                "publisher_policy": str(root / "publishers.json"),
                "metadata_url": "https://mods.projectluma.test/metadata/",
                "targets_url": "https://mods.projectluma.test/targets/",
            }), encoding="utf-8")
            config = CatalogClientConfig.from_file(path)
            self.assertEqual(config.bootstrap_root, root / "root.json")

            value = json.loads(path.read_text(encoding="utf-8"))
            value["metadata_url"] = "https://user:secret@mods.projectluma.test/metadata/"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(CatalogUpdateError, "without credentials"):
                CatalogClientConfig.from_file(path)

            value["metadata_url"] = "https://mods.projectluma.test/metadata/"
            value["bootstrap_root"] = "root.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(CatalogUpdateError, "absolute file path"):
                CatalogClientConfig.from_file(path)

            value["bootstrap_root"] = str(root / "root.json")
            value["metadata_url"] = "https://[broken/metadata/"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(CatalogUpdateError, "valid HTTPS"):
                CatalogClientConfig.from_file(path)

    def test_rejects_duplicate_configuration_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog-client.json"
            path.write_text(
                '{"schema":"org.luma.mod-catalog-client/v0.1",'
                '"schema":"org.luma.mod-catalog-client/v0.1",'
                '"bootstrap_root":"/root.json",'
                '"publisher_policy":"/publishers.json",'
                '"metadata_url":"https://mods.test/metadata/",'
                '"targets_url":"https://mods.test/targets/"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CatalogUpdateError, "duplicate field"):
                CatalogClientConfig.from_file(path)


class CatalogUpdateTests(unittest.TestCase):
    def _client(
        self,
        root: Path,
        index: dict,
        manifest: bytes | None = None,
        extra_targets: dict[str, bytes] | None = None,
    ):
        bootstrap = root / "root.json"
        bootstrap.write_text('{"signed":"test bootstrap supplied to mock"}', encoding="utf-8")
        manifest_target = index["entries"][0]["manifest"]
        target_payloads = {
            "catalog/index.json": json.dumps(index).encode("utf-8"),
            manifest_target: manifest or (
                EXAMPLES / "org.projectluma.mod.green-dock.mod.json"
            ).read_bytes(),
        }
        target_payloads.update(extra_targets or {})

        class Info:
            def __init__(self, path, data):
                self.path = path
                self.length = len(data)

        class FakeUpdater:
            def __init__(self, **kwargs):
                self.target_dir = Path(kwargs["target_dir"])
                self.bootstrap = kwargs["bootstrap"]
                self.refreshed = False

            def refresh(self):
                self.refreshed = True

            def get_targetinfo(self, target):
                data = target_payloads.get(target)
                return Info(target, data) if data is not None else None

            def download_target(self, info):
                destination = self.target_dir / info.path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(target_payloads[info.path])
                return str(destination)

        holder = {}

        def factory(**kwargs):
            holder["updater"] = FakeUpdater(**kwargs)
            return holder["updater"]

        client = TufCatalogClient(
            bootstrap_root=bootstrap,
            cache_root=root / "cache",
            metadata_base_url="https://mods.example/metadata/",
            target_base_url="https://mods.example/targets/",
            updater_factory=factory,
        )
        return client, holder

    def _index(self):
        entries = [{
            "id": "org.projectluma.mod.green-dock",
            "version": "1.0.0",
            "manifest": (
                "mods/community/org.projectluma.mod.green-dock/1.0.0/"
                "manifest.mod.json"
            ),
            "signature_bundles": [],
            "evidence": [],
            "payloads": [],
            "trust_role": "community",
        }]
        return {
            "schema": "org.luma.mod-catalog/v0.1",
            "snapshot_id": "sha256:" + hashlib.sha256(
                json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "entries": entries,
        }

    def test_tuf_refresh_precedes_strict_manifest_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client, holder = self._client(Path(directory), self._index())
            snapshot = client.refresh()
            self.assertTrue(holder["updater"].refreshed)
            inspection = client.inspect_entry(
                snapshot, "org.projectluma.mod.green-dock"
            )
            self.assertEqual(inspection.mod.identity.version, "1.0.0")

    def test_catalog_rejects_target_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = self._index()
            index["entries"][0]["manifest"] = "../outside.mod.json"
            client, _holder = self._client(Path(directory), index)
            with self.assertRaisesRegex(CatalogUpdateError, "target path"):
                client.refresh()

    def test_catalog_recomputes_snapshot_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = self._index()
            index["snapshot_id"] = f"sha256:{'a' * 64}"
            client, _holder = self._client(Path(directory), index)
            with self.assertRaisesRegex(CatalogUpdateError, "content-addressed"):
                client.refresh()

    def test_catalog_enforces_delegated_role_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = self._index()
            index["entries"][0]["manifest"] = (
                "mods/luma-core/org.projectluma.mod.green-dock/1.0.0/manifest.mod.json"
            )
            index["snapshot_id"] = "sha256:" + hashlib.sha256(
                json.dumps(index["entries"], sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            client, _holder = self._client(Path(directory), index)
            with self.assertRaisesRegex(CatalogUpdateError, "exact community Mod namespace"):
                client.refresh()

    def test_reconstructed_catalog_snapshot_cannot_authorize_a_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client, _holder = self._client(root, self._index())
            refreshed = client.refresh()
            reconstructed = CatalogSnapshot(
                refreshed.snapshot_id,
                refreshed.entries,
                refreshed.index_path,
            )
            with self.assertRaisesRegex(CatalogUpdateError, "live TUF refresh"):
                client.resolve_plan(
                    reconstructed,
                    "org.projectluma.mod.green-dock",
                    host(),
                    None,
                )

    def test_snapshot_is_bound_to_the_client_that_refreshed_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "first").mkdir()
            (root / "second").mkdir()
            first, _first_holder = self._client(root / "first", self._index())
            second, _second_holder = self._client(root / "second", self._index())
            snapshot = first.refresh()
            with self.assertRaisesRegex(CatalogUpdateError, "this client's live TUF"):
                second.inspect_manifest_entry(
                    snapshot, "org.projectluma.mod.green-dock"
                )

    def test_catalog_and_manifest_identity_must_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = self._index()
            index["entries"][0]["version"] = "1.0.1"
            index["entries"][0]["manifest"] = (
                "mods/community/org.projectluma.mod.green-dock/1.0.1/"
                "manifest.mod.json"
            )
            index["snapshot_id"] = "sha256:" + hashlib.sha256(
                json.dumps(index["entries"], sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            client, _holder = self._client(Path(directory), index)
            with self.assertRaisesRegex(CatalogUpdateError, "identity does not match"):
                client.inspect_entry(client.refresh(), "org.projectluma.mod.green-dock")

    def test_catalog_requires_https_even_with_mock_updater(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bootstrap = root / "root.json"
            bootstrap.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(CatalogUpdateError, "HTTPS"):
                TufCatalogClient(
                    bootstrap_root=bootstrap,
                    cache_root=root / "cache",
                    metadata_base_url="http://mods.example/metadata/",
                    target_base_url="https://mods.example/targets/",
                    updater_factory=lambda **_kwargs: object(),
                )

    def test_live_tuf_plan_authorizes_reversible_verified_preference_mod(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "green.profile.json"
            profile_path.write_text(json.dumps({
                "schema": "org.luma.mod-profile/v0.1",
                "mod_id": "org.projectluma.mod.green-dock",
                "version": "1.0.0",
                "values": {
                    "org.luma.shell.dock.appearance": {"surface": "green-glow"}
                },
            }), encoding="utf-8")
            manifest = example()
            manifest["dependencies"] = []
            manifest["capabilities"]["requires"] = []
            manifest["evidence"] = {
                "source": "repo:test-profile",
                "source_digest": (
                    "sha256:" + hashlib.sha256(profile_path.read_bytes()).hexdigest()
                ),
            }
            unsigned = inspect_manifest(write_manifest(root, manifest, "unsigned.mod.json"))
            bundle = b'{"test-only":"catalog Sigstore fixture"}'
            bundle_digest = hashlib.sha256(bundle).hexdigest()
            manifest["signatures"] = [{
                "signer": "org.projectluma",
                "algorithm": "sigstore-bundle-v0.3",
                "manifest_digest": f"sha256:{unsigned.signing_sha256}",
                "bundle": "publisher.sigstore.json",
                "bundle_digest": f"sha256:{bundle_digest}",
            }]
            manifest_payload = json.dumps(manifest).encode("utf-8")
            entry = {
                "id": "org.projectluma.mod.green-dock",
                "version": "1.0.0",
                "manifest": (
                    "mods/luma-core/org.projectluma.mod.green-dock/1.0.0/"
                    "manifest.mod.json"
                ),
                "signature_bundles": [
                    "mods/luma-core/org.projectluma.mod.green-dock/1.0.0/"
                    "publisher.sigstore.json"
                ],
                "evidence": [],
                "payloads": [],
                "trust_role": "luma-core",
            }
            index = {
                "schema": "org.luma.mod-catalog/v0.1",
                "snapshot_id": "sha256:" + hashlib.sha256(
                    json.dumps([entry], sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
                "entries": [entry],
            }
            client, _holder = self._client(
                root,
                index,
                manifest_payload,
                {entry["signature_bundles"][0]: bundle},
            )
            policy_path = root / "trust-policy.json"
            policy_path.write_text(json.dumps({
                "schema": "org.luma.mod-trust-policy/v0.1",
                "publishers": [{
                    "id": "org.projectluma",
                    "name": "Project Luma",
                    "level": "luma-core",
                    "identity": "https://example.test/luma-release",
                    "issuer": "https://issuer.example.test",
                }],
            }), encoding="utf-8")

            def accept_signature(command, **_kwargs):
                return subprocess.CompletedProcess(command, 0, stdout="OK", stderr="")

            trusted = client.resolve_plan(
                client.refresh(),
                "org.projectluma.mod.green-dock",
                host(),
                TrustPolicy.from_file(policy_path),
                runner=accept_signature,
            )
            authorization = trusted.authorization()
            preference_path = root / "preferences.json"
            original = {"org.luma.shell.dock.appearance": {"surface": "prairie-default"}}
            preference_path.write_text(json.dumps(original), encoding="utf-8")
            lifecycle = PreferenceLifecycle(
                StateStore(root / "state"),
                FilePreferenceBackend(
                    preference_path, {"org.luma.shell.dock.appearance"}
                ),
            )
            installed = lifecycle.install(
                trusted.plan,
                PreferenceProfile.from_file(profile_path),
                authorization,
                expected_generation=0,
            )
            self.assertEqual(
                installed["installed"]["org.projectluma.mod.green-dock"]["trust_level"],
                "luma-core",
            )
            lifecycle.remove("org.projectluma.mod.green-dock", expected_generation=1)
            self.assertEqual(json.loads(preference_path.read_text()), original)

            tampered_profile = root / "tampered.profile.json"
            tampered_profile.write_text(
                profile_path.read_text().replace("green-glow", "prairie-default"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(TransactionError, "profile bytes"):
                lifecycle.install(
                    trusted.plan,
                    PreferenceProfile.from_file(tampered_profile),
                    authorization,
                    expected_generation=2,
                )

    def test_tuf_catalog_acquires_digest_bound_preference_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = json.dumps({
                "schema": "org.luma.mod-profile/v0.1",
                "mod_id": "org.projectluma.mod.green-dock",
                "version": "1.0.0",
                "values": {
                    "org.luma.shell.dock.appearance": {"surface": "green-glow"}
                },
            }).encode("utf-8")
            digest = hashlib.sha256(profile).hexdigest()
            manifest = example()
            manifest["dependencies"] = []
            manifest["capabilities"]["requires"] = []
            manifest["evidence"] = {
                "source": "repo:test-profile",
                "source_digest": f"sha256:{digest}",
            }
            entry = {
                "id": "org.projectluma.mod.green-dock",
                "version": "1.0.0",
                "manifest": (
                    "mods/community/org.projectluma.mod.green-dock/1.0.0/"
                    "manifest.mod.json"
                ),
                "signature_bundles": [],
                "evidence": [f"evidence/community/sha256/{digest}"],
                "payloads": [],
                "trust_role": "community",
            }
            index = {
                "schema": "org.luma.mod-catalog/v0.1",
                "snapshot_id": "sha256:" + hashlib.sha256(
                    json.dumps([entry], sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "entries": [entry],
            }
            client, _holder = self._client(
                root,
                index,
                json.dumps(manifest).encode("utf-8"),
                {entry["evidence"][0]: profile},
            )
            snapshot = client.refresh()
            acquired = client.acquire_preference_profile(
                snapshot, "org.projectluma.mod.green-dock"
            )
            self.assertEqual(acquired.source_sha256, digest)
            self.assertEqual(acquired.values["org.luma.shell.dock.appearance"]["surface"], "green-glow")

            runtime = TrustedCatalogRuntime(client, None)
            reviewed = runtime.prepare_transaction(
                "org.projectluma.mod.green-dock", host()
            )
            repeated = runtime.prepare_transaction(
                "org.projectluma.mod.green-dock",
                host(),
                reviewed_composition_sha256=reviewed.trusted.plan.composition_sha256,
                reviewed_profile_sha256=reviewed.profile.source_sha256,
            )
            self.assertEqual(
                repeated.trusted.snapshot_id, reviewed.trusted.snapshot_id
            )

    def test_catalog_runtime_refuses_changed_post_review_composition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = json.dumps({
                "schema": "org.luma.mod-profile/v0.1",
                "mod_id": "org.projectluma.mod.green-dock",
                "version": "1.0.0",
                "values": {
                    "org.luma.shell.dock.appearance": {"surface": "green-glow"}
                },
            }).encode("utf-8")
            digest = hashlib.sha256(profile).hexdigest()
            manifest = example()
            manifest["dependencies"] = []
            manifest["capabilities"]["requires"] = []
            manifest["evidence"] = {
                "source": "repo:test-profile",
                "source_digest": f"sha256:{digest}",
            }
            entry = {
                "id": "org.projectluma.mod.green-dock",
                "version": "1.0.0",
                "manifest": "mods/community/org.projectluma.mod.green-dock/1.0.0/manifest.mod.json",
                "signature_bundles": [],
                "evidence": [f"evidence/community/sha256/{digest}"],
                "payloads": [],
                "trust_role": "community",
            }
            index = {
                "schema": "org.luma.mod-catalog/v0.1",
                "snapshot_id": "sha256:" + hashlib.sha256(
                    json.dumps([entry], sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "entries": [entry],
            }
            client, _holder = self._client(
                root,
                index,
                json.dumps(manifest).encode(),
                {entry["evidence"][0]: profile},
            )
            runtime = TrustedCatalogRuntime(client, None)
            with self.assertRaisesRegex(CatalogUpdateError, "changed after review"):
                runtime.prepare_transaction(
                    "org.projectluma.mod.green-dock",
                    host(),
                    reviewed_composition_sha256="0" * 64,
                )

    def test_signed_staging_acceptance_binds_complete_ceremony_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = self._index()
            client, _holder = self._client(root, index)
            manifest = (EXAMPLES / "org.projectluma.mod.green-dock.mod.json").read_bytes()
            index_payload = json.dumps(index).encode("utf-8")
            request = {
                "schema": "org.luma.mod-catalog-ceremony-request/v0.1",
                "repository": "staging-test",
                "snapshot_id": index["snapshot_id"],
                "policy_sha256": "f" * 64,
                "metadata_versions": {
                    role: 1 for role in (
                        "root", "targets", "snapshot", "timestamp",
                        "luma-core", "luma-verified", "community",
                    )
                },
                "roles": {},
            }
            records = {
                "targets": [("catalog/index.json", index_payload)],
                "community": [(index["entries"][0]["manifest"], manifest)],
            }
            for role_number, role in enumerate(request["metadata_versions"], start=1):
                request["roles"][role] = {
                    "threshold": 1,
                    "key_ids": [f"{role_number:064x}"],
                    "custody": "online",
                    "expires_days": 1,
                    "targets": [
                        {
                            "path": name,
                            "length": len(payload),
                            "sha256": hashlib.sha256(payload).hexdigest(),
                        }
                        for name, payload in records.get(role, [])
                    ],
                }
            request_path = root / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            receipt = accept_catalog(client, request_path, root / "receipt.json")
            self.assertEqual(receipt["snapshot_id"], index["snapshot_id"])
            self.assertEqual(len(receipt["targets"]), 2)


class CatalogAuthorTests(unittest.TestCase):
    def test_unsigned_target_handoff_is_deterministic_and_content_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = b'{"mediaType":"application/vnd.dev.sigstore.bundle+json;version=0.3"}'
            (root / "publisher.sigstore.json").write_bytes(bundle)
            value = example()
            unsigned = inspect_manifest(write_manifest(root, value, "unsigned.mod.json"))
            value["signatures"] = [{
                "signer": "org.projectluma",
                "algorithm": "sigstore-bundle-v0.3",
                "manifest_digest": f"sha256:{unsigned.signing_sha256}",
                "bundle": "publisher.sigstore.json",
                "bundle_digest": f"sha256:{hashlib.sha256(bundle).hexdigest()}",
            }]
            write_manifest(root, value, "green.mod.json")
            spec = root / "release.json"
            spec.write_text(json.dumps({
                "schema": "org.luma.mod-catalog-release/v0.1",
                "entries": [{
                    "manifest": "green.mod.json",
                    "signature_bundles": ["publisher.sigstore.json"],
                    "evidence": [],
                    "payloads": [],
                    "trust_role": "community",
                }],
            }), encoding="utf-8")
            first = assemble_catalog(spec, root / "first")
            second = assemble_catalog(spec, root / "second")
            self.assertEqual(first, second)
            self.assertEqual(first["schema"], "org.luma.mod-catalog-signing-handoff/v0.1")
            index = json.loads((root / "first/targets/catalog/index.json").read_text())
            self.assertEqual(index["entries"][0]["id"], "org.projectluma.mod.green-dock")
            self.assertEqual(index["snapshot_id"], first["snapshot_id"])

    def test_catalog_handoff_refuses_bundle_not_bound_by_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_manifest(root, example(), "green.mod.json")
            (root / "unexpected.sigstore.json").write_text("{}", encoding="utf-8")
            spec = root / "release.json"
            spec.write_text(json.dumps({
                "schema": "org.luma.mod-catalog-release/v0.1",
                "entries": [{
                    "manifest": "green.mod.json",
                    "signature_bundles": ["unexpected.sigstore.json"],
                    "evidence": [], "payloads": [], "trust_role": "community",
                }],
            }), encoding="utf-8")
            with self.assertRaisesRegex(LumaModsError, "do not match manifest"):
                assemble_catalog(spec, root / "output")

    def test_preference_profile_is_published_as_content_addressed_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = BUILTIN_CATALOG / "manifests/org.projectluma.mod.green-dock.mod.json"
            profile = BUILTIN_CATALOG / "profiles/org.projectluma.mod.green-dock.profile.json"
            shutil.copy2(manifest, root / "green.mod.json")
            shutil.copy2(profile, root / "green.profile.json")
            spec = root / "release.json"
            spec.write_text(json.dumps({
                "schema": "org.luma.mod-catalog-release/v0.1",
                "entries": [{
                    "manifest": "green.mod.json",
                    "signature_bundles": [],
                    "evidence": ["green.profile.json"],
                    "payloads": [],
                    "trust_role": "community",
                }],
            }), encoding="utf-8")
            assemble_catalog(spec, root / "output")
            digest = hashlib.sha256(profile.read_bytes()).hexdigest()
            index = json.loads(
                (root / "output/targets/catalog/index.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                index["entries"][0]["evidence"],
                [f"evidence/community/sha256/{digest}"],
            )


class CatalogCeremonyTests(unittest.TestCase):
    @staticmethod
    def _policy(root: Path) -> Path:
        keys = iter(f"{number:064x}" for number in range(1, 20))
        roles = {}
        for name, count, threshold, custody, expiry in (
            ("root", 3, 2, "offline", 365),
            ("targets", 3, 2, "offline", 90),
            ("snapshot", 2, 1, "online", 7),
            ("timestamp", 2, 1, "online", 1),
            ("luma-core", 3, 2, "offline", 90),
            ("luma-verified", 2, 1, "online", 30),
            ("community", 2, 1, "online", 30),
        ):
            roles[name] = {
                "threshold": threshold,
                "key_ids": [next(keys) for _ in range(count)],
                "custody": custody,
                "expires_days": expiry,
            }
        path = root / "policy.json"
        path.write_text(json.dumps({
            "schema": "org.luma.mod-catalog-ceremony-policy/v0.1",
            "repository": "production",
            "metadata_versions": {name: 1 for name in roles},
            "roles": roles,
        }), encoding="utf-8")
        return path

    @staticmethod
    def _handoff(root: Path) -> Path:
        source = root / "source"
        source.mkdir()
        shutil.copy2(
            EXAMPLES / "org.projectluma.mod.green-dock.mod.json",
            source / "green.mod.json",
        )
        (source / "release.json").write_text(json.dumps({
            "schema": "org.luma.mod-catalog-release/v0.1",
            "entries": [{
                "manifest": "green.mod.json",
                "signature_bundles": [],
                "evidence": [],
                "payloads": [],
                "trust_role": "community",
            }],
        }), encoding="utf-8")
        destination = root / "handoff"
        assemble_catalog(source / "release.json", destination)
        return destination

    def test_ceremony_request_is_keyless_content_bound_and_role_separated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = prepare_ceremony(
                self._policy(root), self._handoff(root), root / "request.json"
            )
            self.assertEqual(
                request["schema"], "org.luma.mod-catalog-ceremony-request/v0.1"
            )
            self.assertEqual(len(request["roles"]["community"]["targets"]), 1)
            self.assertEqual(len(request["roles"]["targets"]["targets"]), 1)
            self.assertNotIn("private", json.dumps(request).lower())

    def test_ceremony_refuses_unreviewed_extra_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            handoff = self._handoff(root)
            (handoff / "targets/unreviewed").write_text("extra", encoding="utf-8")
            with self.assertRaisesRegex(LumaModsError, "differ from"):
                prepare_ceremony(self._policy(root), handoff, root / "request.json")

    def test_ceremony_refuses_online_key_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = self._policy(root)
            value = json.loads(policy.read_text(encoding="utf-8"))
            value["roles"]["timestamp"]["key_ids"][0] = value["roles"]["root"]["key_ids"][0]
            policy.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(LumaModsError, "reused"):
                prepare_ceremony(policy, self._handoff(root), root / "request.json")


class AutomaticUpdatePolicyTests(unittest.TestCase):
    @staticmethod
    def _verification(level: str = "community") -> VerificationResult:
        return VerificationResult(
            level=level,
            label=level,
            verified=True,
            reason="test proof",
            publisher_id="org.projectluma",
            signer_identity="test@example.invalid",
            issuer="https://issuer.example.invalid",
        )

    def _pair(self, root: Path) -> tuple:
        old = example()
        new = deepcopy(old)
        old["identity"]["version"] = "1.0.0"
        new["identity"]["version"] = "1.0.1"
        return (
            inspect_manifest(write_manifest(root, old, "old.mod.json")),
            inspect_manifest(write_manifest(root, new, "new.mod.json")),
        )

    def test_exact_verified_envelope_can_update_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            old, new = self._pair(Path(directory))
            decision = evaluate_update(old, new, self._verification(), self._verification())
            self.assertTrue(decision.automatic)
            self.assertEqual(decision.disposition, "automatic-compatible")

    def test_new_dependency_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, _new = self._pair(root)
            value = example()
            value["identity"]["version"] = "1.0.1"
            value["dependencies"][0]["reason"] = "A changed dependency contract."
            new = inspect_manifest(write_manifest(root, value, "broader.mod.json"))
            decision = evaluate_update(old, new, self._verification(), self._verification())
            self.assertFalse(decision.automatic)
            self.assertIn("Required Mods changed.", decision.reasons)

    def test_weaker_trust_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            old, new = self._pair(Path(directory))
            decision = evaluate_update(
                old, new, self._verification("luma-verified"), self._verification("community")
            )
            self.assertFalse(decision.automatic)
            self.assertIn("weaker verification", " ".join(decision.reasons))


class RuntimePathTests(unittest.TestCase):
    def test_xdg_paths_must_be_absolute(self) -> None:
        with patch.dict(os.environ, {"XDG_STATE_HOME": "relative/state"}, clear=False):
            with self.assertRaisesRegex(StateError, "absolute"):
                UserPaths.current()

    def test_xdg_paths_are_fixed_below_luma_namespaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CONFIG_HOME": str(root / "config"),
            }, clear=False):
                paths = UserPaths.current()
            self.assertEqual(paths.state_root, root / "state" / "luma" / "mods")
            self.assertEqual(
                paths.preference_file, root / "config" / "luma" / "preferences.json"
            )

    def test_runtime_host_context_reflects_managed_state_without_host_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CONFIG_HOME": str(root / "config"),
                "LUMA_PRESENTATION_MODE": "fullscreen-mobile",
            }, clear=False):
                context = UserRuntime.current().host_context()
            self.assertEqual(context.presentation, "handheld")
            self.assertEqual(context.installed_mods, {})
            self.assertEqual(context.capabilities["org.luma.apps.core"], "1.0.0")

    def test_builtin_profile_and_authorization_are_explicit(self) -> None:
        plan = resolve_mod(
            "org.projectluma.mod.green-dock",
            Catalog.from_directory(BUILTIN_CATALOG / "manifests"),
            host(),
        )
        profile = profile_for(BUILTIN_CATALOG, plan.target_id)
        self.assertEqual(profile.mod_id, plan.target_id)
        with self.assertRaisesRegex(LumaModsError, "Local / Unverified"):
            authorization_for_plan(plan, None, confirmed_unverified=False)
        authorization = authorization_for_plan(
            plan, None, confirmed_unverified=True
        )
        self.assertEqual(authorization.level_for(plan.target_id), "local-unverified")


class SystemHostProfileTests(unittest.TestCase):
    def test_profile_combines_immutable_policy_with_exact_runtime_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "system-host.json"
            profile_path.write_text(json.dumps({
                "schema": "org.luma.mod-system-host/v0.1",
                "luma_base": "0.1.0",
                "presentation": "desktop",
                "capabilities": {"org.luma.apps.core": "1.0.0"},
                "capability_providers": {},
                "effect_owners": {},
            }), encoding="utf-8")
            dmi = root / "dmi"
            dmi.mkdir()
            (dmi / "sys_vendor").write_text("LENOVO\n", encoding="utf-8")
            (dmi / "product_name").write_text("20TH003HUS\n", encoding="utf-8")
            profile = SystemHostProfile.from_file(profile_path, require_root=False)
            context = profile.context(
                dmi_root=dmi,
                architecture="amd64",
                kernel_release="6.15.10-200.fc44.x86_64",
            )
            self.assertEqual(context.architecture, "x86_64")
            self.assertEqual(context.hardware, ("dmi:LENOVO:20TH003HUS",))
            self.assertEqual(context.install_mode, "running-system")

    def test_profile_refuses_dynamic_identity_fields_and_insecure_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "system-host.json"
            value = {
                "schema": "org.luma.mod-system-host/v0.1",
                "luma_base": "0.1.0",
                "presentation": "desktop",
                "capabilities": {},
                "capability_providers": {},
                "effect_owners": {},
                "hardware": ["dmi:Forged:Machine"],
            }
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(TransactionError, "invalid shape"):
                SystemHostProfile.from_file(path, require_root=False)
            del value["hardware"]
            path.write_text(json.dumps(value), encoding="utf-8")
            path.chmod(0o666)
            with self.assertRaisesRegex(TransactionError, "non-writable"):
                SystemHostProfile.from_file(path, require_root=False)

    def test_dmi_identity_refuses_delimiter_injection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sys_vendor").write_text("Example:Forged\n", encoding="utf-8")
            (root / "product_name").write_text("Machine\n", encoding="utf-8")
            self.assertEqual(dmi_hardware_identities(root), ())


class SystemCatalogAdmissionTests(unittest.TestCase):
    @staticmethod
    def _fixture(root: Path, *, verified: bool = True):
        root.mkdir(parents=True, exist_ok=True)
        payload = b"system catalog RPM fixture"
        digest = hashlib.sha256(payload).hexdigest()
        payload_path = root / digest
        payload_path.write_bytes(payload)
        value = example("org.projectluma.prairie.dock-style-api.mod.json")
        value["identity"]["id"] = "org.example.system-mod"
        value["identity"]["name"] = "Catalog System Mod"
        value["identity"]["kind"] = "core-system"
        value["effects"]["files"] = ["/usr/share/luma/catalog-system-mod"]
        value["payloads"] = [{
            "backend": "rpm-set",
            "digest": f"sha256:{digest}",
            "size": len(payload),
            "architecture": "x86_64",
            "role": "system-content",
            "source": "https://mods.example/payload",
        }]
        manifest = write_manifest(root, value)
        inspection = inspect_manifest(manifest)
        plan = resolve_mod(
            "org.example.system-mod", Catalog((inspection,)), host()
        )
        verification = VerificationResult(
            level="luma-core" if verified else "local-unverified",
            label="Luma Core" if verified else "Local / Unverified",
            verified=verified,
            reason="test publisher proof",
            publisher_id=inspection.mod.identity.publisher.id,
        )
        trusted = TrustedCatalogPlan(
            snapshot_id="sha256:" + "a" * 64,
            plan=plan,
            verifications={"org.example.system-mod": verification},
            trust_roles={"org.example.system-mod": "luma-core"},
            admitted_manifests={"org.example.system-mod": inspection.source_sha256},
            _tuf_verified=True,
        )

        class Client:
            calls = 0

            def acquire_payloads(self, _snapshot, identifier):
                self.calls += 1
                if identifier != "org.example.system-mod":
                    raise AssertionError(identifier)
                return (payload_path,)

        class Runtime:
            def __init__(self):
                self.snapshot = object()
                self.client = Client()
                self.refreshes = 0

            def refresh(self):
                self.refreshes += 1

            def plan(self, identifier, context):
                if identifier != "org.example.system-mod":
                    raise AssertionError(identifier)
                if context.hardware:
                    raise AssertionError("test host should be exact and empty")
                return trusted

        return Runtime(), trusted, payload, digest

    def test_root_runtime_rebuilds_and_materializes_reviewed_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime, trusted, payload, digest = self._fixture(root)
            store = ArtifactStore(root / "store")
            admission = SystemCatalogAdmission(runtime, host, store)
            result = admission.prepare(
                "org.example.system-mod",
                reviewed_snapshot_id=trusted.snapshot_id,
                reviewed_composition_sha256=trusted.plan.composition_sha256,
                recovery_ready=True,
            )
            self.assertEqual(runtime.refreshes, 1)
            self.assertEqual(runtime.client.calls, 1)
            self.assertEqual(result.request.target_id, "org.example.system-mod")
            self.assertEqual((root / "store" / digest).read_bytes(), payload)

    def test_changed_review_or_unverified_publisher_never_acquires_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime, trusted, _payload, _digest = self._fixture(root)
            admission = SystemCatalogAdmission(runtime, host, ArtifactStore(root / "store"))
            with self.assertRaisesRegex(CatalogUpdateError, "changed after review"):
                admission.prepare(
                    "org.example.system-mod",
                    reviewed_snapshot_id="sha256:" + "b" * 64,
                    reviewed_composition_sha256=trusted.plan.composition_sha256,
                    recovery_ready=True,
                )
            self.assertEqual(runtime.client.calls, 0)

            unverified_runtime, unverified, _payload, _digest = self._fixture(
                root / "unverified", verified=False
            )
            unverified_admission = SystemCatalogAdmission(
                unverified_runtime, host, ArtifactStore(root / "other-store")
            )
            with self.assertRaisesRegex(TransactionError, "publisher verification"):
                unverified_admission.prepare(
                    "org.example.system-mod",
                    reviewed_snapshot_id=unverified.snapshot_id,
                    reviewed_composition_sha256=unverified.plan.composition_sha256,
                    recovery_ready=True,
                )
            self.assertEqual(unverified_runtime.client.calls, 0)


class PrivilegedBoundaryTests(unittest.TestCase):
    def test_closed_backend_has_no_accidental_system_fallback(self) -> None:
        plan = resolve_mod(
            "org.projectluma.mod.green-dock", Catalog.from_directory(EXAMPLES), host()
        )
        request = build_system_request(
            plan,
            {
                "org.projectluma.mod.green-dock": "community",
                "org.projectluma.prairie.dock-style-api": "luma-core",
            },
            recovery_ready=False,
        )
        self.assertNotIn("command", json.dumps(request.as_dict()))
        with self.assertRaisesRegex(TransactionError, "disabled"):
            ClosedSystemBackend().stage(request)

    def test_system_effect_requires_immutable_payload_and_verified_publisher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = example("org.projectluma.prairie.dock-style-api.mod.json")
            value["identity"]["id"] = "org.example.system-mod"
            value["identity"]["name"] = "System Mod"
            value["identity"]["kind"] = "core-system"
            value["effects"]["files"] = ["/usr/share/luma/example"]
            value["payloads"] = [{
                "backend": "rpm-set",
                "digest": f"sha256:{'1' * 64}",
                "size": 4096,
                "architecture": "x86_64",
                "role": "system-content",
                "source": "https://example.invalid/source",
            }]
            write_manifest(root, value)
            plan = resolve_mod("org.example.system-mod", Catalog.from_directory(root), host())
            with self.assertRaisesRegex(TransactionError, "catalog-backed"):
                build_system_request(plan, {}, recovery_ready=True)
            request = build_system_request(
                plan, {"org.example.system-mod": "luma-core"}, recovery_ready=True
            )
            self.assertEqual(request.artifacts[0].backend, "rpm-set")

    def test_untrusted_system_request_round_trips_but_rejects_extra_input(self) -> None:
        request = SystemCompositionRequest(
            target_id="org.example.system-mod",
            composition_sha256="a" * 64,
            activation="reboot",
            artifacts=(ArtifactRequest(
                mod_id="org.example.system-mod",
                backend="rpm-set",
                digest=f"sha256:{'b' * 64}",
                size=123,
                architecture="x86_64",
                role="system-content",
                source="https://example.invalid/payload.rpm",
            ),),
            declared_effects={"packages": ("example",)},
            previous_composition_required=True,
        )
        parsed = SystemCompositionRequest.from_dict(request.as_dict())
        self.assertEqual(parsed, request)
        hostile = request.as_dict()
        hostile["command"] = "sh -c arbitrary"
        with self.assertRaisesRegex(TransactionError, "invalid shape"):
            SystemCompositionRequest.from_dict(hostile)
        insecure = request.as_dict()
        insecure["artifacts"][0]["source"] = "http://example.invalid/payload.rpm"
        with self.assertRaisesRegex(TransactionError, "HTTPS"):
            SystemCompositionRequest.from_dict(insecure)

    def test_rpm_ostree_backend_uses_verified_store_and_fixed_argv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b"bounded rpm fixture"
            digest = hashlib.sha256(payload).hexdigest()
            artifact_root = root / "sha256"
            artifact_root.mkdir()
            (artifact_root / digest).write_bytes(payload)
            artifact = ArtifactRequest(
                mod_id="org.example.system-mod",
                backend="rpm-set",
                digest=f"sha256:{digest}",
                size=len(payload),
                architecture="x86_64",
                role="system-content",
                source="https://example.invalid/payload.rpm",
            )
            request = SystemCompositionRequest(
                target_id="org.example.system-mod",
                composition_sha256="c" * 64,
                activation="reboot",
                artifacts=(artifact,),
                declared_effects={"packages": ("example",)},
                previous_composition_required=True,
            )
            commands: list[tuple[str, ...]] = []

            def runner(command):
                commands.append(tuple(command))
                if tuple(command) == ("rpm-ostree", "status", "--json"):
                    return json.dumps({"deployments": [{
                        "staged": True, "checksum": "d" * 64
                    }]})
                return ""

            backend = RpmOstreeBackend(ArtifactStore(artifact_root), runner)
            self.assertEqual(backend.stage(request), "d" * 64)
            rpm_path = artifact_root / f"{digest}.rpm"
            self.assertEqual(
                commands[0],
                (
                    "rpm-ostree", "install", "--idempotent",
                    str(rpm_path),
                ),
            )
            self.assertTrue(rpm_path.samefile(artifact_root / digest))
            self.assertTrue(Path(commands[0][-1]).is_absolute())
            self.assertFalse(
                any(token in {"sh", "bash"} for command in commands for token in command)
            )
            self.assertTrue(request.previous_composition_required)

    def test_rpm_ostree_backend_rejects_foreign_rpm_staging_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b"bounded rpm fixture"
            digest = hashlib.sha256(payload).hexdigest()
            artifact_root = root / "sha256"
            artifact_root.mkdir()
            (artifact_root / digest).write_bytes(payload)
            (artifact_root / f"{digest}.rpm").write_bytes(b"different inode")
            artifact = ArtifactRequest(
                mod_id="org.example.system-mod",
                backend="rpm-set",
                digest=f"sha256:{digest}",
                size=len(payload),
                architecture="x86_64",
                role="system-content",
                source="https://example.invalid/payload.rpm",
            )
            with self.assertRaisesRegex(TransactionError, "does not reference verified artifact"):
                ArtifactStore(artifact_root).require_rpm(artifact)

    def test_artifact_admission_rehashes_and_owns_content_address(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b"TUF-verified system payload"
            digest = hashlib.sha256(payload).hexdigest()
            source = root / "download"
            source.write_bytes(payload)
            artifact = ArtifactRequest(
                mod_id="org.example.system-mod",
                backend="rpm-set",
                digest=f"sha256:{digest}",
                size=len(payload),
                architecture="x86_64",
                role="system-content",
                source="https://mods.example/payload",
            )
            store = ArtifactStore(root / "store")
            admitted = store.admit(artifact, source)
            self.assertEqual(admitted.name, digest)
            self.assertEqual(admitted.read_bytes(), payload)
            source.write_bytes(b"changed after admission")
            self.assertEqual(store.require(artifact).read_bytes(), payload)

    def test_artifact_admission_rejects_changed_or_symlinked_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b"expected"
            digest = hashlib.sha256(payload).hexdigest()
            artifact = ArtifactRequest(
                mod_id="org.example.system-mod",
                backend="rpm-set",
                digest=f"sha256:{digest}",
                size=len(payload),
                architecture="x86_64",
                role="system-content",
                source="https://mods.example/payload",
            )
            changed = root / "changed"
            changed.write_bytes(b"mismatch")
            store = ArtifactStore(root / "store")
            with self.assertRaisesRegex(TransactionError, "identity changed"):
                store.admit(artifact, changed)
            real = root / "real"
            real.write_bytes(payload)
            link = root / "link"
            link.symlink_to(real)
            with self.assertRaisesRegex(TransactionError, "open verified artifact"):
                store.admit(artifact, link)

    def test_recovery_reboot_effect_is_refused_without_recovery_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = example("org.projectluma.prairie.dock-style-api.mod.json")
            value["identity"]["id"] = "org.example.kernel-mod"
            value["identity"]["kind"] = "core-system"
            value["compatibility"]["hardware"] = ["dmi:Example:Machine"]
            value["compatibility"]["kernel_releases"] = ["6.15.10-200.fc44.x86_64"]
            value["effects"]["kernel_modules"] = ["example_module"]
            value["payloads"] = [{
                "backend": "rpm-set", "digest": f"sha256:{'2' * 64}",
                "size": 1024, "architecture": "x86_64", "role": "kernel-module",
                "source": "https://example.invalid/source",
            }]
            write_manifest(root, value)
            plan = resolve_mod(
                "org.example.kernel-mod",
                Catalog.from_directory(root),
                host(
                    hardware=["dmi:Example:Machine"],
                    kernel_release="6.15.10-200.fc44.x86_64",
                ),
            )
            with self.assertRaisesRegex(TransactionError, "recovery-capable"):
                build_system_request(
                    plan, {"org.example.kernel-mod": "luma-core"}, recovery_ready=False
                )

    def test_verified_hardware_kernel_mod_requires_pinning_and_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = example("org.projectluma.prairie.dock-style-api.mod.json")
            value["identity"]["id"] = "org.example.surface-support"
            value["identity"]["kind"] = "hardware"
            value["compatibility"]["hardware"] = ["dmi:Microsoft:Surface"]
            value["compatibility"]["kernel_releases"] = ["6.15.10-200.fc44.x86_64"]
            value["effects"]["kernel_modules"] = ["surface_aggregator"]
            value["payloads"] = [{
                "backend": "rpm-set", "digest": f"sha256:{'3' * 64}",
                "size": 1024, "architecture": "x86_64", "role": "kernel-module",
                "source": "https://example.invalid/surface.rpm",
            }]
            value["recovery"]["previous_composition_retained"] = True
            value["recovery"]["health_gate"] = "surface-hardware-v1"
            write_manifest(root, value)
            plan = resolve_mod(
                "org.example.surface-support",
                Catalog.from_directory(root),
                host(
                    hardware=["dmi:Microsoft:Surface"],
                    kernel_release="6.15.10-200.fc44.x86_64",
                ),
            )
            request = build_system_request(
                plan, {"org.example.surface-support": "luma-verified"}, recovery_ready=True
            )
            self.assertTrue(request.previous_composition_required)
            with self.assertRaisesRegex(TransactionError, "Luma Core or Luma Verified"):
                build_system_request(
                    plan, {"org.example.surface-support": "community"}, recovery_ready=True
                )


class SystemTransactionStateTests(unittest.TestCase):
    def _request(self) -> SystemCompositionRequest:
        return SystemCompositionRequest(
            target_id="org.example.system-mod",
            composition_sha256="c" * 64,
            activation="reboot",
            artifacts=(ArtifactRequest(
                mod_id="org.example.system-mod",
                backend="rpm-set",
                digest=f"sha256:{'b' * 64}",
                size=123,
                architecture="x86_64",
                role="system-content",
                source="https://example.invalid/payload.rpm",
            ),),
            declared_effects={"packages": ("example",)},
            previous_composition_required=True,
        )

    class Backend:
        def __init__(self):
            self.booted = "a" * 64
            self.staged = None
            self.commands = []

        def booted_checksum(self):
            return self.booted

        def staged_checksum(self):
            return self.staged

        def stage(self, _request):
            self.commands.append("stage")
            self.staged = "d" * 64
            return self.staged

        def activate(self, candidate):
            self.commands.append(("activate", candidate))

        def rollback(self, candidate):
            self.commands.append(("rollback", candidate))

        def cancel_staged(self, candidate=None):
            if candidate is not None and self.staged != candidate:
                raise TransactionError("candidate mismatch")
            self.commands.append(("cancel", self.staged))
            self.staged = None

    def test_candidate_is_journaled_before_activation_and_promoted_after_boot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = self.Backend()
            state = SystemStateStore(Path(directory) / "state")
            coordinator = SystemTransactionCoordinator(backend, state)
            candidate = coordinator.stage(self._request())
            pending = state.read()["pending"]
            self.assertEqual(pending["known_good_checksum"], "a" * 64)
            self.assertEqual(pending["candidate_checksum"], "d" * 64)
            coordinator.activate(candidate)
            backend.booted = candidate
            self.assertEqual(coordinator.observe_boot(), "candidate")
            self.assertTrue(coordinator.promote_booted_candidate())
            result = state.read()
            self.assertIsNone(result["pending"])
            self.assertEqual(result["known_good_checksum"], candidate)
            self.assertEqual(result["history"][-1]["result"], "promoted")

    def test_unpromoted_candidate_reaches_fixed_rollback_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = self.Backend()
            state = SystemStateStore(Path(directory) / "state")
            coordinator = SystemTransactionCoordinator(
                backend, state, boot_attempt_limit=2
            )
            candidate = coordinator.stage(self._request())
            coordinator.activate(candidate)
            backend.booted = candidate
            self.assertEqual(coordinator.observe_boot(), "candidate")
            self.assertEqual(coordinator.observe_boot(), "rollback")
            self.assertEqual(backend.commands[-1], ("rollback", candidate))

    def test_interrupted_staging_is_cancelled_before_new_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = self.Backend()
            state = SystemStateStore(Path(directory) / "state")
            state.prepare(self._request(), backend.booted, boot_attempt_limit=2)
            backend.staged = "e" * 64
            coordinator = SystemTransactionCoordinator(backend, state)
            self.assertTrue(coordinator.recover_incomplete_staging())
            recovered = state.read()
            self.assertIsNone(recovered["pending"])
            self.assertEqual(recovered["history"][-1]["result"], "recovered")

    def test_system_state_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            state_root.mkdir()
            outside = root / "outside.json"
            outside.write_text(json.dumps(empty_system_state()), encoding="utf-8")
            (state_root / "system-state.json").symlink_to(outside)
            with self.assertRaisesRegex(StateError, "safely"):
                SystemStateStore(state_root).read()

    def test_recovery_record_names_the_shipped_promotion_unit(self) -> None:
        self.assertEqual(RECOVERY_HEALTH_UNIT, "luma-mod-boot-promote.service")
        self.assertTrue(validate_recovery_record({
            "schema": "org.luma.mod-recovery-readiness/v0.1",
            "known_good_checksum": "a" * 64,
            "health_unit": "luma-mod-boot-promote.service",
            "boot_attempt_limit": 2,
        }))
        self.assertFalse(validate_recovery_record({
            "schema": "org.luma.mod-recovery-readiness/v0.1",
            "known_good_checksum": "a" * 64,
            "health_unit": "luma-mod-boot-health.service",
            "boot_attempt_limit": 2,
        }))

    def test_polkit_signature_is_the_real_nested_check_authorization_tuple(self) -> None:
        self.assertEqual(
            POLKIT_CHECK_AUTHORIZATION_SIGNATURE,
            "((sa{sv})sa{ss}us)",
        )
        self.assertNotIn("@", POLKIT_CHECK_AUTHORIZATION_SIGNATURE)

    def test_polkit_result_requires_gio_method_tuple_wrapper(self) -> None:
        self.assertTrue(polkit_authorized(((True, False, {}),)))
        self.assertFalse(polkit_authorized(((False, True, {"reason": "auth"}),)))
        with self.assertRaisesRegex(TransactionError, "invalid authorization result"):
            polkit_authorized((True, False, {}))
        with self.assertRaisesRegex(TransactionError, "invalid authorization fields"):
            polkit_authorized(((1, False, {}),))

    def test_boot_units_create_their_protected_state_directory(self) -> None:
        for name in (
            "luma-mod-boot-observe.service",
            "luma-mod-boot-promote.service",
            "luma-mod-boot-watchdog.service",
        ):
            unit = (MODS_SOURCE / "data" / name).read_text(encoding="utf-8")
            self.assertIn("StateDirectory=luma/mods", unit)
            self.assertIn("StateDirectoryMode=0750", unit)

    def test_recovery_drill_is_bounded_and_never_a_product_input(self) -> None:
        dropin = (
            REPO_ROOT
            / "tests/fixtures/mods/recovery-drill/90-luma-recovery-drill.conf"
        ).read_text(encoding="utf-8")
        self.assertIn("ExecStart=/usr/bin/false", dropin)
        self.assertNotIn("ExecStop", dropin)
        self.assertNotIn("ExecStartPre", dropin)
        for package_input in (
            REPO_ROOT / "config/desktop/packages.txt",
            REPO_ROOT / "config/shared/application-packages.txt",
        ):
            self.assertNotIn(
                "luma-mod-recovery-drill",
                package_input.read_text(encoding="utf-8"),
            )


class AuthoringTests(unittest.TestCase):
    def test_authoring_lint_and_evidence_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence.json"
            command = [
                sys.executable,
                "-m",
                "luma_mods.author",
                "build",
                str(BUILTIN_CATALOG / "manifests/org.projectluma.mod.green-dock.mod.json"),
                "--output",
                str(evidence),
            ]
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(MODS_SOURCE)
            first = subprocess.run(
                command, check=False, capture_output=True, text=True, env=environment
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            first_bytes = evidence.read_bytes()
            second = subprocess.run(
                command, check=False, capture_output=True, text=True, env=environment
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(evidence.read_bytes(), first_bytes)
            value = json.loads(first_bytes)
            self.assertEqual(value["schema"], "org.luma.mod-authoring-evidence/v0.1")
            self.assertEqual(
                value["effects"]["settings"],
                ["org.luma.shell.dock.appearance"],
            )

    def test_author_init_never_overwrites_existing_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(MODS_SOURCE)
            command = [
                sys.executable, "-m", "luma_mods.author", "init", str(root),
                "--id", "org.example.calm", "--name", "Calm",
                "--publisher-id", "org.example", "--publisher-name", "Example",
            ]
            created = subprocess.run(
                command, check=False, capture_output=True, text=True, env=environment
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            refused = subprocess.run(
                command, check=False, capture_output=True, text=True, env=environment
            )
            self.assertEqual(refused.returncode, 2)
            self.assertIn("File exists", refused.stderr)

    def test_author_reproducibility_compares_bounded_tree_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            (first / "artifact.rpm").write_bytes(b"same payload")
            (second / "artifact.rpm").write_bytes(b"same payload")
            report = root / "report.json"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(MODS_SOURCE)
            command = [
                sys.executable, "-m", "luma_mods.author", "verify-reproducible",
                str(first), str(second), "--output", str(report),
            ]
            matched = subprocess.run(
                command, check=False, capture_output=True, text=True, env=environment
            )
            self.assertEqual(matched.returncode, 0, matched.stderr)
            self.assertTrue(json.loads(report.read_text(encoding="utf-8"))["reproducible"])
            (second / "artifact.rpm").write_bytes(b"different payload")
            mismatched = subprocess.run(
                command, check=False, capture_output=True, text=True, env=environment
            )
            self.assertEqual(mismatched.returncode, 3)
            self.assertFalse(json.loads(report.read_text(encoding="utf-8"))["reproducible"])

    def test_author_artifact_tree_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            payload = root / "payload"
            payload.write_bytes(b"payload")
            (first / "escape").symlink_to(payload)
            (second / "escape").symlink_to(payload)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(MODS_SOURCE)
            result = subprocess.run([
                sys.executable, "-m", "luma_mods.author", "verify-reproducible",
                str(first), str(second), "--output", str(root / "report.json"),
            ], check=False, capture_output=True, text=True, env=environment)
            self.assertEqual(result.returncode, 2)
            self.assertIn("must not contain symlinks", result.stderr)

    def test_high_impact_pilots_require_complete_evidence(self) -> None:
        pilots = REPO_ROOT / "examples/mods/pilots"
        for manifest, host_file, expected_checks in (
            (
                "org.kde.plasma.luma-experience.candidate.mod.json",
                "host-kde-desktop.json",
                18,
            ),
            (
                "org.projectluma.pilot.hardware.lenovo-20th003hus.candidate.mod.json",
                "host-thinkpad-p1-gen3.json",
                12,
            ),
            (
                "org.projectluma.pilot.hardware.microsoft-surface-pro-9.candidate.mod.json",
                "host-microsoft-surface-pro-9.json",
                12,
            ),
        ):
            with self.subTest(manifest=manifest):
                evidence = pilot_template(pilots / manifest, pilots / host_file)
                self.assertEqual(len(evidence["checks"]), expected_checks)
                self.assertFalse(evidence["release_ready"])
                validate_pilot(evidence)
                evidence["release_ready"] = True
                with self.assertRaisesRegex(LumaModsError, "does not match"):
                    validate_pilot(evidence)

    def test_sandbox_build_uses_two_fresh_locked_down_containers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            commands = []

            def runner(command):
                commands.append(tuple(command))
                output_mount = next(token for token in command if token.endswith(":/out:rw,Z"))
                output = Path(output_mount.removeprefix("--volume=").split(":/out", 1)[0])
                (output / "payload").write_bytes(b"reproducible")

            result = reproducible_build(
                source,
                root / "release",
                "registry.example/luma-builder@sha256:" + "a" * 64,
                ["/usr/bin/build", "/src", "/out"],
                runner=runner,
                require_rootless=False,
            )
            self.assertEqual(len(commands), 2)
            for command in commands:
                self.assertIn("--network=none", command)
                self.assertIn("--read-only", command)
                self.assertIn("--cap-drop=all", command)
                self.assertIn("--security-opt=no-new-privileges", command)
            self.assertEqual(result.files, 1)
            self.assertEqual((root / "release/payload").read_bytes(), b"reproducible")

    def test_sandbox_build_refuses_unpinned_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            with self.assertRaisesRegex(LumaModsError, "pinned"):
                reproducible_build(
                    source, root / "output", "fedora:latest", ["build"],
                    runner=lambda _command: None, require_rootless=False,
                )


if __name__ == "__main__":
    unittest.main()
