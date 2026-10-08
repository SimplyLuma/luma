# SPDX-License-Identifier: Apache-2.0
import json
import tempfile
import unittest
from pathlib import Path

from luma_vitals import power
from luma_vitals.battery_cli import render
from luma_vitals.detectors import PowerDetectors
from luma_vitals.power import Attribution, Draw, PowerSample
from luma_vitals.store import Store


def battery(root: Path, **files) -> Path:
    path = root / "BAT0"
    path.mkdir(parents=True, exist_ok=True)
    for name, value in {"type": "Battery", **files}.items():
        (path / name).write_text(str(value))
    return path


def reading(when, watts, percent, energy, *, on_battery=True, attribution=None, domains=None, cost=0.4):
    return PowerSample(time=when, cost_ms=cost, domains=domains or {},
                       attribution=attribution,
                       draw=Draw(on_battery=on_battery, status="Discharging" if on_battery else "Charging",
                                 watts=watts, energy_wh=energy, energy_full_wh=58.3, percent=percent,
                                 seconds_to_empty=energy / watts * 3600 if watts else None,
                                 screen_percent=54.0, lid_closed=False))


class TheDraw(unittest.TestCase):
    def test_watts_and_runtime_come_from_the_battery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            battery(root, status="Discharging\n", power_now=15193000, energy_now=53270000,
                    energy_full=58300000, capacity=91, voltage_now=12693000)
            power.POWER_SUPPLY = root
            now = power.draw()
        self.assertTrue(now.on_battery)
        self.assertAlmostEqual(now.watts, 15.193, places=3)
        self.assertAlmostEqual(now.energy_wh, 53.27, places=2)
        self.assertAlmostEqual(now.seconds_to_empty / 3600, 3.506, places=2)

    def test_charge_reporting_firmware_is_converted_to_energy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            battery(root, status="Discharging\n", power_now=0, current_now=1000000,
                    charge_now=4000000, charge_full=5000000, voltage_now=12000000, capacity=80)
            power.POWER_SUPPLY = root
            now = power.draw()
        self.assertAlmostEqual(now.watts, 12.0, places=2)
        self.assertAlmostEqual(now.energy_wh, 48.0, places=2)

    def test_on_mains_nothing_is_attributed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            battery(root, status="Charging\n", power_now=0, energy_now=58000000, capacity=99)
            power.POWER_SUPPLY = root
            taken = power.sample(power.Attributor())
        self.assertFalse(taken.draw.on_battery)
        self.assertIsNone(taken.attribution)
        self.assertEqual(taken.domains, {})


class TheEnergyCounters(unittest.TestCase):
    def test_a_fresh_publication_is_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "energy.json"
            path.write_text(json.dumps({"time": 1e12, "watts": {"package": 9.5, "graphics": 1.9}}))
            import time as clock
            real, clock.time = clock.time, lambda: 1e12
            try:
                self.assertEqual(power.domains(path), {"package": 9.5, "graphics": 1.9})
            finally:
                clock.time = real

    def test_a_stale_or_missing_publication_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "energy.json"
            self.assertEqual(power.domains(path), {})
            path.write_text(json.dumps({"time": 1.0, "watts": {"package": 9.5}}))
            self.assertEqual(power.domains(path), {})


