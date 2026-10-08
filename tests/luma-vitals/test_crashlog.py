# SPDX-License-Identifier: MPL-2.0
"""ADR-026 §6: an Intel Crash Log in the boot error record is decoded once and named.

The fake-iclg tests run everywhere. The tests marked "real iclg" run when
`iclg` is installed (or LUMA_ICLG names it). The real-machine test decodes
tests/fixtures/crash/x1-2in1-gen10-lunar-lake-20260915.berr, the Boot Error
Region the X1 2-in-1 Gen 10 left after its 2026-09-15 reset, as iclg reads it
("BERR" followed by the region). LUMA_CRASHLOG_SAMPLE names another capture
instead. The fixture only holds processor state from that one boot.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import stat
import struct
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

HERE = Path(__file__).resolve()
SOURCE = HERE.parents[2] / "src/luma-vitals/crash-evidence/luma-crash-evidence-intel"
if not SOURCE.exists():  # packaged test tree
    SOURCE = HERE.parents[1] / "crash-evidence/luma-crash-evidence-intel"
CAPTURE = SOURCE.with_name("luma-crash-evidence")

PREVIOUS = "4b7561bea6f547cc9772a5b4b6ccdee2"
CURRENT = "742dc8920f82440ca7b2531278a532a6"
LATER = "9d0b6c0a6b0a4f4e8a3c1e2f3a4b5c6d"

REAL_ICLG = os.environ.get("LUMA_ICLG") or shutil.which("iclg")
FIXTURE = HERE.parents[1] / "fixtures/crash/x1-2in1-gen10-lunar-lake-20260915.berr"
SAMPLE = os.environ.get("LUMA_CRASHLOG_SAMPLE") or (str(FIXTURE) if FIXTURE.exists() else None)
#: What iclg 1.2.0 reports for the X1 2-in-1 Gen 10 reset of 2026-09-15.
SAMPLE_TRIAGE = [
    "CORE_TIMEOUT.MULTIPLE_STUCK_TRANSACTIONS",
    "MCA.BANK3.INTERNAL_TIMER_ERROR.MSCOD_1104H",
    "CRASHLOG_REASON.PMC.10H",
    "RESET_CAUSE.HOST_PARTITION_RESET.HI_PRPC",
    "CRASHLOG_REASON.PUNIT.20004H",
    "RESET_CAUSE.FIRMWARE_GLOBAL_RESET.FW_GBLRST_SCRATCH16",
    "RESET_CAUSE.GLOBAL_RESET.PMC_FW",
]
SAMPLE_LIP = 0xFFFFFFFFC0ABFED7


def load(name, source=SOURCE):
    loader = importlib.machinery.SourceFileLoader(name, str(source))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


# --- synthetic boot error regions (UEFI 2.10 appendix N, ACPI 6.5 §18.3.2) ---

def error_region(sections):
    """A Generic Error Status Block holding version-3 Generic Error Data Entries."""
    entries = b""
    for guid, body in sections:
        entries += uuid.UUID(guid).bytes_le + struct.pack("<IHBBI", 1, 0x300, 0, 0, len(body))
        entries += bytes(16) + bytes(20) + struct.pack("<Q", 0) + body
    return struct.pack("<IIIII", 1, 0, 0, len(entries), 1) + entries


FIRMWARE_ERROR_RECORD = "81212a96-09ed-4996-9471-8d729c8e69ed"


def firmware_error_record(record_guid, payload):
    return struct.pack("<BB6xQ", 2, 2, 0) + uuid.UUID(record_guid).bytes_le + payload


#: Shaped like an Intel Crash Log section; its payload is only a stand-in for the fake iclg.
CRASHLOG_SHAPED = error_region([(FIRMWARE_ERROR_RECORD,
                                 firmware_error_record("8f87f311-c998-4d9e-a0c4-6065518c4f6d", b"crashlog" * 16))])


#: Non-Crash-Log records a boot error region can carry on other machines.
NON_CRASHLOG_REGIONS = {
    # IA32/X64 processor error: how AMD and older Intel platforms report MCEs.
    "x86-processor-error": error_region([("dc3ea0b0-a144-4797-b95b-53fa242b6e1d",
                                          struct.pack("<QQQ", 0x7, 0x8000000000000000, 0) + bytes(40))]),
    # Platform memory error.
    "memory-error": error_region([("a5bc1114-6f64-4ede-b863-3e83ed7c83b1", bytes(80))]),
    # A firmware error record that references something other than a Crash Log.
    "firmware-error-record-other": error_region([(FIRMWARE_ERROR_RECORD, firmware_error_record(
        str(uuid.uuid5(uuid.NAMESPACE_DNS, "not-a-crashlog")), b"crashlog" * 16))]),
    "empty-status-block": struct.pack("<IIIII", 0, 0, 0, 0, 0),
    "truncated": b"\x01\x00\x00\x00\x00\x00",
    "noise": bytes((i * 131 + 7) % 256 for i in range(4096)),
}


class Machine:
    """A capture directory, a module map and a fake boot, driven like the real unit."""

    def __init__(self, directory, iclg):
        self.root = Path(directory)
        self.state = self.root / "state"
        self.state.mkdir(exist_ok=True)
        self.iclg = str(iclg) if iclg else str(self.root / "no-iclg")
        self.logged = []
        self.previous = PREVIOUS

    def capture(self, boot, data, *, modules=None):
        target = self.state / boot
        target.mkdir(exist_ok=True)
        (target / "BERT").write_bytes(b"BERT" + bytes(44))
        (target / "BERT.data").write_bytes(data)
        import hashlib
        digest = hashlib.sha256((target / "BERT").read_bytes() + data).hexdigest()
        (target / "summary.json").write_text(json.dumps({"boot_id": boot, "sha256": digest, "new": True,
                                                         "data_bytes": len(data)}))
        return target

    def run(self, boot, *, proc_modules="", text_addresses=None):
        (self.root / "boot_id").write_text(boot + "\n")
        (self.root / "modules").write_text(proc_modules)
        sys_module = self.root / "sys-module"
        shutil.rmtree(sys_module, ignore_errors=True)
        for name, address in (text_addresses or {}).items():
            (sys_module / name / "sections").mkdir(parents=True)
            (sys_module / name / "sections" / ".text").write_text(f"{address:#x}\n")
        module = load(f"intel_{boot}_{len(self.logged)}")
        module.STATE = self.state
        module.BOOT_ID = self.root / "boot_id"
        module.ICLG = self.iclg
        module.PROC_MODULES = self.root / "modules"
        module.SYS_MODULE = sys_module
        module.previous_boot_id = lambda: self.previous
        module.log = lambda priority, message, fields: self.logged.append((priority, message, fields))
        return module.main()


def fake_iclg(directory, *, decodes=True, tags=("CORE_TIMEOUT.SINGLE_STUCK_TRANSACTION",), lip=None):
    """A stand-in for iclg that records how often it ran."""
    counter = Path(directory) / "iclg-runs"
    tree = {"crashlog_data": {"pcore": {"core2": {"thread0": {"thread": {"arch_state": {
        "lip": f"{lip:#x}" if lip else "0xdeadbeefdeadbeef"}}}}}}}
    script = Path(directory) / "iclg"
    script.write_text(f"""#!{sys.executable}
