# SPDX-License-Identifier: Apache-2.0
"""luma-device-boot decisions against fake partitions and a fake slot tool."""
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from luma_update import device_boot as db

PART = 8192             # bytes per fake boot partition
RELEASE = "7.1.2-luma-fp-ims1.gmu2.selinux1"
NOTES = b"kernel notes of the new build"
OLD = b"OLD-BOOT" * 600
NEW = b"NEW-BOOT" * 700


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeSlots:
    swaps_every_pair = True

    def __init__(self, current="a"):
        self.current_slot = current
        self.active_slot = current
        self.flags = {"a": {"successful": True, "bootable": True}, "b": {"successful": True, "bootable": True}}
        self.calls = []
        self.fail_set_active = False

    def current(self):
        return self.current_slot

    def active(self):
        return self.active_slot

    def successful(self, slot):
        return self.flags[slot]["successful"]

    def bootable(self, slot):
        return self.flags[slot]["bootable"]

    def set_active(self, slot):
        self.calls.append(("set_active", slot))
        if self.fail_set_active:
            raise db.BootError("Failed to set active")
        self.active_slot = slot
        self.flags[slot] = {"successful": False, "bootable": True}

    def mark_successful(self, slot):
        self.calls.append(("mark_successful", slot))
        self.flags[slot]["successful"] = True

    def mark_unbootable(self, slot):
        self.calls.append(("mark_unbootable", slot))
        self.flags[slot]["bootable"] = False


class CorruptingDisks(db.Disks):
    def write(self, path, image, size):
        super().write(path, image, size)
        with open(path, "r+b") as stream:
            stream.write(b"X")


class Phone:
    """A root with sysfs partitions, /dev files, a package manifest and the slot tool."""

    def __init__(self, test, image=NEW, current="a", disks=db.Disks):
        self.tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.devs = {}
        self.slots = FakeSlots(current)
        self.reboots = 0
        self.release = RELEASE
        self.notes = NOTES
        partitions = {
            "boot_a": OLD, "boot_b": OLD,
            "dtbo_a": b"", "dtbo_b": b"",
            "modem_a": b"modem firmware", "modem_b": b"modem firmware",
            "modemst1": b"IMEI and NV items", "modemst2": b"IMEI and NV items",
            "fsg": b"fsg", "fsc": b"fsc", "persist": b"persist",
        }
        for index, (name, data) in enumerate(partitions.items()):
            self.add_partition(name, f"sda{index + 1}", data)
        self.write("proc/cmdline", f"quiet androidboot.slot_suffix=_{current} ostree=/ostree/root.a\n")
        self.write("sys/firmware/devicetree/base/compatible", "fairphone,fp6\0qcom,milos\0")
        self.write("proc/sys/kernel/random/boot_id", "boot-1\n")
        self.image = image
        self.manifest()
        self.system = db.System(root=self.root, disks=disks(self.root, block_only=False), slots=self.slots,
                                reboot=self.reboot, kernel_release=lambda: self.release, clock=lambda: 1794600000)
        self.system.notes_sha256 = lambda: sha(self.notes)

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode())

    def add_partition(self, name, dev, data, size=PART):
        self.write(f"sys/class/block/{dev}/uevent", f"MAJOR=8\nDEVNAME={dev}\nDEVTYPE=partition\nPARTNAME={name}\n")
        self.write(f"sys/class/block/{dev}/size", f"{size // 512}\n")
        self.write(f"dev/{dev}", data.ljust(size, b"\0"))
        self.devs[name] = dev

    def manifest(self, **overrides):
        data = {
            "schema": 1, "device": "fairphone/fp6", "compatible": ["fairphone,fp6"], "slots": "qbootctl",
            "partitions": ["boot_a", "boot_b"], "kernel_release": RELEASE,
            "kernel_notes_sha256": sha(NOTES),
            "images": [{"partition": "boot", "file": "boot.bin", "sha256": sha(self.image),
                        "size": len(self.image), "partition_size": PART}],
        }
        data.update(overrides)
        self.write("usr/lib/luma/device-boot/fairphone-fp6/boot.bin", self.image)
        self.write("usr/lib/luma/device-boot/fairphone-fp6/device-boot.json", json.dumps(data))

    def reboot(self):
        self.reboots += 1

    def part(self, name):
        return (self.root / "dev" / self.devs[name]).read_bytes()

    def boot_into(self, slot):
        """The bootloader starts `slot`; a new boot id."""
        self.slots.current_slot = self.slots.active_slot = slot
        self.write("proc/cmdline", f"quiet androidboot.slot_suffix=_{slot}\n")
        self.write("proc/sys/kernel/random/boot_id", f"boot-{slot}-{self.reboots}\n")

    def updater(self):
        return db.DeviceBoot(self.system)

    def protected_untouched(self, test):
        for name in ("modemst1", "modemst2", "fsg", "fsc", "persist"):
            test.assertEqual(self.part(name).rstrip(b"\0"), {
                "modemst1": b"IMEI and NV items", "modemst2": b"IMEI and NV items",
                "fsg": b"fsg", "fsc": b"fsc", "persist": b"persist"}[name])


