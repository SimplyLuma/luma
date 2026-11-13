# SPDX-License-Identifier: Apache-2.0
import errno
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from luma_update import device_layer as dl

BASE = "a" * 64
IMAGE_BUILT = 1794520800    # 2026-11-12T22:40:00Z: a fresh phone's clock
KIT_SIGNED = 1794536071     # 2026-11-13T02:14:31Z
LAYERED = "b" * 64


def kit_json(**overrides):
    data = {
        "schema": 1, "device": "fairphone/fp6", "name": "Fairphone 6", "version": "20261107.1",
        "compatible": ["fairphone,fp6"],
        "repo": {"id": "luma-fairphone-fp6", "url": "https://dl.simplyluma.com/os/devices/aarch64/fairphone-fp6/"},
        "install": ["kernel-modules-luma-fp6", "luma-fp6-platform"],
        "replace": ["ModemManager"],
        "packages": ["kernel-modules-luma-fp6", "luma-fp6-platform", "ModemManager"],
    }
    data.update(overrides)
    return json.dumps(data)


def _librepo_loads():
    try:
        dl.ctypes.CDLL("librepo.so.0")
        return True
    except OSError:
        return False


class FakeSystem:
    """rpm-ostree, gpgv and systemctl as the service sees them."""

    def __init__(self, root: Path):
        self.root = root
        self.calls = []
        self.booted = BASE
        self.staged = None
        self.good_signatures = True
        self.signed = {}  # signature file name -> creation time gpgv reports
        self.fail_install = False
        self.repos_during_install = None
        self.devices = {}
        self.write_origin(BASE, "")

    def write_origin(self, checksum, extra):
        path = self.root / "sysroot/ostree/deploy/luma/deploy" / f"{checksum}.0.origin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[origin]\nbaserefspec=luma:luma/1/aarch64/nightly\n" + extra)

    def boot_layered(self):
        self.write_origin(LAYERED, "\n[packages]\nrequested=kernel-modules-luma-fp6;luma-fp6-platform;\n\n"
                                   "[overrides]\nreplace=repo=luma-fairphone-fp6,ModemManager;\n")
        self.booted, self.staged = LAYERED, None

    def __call__(self, argv):
        argv = list(argv)
        self.calls.append(argv)
        ok = subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "gpgv":
            if not self.good_signatures:
                return subprocess.CompletedProcess(argv, 1, "", "BAD signature")
            created = self.signed.get(Path(argv[-2]).name)
            if created is None:
                return ok
            return subprocess.CompletedProcess(argv, 0, f"[GNUPG:] NEWSIG\n[GNUPG:] GOODSIG C8CE1A1B51D96CE2 Luma\n"
                                                        f"[GNUPG:] VALIDSIG {'F' * 40} 2026-11-13 {created} 0 4 0 1 10 00 "
                                                        f"{'F' * 40}\n", "")
        if argv[:3] == ["rpm-ostree", "status", "--json"]:
            deployments = [{"osname": "luma", "checksum": self.booted, "serial": 0, "booted": True,
                            "requested-packages": []}]
            if self.staged:
                deployments.insert(0, {"osname": "luma", "checksum": self.staged, "serial": 0, "booted": False,
                                       "staged": True,
                                       "requested-packages": ["kernel-modules-luma-fp6", "luma-fp6-platform"]})
            return subprocess.CompletedProcess(argv, 0, json.dumps({"deployments": deployments}), "")
        if argv[:2] == ["rpm-ostree", "install"] or argv[:3] == ["rpm-ostree", "override", "replace"]:
            self.repos_during_install = sorted(p.name for p in (self.root / "etc/yum.repos.d").glob("*.repo"))
            if self.fail_install:
                return subprocess.CompletedProcess(argv, 1, "", "error: Packages not found: luma-fp6-platform")
            self.staged = LAYERED
        if argv[:2] == ["blkid", "-U"] and argv[2] in self.devices:
            return subprocess.CompletedProcess(argv, 0, self.devices[argv[2]] + "\n", "")
        return ok


class DeviceLayerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "etc/luma").mkdir(parents=True)
        (self.root / "etc/luma/device-kit.conf").write_text("# test\nsource=/kitfs\n")
        repos = self.root / "etc/yum.repos.d"
        repos.mkdir(parents=True)
        (repos / "fedora.repo").write_text("[fedora]\n")
        (repos / "fedora-updates.repo").write_text("[updates]\n")
        (self.root / "etc/pki/ostree").mkdir(parents=True)
        (self.root / "etc/pki/ostree/luma-release.gpg").write_bytes(b"key")
        (self.root / "etc/pki/rpm-gpg").mkdir(parents=True)
        (self.root / "etc/pki/rpm-gpg/RPM-GPG-KEY-luma-os-release").write_text("armored key")
        compatible = self.root / "sys/firmware/devicetree/base/compatible"
        compatible.parent.mkdir(parents=True)
        compatible.write_bytes(b"fairphone,fp6\0qcom,milos\0")
        (self.root / "dev").mkdir()
        (self.root / "dev/fuse").write_text("")
        self.kit = self.root / "kitfs/luma-device-kit"
        (self.kit / "repo/repodata").mkdir(parents=True)
        (self.kit / "repo/repodata/repomd.xml").write_text("<repomd/>")
        (self.kit / "repo/repodata/repomd.xml.asc").write_text("sig")
        (self.kit / "kit.json").write_text(kit_json())
        (self.kit / "kit.json.asc").write_text("sig")
        self.system = FakeSystem(self.root)
        self.imported = []

        def import_key(key, gpgdir):
            self.imported.append((key, gpgdir))
            (gpgdir / "C8CE1A1B51D96CE2.pub").write_bytes(key.read_bytes())

        self.clock = [IMAGE_BUILT]
        self.clock_set = []

        def set_clock(seconds):
            self.clock_set.append(seconds)
            self.clock[0] = seconds

        self.layer = dl.DeviceLayer(dl.Paths(root=self.root), run=self.system, sleep=lambda _: None,
                                    import_key=import_key, now=lambda: self.clock[0], set_clock=set_clock)

    def tearDown(self):
        self.tmp.cleanup()

    def commands(self, prefix):
        return [c for c in self.system.calls if c[:len(prefix)] == prefix]

    def transactions(self):
        return [c for c in self.system.calls
                if c[:2] == ["rpm-ostree", "install"] or c[:3] == ["rpm-ostree", "override", "replace"]]

    def repo_file(self):
        return (self.root / "etc/yum.repos.d/luma-fairphone-fp6.repo").read_text()

    def test_first_boot_layers_offline_and_restarts_once(self):
        self.assertEqual(self.layer.apply(), 0)
        # One transaction: the OS is assembled and deployed once.
        self.assertEqual(self.transactions(), [[
            "rpm-ostree", "override", "replace", "--experimental", "--from", "repo=luma-fairphone-fp6",
            "ModemManager", "--install=kernel-modules-luma-fp6", "--install=luma-fp6-platform"]])
        # Only the device repository is configured during the transaction ...
        self.assertEqual(self.system.repos_during_install, ["luma-fairphone-fp6.repo"])
        # ... and afterwards every repository is back, the device one online.
        names = sorted(p.name for p in (self.root / "etc/yum.repos.d").glob("*.repo"))
        self.assertEqual(names, ["fedora-updates.repo", "fedora.repo", "luma-fairphone-fp6.repo"])
        text = self.repo_file()
        self.assertIn("baseurl=https://dl.simplyluma.com/os/devices/aarch64/fairphone-fp6/\n", text)
        self.assertIn("repo_gpgcheck=1\n", text)
        self.assertIn("gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-luma-os-release\n", text)
        self.assertIn("includepkgs=ModemManager,kernel-modules-luma-fp6,luma-fp6-platform\n", text)
        self.assertEqual(self.commands(["systemctl"]), [["systemctl", "--no-block", "reboot"]])
        verified = [c[-1] for c in self.commands(["gpgv"])]
        self.assertTrue(verified[0].endswith("luma-device-kit/kit.json"))
        self.assertTrue(verified[1].endswith("var/lib/luma-device-layer/repo/repodata/repomd.xml"))
        self.assertFalse((self.root / "var/lib/luma-device-layer/layered").exists())

    def test_staged_repository_carries_the_key_libdnf_checks(self):
        """libdnf checks a file:// repository's repomd.xml against <baseurl>/gpgdir only."""
        (self.kit / "repo/gpgdir").mkdir()
        (self.kit / "repo/gpgdir/0123456789ABCDEF.pub").write_text("not ours")
        repos_seen = []
        call = self.system.__call__

        def run(argv):
            if list(argv[:3]) == ["rpm-ostree", "override", "replace"]:
                staged = self.root / "var/lib/luma-device-layer/repo"
                repos_seen.append((self.repo_file(), sorted(p.name for p in (staged / "gpgdir").iterdir())))
            return call(argv)

        self.layer.run = run
        self.assertEqual(self.layer.apply(), 0)
        staged = self.root / "var/lib/luma-device-layer/repo"
        self.assertEqual(self.imported, [(self.root / "etc/pki/rpm-gpg/RPM-GPG-KEY-luma-os-release",
                                          staged / "gpgdir")])
        text, keys = repos_seen[0]
        self.assertIn(f"baseurl=file://{staged}\n".replace(str(self.root), ""), text)
        self.assertEqual(keys, ["C8CE1A1B51D96CE2.pub"])

    def test_missing_rpm_key_is_refused(self):
        (self.root / "etc/pki/rpm-gpg/RPM-GPG-KEY-luma-os-release").unlink()
        self.assertEqual(self.layer.apply(), 1)
        self.assertEqual(self.commands(["rpm-ostree", "override"]), [])
        self.assertIn("no release key at", self.layer.load_state()["last_error"])

    @unittest.skipUnless(_librepo_loads(), "librepo is not installed")
    def test_librepo_import_reports_a_bad_key(self):
        bad = self.root / "bad.asc"
        bad.write_text("not a key")
        (self.root / "gpgdir").mkdir()
        with self.assertRaises(dl.LayerError):
            dl.librepo_import_key(bad, self.root / "gpgdir")

    def test_install_only_kit_is_one_install(self):
        (self.kit / "kit.json").write_text(kit_json(replace=[]))
        self.assertEqual(self.layer.apply(), 0)
        self.assertEqual(self.transactions(),
                         [["rpm-ostree", "install", "--idempotent", "kernel-modules-luma-fp6", "luma-fp6-platform"]])

    def test_layer_staged_by_a_stopped_run_restarts_into_it(self):
        """A run stopped after its transaction (timeout, power) left the layer staged."""
        self.system.staged = "c" * 64
        self.assertEqual(self.layer.apply(), 0)
        self.assertEqual(self.transactions(), [])
        self.assertEqual(self.commands(["systemctl"]), [["systemctl", "--no-block", "reboot"]])
        self.assertEqual(self.layer.load_state()["status"], "staged")

    def test_second_boot_marks_the_layer_and_does_nothing_else(self):
        self.layer.apply()
        self.system.boot_layered()
        self.system.calls.clear()
        self.assertEqual(self.layer.apply(), 0)
        self.assertTrue((self.root / "var/lib/luma-device-layer/layered").exists())
        self.assertEqual(self.transactions(), [])
        self.assertEqual(self.commands(["systemctl"]), [])
        self.assertFalse((self.root / "var/lib/luma-device-layer/repo").exists())
        self.assertIn("baseurl=https://", self.repo_file())
        self.system.calls.clear()
        self.assertEqual(self.layer.apply(), 0)
        self.assertEqual(self.system.calls, [])

    def test_rolled_back_layer_is_retried_once_then_never(self):
        self.assertEqual(self.layer.apply(), 0)
        self.system.staged = None  # greenboot rolled back to the generic deployment
        self.assertEqual(self.layer.apply(), 0)
        self.system.staged = None
        self.system.calls.clear()
        self.assertEqual(self.layer.apply(), 1)
        self.assertEqual(self.transactions(), [])
        self.assertEqual(self.commands(["systemctl"]), [])
        self.assertIn("not trying again", self.layer.load_state()["last_error"])
        self.system.calls.clear()
        self.assertEqual(self.layer.apply(retry=True), 0)
        self.assertEqual(len(self.transactions()), 1)

    # -- clock -------------------------------------------------------------------

    def timesync_clock(self):
        return self.root / "var/lib/systemd/timesync/clock"

    def test_clock_before_the_kit_signature_is_stepped_forward(self):
        """No RTC and no network: the clock starts at the image's build time."""
        self.system.signed = {"kit.json.asc": KIT_SIGNED, "repomd.xml.asc": KIT_SIGNED - 600}
        steps = []
        call = self.system.__call__

        def run(argv):
            if list(argv[:3]) == ["rpm-ostree", "override", "replace"]:
                steps.append(self.clock[0])
            return call(argv)

        self.layer.run = run
        self.assertEqual(self.layer.apply(), 0)
        self.assertEqual(self.clock_set, [KIT_SIGNED])
        self.assertEqual(steps, [KIT_SIGNED])
        self.assertEqual(int(self.timesync_clock().stat().st_mtime), KIT_SIGNED)

    def test_clock_uses_the_newest_verified_signature(self):
        self.system.signed = {"kit.json.asc": KIT_SIGNED, "repomd.xml.asc": KIT_SIGNED + 60}
        self.assertEqual(self.layer.apply(), 0)
        self.assertEqual(self.clock_set, [KIT_SIGNED + 60])

    def test_clock_already_later_is_untouched(self):
        self.system.signed = {"kit.json.asc": KIT_SIGNED, "repomd.xml.asc": KIT_SIGNED}
        self.clock[0] = KIT_SIGNED + 86400
        self.assertEqual(self.layer.apply(), 0)
        self.assertEqual(self.clock_set, [])
        self.assertFalse(self.timesync_clock().exists())

    def test_kept_clock_file_only_moves_forward(self):
        self.system.signed = {"kit.json.asc": KIT_SIGNED}
        self.timesync_clock().parent.mkdir(parents=True)
        self.timesync_clock().touch()
        os.utime(self.timesync_clock(), (KIT_SIGNED + 5, KIT_SIGNED + 5))
        self.assertEqual(self.layer.apply(), 0)
        self.assertEqual(int(self.timesync_clock().stat().st_mtime), KIT_SIGNED + 5)

    def test_absurd_signature_time_fails_before_the_transaction_and_is_retried(self):
        self.system.signed = {"kit.json.asc": IMAGE_BUILT + 3 * 366 * 86400}
        self.assertEqual(self.layer.apply(), 1)
        self.assertEqual(self.clock_set, [])
        self.assertEqual(self.transactions(), [])
        state = self.layer.load_state()
        self.assertIn("more than two years after the clock", state["last_error"])
        self.assertEqual(state.get("attempts", 0), 0)

    def test_iso_signature_timestamps_are_read(self):
        status = f"[GNUPG:] VALIDSIG {'F' * 40} 2026-11-13 20261113T021431 0 4 0 1 10 00 {'F' * 40}\n"
        self.assertEqual(dl.signature_time(status), KIT_SIGNED)
        self.assertIsNone(dl.signature_time("[GNUPG:] GOODSIG C8CE1A1B51D96CE2 Luma\n"))

    # -- attempts ------------------------------------------------------------------

    def test_failed_transactions_are_not_staged_attempts(self):
        """A transaction that fails before staging is tried again on every boot."""
        self.system.fail_install = True
        for _ in range(dl.MAX_ATTEMPTS + 1):
            self.assertEqual(self.layer.apply(), 1)
            self.assertIn("Packages not found", self.layer.load_state()["last_error"])
        self.assertEqual(self.layer.load_state().get("attempts", 0), 0)
        self.system.fail_install = False
        self.assertEqual(self.layer.apply(), 0)
        self.assertEqual(self.layer.load_state()["attempts"], 1)
        self.assertEqual(len(self.transactions()), dl.MAX_ATTEMPTS + 2)

    def test_a_staged_layer_found_again_is_counted_once(self):
        self.assertEqual(self.layer.apply(reboot=False), 0)
        self.assertEqual(self.layer.apply(), 0)  # stopped before the restart; found again
        self.assertEqual(self.layer.load_state()["attempts"], 1)
        self.assertEqual(len(self.transactions()), 1)

    def test_failed_transaction_restores_repositories_and_does_not_restart(self):
        self.system.fail_install = True
        self.assertEqual(self.layer.apply(), 1)
        self.assertEqual(self.commands(["systemctl"]), [])
        names = sorted(p.name for p in (self.root / "etc/yum.repos.d").glob("*.repo"))
        self.assertEqual(names, ["fedora-updates.repo", "fedora.repo", "luma-fairphone-fp6.repo"])
        self.assertIn("Packages not found", self.layer.load_state()["last_error"])

    def test_interrupted_run_gets_its_repositories_back(self):
        repos = self.root / "etc/yum.repos.d"
        (repos / "fedora.repo").rename(repos / "fedora.repo.luma-aside")
        self.layer.restore_repos()
        self.assertTrue((repos / "fedora.repo").exists())
        self.assertEqual(list(repos.glob("*.luma-aside")), [])

    def test_repositories_never_move_across_mounts(self):
        """/etc and /var are separate mounts: a rename between them fails with EXDEV."""
        real = os.replace

        def replace(src, dst):
            if Path(src).parent != Path(dst).parent and "etc" in {Path(src).parts[-2], Path(dst).parts[-2]}:
                raise OSError(errno.EXDEV, "Invalid cross-device link", str(src))
            return real(src, dst)

        with mock.patch.object(dl.os, "replace", replace):
            self.assertEqual(self.layer.apply(), 0)
        self.assertEqual(self.system.repos_during_install, ["luma-fairphone-fp6.repo"])
        names = sorted(p.name for p in (self.root / "etc/yum.repos.d").iterdir())
        self.assertEqual(names, ["fedora-updates.repo", "fedora.repo", "luma-fairphone-fp6.repo"])

    def test_kit_for_another_device_is_refused(self):
        (self.root / "sys/firmware/devicetree/base/compatible").write_bytes(b"google,tokay\0google,zumapro\0")
        self.assertEqual(self.layer.apply(), 1)
        self.assertEqual(self.commands(["rpm-ostree"]), [])
        self.assertIn("this device is google,tokay", self.layer.load_state()["last_error"])

    def test_unsigned_kit_is_refused(self):
        self.system.good_signatures = False
        self.assertEqual(self.layer.apply(), 1)
        self.assertEqual(self.commands(["rpm-ostree"]), [])
        self.assertIn("not signed by the Luma OS Release key", self.layer.load_state()["last_error"])

    def test_missing_signature_is_refused(self):
        (self.kit / "kit.json.asc").unlink()
        self.assertEqual(self.layer.apply(), 1)
        self.assertIn("has no signature", self.layer.load_state()["last_error"])

    def test_no_kit_is_not_an_error(self):
        (self.kit / "kit.json").unlink()
        self.assertEqual(self.layer.apply(), 0)
        self.assertEqual(self.commands(["rpm-ostree"]), [])
        (self.root / "etc/luma/device-kit.conf").unlink()
        self.assertEqual(self.layer.apply(), 0)

    def test_absent_filesystem_is_not_an_error(self):
        (self.root / "etc/luma/device-kit.conf").write_text("source=UUID=032D-57AD\n")
        self.assertEqual(self.layer.apply(wait=0), 0)
        self.assertEqual(self.commands(["blkid"]), [["blkid", "-U", "032D-57AD"]])

    def kit_on(self, where):
        """The kit on LumaBoot (/dev/loop0p1), its files at ROOT/where."""
        (self.root / "etc/luma/device-kit.conf").write_text("source=UUID=032D-57AD\n")
        self.system.devices["032D-57AD"] = "/dev/loop0p1"
        target = self.root / where
        target.mkdir(parents=True, exist_ok=True)
        os.rename(self.kit, target / "luma-device-kit")

    def mountinfo(self, *lines):
        path = self.root / "proc/self/mountinfo"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(line + "\n" for line in lines))

    def test_kit_filesystem_mounted_read_write_by_the_initramfs_is_read_in_place(self):
        # postmarketOS mounts its boot partition on /boot (rw) when /etc/fstab is
        # empty; mounting it read-only again fails with "would change RO state".
        self.kit_on("boot")
        self.mountinfo(
            "1 0 259:2 /ostree/deploy/luma/deploy/x.0 / rw,relatime shared:1 - ext4 /dev/loop0p2 rw",
            "92 1 259:1 / /boot rw,nosuid,nodev,noexec,relatime shared:40 - vfat /dev/loop0p1 rw,fmask=0077")
        self.assertEqual(self.layer.apply(), 0)
        self.assertEqual(self.commands(["mount"]), [])
        self.assertEqual(self.commands(["umount"]), [])
        self.assertEqual(len(self.transactions()), 1)
        self.assertEqual(self.commands(["gpgv"])[0][-1], str(self.root / "boot/luma-device-kit/kit.json"))
        self.assertTrue((self.root / "boot/luma-device-kit/kit.json").is_file())

    def test_existing_mount_is_found_by_device_number(self):
        self.kit_on("media/kit dir")
        self.mountinfo("40 1 259:1 / /media/kit\\040dir rw shared:9 - vfat /dev/block/259:1 rw")
        with mock.patch.object(dl.DeviceLayer, "device_number", return_value="259:1"):
            self.assertEqual(self.layer.apply(), 0)
        self.assertEqual(self.commands(["mount"]) + self.commands(["umount"]), [])
        self.assertEqual(self.commands(["gpgv"])[0][-1], str(self.root / "media/kit dir/luma-device-kit/kit.json"))

    def test_kit_is_mounted_read_only_and_only_that_mount_is_undone(self):
        # A bind mount of a subdirectory is not the kit filesystem's top.
        self.kit_on("run/luma-device-kit")
        self.mountinfo("50 1 259:1 /luma-device-kit /srv/kit rw - vfat /dev/loop0p1 rw")
        self.system.fail_install = True
        self.assertEqual(self.layer.apply(), 1)
        mnt = str(self.root / "run/luma-device-kit")
        self.assertEqual(self.commands(["mount"]),
                         [["mount", "-o", "ro,nodev,nosuid,noexec", "/dev/loop0p1", mnt]])
        self.assertEqual(self.commands(["umount"]), [["umount", mnt]])

    def test_a_mount_left_on_the_kit_mount_point_is_reused_and_undone(self):
        self.kit_on("run/luma-device-kit")
        self.mountinfo("60 1 259:1 / /run/luma-device-kit ro - vfat /dev/loop0p1 ro")
        self.assertEqual(self.layer.apply(), 0)
        self.assertEqual(self.commands(["mount"]), [])
        self.assertEqual(self.commands(["umount"]), [["umount", str(self.root / "run/luma-device-kit")]])

    # -- fuse ------------------------------------------------------------------

    RELEASE = "7.1.2-luma-fp-ims1.gmu2.selinux1"

    def fuse_system(self, signed=True, modprobe=False):
        """No /dev/fuse; fuse.ko only in the kit's kernel-modules package."""
        (self.root / "dev/fuse").unlink()
        (self.root / "proc/sys/kernel").mkdir(parents=True)
        (self.root / "proc/sys/kernel/osrelease").write_text(self.RELEASE + "\n")
        packages = self.kit / "repo/Packages"
        packages.mkdir()
        (packages / "luma-fp6-platform.rpm").write_bytes(b"rpm")
        (packages / "kernel-modules-luma-fp6.rpm").write_bytes(b"rpm")
        member = f"/usr/lib/modules/{self.RELEASE}/kernel/fs/fuse/fuse.ko"
        call = self.system.__call__
        root = self.root

        def run(argv):
            argv = list(argv)
            if argv[0] == "modprobe":
                self.system.calls.append(argv)
                if modprobe:
                    (root / "dev/fuse").write_text("")
                    return subprocess.CompletedProcess(argv, 0, "", "")
                return subprocess.CompletedProcess(argv, 1, "", "modprobe: FATAL: Module fuse not found")
            if argv[:2] == ["rpm", "-qp"]:
                self.system.calls.append(argv)
                files = (f"/usr/lib/modules/{self.RELEASE}/kernel/drivers/x.ko\n{member}\n"
                         "/usr/lib/modules/6.0.0/kernel/fs/fuse/fuse.ko\n"
                         if argv[-1].endswith("kernel-modules-luma-fp6.rpm") else "/usr/libexec/x\n")
                return subprocess.CompletedProcess(argv, 0, files, "")
            if argv[:1] == ["rpmkeys"] and "-K" in argv:
                self.system.calls.append(argv)
                out = "digests signatures OK" if signed else "digests SIGNATURES NOT OK"
                return subprocess.CompletedProcess(argv, 0 if signed else 1, f"{argv[-1]}: {out}\n", "")
            if argv[:2] == ["sh", "-c"] and "rpm2cpio" in argv[2]:
                self.system.calls.append(argv)
                target = Path(argv[5]) / argv[6].lstrip("./")
                target.parent.mkdir(parents=True)
                target.write_bytes(b"\x7fELF fuse")
                return subprocess.CompletedProcess(argv, 0, "", "")
            if argv[0] == "insmod":
                self.system.calls.append(argv)
                self.insmodded = Path(argv[1]).read_bytes()
                (root / "dev/fuse").write_text("")
                return subprocess.CompletedProcess(argv, 0, "", "")
            return call(argv)

        self.layer.run = run

    def test_missing_fuse_is_loaded_from_the_kit_before_the_transaction(self):
        self.fuse_system()
        self.assertEqual(self.layer.apply(), 0)
        names = [c[0] if c[0] != "rpm-ostree" else " ".join(c[:3]) for c in self.system.calls]
        self.assertLess(names.index("insmod"), names.index("rpm-ostree override replace"))
        self.assertEqual(self.insmodded, b"\x7fELF fuse")
        verified = [c for c in self.system.calls if c[0] == "rpmkeys" and "-K" in c]
        self.assertEqual(len(verified), 1)
        self.assertTrue(verified[0][-1].endswith("var/lib/luma-device-layer/repo/Packages/kernel-modules-luma-fp6.rpm"))
        self.assertFalse((self.root / "var/lib/luma-device-layer/fuse.ko").exists())
        self.assertEqual(len(self.transactions()), 1)

    def test_fuse_from_the_running_system_needs_no_kit_package(self):
        self.fuse_system(modprobe=True)
        self.assertEqual(self.layer.apply(), 0)
        self.assertEqual(self.commands(["insmod"]), [])
        self.assertEqual(len(self.transactions()), 1)

    def test_unsigned_fuse_package_is_refused(self):
        self.fuse_system(signed=False)
        self.assertEqual(self.layer.apply(), 1)
        self.assertEqual(self.commands(["insmod"]), [])
        self.assertEqual(self.transactions(), [])
        self.assertIn("not signed", self.layer.load_state()["last_error"])

    def test_no_fuse_for_the_running_kernel_is_an_error_before_the_transaction(self):
        self.fuse_system()
        (self.root / "proc/sys/kernel/osrelease").write_text("7.9.9\n")
        self.assertEqual(self.layer.apply(), 1)
        self.assertEqual(self.transactions(), [])
        self.assertIn("no package in the kit has fuse.ko for 7.9.9", self.layer.load_state()["last_error"])

    @unittest.skipUnless(hasattr(dl, "decompress"), "no decompress")
    def test_module_decompression(self):
        import gzip
        import lzma
        self.assertEqual(dl.decompress(gzip.compress(b"ko"), ".gz"), b"ko")
        self.assertEqual(dl.decompress(lzma.compress(b"ko"), ".xz"), b"ko")
        self.assertEqual(dl.decompress(b"ko", ""), b"ko")
        try:
            from compression import zstd
        except ImportError:
            return
        self.assertEqual(dl.decompress(zstd.compress(b"ko"), ".zst"), b"ko")

    # -- staged deployment labels ----------------------------------------------

    def labelled(self, labels):
        """SELinux on; /usr/lib/systemd/systemd of the staged deployment reads as labels[i]."""
        (self.root / "sys/fs/selinux").mkdir(parents=True)
        (self.root / "sys/fs/selinux/enforce").write_text("1")
        top = self.root / "sysroot/ostree/deploy/luma/deploy" / f"{LAYERED}.0"
        (top / "usr/lib/systemd").mkdir(parents=True)
        (top / "usr/lib/systemd/systemd").write_text("")
        (top / "usr/etc/selinux/targeted/contexts/files").mkdir(parents=True)
        (top / "usr/etc/selinux/config").write_text("SELINUX=enforcing\nSELINUXTYPE=targeted\n")
        (top / "usr/etc/selinux/targeted/contexts/files/file_contexts").write_text("")
        seen = []

        def label(path):
            seen.append(path)
            relabelled = bool(self.commands(["unshare"]))
            return labels[1 if relabelled else 0]

        self.layer.label = label
        return top, seen

    def test_correct_labels_are_left_alone(self):
        top, seen = self.labelled(["system_u:object_r:init_exec_t:s0", None])
        self.assertEqual(self.layer.apply(), 0)
        self.assertEqual(seen, [top / "usr/lib/systemd/systemd"])
        self.assertEqual(self.commands(["unshare"]), [])
        self.assertEqual(self.commands(["systemctl"]), [["systemctl", "--no-block", "reboot"]])

    def test_mislabelled_staged_usr_is_relabelled_before_the_restart(self):
        top, _ = self.labelled(["system_u:object_r:system_conf_t:s0", "system_u:object_r:init_exec_t:s0"])
        self.assertEqual(self.layer.apply(), 0)
        relabel = self.commands(["unshare"])
        self.assertEqual(len(relabel), 1)
        self.assertEqual(relabel[0][-3:], ["/sysroot", str(top),
                                           str(top / "usr/etc/selinux/targeted/contexts/files/file_contexts")])
        self.assertIn('setfiles -F -r "$2" "$3" "$2/usr"', relabel[0][6])
        self.assertLess(self.system.calls.index(relabel[0]),
                        self.system.calls.index(["systemctl", "--no-block", "reboot"]))

    def test_layer_staged_by_a_stopped_run_is_label_checked_too(self):
        self.system.staged = LAYERED
        self.labelled([None, "system_u:object_r:init_exec_t:s0"])
        self.assertEqual(self.layer.apply(), 0)
        self.assertEqual(len(self.commands(["unshare"])), 1)
        self.assertEqual(self.transactions(), [])

    def test_unrepairable_labels_drop_the_staged_deployment_and_do_not_restart(self):
        self.labelled(["system_u:object_r:system_conf_t:s0", "system_u:object_r:system_conf_t:s0"])
        self.assertEqual(self.layer.apply(), 1)
        self.assertEqual(self.commands(["rpm-ostree", "cleanup"]), [["rpm-ostree", "cleanup", "--pending"]])
        self.assertEqual(self.commands(["systemctl"]), [])
        self.assertIn("still system_u:object_r:system_conf_t:s0", self.layer.load_state()["last_error"])

    def test_selinux_config_parser(self):
        self.assertEqual(dl.selinux_type("SELINUX=enforcing\nSELINUXTYPE=mls\n"), "mls")
        self.assertEqual(dl.selinux_type("SELINUXTYPE=../x\n"), "targeted")
        self.assertEqual(dl.selinux_type(""), "targeted")

    def test_mountinfo_parser(self):
        self.assertEqual(dl.parse_mountinfo(
            "36 35 98:0 /mnt1 /mnt\\0402 rw,noatime master:1 - ext3 /dev/root rw,errors=continue\n"
            "garbage\n"
            "37 1 0:5 / /dev rw - devtmpfs devtmpfs rw\n"),
            [("98:0", "/mnt1", "/mnt 2", "/dev/root"), ("0:5", "/", "/dev", "devtmpfs")])

    def test_kit_manifest_rules(self):
        dl.parse_kit(kit_json())
        bad = [
            kit_json(schema=2),
            kit_json(compatible=[]),
            kit_json(repo={"id": "x", "url": "http://insecure/"}),
            kit_json(install=["luma-other"]),
            kit_json(install=[], replace=[]),
            kit_json(install=["bad name"]),
        ]
        for text in bad:
            with self.assertRaises(dl.LayerError, msg=text):
                dl.parse_kit(text)

    def test_origin_parser(self):
        requested, replaced = dl.parse_origin(
            "[origin]\nbaserefspec=luma:luma/1/aarch64/nightly\n\n[packages]\nrequested=a;b;\n\n"
            "[overrides]\nreplace=repo=luma-fairphone-fp6,ModemManager,libqmi;\n")
        self.assertEqual(requested, {"a", "b"})
        self.assertEqual(replaced, {"luma-fairphone-fp6": {"ModemManager", "libqmi"}})


if __name__ == "__main__":
    unittest.main()
