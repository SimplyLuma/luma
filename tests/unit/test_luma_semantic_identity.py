# SPDX-License-Identifier: Apache-2.0
"""Identity-boundary tests for the Luma Semantic Broker."""

from __future__ import annotations

from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
BROKER = ROOT / "src/luma-platform/broker"
sys.path.insert(0, str(BROKER))

from luma_semantic_broker.identity import IdentityError, IdentityResolver  # noqa: E402
from luma_semantic_broker.desktop import (  # noqa: E402
    _visible_owner_uid,
    desktop_application,
)
from luma_semantic_broker.model import IdentityStrength  # noqa: E402
from luma_semantic_broker.prompt import PromptRunner, safe_label  # noqa: E402


class Credentials:
    def __init__(self, *, pid: int = 4242, owner: str = ":1.50") -> None:
        self.pid = pid
        self.owner = owner

    def credentials(self, _sender: str) -> tuple[int, int]:
        return os.getuid(), self.pid

    def name_owner(self, _name: str) -> str:
        return self.owner

def desktop(app_id: str) -> tuple[bool, str]:
    known = {
        "org.example.Agent": "Example Agent",
        "org.projectluma.Notes": "Notes",
    }
    return app_id in known, known.get(app_id, app_id)


class IdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.proc = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def resolver(self, credentials: Credentials) -> IdentityResolver:
        return IdentityResolver(
            credentials,
            proc_root=self.proc,
            desktop_lookup=desktop,
        )

    def process(self, pid: int = 4242) -> Path:
        root = self.proc / str(pid)
        (root / "root").mkdir(parents=True)
        return root

    def test_flatpak_identity_comes_from_process_metadata(self) -> None:
        process = self.process()
        (process / "root/.flatpak-info").write_text(
            "[Application]\nname=org.example.Agent\n", encoding="utf-8"
        )
        identity = self.resolver(Credentials()).resolve(":1.20")
        self.assertEqual(identity.key, "flatpak:org.example.Agent")
        self.assertEqual(identity.label, "Example Agent")
        self.assertEqual(identity.strength, IdentityStrength.SANDBOXED)
        self.assertTrue(identity.persistent)

    def test_managed_native_identity_comes_from_verified_scope(self) -> None:
        process = self.process()
        (process / "cgroup").write_text(
            "0::/user.slice/app-org.projectluma.Notes-a1b2.scope\n",
            encoding="utf-8",
        )
        identity = self.resolver(Credentials()).resolve(":1.21")
        self.assertEqual(identity.key, "native:org.projectluma.Notes")
        self.assertEqual(identity.strength, IdentityStrength.MANAGED_NATIVE)
        self.assertFalse(identity.persistent)

    def test_background_agent_carries_its_app_identity(self) -> None:
        process = self.process()
        (process / "cgroup").write_text(
            "0::/user.slice/user-1000.slice/user@1000.service/luma-background.slice/"
            "luma-background-essential.slice/app-org.projectluma.Notes-agent.service\n",
            encoding="utf-8",
        )
        identity = self.resolver(Credentials()).resolve(":1.23")
        self.assertEqual(identity.key, "native:org.projectluma.Notes")
        self.assertEqual(identity.strength, IdentityStrength.MANAGED_NATIVE)

    def test_agent_unit_outside_the_background_slice_is_not_an_identity(self) -> None:
        process = self.process()
        (process / "cgroup").write_text(
            "0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
            "app-org.projectluma.Notes-agent.service\n",
            encoding="utf-8",
        )
        identity = self.resolver(Credentials()).resolve(":1.24")
        self.assertEqual(identity.app_id, "")

    def test_unidentified_native_client_is_transient(self) -> None:
        self.process()
        identity = self.resolver(Credentials()).resolve(":1.22")
        self.assertEqual(identity.strength, IdentityStrength.TRANSIENT_NATIVE)
        self.assertFalse(identity.persistent)
        self.assertEqual(identity.app_id, "")

    def test_live_extension_host_requires_shell_bus_owner_and_executable(self) -> None:
        process = self.process()
        (process / "exe").symlink_to("/usr/bin/gnome-shell")
        identity = self.resolver(Credentials()).require_shell_host(":1.50")
        self.assertEqual(identity.sender, ":1.50")
        with self.assertRaises(IdentityError):
            self.resolver(Credentials(owner=":1.51")).require_shell_host(":1.50")

    def test_live_extension_host_uses_bounded_kernel_metadata_in_mount_namespace(self) -> None:
        process = self.process()
        (process / "comm").write_text("gnome-shell\n", encoding="utf-8")
        (process / "cmdline").write_bytes(b"/usr/bin/gnome-shell\0--mode=user\0")
        (process / "cgroup").write_text(
            "0::/user.slice/user-1000.slice/user@1000.service/"
            "session.slice/org.gnome.Shell@user.service\n",
            encoding="utf-8",
        )
        resolver = self.resolver(Credentials())
        with mock.patch.object(Path, "readlink", side_effect=PermissionError):
            self.assertEqual(resolver.require_shell_host(":1.50").sender, ":1.50")
            (process / "cgroup").write_text(
                "0::/user.slice/app-org.example.Fake-a1.scope\n",
                encoding="utf-8",
            )
            with self.assertRaises(IdentityError):
                resolver.require_shell_host(":1.50")

    def test_unmanaged_provider_cannot_claim_an_installed_application(self) -> None:
        self.process()
        resolver = self.resolver(Credentials())
        with self.assertRaises(IdentityError):
            resolver.provider(":1.23", "org.projectluma.Notes")

    def test_managed_native_provider_must_match_its_scope(self) -> None:
        process = self.process()
        (process / "cgroup").write_text(
            "0::/user.slice/app-org.projectluma.Notes-a1b2.scope\n",
            encoding="utf-8",
        )
        resolver = self.resolver(Credentials())
        identity = resolver.provider(":1.23", "org.projectluma.Notes")
        self.assertEqual(identity.key, "native:org.projectluma.Notes")
        with self.assertRaises(IdentityError):
            resolver.provider(":1.23", "org.example.Agent")

    def test_provider_cannot_claim_another_flatpak_identity(self) -> None:
        process = self.process()
        (process / "root/.flatpak-info").write_text(
            "[Application]\nname=org.example.Agent\n", encoding="utf-8"
        )
        with self.assertRaises(IdentityError):
            self.resolver(Credentials()).provider(
                ":1.24", "org.projectluma.Notes"
            )

    def test_consent_labels_are_single_line_and_directionally_safe(self) -> None:
        self.assertEqual(
            safe_label("Agent\n\u202eSystem\tPrompt"),
            "Agent System Prompt",
        )

    def test_consent_ui_runs_outside_the_device_denied_broker_sandbox(self) -> None:
        runner = PromptRunner(executable="/trusted/consent", runner="/systemd-run")
        self.assertEqual(
            runner._command(["/trusted/consent", "access"]),
            [
                "/systemd-run",
                "--user",
                "--wait",
                "--collect",
                "--quiet",
                "--property=NoNewPrivileges=yes",
                "--",
                "/trusted/consent",
                "access",
            ],
        )

    def test_desktop_label_requires_a_bounded_owned_application_entry(self) -> None:
        applications = self.proc / "applications"
        applications.mkdir()
        entry = applications / "org.projectluma.Notes.desktop"
        entry.write_text(
            "[Desktop Entry]\nType=Application\nName=Notes\n",
            encoding="utf-8",
        )
        self.assertEqual(
            desktop_application(
                "org.projectluma.Notes",
                roots=(applications,),
                owner_uid=os.getuid(),
            ),
            (True, "Notes"),
        )
        self.assertEqual(
            desktop_application(
                "org.projectluma.Notes",
                roots=(applications,),
                owner_uid=os.getuid() + 1,
            ),
            (False, "org.projectluma.Notes"),
        )

    def test_root_owner_maps_to_overflow_uid_in_hardened_user_namespace(self) -> None:
        uid_map = self.proc / "uid_map"
        overflow_uid = self.proc / "overflowuid"
        uid_map.write_text("1000 1000 1\n", encoding="utf-8")
        overflow_uid.write_text("65534\n", encoding="utf-8")
        self.assertEqual(
            _visible_owner_uid(
                0,
                uid_map_path=uid_map,
                overflow_uid_path=overflow_uid,
            ),
            65534,
        )
        self.assertEqual(
            _visible_owner_uid(
                1000,
                uid_map_path=uid_map,
                overflow_uid_path=overflow_uid,
            ),
            1000,
        )


if __name__ == "__main__":
    unittest.main()
