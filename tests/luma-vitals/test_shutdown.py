# SPDX-License-Identifier: Apache-2.0
"""How the previous boot ended, judged from recorded journals (fixtures/)."""
import json
import struct
import tempfile
import time
import unittest
from pathlib import Path

from luma_vitals.crashes import CrashWatch, previous_boot, wtmp_records

FIXTURES = Path(__file__).parent / "fixtures"
PREVIOUS = "d847bc231a8c4607a069dca587abe8da"
CURRENT = "d510120f9c964ba285da6fbca0b88c7f"
FIRST = 1789763568000000     # previous boot's first record, microseconds
LAST = 1789786456469883      # ... and its last
NEXT = 1789786488000000      # this boot's first record


class State(dict):
    def set(self, key, value):
        self[key] = value


def journalctl(fixture: str, *, readable: bool = True):
    """A journalctl that answers from a fixture of the previous boot's records.

    It honours what previous_boot asks of the real one: -b, _PID=, MESSAGE_ID=
    and -n; a person without access to the system journal sees nothing from
    system services.
    """
    entries = [json.loads(line) for line in (FIXTURES / fixture).read_text().splitlines() if line]

    def runner(arguments):
        if "--list-boots" in arguments:
            return json.dumps([{"index": -1, "boot_id": PREVIOUS, "first_entry": FIRST, "last_entry": LAST},
                               {"index": 0, "boot_id": CURRENT, "first_entry": NEXT, "last_entry": NEXT + 1}])
        assert arguments[arguments.index("-b") + 1] == PREVIOUS
        rows = entries if readable else []
        for argument in arguments:
            if argument.startswith(("_PID=", "MESSAGE_ID=")):
                key, value = argument.split("=", 1)
                rows = [e for e in rows if e.get(key) == value]
        if "-n" in arguments:
            rows = rows[-int(arguments[arguments.index("-n") + 1]):]
        return "\n".join(json.dumps(e) for e in rows)
    return runner


def utmp(kind: int, user: str, seconds: int) -> bytes:
    """One struct utmp record as glibc writes it on 64-bit Linux."""
    record = bytearray(384)
    struct.pack_into("<h", record, 0, kind)
    record[8:10] = b"~~"
    record[44:44 + len(user)] = user.encode()
    struct.pack_into("<ii", record, 340, seconds, 0)
    return bytes(record)


