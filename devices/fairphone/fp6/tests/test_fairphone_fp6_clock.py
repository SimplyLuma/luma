# SPDX-License-Identifier: Apache-2.0
"""luma-clock-save/-restore keep the wall clock across power-off with the PMIC RTC.

Runs each unit's ExecStart under /bin/sh with its paths moved into a temporary
directory and a stand-in `date` that reports a fixed time and records `-s`.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
UNITS = ROOT / "src" / "luma-fp6-platform" / "root" / "usr" / "lib" / "systemd" / "system"

NOW = 1_795_000_000          # the clock the phone booted with
RTC = 188_300_000            # PMIC RTC seconds since its own (1970s) base

FAKE_DATE = """#!/bin/sh
case "$*" in
  "-u +%s") echo "$LUMA_TEST_NOW" ;;
  "-u -s @"*) echo "${3#@}" > "$LUMA_TEST_SET" ;;
  *) echo "unexpected date $*" >&2; exit 2 ;;
esac
"""


def exec_start(unit: str) -> str:
    for line in (UNITS / unit).read_text().splitlines():
        if line.startswith("ExecStart=/bin/sh -c '"):
            return line[len("ExecStart=/bin/sh -c '"):-1].replace("%%", "%")
    raise AssertionError(f"{unit} has no ExecStart=/bin/sh -c")


class ClockUnits(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        (self.dir / "bin").mkdir()
        (self.dir / "luma").mkdir()
        (self.dir / "rtc").mkdir()
        fake = self.dir / "bin" / "date"
        fake.write_text(FAKE_DATE)
        fake.chmod(0o755)

    def write(self, name: str, value: int):
        (self.dir / name).write_text(f"{value}\n")

    def run_unit(self, unit: str) -> None:
        script = (exec_start(unit)
                  .replace("/var/lib/luma", str(self.dir / "luma"))
                  .replace("/sys/class/rtc/rtc0", str(self.dir / "rtc")))
        env = {**os.environ, "PATH": f"{self.dir / 'bin'}:/usr/bin:/bin",
               "LUMA_TEST_NOW": str(NOW), "LUMA_TEST_SET": str(self.dir / "set")}
        subprocess.run(["/bin/sh", "-c", script], env=env, check=True)

    def clock_set_to(self):
        path = self.dir / "set"
        return int(path.read_text()) if path.exists() else None

    def test_save_records_the_time_and_its_offset_from_the_rtc(self):
        self.write("rtc/since_epoch", RTC)

        self.run_unit("luma-clock-save.service")

        self.assertEqual(int((self.dir / "luma/clock").read_text()), NOW)
        self.assertEqual(int((self.dir / "luma/rtc-offset").read_text()), NOW - RTC)

    def test_restore_adds_the_time_the_phone_was_off(self):
        saved, off_for = NOW + 60, 3600
        self.write("luma/clock", saved)
        self.write("luma/rtc-offset", saved - RTC)
        self.write("rtc/since_epoch", RTC + off_for)

        self.run_unit("luma-clock-restore.service")

        self.assertEqual(self.clock_set_to(), saved + off_for)

    def test_restore_falls_back_to_the_saved_time_when_the_rtc_was_reset(self):
        saved = NOW + 60
        self.write("luma/clock", saved)
        self.write("luma/rtc-offset", saved - RTC)
        self.write("rtc/since_epoch", 5)

        self.run_unit("luma-clock-restore.service")

        self.assertEqual(self.clock_set_to(), saved)

    def test_restore_uses_the_saved_time_without_an_offset(self):
        saved = NOW + 60
        self.write("luma/clock", saved)
        self.write("rtc/since_epoch", RTC)

        self.run_unit("luma-clock-restore.service")

        self.assertEqual(self.clock_set_to(), saved)

    def test_restore_never_moves_the_clock_back(self):
        self.write("luma/clock", NOW - 600)
        self.write("luma/rtc-offset", NOW - 600 - RTC)
        self.write("rtc/since_epoch", RTC)

        self.run_unit("luma-clock-restore.service")

        self.assertIsNone(self.clock_set_to())

    def test_restore_runs_with_only_an_offset_saved(self):
        unit = (UNITS / "luma-clock-restore.service").read_text()

        self.assertIn("ConditionPathExists=|/var/lib/luma/rtc-offset", unit)


if __name__ == "__main__":
    unittest.main()