class TheSession(unittest.TestCase):
    def store(self) -> Store:
        return Store(Path(tempfile.mkdtemp()) / "vitals.db")

    def test_a_run_on_battery_is_one_session_with_its_average_and_runtime(self):
        store = self.store()
        attribution = Attribution(wakeups={"gnome-shell": 360.0}, cpu={"chrome": 22.0},
                                  interrupts={"30 i2c_designware.2": 1729.0}, devices={"i2c-SYNA8030:00": 49.0},
                                  package_idle_percent=0.0, gpu_idle_percent=70.0,
                                  blockers=["the throughput-performance power profile is in force on battery"])
        first = store.record_power(reading(1000.0, 15.2, 92, 53.6, attribution=attribution), {"chrome.scope": 22.0})
        store.record_power(reading(4600.0, 14.4, 85, 49.2, attribution=attribution), {"chrome.scope": 20.0})
        report = store.battery_report()
        self.assertEqual(report["session"], first)
        self.assertEqual(report["samples"], 2)
        self.assertFalse(report["closed"])
        # 4.4 Wh in one hour is 4.4 W, whatever the battery said at the time.
        self.assertAlmostEqual(report["watts"]["average"], 4.4, places=1)
        self.assertAlmostEqual(report["runtime_hours"], 11.18, places=1)
        self.assertAlmostEqual(report["full_charge_hours"], 13.25, places=1)
        self.assertEqual(report["costs"]["interrupt"][0]["name"], "30 i2c_designware.2")
        self.assertEqual(report["blockers"][0],
                         "the throughput-performance power profile is in force on battery")
        self.assertAlmostEqual(report["overhead_ms_per_sample"], 0.4, places=2)

    def test_plugging_in_closes_the_session_and_unplugging_opens_another(self):
        store = self.store()
        first = store.record_power(reading(1000.0, 15.0, 90, 52.0), {})
        self.assertIsNone(store.record_power(reading(2000.0, None, 91, 53.0, on_battery=False), {}))
        second = store.record_power(reading(9000.0, 12.0, 88, 51.0), {})
        self.assertNotEqual(first, second)
        self.assertTrue(store.battery_report(first)["closed"])
        self.assertEqual(store.battery_report()["session"], second)

    def test_nothing_recorded_reads_back_as_nothing(self):
        self.assertIsNone(self.store().battery_report())
        self.assertEqual(render(None), ["Nothing has been recorded on battery yet."])

    def test_the_readout_says_the_draw_the_runtime_and_the_blockers(self):
        store = self.store()
        store.record_power(reading(1000.0, 15.2, 92, 53.6, domains={"package": 9.8, "graphics": 1.9},
                                   attribution=Attribution(cpu={"gnome-shell": 8.8}, gpu_idle_percent=70.0,
                                                           blockers=["14 devices may not power down: xe"])),
                           {"app-gnome-shell.scope": 8.8})
        text = "\n".join(render(store.battery_report()))
        self.assertIn("Running on battery", text)
        self.assertIn("Processor package 9.8 W", text)
        self.assertIn("graphics idle 70% of the time", text)
        self.assertIn("14 devices may not power down", text)
        self.assertIn("Measuring this took", text)


class TheDetectors(unittest.TestCase):
    def test_nothing_is_said_on_mains_power(self):
        taken = reading(1000.0, None, 99, 58.0, on_battery=False,
                        attribution=Attribution(blockers=["anything at all"]))
        self.assertEqual(PowerDetectors().observe(taken), [])

    def test_a_blocker_is_said_once_an_hour(self):
        detectors = PowerDetectors()
        taken = reading(1000.0, 15.0, 90, 52.0,
                        attribution=Attribution(blockers=["the throughput-performance profile is in force"]))
        self.assertEqual(len(detectors.observe(taken)), 1)
        self.assertEqual(detectors.observe(reading(1600.0, 15.0, 89, 51.0, attribution=taken.attribution)), [])
        later = reading(6000.0, 15.0, 80, 47.0, attribution=taken.attribution)
        self.assertEqual(len(detectors.observe(later)), 1)

    def test_a_wakeup_storm_names_the_process(self):
        taken = reading(1000.0, 15.0, 90, 52.0, attribution=Attribution(wakeups={"kworker/u32:5-xe": 2400.0}))
        events = PowerDetectors().observe(taken)
        self.assertEqual([e.kind for e in events], ["wakeup-storm"])
        self.assertIn("2400 times a second", events[0].summary)

    def test_a_short_runtime_names_the_busiest(self):
        taken = reading(1000.0, 28.0, 20, 11.0, attribution=Attribution(cpu={"chrome": 94.0, "gnome-shell": 12.0}))
        events = PowerDetectors().observe(taken, runtime_hours=0.4)
        self.assertEqual([e.kind for e in events], ["battery-short"])
        self.assertIn("chrome 94%", events[0].summary)


class TheAttribution(unittest.TestCase):
    def test_the_first_reading_is_only_a_baseline_and_the_second_is_a_rate(self):
        attributor = power.Attributor()
        self.assertIsNone(attributor.read(1000.0))
        self.assertFalse(attributor.due(1030.0))
        self.assertTrue(attributor.due(1060.0))
        second = attributor.read(1060.0)
        self.assertIsInstance(second, Attribution)
        # Read from this machine's own /proc: every rate must at least be sane.
        self.assertTrue(all(rate >= 0 for rate in second.wakeups.values()))
        self.assertTrue(all(0 <= percent <= 110 for percent in second.idle_residency.values()))


if __name__ == "__main__":
    unittest.main()
