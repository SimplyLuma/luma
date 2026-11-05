# SPDX-License-Identifier: Apache-2.0
"""Phone initramfs: choose ostree's default deployment from its boot entries."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "scripts/mobile/common/initramfs/luma-ostree-boot.sh"
HOOK = ROOT / "scripts/mobile/common/initramfs/hooks-extra/50-luma-ostree-default.sh"
SHELL = os.environ.get("LUMA_TEST_SH", "sh")
ANDROID = "console=tty0 androidboot.slot_suffix=_a ostree=/ostree/boot.0/luma/stale/0 quiet"


class OstreeDefault(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "boot/loader.1/entries").mkdir(parents=True)
        (self.root / "boot/loader").symlink_to("loader.1")
        (self.root / "ostree/boot.1.0/luma").mkdir(parents=True)
        (self.root / "ostree/boot.1").symlink_to("boot.1.0")
        self.entries = {}

    def tearDown(self):
        self.tmp.cleanup()

    def deployment(self, name, version, usable=True):
        commit, boot = name * 64, ("b" + name) * 32
        deploy = self.root / f"ostree/deploy/luma/deploy/{commit}.0"
        (deploy / "etc").mkdir(parents=True, exist_ok=True)
        systemd = deploy / "usr/lib/systemd/systemd"
        if usable:
            systemd.parent.mkdir(parents=True, exist_ok=True)
            systemd.write_text("")
            systemd.chmod(0o755)
        link = self.root / f"ostree/boot.1.0/luma/{boot}/0"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(f"../../../deploy/luma/deploy/{commit}.0")
        target = f"/ostree/boot.1/luma/{boot}/0"
        entry = self.root / f"boot/loader.1/entries/ostree-{version}.conf"
        entry.write_text(f"title Luma ({name})\nversion {version}\nlinux /ostree/luma-{boot}/vmlinuz\n"
                         f"options root=UUID=x rw ostree={target} quiet\n")
        self.entries[name] = (target, f"deploy/luma/deploy/{commit}.0")
        return target

    def grubenv(self, **values):
        path = self.root / "boot/grub2/grubenv"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "# GRUB Environment Block\n" + "".join(f"{k}={v}\n" for k, v in values.items())
        path.write_text(body + "#" * (1024 - len(body)))
        return path

    def env(self):
        path = self.root / "boot/grub2/grubenv"
        data = path.read_bytes()
        self.assertEqual(len(data), 1024)
        lines = data.decode().split("\n")
        self.assertEqual(lines[0], "# GRUB Environment Block")
        return dict(line.split("=", 1) for line in lines[1:] if "=" in line and not line.startswith("#"))

    def run_boot(self, cmdline=ANDROID):
        script = ('. "$1"; luma_ostree_resolve "$2" || { echo rc=1; exit 0; }; '
                  'luma_ostree_apply "$2" "$3" || echo apply=1; '
                  'printf "target=%s\\nindex=%s\\nreason=%s\\ncmdline=%s\\n" '
                  '"$LUMA_OSTREE_TARGET" "$LUMA_OSTREE_INDEX" "$LUMA_OSTREE_REASON" "$LUMA_OSTREE_CMDLINE"')
        out = subprocess.run([SHELL, "-c", script, "sh", str(LIB), str(self.root), cmdline],
                             env={**os.environ, "LUMA_OSTREE_LOG": "/dev/null",
                                  "LUMA_OSTREE_STATE": str(self.root / "run/ostree-boot")},
                             capture_output=True, text=True, check=True).stdout
        return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)

    def slot(self, name="a"):
        link = self.root / f"ostree/root.{name}"
        return os.readlink(link) if link.is_symlink() else None

    def test_highest_version_boots_and_slot_link_follows(self):
        self.deployment("1", 1)
        new = self.deployment("2", 2)
        (self.root / "ostree/root.a").symlink_to(self.entries["1"][1])
        result = self.run_boot()
        self.assertEqual(result["target"], new)
        self.assertEqual(result["reason"], "default")
        self.assertEqual(self.slot(), self.entries["2"][1])
        self.assertEqual(result["cmdline"],
                         f"console=tty0 androidboot.slot_suffix=_a quiet ostree={new}")
        self.assertIn(f"target={new}\n",
                      (self.root / "run/ostree-boot").read_text())

    def test_rollback_order_is_followed(self):
        self.deployment("1", 2)
        self.deployment("2", 1)
        self.assertEqual(self.run_boot()["target"], self.entries["1"][0])
        self.assertEqual(self.slot(), self.entries["1"][1])

    def test_versions_compare_as_numbers(self):
        self.deployment("1", 9)
        self.deployment("2", 10)
        self.assertEqual(self.run_boot()["target"], self.entries["2"][0])

    def test_counter_goes_down_while_default_boots(self):
        self.deployment("1", 1)
        self.deployment("2", 2)
        self.grubenv(boot_counter=3, boot_success=0, greenboot_next_deployment_id="x")
        self.assertEqual(self.run_boot()["index"], "0")
        self.assertEqual(self.env(), {"boot_counter": "2", "boot_success": "0",
                                      "greenboot_next_deployment_id": "x"})

    def test_counter_exhausted_boots_previous_until_greenboot_acts(self):
        self.deployment("1", 1)
        self.deployment("2", 2)
        self.grubenv(boot_counter=0, boot_success=0)
        for _ in range(2):
            result = self.run_boot()
            self.assertEqual(result["target"], self.entries["1"][0])
            self.assertEqual(result["reason"], "greenboot fallback")
            self.assertEqual(self.slot(), self.entries["1"][1])
            self.assertEqual(self.env()["boot_counter"], "-1")

    def test_successful_boot_only_resets_success(self):
        self.deployment("1", 1)
        self.deployment("2", 2)
        self.grubenv(boot_counter=3, boot_success=1)
        self.assertEqual(self.run_boot()["index"], "0")
        self.assertEqual(self.env(), {"boot_counter": "3", "boot_success": "0"})

    def test_unusable_default_falls_back(self):
        self.deployment("1", 1)
        self.deployment("2", 2, usable=False)
        result = self.run_boot()
        self.assertEqual(result["target"], self.entries["1"][0])
        self.assertTrue(result["reason"].startswith("skipped unusable"))

    def test_single_entry_ignores_exhausted_counter(self):
        self.deployment("1", 1)
        self.grubenv(boot_counter=0, boot_success=0)
        self.assertEqual(self.run_boot()["index"], "0")

    def test_no_entries_keeps_existing_link(self):
        (self.root / "ostree/root.a").symlink_to("deploy/luma/deploy/old.0")
        self.assertEqual(self.run_boot(), {"rc": "1"})
        self.assertEqual(self.slot(), "deploy/luma/deploy/old.0")

    def test_slot_b_and_plain_cmdline(self):
        self.deployment("1", 1)
        self.run_boot("androidboot.slot_suffix=_b")
        self.assertEqual(self.slot("b"), self.entries["1"][1])
        self.assertIsNone(self.slot("a"))
        result = self.run_boot("root=UUID=x ostree=/ostree/boot.0/luma/old/0")
        self.assertEqual(result["cmdline"], f"root=UUID=x ostree={self.entries['1'][0]}")
        self.assertIsNone(self.slot("a"))

    def test_hook_is_executable_and_sources_the_library(self):
        self.assertTrue(os.access(HOOK, os.X_OK))
        self.assertIn(". /usr/lib/luma/ostree-boot.sh", HOOK.read_text())


if __name__ == "__main__":
    unittest.main()