import json, sys
from pathlib import Path
counter = Path({str(counter)!r})
counter.write_text(str(int(counter.read_text() or 0) + 1) if counter.exists() else "1")
command = sys.argv[1]
if command == "--version":
    print("iclg 0.0-test"); raise SystemExit(0)
record = Path(sys.argv[2]).read_bytes()
assert record.startswith(b"BERR"), record[:4]
if not {decodes!r}:
    print("[ERROR] Fatal Error: No Crash Log found", file=sys.stderr); raise SystemExit(1)
if command == "decode":
    print({json.dumps(tree)!r})
elif command == "triage":
    print("\\n".join({list(tags)!r}))
""")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script, counter


class Decoding(unittest.TestCase):
    def test_a_new_crash_log_is_decoded_once_and_journalled_with_its_cause(self):
        with tempfile.TemporaryDirectory() as directory:
            iclg, runs = fake_iclg(directory, tags=("CORE_TIMEOUT.MULTIPLE_STUCK_TRANSACTIONS",
                                                     "RESET_CAUSE.GLOBAL_RESET.PMC_FW"))
            machine = Machine(directory, iclg)
            target = machine.capture(CURRENT, CRASHLOG_SHAPED)
            self.assertEqual(machine.run(CURRENT), 0)
            record = json.loads((target / "crashlog.json").read_text())
            self.assertTrue(record["decoded"] and record["new"])
            self.assertEqual(record["triage"], ["CORE_TIMEOUT.MULTIPLE_STUCK_TRANSACTIONS",
                                                "RESET_CAUSE.GLOBAL_RESET.PMC_FW"])
            self.assertEqual((target / "crashlog-triage.txt").read_text().split(), record["triage"])
            self.assertIn("crashlog_data", json.loads((target / "crashlog-decoded.json").read_text()))
            self.assertEqual(len(machine.logged), 1)
            priority, message, fields = machine.logged[0]
            self.assertEqual(priority, 3)
            self.assertTrue(message.startswith("The computer restarted unexpectedly"))
            self.assertIn(("MESSAGE_ID", "e6f1229b2154445eaaf8f207d149fb1f"), fields)
            self.assertEqual([v for k, v in fields if k == "LUMA_CRASHLOG_TRIAGE"], record["triage"])
            for name in ("crashlog.json", "crashlog-decoded.json", "crashlog-triage.txt"):
                self.assertEqual(stat.S_IMODE((target / name).stat().st_mode), 0o644)

            # The unit running again in the same boot changes nothing.
            decodes = runs.read_text()
            self.assertEqual(machine.run(CURRENT), 0)
            self.assertEqual((len(machine.logged), runs.read_text()), (1, decodes))

            # The firmware keeps reporting the same record at the next boot: not decoded
            # again, not journalled again, but the boot's summary points at the first decode.
            later = machine.capture(LATER, CRASHLOG_SHAPED)
            self.assertEqual(machine.run(LATER), 0)
            self.assertEqual((len(machine.logged), runs.read_text()), (1, decodes))
            repeat = json.loads((later / "crashlog.json").read_text())
            self.assertEqual((repeat["new"], repeat["first_decoded_in"]), (False, CURRENT))

    def test_the_last_instruction_is_named_from_the_previous_boots_module_map(self):
        with tempfile.TemporaryDirectory() as directory:
            iclg, _ = fake_iclg(directory, lip=SAMPLE_LIP)
            machine = Machine(directory, iclg)
            # The boot that later crashed keeps its module map (addresses need CAP_SYSLOG).
            machine.capture(PREVIOUS, b"")
            proc = ("i915 5611520 0 - Live 0xffffffffc1600000\n"
                    "xe 4993024 1 - Live 0xffffffffc0a00000\n"
                    "thunderbolt 638976 0 - Live 0xffffffffc0800000\n")
            machine.run(PREVIOUS, proc_modules=proc, text_addresses={"xe": 0xFFFFFFFFC0A00000})
            kept = machine.state / PREVIOUS / "modules.json"
            self.assertEqual(stat.S_IMODE(kept.stat().st_mode), 0o600)
            self.assertEqual(machine.logged, [])  # An empty record is not a crash.

            target = machine.capture(CURRENT, CRASHLOG_SHAPED)
            machine.run(CURRENT, proc_modules=proc.replace("c0a00000", "c0e00000"))
            pointer = json.loads((target / "crashlog.json").read_text())["instruction_pointers"][0]
            self.assertEqual((pointer["module"], pointer["offset"], pointer["module_map_boot"]),
                             ("xe", "0xbfed7", PREVIOUS))
            self.assertEqual(pointer["where"], "pcore.core2.thread0")
            self.assertIn("last instruction in xe+0xbfed7", machine.logged[0][1])

    def test_an_older_module_map_is_never_used_to_name_an_address(self):
        with tempfile.TemporaryDirectory() as directory:
            iclg, _ = fake_iclg(directory, lip=SAMPLE_LIP)
            machine = Machine(directory, iclg)
            machine.capture("olderboot", b"")
            machine.run("olderboot", proc_modules="xe 4993024 1 - Live 0xffffffffc0a00000\n")
            target = machine.capture(CURRENT, CRASHLOG_SHAPED)
            machine.run(CURRENT)  # The previous boot (PREVIOUS) kept no map.
            pointer = json.loads((target / "crashlog.json").read_text())["instruction_pointers"][0]
            self.assertNotIn("module", pointer)
            self.assertEqual(pointer["address"], hex(SAMPLE_LIP))

    def test_a_map_without_addresses_is_not_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            machine = Machine(directory, None)
            machine.capture(CURRENT, b"")
            machine.run(CURRENT, proc_modules="xe 4993024 1 - Live 0x0000000000000000\n")
            self.assertFalse((machine.state / CURRENT / "modules.json").exists())

    def test_only_intel_crash_log_sections_are_recognised(self):
        module = load("intel_sections")
        self.assertEqual(module.crash_log_sections(CRASHLOG_SHAPED), 1)
        two = error_region([(FIRMWARE_ERROR_RECORD, firmware_error_record(
            "8f87f311-c998-4d9e-a0c4-6065518c4f6d", b"a" * 40))] * 2
            + [("a5bc1114-6f64-4ede-b863-3e83ed7c83b1", bytes(80))])
        self.assertEqual(module.crash_log_sections(two), 2)
        for name, region in NON_CRASHLOG_REGIONS.items():
            self.assertEqual(module.crash_log_sections(region), 0, name)
        # A data length that runs past the record is not trusted.
        self.assertEqual(module.crash_log_sections(CRASHLOG_SHAPED[:-10]), 0)

    def test_nearest_text_below_owns_the_address(self):
        module = load("intel_resolve")
        mapping = {"modules": [{"name": "big", "base": 0x1000, "size": 0x10000, "text": 0x1000},
                               {"name": "small", "base": 0x3000, "size": 0x800, "text": 0x3000}]}
        self.assertEqual(module.resolve(0x3100, mapping)["module"], "small")
        self.assertEqual(module.resolve(0x2000, mapping)["module"], "big")
        self.assertEqual(module.resolve(0x90000, mapping), {})

    def test_nothing_happens_without_a_record_iclg_or_a_crash_log(self):
        with tempfile.TemporaryDirectory() as directory:
            iclg, runs = fake_iclg(directory, decodes=False)
            machine = Machine(directory, iclg)
            # No BERT captured at this boot.
            self.assertEqual(machine.run(CURRENT), 0)
            self.assertEqual(list(machine.state.iterdir()), [])
            # An empty (all-zero) record.
            target = machine.capture(CURRENT, bytes(64))
            self.assertEqual(machine.run(CURRENT), 0)
            self.assertFalse((target / "crashlog.json").exists())
            self.assertFalse(runs.exists())
            # Records that are not Intel Crash Logs never reach iclg.
            for name, region in NON_CRASHLOG_REGIONS.items():
                target = machine.capture(LATER, region)
                self.assertEqual(machine.run(LATER), 0)
                self.assertFalse((target / "crashlog.json").exists(), name)
            self.assertFalse(runs.exists())
            # A Crash Log section iclg cannot read: noted once, never journalled.
            target = machine.capture("rejected", CRASHLOG_SHAPED)
            self.assertEqual(machine.run("rejected"), 0)
            record = json.loads((target / "crashlog.json").read_text())
            self.assertEqual((record["decoded"], record["reason"]), (False, "[ERROR] Fatal Error: No Crash Log found"))
            self.assertFalse((target / "crashlog-decoded.json").exists())
            self.assertEqual(machine.logged, [])
        with tempfile.TemporaryDirectory() as directory:
            machine = Machine(directory, None)  # iclg is not installed (for example on AMD images)
            target = machine.capture(CURRENT, CRASHLOG_SHAPED)
            self.assertEqual(machine.run(CURRENT), 0)
            self.assertFalse((target / "crashlog.json").exists())
            self.assertEqual(machine.logged, [])

    def test_a_broken_decoder_never_fails_the_boot(self):
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "iclg"
            broken.write_text(f"#!{sys.executable}\nprint('{{not json')\n")
            broken.chmod(0o755)
            machine = Machine(directory, broken)
            target = machine.capture(CURRENT, CRASHLOG_SHAPED)
            self.assertEqual(machine.run(CURRENT), 0)
            self.assertFalse(json.loads((target / "crashlog.json").read_text())["decoded"])
            self.assertEqual(machine.logged, [])


@unittest.skipUnless(REAL_ICLG, "real iclg is not installed")
class RealDecoder(unittest.TestCase):
    """The installed iclg against records that are not Intel Crash Logs."""

    def test_non_crashlog_records_are_left_alone(self):
        for name, region in NON_CRASHLOG_REGIONS.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                machine = Machine(directory, REAL_ICLG)
                target = machine.capture(CURRENT, region)
                self.assertEqual(machine.run(CURRENT), 0)
                self.assertEqual(machine.logged, [], name)
                self.assertFalse((target / "crashlog-decoded.json").exists(), name)
                if (target / "crashlog.json").exists():
                    self.assertFalse(json.loads((target / "crashlog.json").read_text())["decoded"])

    @unittest.skipUnless(SAMPLE, "LUMA_CRASHLOG_SAMPLE is not set")
    def test_the_real_lunar_lake_reset_is_named(self):
        sample = Path(SAMPLE).read_bytes()
        self.assertTrue(sample.startswith(b"BERR"))
        self.assertGreaterEqual(load("intel_real_sections").crash_log_sections(sample[4:]), 1)
        with tempfile.TemporaryDirectory() as directory:
            machine = Machine(directory, REAL_ICLG)
            machine.capture(PREVIOUS, b"")
            machine.run(PREVIOUS, proc_modules="xe 4993024 1 - Live 0xffffffffc0a00000\n",
                        text_addresses={"xe": 0xFFFFFFFFC0A00000})
            target = machine.capture(CURRENT, sample[4:])
            self.assertEqual(machine.run(CURRENT), 0)
            record = json.loads((target / "crashlog.json").read_text())
            self.assertTrue(record["decoded"])
            self.assertEqual(record["triage"], SAMPLE_TRIAGE)
            pointers = record["instruction_pointers"]
            self.assertIn({"where": "pcore.core8.thread0", "address": hex(SAMPLE_LIP), "module": "xe",
                           "offset": "0xbfed7", "kernel_release": pointers[0].get("kernel_release", ""),
                           "module_map_boot": PREVIOUS}, pointers)
            decoded = json.loads((target / "crashlog-decoded.json").read_text())
            self.assertEqual(decoded["crashlog_data"]["pcore"]["core8"]["thread0"]["thread"]["arch_state"]
                             ["mca"]["bank3"]["status"], "0xb200000011040400")
            self.assertEqual(len(machine.logged), 1)
            print("\n" + machine.logged[0][1], file=sys.stderr)


class VitalsEvent(unittest.TestCase):
    def test_the_vitals_event_names_the_cause(self):
        from luma_vitals.crashes import CrashWatch

        class State(dict):
            def set(self, key, value):
                self[key] = value

        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / CURRENT
            capture.mkdir()
            (capture / "summary.json").write_text(json.dumps({"new": True, "sha256": "ab", "data_bytes": 27532}))
            (capture / "crashlog.json").write_text(json.dumps({
                "sha256": "ab", "decoded": True, "triage": SAMPLE_TRIAGE[:2],
                "instruction_pointers": [{"where": "pcore.core8.thread0", "address": hex(SAMPLE_LIP),
                                          "module": "xe", "offset": "0xbfed7"}]}))
            watch = CrashWatch(State(), runner=lambda arguments: "", uid=1000, boot_id=CURRENT,
                               pstore_root=Path(directory) / "none", firmware_root=Path(directory))
            [event] = watch.firmware_events()
            self.assertEqual(event.kind, "firmware-crash-record")
            self.assertIn("restarted unexpectedly", event.summary)
            self.assertIn("CORE_TIMEOUT.MULTIPLE_STUCK_TRANSACTIONS", event.summary)
            self.assertIn("xe+0xbfed7", event.summary)
            self.assertEqual(event.details["triage"], SAMPLE_TRIAGE[:2])

            # A decode of some other record is not attributed to this one.
            (capture / "crashlog.json").write_text(json.dumps({"sha256": "cd", "decoded": True, "triage": ["X"]}))
            [event] = CrashWatch(State(), runner=lambda arguments: "", uid=1000, boot_id=CURRENT,
                                 pstore_root=Path(directory) / "none",
                                 firmware_root=Path(directory)).firmware_events()
            self.assertNotIn("triage", event.details)


class CaptureThenDecode(unittest.TestCase):
    """The unit's two steps in order, with the real capture script."""

    def test_capture_then_decode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tables, state = root / "tables", root / "state"
            (tables / "data").mkdir(parents=True)
            (tables / "BERT").write_bytes(b"BERT" + bytes(44))
            (tables / "data" / "BERT").write_bytes(CRASHLOG_SHAPED)
            environment = {"LUMA_CRASH_TABLES": str(tables), "STATE_DIRECTORY": str(state)}
            old = {k: os.environ.get(k) for k in environment}
            os.environ.update(environment)
            try:
                capture = load("capture_then_decode", CAPTURE)
            finally:
                for key, value in old.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            capture.log = lambda *_args, **_fields: None
            capture.BOOT_ID = root / "boot_id"
            capture.BOOT_ID.write_text(CURRENT + "\n")
            self.assertEqual(capture.main(), 0)
            iclg, _ = fake_iclg(directory)
            machine = Machine(directory, iclg)
            machine.state = state
            self.assertEqual(machine.run(CURRENT), 0)
            record = json.loads((state / CURRENT / "crashlog.json").read_text())
            summary = json.loads((state / CURRENT / "summary.json").read_text())
            self.assertEqual(record["sha256"], summary["sha256"])
            self.assertEqual(len(machine.logged), 1)


if __name__ == "__main__":
    unittest.main()