class ApplyTest(unittest.TestCase):
    def test_no_manifest_does_nothing(self):
        phone = Phone(self)
        (phone.root / "usr/lib/luma/device-boot/fairphone-fp6/device-boot.json").unlink()
        self.assertEqual(phone.updater().apply(), 0)
        self.assertEqual(phone.slots.calls, [])

    def test_other_device_manifest_is_ignored(self):
        phone = Phone(self)
        phone.manifest(compatible=["google,tokay"])
        self.assertEqual(phone.updater().apply(), 0)
        self.assertEqual(phone.slots.calls, [])
        self.assertEqual(phone.part("boot_b")[:len(OLD)], OLD)

    def test_in_step_does_nothing(self):
        phone = Phone(self, image=OLD)
        self.assertEqual(phone.updater().apply(), 0)
        self.assertEqual((phone.slots.calls, phone.reboots), ([], 0))

    def test_new_image_goes_to_the_inactive_slot_and_restarts_once(self):
        phone = Phone(self)
        self.assertEqual(phone.updater().apply(), 0)
        self.assertEqual(phone.part("boot_b")[:len(NEW)], NEW)
        self.assertEqual(phone.part("boot_a")[:len(OLD)], OLD)
        self.assertEqual(phone.slots.calls, [("set_active", "b")])
        self.assertEqual(phone.reboots, 1)
        pending = json.loads((phone.root / db.STATE_DIR / "pending.json").read_text())
        self.assertEqual((pending["slot"], pending["from"], pending["kernel_release"]), ("b", "a", RELEASE))
        self.assertTrue(phone.slots.successful("a"))
        phone.protected_untouched(self)

    def test_from_slot_b_writes_slot_a(self):
        phone = Phone(self, current="b")
        phone.updater().apply()
        self.assertEqual(phone.part("boot_a")[:len(NEW)], NEW)
        self.assertEqual(phone.part("boot_b")[:len(OLD)], OLD)
        self.assertEqual(phone.slots.calls, [("set_active", "a")])

    def test_no_reboot_switches_only(self):
        phone = Phone(self)
        phone.updater().apply(reboot=False)
        self.assertEqual((phone.slots.active_slot, phone.reboots), ("b", 0))

    def test_waits_while_the_running_slot_is_not_successful(self):
        phone = Phone(self)
        phone.slots.flags["a"]["successful"] = False
        self.assertEqual(phone.updater().apply(), 0)
        self.assertEqual((phone.slots.calls, phone.reboots), ([], 0))
        self.assertEqual(phone.part("boot_b")[:len(OLD)], OLD)

    def test_corrupt_package_image_is_refused_before_writing(self):
        phone = Phone(self)
        phone.write("usr/lib/luma/device-boot/fairphone-fp6/boot.bin", NEW[:-1] + b"?")
        with self.assertRaises(db.BootError):
            phone.updater().apply()
        self.assertEqual(phone.part("boot_b")[:len(OLD)], OLD)
        self.assertEqual(phone.slots.calls, [])

    def test_differing_firmware_pair_is_refused(self):
        phone = Phone(self)
        phone.write(f"dev/{phone.devs['modem_b']}", b"older modem firmware".ljust(PART, b"\0"))
        with self.assertRaisesRegex(db.Refused, "modem_a and modem_b differ"):
            phone.updater().apply()
        self.assertEqual(phone.part("boot_b")[:len(OLD)], OLD)
        self.assertEqual((phone.slots.calls, phone.reboots), ([], 0))

    def test_readback_mismatch_marks_the_slot_unbootable_and_does_not_switch(self):
        phone = Phone(self, disks=CorruptingDisks)
        with self.assertRaisesRegex(db.BootError, "reads back different"):
            phone.updater().apply()
        self.assertEqual(phone.slots.calls, [("mark_unbootable", "b")])
        self.assertEqual((phone.slots.active_slot, phone.reboots), ("a", 0))
        self.assertFalse((phone.root / db.STATE_DIR / "pending.json").exists())

    def test_slot_already_holding_the_image_is_not_rewritten(self):
        phone = Phone(self)
        phone.write(f"dev/{phone.devs['boot_b']}", NEW.ljust(PART, b"\0"))
        before = (phone.root / "dev" / phone.devs["boot_b"]).stat().st_mtime_ns
        phone.updater().apply()
        self.assertEqual((phone.root / "dev" / phone.devs["boot_b"]).stat().st_mtime_ns, before)
        self.assertEqual(phone.slots.calls, [("set_active", "b")])

    def test_failed_switch_restores_the_running_slot(self):
        phone = Phone(self)
        phone.slots.fail_set_active = True
        with self.assertRaisesRegex(db.BootError, "switching to slot _b failed"):
            phone.updater().apply()
        self.assertEqual((phone.slots.active_slot, phone.reboots), ("a", 0))
        self.assertFalse((phone.root / db.STATE_DIR / "pending.json").exists())

    def test_duplicate_partition_name_is_refused(self):
        phone = Phone(self)
        phone.add_partition("boot_b", "mmcblk1p1", OLD)
        with self.assertRaisesRegex(db.BootError, "2 found"):
            phone.updater().apply()
        self.assertEqual(phone.slots.calls, [])

    def test_wrong_partition_size_is_refused(self):
        phone = Phone(self)
        phone.manifest(images=[{"partition": "boot", "file": "boot.bin", "sha256": sha(NEW),
                                "size": len(NEW), "partition_size": PART * 2}])
        with self.assertRaisesRegex(db.Refused, "bytes, the manifest says"):
            phone.updater().apply()

    def test_slot_tool_and_command_line_must_agree(self):
        phone = Phone(self)
        phone.slots.current_slot = "b"
        with self.assertRaisesRegex(db.BootError, "kernel command line"):
            phone.updater().apply()
        self.assertEqual(phone.slots.calls, [])


