# SPDX-License-Identifier: Apache-2.0
"""Fairphone 6 boot image: the initramfs seeds the private modem store on the
first start, before any normal boot lets the bootloader erase modemst."""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

DEVICE = Path(__file__).resolve().parents[1]
ROOT = DEVICE.parents[2]
HOOK = ROOT / "scripts/mobile/fp6-initramfs/hooks-cleanup/60-luma-fp6-modem-store.sh"
BUILDER = ROOT / "scripts/mobile/build-fp6-boot-image.sh"
SHELL = os.environ.get("LUMA_TEST_SH", "sh").split()
MODEL = b"The Fairphone (Gen. 6)\0"
MIB = 1024 * 1024
FILES = {"modemst1": "modem_fs1", "modemst2": "modem_fs2", "fsc": "modem_fsc",
         "fsg": "modem_fsg", "study": "modem_study"}
SIZES = {"modemst1": 10 * MIB, "modemst2": 10 * MIB, "fsc": 128 * 1024, "fsg": 3 * MIB, "study": 512 * 1024}
UNIT = "luma-fp6-modem-store-label.service"


def pattern(label, size):
    seed = hashlib.sha256(label.encode()).digest()
    return (seed * (size // len(seed) + 1))[:size]


class ModemStoreSeed(unittest.TestCase):
    maxDiff = 2000
    longMessage = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        model = self.root / "sys/firmware/devicetree/base/model"
        model.parent.mkdir(parents=True)
        model.write_bytes(MODEL)
        (self.root / "dev").mkdir()
        (self.root / "run").mkdir()
        (self.root / "sysroot/var/lib").mkdir(parents=True)
        self.index = 0
        self.parts = {}
        for label, size in SIZES.items():
            self.partition(label, pattern(label, size))
        self.var = self.root / "sysroot/var/lib/luma"
        self.store = self.var / "rmtfs"

    def tearDown(self):
        self.tmp.cleanup()

    def partition(self, label, data):
        self.index += 1
        name = f"sde{self.index}"
        block = self.root / "sys/class/block" / name
        block.mkdir(parents=True)
        (block / "uevent").write_text(f"MAJOR=8\nMINOR={self.index}\nDEVNAME={name}\nDEVTYPE=partition\n"
                                      f"PARTN={self.index}\nPARTNAME={label}\n")
        (block / "size").write_text(f"{len(data) // 512}\n")
        (self.root / "dev" / name).write_bytes(data)
        self.parts[label] = self.root / "dev" / name

    def run_hook(self):
        log = self.root / "kmsg"
        log.unlink(missing_ok=True)
        subprocess.run([*SHELL, str(HOOK)], check=True, capture_output=True,
                       env={**os.environ, "LUMA_FP6_TEST_ROOT": str(self.root), "LUMA_FP6_MODEM_STORE_LOG": str(log)})
        return log.read_text() if log.exists() else ""

    def digests(self):
        return {label: hashlib.sha256(path.read_bytes()).hexdigest() for label, path in self.parts.items()}

    def assertNothingSeeded(self):
        self.assertFalse(self.store.exists())
        self.assertFalse((self.var / "rmtfs.new").exists())
        self.assertFalse((self.var / "rmtfs.relabel").exists())
        self.assertFalse((self.root / "run/systemd").exists())

    def test_seeds_the_store_from_intact_partitions(self):
        before = self.digests()
        out = self.run_hook()
        self.assertIn("state=seeded partitions_written=0 identifiers_logged=0", out)
        # Read only: every partition is unchanged.
        self.assertEqual(self.digests(), before)
        self.assertEqual(self.store.stat().st_mode & 0o7777, 0o700)
        for label, name in FILES.items():
            copy = self.store / name
            self.assertEqual(copy.read_bytes(), self.parts[label].read_bytes(), label)
            self.assertEqual(copy.stat().st_mode & 0o7777, 0o600, label)
        self.assertEqual((self.store / "modem_tunning").read_bytes(), b"")
        manifest = json.loads((self.store / "luma-fp6-modem-store.json").read_text())
        self.assertEqual(manifest["schema"], "org.projectluma.fp6-modem-store/v1")
        self.assertIs(manifest["partitions_written"], False)
        self.assertRegex(manifest["seeded_utc"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(manifest["sources"], {
            label: {"file": FILES[label], "bytes": SIZES[label], "sha256": before[label]} for label in FILES})
        self.assertEqual(sorted(p.name for p in self.store.iterdir()),
                         sorted([*FILES.values(), "modem_tunning", "luma-fp6-modem-store.json"]))
        self.assertFalse((self.var / "rmtfs.new").exists())

    def test_manifest_reads_as_a_real_seed_to_the_store_service(self):
        # luma-fp6-modem-store retires a store whose manifest records all-zero
        # modemst; it finds the entries with this pattern.
        self.run_hook()
        text = (self.store / "luma-fp6-modem-store.json").read_text()
        zero = hashlib.sha256(bytes(10 * MIB)).hexdigest()
        for label in ("modemst1", "modemst2"):
            entry = re.search(r'"%s":\{[^}]*\}' % label, text).group(0)
            self.assertIn('"sha256":"', entry)
            self.assertNotIn(zero, entry)

    def test_labels_the_store_on_the_next_systemd_start(self):
        self.run_hook()
        self.assertTrue((self.var / "rmtfs.relabel").is_file())
        unit = self.root / "run/systemd/system" / UNIT
        text = unit.read_text()
        before = re.search(r"^Before=(.*)$", text, re.M).group(1).split()
        self.assertLessEqual({"rmtfs.service", "luma-fp6-modem-store.service", "luma-device-layer.service",
                              "sysinit.target"}, set(before))
        self.assertIn("ConditionPathExists=/var/lib/luma/rmtfs.relabel", text)
        self.assertIn("ExecStart=/usr/sbin/restorecon -RF /var/lib/luma/rmtfs", text)
        self.assertIn("ExecStart=/usr/bin/rm -f /var/lib/luma/rmtfs.relabel", text)
        link = self.root / "run/systemd/system/sysinit.target.wants" / UNIT
        self.assertEqual(os.readlink(link), f"../{UNIT}")

    def test_blank_modemst_is_never_seeded(self):
        for label in ("modemst1", "modemst2"):
            self.parts[label].write_bytes(bytes(10 * MIB))
        out = self.run_hook()
        self.assertIn("state=deferred reason=modemst_blank", out)
        self.assertNothingSeeded()

    def test_one_intact_modemst_is_seeded(self):
        # As luma-fp6-modem-store: only both erased counts as blank.
        self.parts["modemst2"].write_bytes(bytes(10 * MIB))
        self.assertIn("state=seeded", self.run_hook())
        self.assertEqual((self.store / "modem_fs2").read_bytes(), bytes(10 * MIB))

    def test_an_existing_store_is_left_to_the_store_service(self):
        self.store.mkdir(parents=True, mode=0o700)
        (self.store / "modem_fs1").write_bytes(b"modem wrote this")
        self.assertEqual(self.run_hook(), "")
        self.assertEqual(sorted(p.name for p in self.store.iterdir()), ["modem_fs1"])
        self.assertEqual((self.store / "modem_fs1").read_bytes(), b"modem wrote this")
        self.assertFalse((self.root / "run/systemd").exists())

    def test_an_unlabelled_store_asks_again_until_labelled(self):
        self.run_hook()
        unit = self.root / "run/systemd/system" / UNIT
        unit.unlink()
        before = (self.store / "luma-fp6-modem-store.json").read_bytes()
        self.assertEqual(self.run_hook(), "")
        self.assertTrue(unit.is_file())
        self.assertEqual((self.store / "luma-fp6-modem-store.json").read_bytes(), before)
        (self.var / "rmtfs.relabel").unlink()
        unit.unlink()
        self.run_hook()
        self.assertFalse(unit.exists())

    def test_other_devices_are_untouched(self):
        (self.root / "sys/firmware/devicetree/base/model").write_bytes(b"Fairphone 5\0")
        self.assertEqual(self.run_hook(), "")
        self.assertNothingSeeded()

    def test_missing_or_ambiguous_partitions_seed_nothing(self):
        self.partition("fsg", pattern("fsg", SIZES["fsg"]))
        self.assertIn("state=failed reason=partition_missing_fsg", self.run_hook())
        self.assertNothingSeeded()

    def test_blank_fsg_stops(self):
        self.parts["fsg"].write_bytes(bytes(SIZES["fsg"]))
        self.assertIn("state=failed reason=fsg_blank", self.run_hook())
        self.assertNothingSeeded()

    def test_modemst_must_be_10_mib(self):
        self.parts["modemst1"].write_bytes(pattern("modemst1", 4 * MIB))
        (self.root / "sys/class/block/sde1/size").write_text(f"{4 * MIB // 512}\n")
        self.assertIn("state=failed reason=partition_size_modemst1", self.run_hook())
        self.assertNothingSeeded()

    def test_partitions_are_only_ever_read(self):
        code = [line for line in HOOK.read_text().splitlines() if not line.lstrip().startswith("#")]
        script = "\n".join(code)
        for line in code:
            if re.search(r"\bdd\b", line):
                self.assertIn('of="$stage/$file"', line)
        self.assertNotRegex(script, r'>\s*"\$device"')
        self.assertNotRegex(script, r"\b(blkdiscard|flash_erase|fastboot)\b")

    def test_var_mount_is_read_from_mountinfo(self):
        # prepare-root bind-mounts /var from the same filesystem; busybox
        # mountpoint (st_dev of the directory and its parent) misses that.
        code = "\n".join(line for line in HOOK.read_text().splitlines() if not line.lstrip().startswith("#"))
        self.assertIn("/proc/self/mountinfo", code)
        self.assertNotRegex(code, r"\bmountpoint\b")

    def test_boot_image_carries_the_seed(self):
        builder = BUILDER.read_text()
        self.assertIn('"$o/hooks-cleanup/60-luma-fp6-modem-store.sh"', builder)
        self.assertIn('"hooks-cleanup/60-luma-fp6-modem-store.sh"', builder)
        self.assertTrue(os.access(HOOK, os.X_OK))


if __name__ == "__main__":
    unittest.main()
