# SPDX-License-Identifier: Apache-2.0
"""luma-greenboot-counter: arm staged updates, keep a counted-out fallback.

greenboot 0.16's own trigger asks rpm-ostreed for the staged deployment at
shutdown, when the daemon has usually exited and cannot start again, so
staged updates were never armed: no counter, no greenboot_next_deployment_id,
and after a fallback greenboot cleared the counter without rolling back, so
the failed deployment stayed the default.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

DATA = Path(__file__).resolve().parents[1] / "data"
COUNTER = DATA / "greenboot/luma-greenboot-counter"
HELPER = DATA / "greenboot/luma-greenboot-common.sh"

OLD = "a" * 64
NEW = "b" * 64
OTHER = "c" * 64


def status(*lines):
    return "\n".join(lines) + "\n"


STAGED = status(f"  luma {NEW}.0 (staged)", "    Version: 2", f"* luma {OLD}.0", "    Version: 1")
FALLBACK = status(f"  luma {NEW}.0 (pending)", "    Version: 2", f"* luma {OLD}.0", "    Version: 1")
ROLLED_BACK = status(f"* luma {OLD}.0", "    Version: 1", f"  luma {NEW}.0 (rollback)", "    Version: 2")

# A GRUB environment block as KEY=VALUE lines, with grub2-editenv's verbs.
EDITENV = r'''
file=$1; verb=$2; shift 2
[ -n "${EDITENV_FAIL:-}" ] && exit 1
touch "$file"
case "$verb" in
  list) grep -v '^#' "$file"; exit 0 ;;
  set) for kv; do grep -v "^${kv%%=*}=" "$file" > "$file.new"; echo "$kv" >> "$file.new"; mv "$file.new" "$file"; done ;;
  unset) for k; do grep -v "^$k=" "$file" > "$file.new"; mv "$file.new" "$file"; done ;;
  *) exit 2 ;;
esac
'''


@unittest.skipIf(shutil.which("bash") is None, "needs bash")
class Counter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.calls = self.root / "calls"
        self.grubenv = self.root / "grubenv"
        self.conf = self.root / "greenboot.conf"
        self.mounts = self.root / "mounts"
        self.mounts.write_text("/dev/sda3 /boot ext4 rw,relatime 0 0\n")
        self.marker = self.root / "fp6-qbootctl"
        self.status_file = self.root / "status"
        self.tool("grub2-editenv", EDITENV)
        self.tool("ostree", f'[ "$1 $2" = "admin status" ] || exit 2\n[ -z "${{OSTREE_FAIL:-}}" ] || {{ echo "sysroot busy" >&2; exit 1; }}\ncat "{self.status_file}"\n')
        self.tool("systemctl", f'echo "systemctl $*" >> "{self.calls}"\n[ "$*" = "is-enabled --quiet greenboot-healthcheck.service" ] || exit 2\nexit "${{GREENBOOT_ENABLED_STATUS:-0}}"\n')
        self.tool("rpm-ostree", f'echo "rpm-ostree $*" >> "{self.calls}"\nexit "${{ROLLBACK_STATUS:-0}}"\n')
        self.tool("mount", f'echo "mount $*" >> "{self.calls}"\n')
        self.tool("logger", f'echo "logger $*" >> "{self.calls}"\n')
        text = COUNTER.read_text()
        for original, path in [("/usr/lib/luma-update/luma-greenboot-common.sh", HELPER),
                               ("/etc/greenboot/greenboot.conf", self.conf),
                               ("/proc/self/mounts", self.mounts),
                               ("/usr/lib/greenboot/fp6-qbootctl", self.marker)]:
            self.assertIn(original, text)
            text = text.replace(original, str(path))
        self.script = self.root / "luma-greenboot-counter"
        self.script.write_text(text)

    def tearDown(self):
        self.tmp.cleanup()

    def tool(self, name, body):
        path = self.bin / name
        path.write_text("#!" + shutil.which("bash") + "\n" + body)
        path.chmod(0o755)

    def run_counter(self, verb, deployments=STAGED, **env):
        self.status_file.write_text(deployments)
        full = {"PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin", "LUMA_GREENBOOT_GRUBENV": str(self.grubenv)}
        full.update(env)
        return subprocess.run([shutil.which("bash"), str(self.script), verb], env=full, capture_output=True,
                              text=True, timeout=30)

    def env(self):
        if not self.grubenv.exists():
            return {}
        return dict(line.split("=", 1) for line in self.grubenv.read_text().splitlines() if "=" in line)

    def calls_text(self):
        return self.calls.read_text() if self.calls.exists() else ""

    # arm: shutdown with a staged deployment

    def test_staged_deployment_arms_counter_and_trigger(self):
        self.grubenv.write_text("boot_success=1\n")
        result = self.run_counter("arm")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.env(), {"boot_counter": "3", "boot_success": "0",
                                      "greenboot_next_deployment_id": NEW, "fallback": "1"})
        self.assertNotIn("rpm-ostree", self.calls_text())

    def test_attempts_follow_greenboot_conf(self):
        self.conf.write_text('GREENBOOT_MAX_BOOT_ATTEMPTS=5\n')
        self.run_counter("arm")
        self.assertEqual(self.env()["boot_counter"], "5")
        self.conf.write_text('GREENBOOT_MAX_BOOT_ATTEMPTS="banana"\n')
        self.run_counter("arm")
        self.assertEqual(self.env()["boot_counter"], "3")

    def test_nothing_staged_arms_nothing(self):
        result = self.run_counter("arm", status(f"* luma {OLD}.0", f"  luma {OTHER}.0 (rollback)"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.env(), {})

    def test_finalization_locked_is_not_armed(self):
        result = self.run_counter("arm", status(f"  luma {NEW}.0 (finalization locked)", f"* luma {OLD}.0"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.env(), {})

    def test_not_armed_without_greenboot_judging_boots(self):
        # Masked on the kernel command line (Pixel 9 Pro Fold boot images) or
        # disabled: nothing would clear the counter, so a good update would
        # fall back.
        result = self.run_counter("arm", GREENBOOT_ENABLED_STATUS="1")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.env(), {})
        self.assertIn("not enabled", result.stdout)

    def test_native_fp6_backend_is_left_alone(self):
        self.marker.touch()
        result = self.run_counter("arm")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.env(), {})

    def test_read_only_boot_is_remounted_and_restored(self):
        self.mounts.write_text("/dev/sda3 /boot ext4 ro,relatime 0 0\n")
        result = self.run_counter("arm")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.env()["greenboot_next_deployment_id"], NEW)
        mounts = [line for line in self.calls_text().splitlines() if line.startswith("mount")]
        self.assertEqual(mounts, ["mount -o remount,rw /boot", "mount -o remount,bind,ro /boot"])

    def test_writable_boot_is_not_remounted(self):
        self.run_counter("arm")
        self.assertNotIn("mount ", self.calls_text())

    def test_unreadable_sysroot_is_an_error_in_the_journal(self):
        result = self.run_counter("arm", OSTREE_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("logger --priority user.err --tag luma-greenboot", self.calls_text())
        self.assertEqual(self.env(), {})

    def test_failed_write_is_an_error_in_the_journal(self):
        result = self.run_counter("arm", EDITENV_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot arm the boot counter", self.calls_text())

    # fallback: boot after the counter ran out

    def test_counted_out_fallback_rolls_back_and_clears_greenboot_state(self):
        self.grubenv.write_text(f"boot_counter=-1\nboot_success=0\ngreenboot_next_deployment_id={NEW}\nfallback=1\n")
        result = self.run_counter("fallback", FALLBACK)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rpm-ostree rollback", self.calls_text())
        self.assertEqual(self.env(), {"boot_success": "0"})

    def test_fallback_without_greenboot_trigger_still_rolls_back(self):
        # The case greenboot cannot see: no greenboot_next_deployment_id.
        self.grubenv.write_text("boot_counter=-1\nboot_success=0\n")
        result = self.run_counter("fallback", FALLBACK)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls_text().count("rpm-ostree rollback"), 1)
        self.assertNotIn("boot_counter", self.env())

    def test_already_default_is_not_rolled_back_again(self):
        # A second rollback would make the failed deployment the default again.
        self.grubenv.write_text("boot_counter=-1\nboot_success=0\n")
        result = self.run_counter("fallback", ROLLED_BACK)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("rpm-ostree", self.calls_text())

    def test_counting_boots_are_left_to_greenboot(self):
        for counter in ("2", "0"):
            self.grubenv.write_text(f"boot_counter={counter}\nboot_success=0\n")
            self.run_counter("fallback", FALLBACK)
        self.grubenv.write_text("boot_success=1\n")
        self.run_counter("fallback", FALLBACK)
        self.assertNotIn("rpm-ostree", self.calls_text())

    def test_failed_rollback_keeps_the_counter(self):
        self.grubenv.write_text(f"boot_counter=-1\nboot_success=0\ngreenboot_next_deployment_id={NEW}\n")
        result = self.run_counter("fallback", FALLBACK, ROLLBACK_STATUS="1")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.env()["boot_counter"], "-1")
        self.assertEqual(self.env()["greenboot_next_deployment_id"], NEW)
        self.assertIn("rpm-ostree rollback failed", self.calls_text())

    def test_fallback_needs_greenboot_and_no_fp6_backend(self):
        self.grubenv.write_text("boot_counter=-1\nboot_success=0\n")
        self.run_counter("fallback", FALLBACK, GREENBOOT_ENABLED_STATUS="1")
        self.marker.touch()
        self.run_counter("fallback", FALLBACK)
        self.assertNotIn("rpm-ostree", self.calls_text())

    def test_unknown_verb(self):
        self.assertEqual(self.run_counter("bless").returncode, 2)


class Units(unittest.TestCase):
    def unit(self, name):
        return (DATA / "systemd" / name).read_text().splitlines()

    def test_arm_runs_at_shutdown_before_finalization(self):
        lines = self.unit("luma-greenboot-arm.service")
        # Stop order is the reverse of start order: After= means this stops
        # first, while the deployment is still staged.
        self.assertIn("After=ostree-finalize-staged.service", lines)
        self.assertIn("ExecStop=/usr/lib/luma-update/luma-greenboot-counter arm", lines)
        self.assertIn("DefaultDependencies=no", lines)
        self.assertIn("WantedBy=ostree-finalize-staged.service", lines)
        self.assertFalse([line for line in lines if line.startswith("ExecStart=")])

    def test_fallback_runs_before_greenboot(self):
        lines = self.unit("luma-greenboot-fallback.service")
        self.assertIn("Before=greenboot-healthcheck.service luma-update-boot.service", lines)
        self.assertIn("ExecStart=/usr/lib/luma-update/luma-greenboot-counter fallback", lines)

    def test_both_enabled_by_preset_and_packaged(self):
        preset = (DATA / "presets/80-luma-update.preset").read_text().splitlines()
        spec = (Path(__file__).resolve().parents[3] / "packaging/rpm/luma-update.spec")
        for unit in ("luma-greenboot-arm.service", "luma-greenboot-fallback.service"):
            self.assertIn(f"enable {unit}", preset)
            if spec.exists():
                self.assertIn(f"%{{_unitdir}}/{unit}", spec.read_text())

    def test_grub_boot_success_yields_to_greenboot(self):
        for unit in ("grub-boot-success.service", "grub-boot-success.timer"):
            dropin = (DATA / "systemd-user" / f"{unit}.d/50-luma-greenboot.conf").read_text().splitlines()
            self.assertIn("[Unit]", dropin)
            self.assertIn("ConditionPathExists=!/usr/lib/systemd/system/greenboot-healthcheck.service", dropin)


if __name__ == "__main__":
    unittest.main()