class TrialTest(unittest.TestCase):
    def test_trial_boot_confirmed_then_in_step(self):
        phone = Phone(self)
        phone.updater().apply()
        phone.boot_into("b")
        self.assertEqual(phone.updater().apply(), 0)          # trial boot: in step, nothing to do
        self.assertEqual(phone.updater().confirm(), 0)
        self.assertTrue(phone.slots.successful("b"))
        self.assertIn(("mark_successful", "b"), phone.slots.calls)
        self.assertFalse((phone.root / db.STATE_DIR / "pending.json").exists())
        state = json.loads((phone.root / db.STATE_DIR / "state.json").read_text())
        self.assertEqual(state["last"]["result"], "confirmed")
        calls = len(phone.slots.calls)
        phone.updater().apply()
        self.assertEqual(len(phone.slots.calls), calls)

    def test_wrong_kernel_is_not_confirmed(self):
        phone = Phone(self)
        phone.updater().apply()
        phone.boot_into("b")
        phone.release = "7.1.2-something-else"
        with self.assertRaisesRegex(db.BootError, "not marking the slot successful"):
            phone.updater().confirm()
        self.assertFalse(phone.slots.successful("b"))
        self.assertTrue((phone.root / db.STATE_DIR / "pending.json").exists())

    def test_wrong_kernel_notes_are_not_confirmed(self):
        phone = Phone(self)
        phone.updater().apply()
        phone.boot_into("b")
        phone.notes = b"another build"
        with self.assertRaises(db.BootError):
            phone.updater().confirm()
        self.assertNotIn(("mark_successful", "b"), phone.slots.calls)

    def test_fallback_is_recorded_and_never_retried(self):
        phone = Phone(self)
        phone.updater().apply()
        phone.boot_into("a")                                   # the bootloader fell back
        with self.assertRaisesRegex(db.Refused, "already resolved|did not boot"):
            phone.updater().apply()
        state = json.loads((phone.root / db.STATE_DIR / "state.json").read_text())
        self.assertEqual(state["last"]["result"], "fell back")
        self.assertIn(f"boot:{sha(NEW)}", state["failed"])
        self.assertEqual((phone.reboots, phone.slots.calls.count(("set_active", "b"))), (1, 1))
        phone.boot_into("a")                                   # later boots stay put
        with self.assertRaisesRegex(db.Refused, "did not boot"):
            phone.updater().apply()
        self.assertEqual(phone.reboots, 1)
        self.assertEqual(phone.updater().confirm(), 0)          # nothing pending

    def test_retry_forgets_the_failure(self):
        phone = Phone(self)
        phone.updater().apply()
        phone.boot_into("a")
        with self.assertRaises(db.Refused):
            phone.updater().apply()
        phone.updater().retry()
        phone.updater().apply()
        self.assertEqual(phone.reboots, 2)

    def test_a_newer_image_after_a_failure_is_tried(self):
        phone = Phone(self)
        phone.updater().apply()
        phone.boot_into("a")
        with self.assertRaises(db.Refused):
            phone.updater().apply()
        newer = b"NEWER-BOOT" * 500
        phone.image = newer
        phone.manifest()
        phone.updater().apply()
        self.assertEqual(phone.part("boot_b")[:len(newer)], newer)
        self.assertEqual(phone.reboots, 2)

    def test_rolled_back_deployment_brings_its_image_back(self):
        phone = Phone(self)
        phone.updater().apply()
        phone.boot_into("b")
        phone.updater().confirm()
        phone.image = OLD                                      # rpm-ostree rollback
        phone.manifest()
        phone.updater().apply()
        self.assertEqual(phone.slots.active_slot, "a")         # slot a still held OLD: switch only
        self.assertEqual(phone.part("boot_b")[:len(NEW)], NEW)