class PreviousBoot(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.wtmp = self.root / "wtmp"

    def watch(self, fixture, **kwargs):
        options = {"pstore_root": self.root / "pstore", "firmware_root": self.root / "crash",
                   "boot_id": CURRENT, "wtmp": self.wtmp}
        options.update(kwargs)
        return CrashWatch(State(), runner=journalctl(fixture), uid=1000, **options)

    def test_a_clean_reboot_whose_journal_ends_mid_unmount_is_not_a_crash(self):
        verdict = previous_boot(journalctl("previous-boot-clean-reboot.jsonl"), self.wtmp)
        self.assertTrue(verdict.clean)
        self.assertIn("System is rebooting.", verdict.markers)
        self.assertEqual(self.watch("previous-boot-clean-reboot.jsonl").previous_boot_events(), [])

    def test_pid1_stopping_the_system_is_enough_without_logind(self):
        # A shutdown that bypassed logind: only PID 1's own records remain.
        fixture = self.root / "no-logind.jsonl"
        lines = (FIXTURES / "previous-boot-clean-reboot.jsonl").read_text().splitlines()
        fixture.write_text("\n".join(l for l in lines if "systemd-logind\", \"MESSAGE\": \"System is" not in l))
        verdict = previous_boot(journalctl(str(fixture)), self.wtmp)
        self.assertTrue(verdict.clean)
        self.assertIn("Stopped target local-fs.target - Local File Systems.", verdict.markers)

    def test_a_wtmp_shutdown_record_is_enough_on_its_own(self):
        self.wtmp.write_bytes(utmp(2, "reboot", FIRST // 10**6) + utmp(1, "shutdown", LAST // 10**6 - 5))
        verdict = previous_boot(journalctl("previous-boot-hard-reset.jsonl"), self.wtmp)
        self.assertTrue(verdict.clean)
        self.assertEqual(len(verdict.markers), 1)

    def test_a_wtmp_shutdown_record_from_another_boot_does_not_count(self):
        # An older boot shut down cleanly; the previous one has a boot record
        # and no shutdown record; this boot's own record follows.
        self.wtmp.write_bytes(utmp(2, "reboot", FIRST // 10**6 - 7200) + utmp(1, "shutdown", FIRST // 10**6 - 60)
                              + utmp(2, "reboot", FIRST // 10**6) + utmp(2, "reboot", NEXT // 10**6))
        self.assertEqual([kind for kind, _ in wtmp_records(self.wtmp)], ["reboot", "shutdown", "reboot", "reboot"])
        self.assertFalse(previous_boot(journalctl("previous-boot-hard-reset.jsonl"), self.wtmp).clean)

    def test_a_hard_reset_is_reported_once(self):
        state = State()
        watch = CrashWatch(state, runner=journalctl("previous-boot-hard-reset.jsonl"), uid=1000,
                           pstore_root=self.root / "pstore", firmware_root=self.root / "crash",
                           boot_id=CURRENT, wtmp=self.wtmp)
        [event] = watch.previous_boot_events()
        self.assertEqual(event.kind, "unclean-shutdown")
        self.assertIn("stopped without shutting down", event.summary)
        self.assertEqual(event.details["cause"], "power-or-reset")
        self.assertEqual(event.details["last_sources"], ["gnome-shell", "kernel", "kernel"])
        self.assertEqual(watch.previous_boot_events(), [])

    def test_a_reset_with_a_firmware_error_record_is_called_a_crash(self):
        (self.root / "crash" / CURRENT).mkdir(parents=True)
        (self.root / "crash" / CURRENT / "summary.json").write_text(json.dumps({"new": True}))
        [event] = self.watch("previous-boot-hard-reset.jsonl").previous_boot_events()
        self.assertEqual(event.details["cause"], "crash")

    def test_a_reset_with_a_kernel_crash_record_is_called_a_crash(self):
        (self.root / "pstore").mkdir()
        (self.root / "pstore" / "dmesg-efi-178978645601").write_text("panic")
        events = self.watch("previous-boot-hard-reset.jsonl").previous_boot_events()
        self.assertEqual([e.kind for e in events], ["unclean-shutdown", "kernel-crash-record"])
        self.assertEqual(events[0].details["cause"], "crash")

    def test_power_lost_while_asleep_is_named_and_suspend_is_not_a_shutdown(self):
        # logind's "will suspend now!" shares its MESSAGE_ID with "will reboot now!".
        verdict = previous_boot(journalctl("previous-boot-power-loss.jsonl"), self.wtmp)
        self.assertFalse(verdict.clean)
        self.assertTrue(verdict.asleep)
        [event] = self.watch("previous-boot-power-loss.jsonl").previous_boot_events()
        self.assertEqual(event.details["cause"], "power-lost-asleep")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(LAST / 1e6))
        self.assertTrue(event.summary.endswith(stamp))

    def test_no_verdict_without_access_to_the_system_journal(self):
        runner = journalctl("previous-boot-power-loss.jsonl", readable=False)
        self.assertIsNone(previous_boot(runner, self.wtmp).clean)
        state = State()
        CrashWatch(state, runner=runner, uid=1000, pstore_root=self.root / "pstore",
                   firmware_root=self.root / "crash", boot_id=CURRENT, wtmp=self.wtmp).previous_boot_events()
        self.assertNotIn("previous-boot-checked", state)


if __name__ == "__main__":
    unittest.main()