class ManifestTest(unittest.TestCase):
    def write(self, data):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "device-boot.json"
        base = {"schema": 1, "device": "fairphone/fp6", "compatible": ["fairphone,fp6"], "slots": "qbootctl",
                "partitions": ["boot_a", "boot_b"], "kernel_release": RELEASE, "kernel_notes_sha256": "",
                "images": [{"partition": "boot", "file": "boot.bin", "sha256": "0" * 64, "size": 1,
                            "partition_size": 1}]}
        base.update(data)
        path.write_text(json.dumps(base))
        return path

    def test_valid(self):
        manifest = db.load_manifest(self.write({}))
        self.assertEqual(manifest.key, "boot:" + "0" * 64)

    def test_protected_and_unknown_partitions_are_refused(self):
        for partitions in (["modemst1", "modemst2"], ["persist"], ["fsg"], ["modem_a", "modem_b"],
                           ["boot_a"], ["boot", "boot_b"], ["vbmeta_a", "vbmeta_b"]):
            with self.subTest(partitions=partitions), self.assertRaises(db.BootError):
                db.load_manifest(self.write({"partitions": partitions}))

    def test_image_must_name_a_listed_partition(self):
        with self.assertRaises(db.BootError):
            db.load_manifest(self.write({"images": [{"partition": "dtbo", "file": "dtbo.bin", "sha256": "0" * 64,
                                                     "size": 1, "partition_size": 1}]}))

    def test_image_larger_than_partition(self):
        with self.assertRaises(db.BootError):
            db.load_manifest(self.write({"images": [{"partition": "boot", "file": "boot.bin", "sha256": "0" * 64,
                                                     "size": 2, "partition_size": 1}]}))

    def test_unknown_slot_tool(self):
        with self.assertRaises(db.BootError):
            db.load_manifest(self.write({"slots": "fastboot"}))

    def test_protected_names_never_opened(self):
        disks = db.Disks(Path("/nonexistent"), block_only=False)
        for name in ("modemst1", "modemst2", "fsg", "fsc", "persist", "study"):
            with self.subTest(name=name), self.assertRaises(db.Refused):
                disks.find(name, {name: [("sda1", 1)]})


class QbootctlTest(unittest.TestCase):
    def tool(self, answers):
        calls = []

        def run(argv, **_):
            calls.append(argv[1:])
            out, code = answers.get(tuple(argv[1:]), ("", 1))
            return subprocess.CompletedProcess(argv, code, out + "\n", "")
        return db.Qbootctl(run=run), calls

    def test_parses_qbootctl_answers(self):
        tool, calls = self.tool({
            ("-c",): ("Current slot: _a", 0), ("-a",): ("Active slot: _b", 0),
            ("-n", "a"): ("SLOT _a: is marked successful", 0),
            ("-n", "b"): ("SLOT _b: is not marked successful", 0),
            ("-b", "b"): ("SLOT _b: is marked bootable", 0),
            ("-s", "b"): ("SLOT 1: Set as active slot", 0),
        })
        self.assertEqual((tool.current(), tool.active()), ("a", "b"))
        self.assertTrue(tool.successful("a"))
        self.assertFalse(tool.successful("b"))
        self.assertTrue(tool.bootable("b"))
        tool.set_active("b")
        self.assertEqual(calls[-1], ["-s", "b"])

    def test_unexpected_answers_and_failures_raise(self):
        tool, _ = self.tool({("-c",): ("No slots found, is this an A/B device?", 0),
                             ("-n", "a"): ("SLOT _b: is marked successful", 0)})
        with self.assertRaises(db.BootError):
            tool.current()
        with self.assertRaises(db.BootError):
            tool.successful("a")
        with self.assertRaises(db.BootError):
            tool.set_active("b")


if __name__ == "__main__":
    unittest.main()
