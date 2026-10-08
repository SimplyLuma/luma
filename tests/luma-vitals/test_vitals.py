# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from pathlib import Path

from luma_vitals import sampler
from luma_vitals.detectors import GIB, Detectors, friendly
from luma_vitals.sampler import MachineSample, UnitSample
from luma_vitals.store import Store


def sample(t, units, *, available=8 * GIB, swap_free=8 * GIB, memory_stall=0.0, celsius=None):
    return MachineSample(time=t, memory_total=16 * GIB, memory_available=available, swap_total=8 * GIB,
                         swap_free=swap_free, pressure={"memory": {"some": memory_stall}},
                         temperature_c=celsius, units=units)


def unit(name, cpu_seconds, memory):
    return UnitSample(name, int(cpu_seconds * 1e6), memory, memory, 0, 0, 0)


class Names(unittest.TestCase):
    def test_units_are_named_like_the_apps(self):
        self.assertEqual(friendly("app-gnome-org.mozilla.firefox-5530.scope"), "org.mozilla.firefox")
        self.assertEqual(friendly("localsearch-3.service"), "localsearch-3")
        self.assertEqual(friendly("luma-run-rpm-chatgpt-25ec6b75b803.container"), "chatgpt")
        self.assertEqual(friendly("waydroid.lxc"), "Android apps")
        self.assertEqual(friendly("luma-run-deb-claude-desktop-797594ce81c1.container"), "claude-desktop")
        self.assertEqual(friendly("app-flatpak-com.discordapp.Discord-835380605.scope"), "com.discordapp.Discord")


class Detection(unittest.TestCase):
    def run_minutes(self, minutes, build, **machine):
        detectors, events = Detectors(), []
        for step in range(int(minutes * 4) + 1):
            t = step * 15.0
            found, _cpu = detectors.observe(sample(t, build(t), **machine))
            events.extend(found)
        return events

    def test_a_service_spinning_a_core_is_reported_once(self):
        events = self.run_minutes(10, lambda t: [unit("localsearch-3.service", t * 0.9, 100 << 20)])
        kinds = [e.kind for e in events]
        self.assertEqual(kinds.count("runaway-cpu"), 1)
        self.assertIn("localsearch-3", events[0].summary)

    def test_a_short_burst_is_not(self):
        events = self.run_minutes(10, lambda t: [unit("app-gnome-org.gnome.Nautilus-1.scope",
                                                      min(t, 60) * 0.9, 100 << 20)])
        self.assertFalse([e for e in events if e.kind == "runaway-cpu"])

    def test_growth_and_an_idle_hog(self):
        grow = self.run_minutes(12, lambda t: [unit("localsearch-3.service", t * 0.01, int(GIB * (0.2 + t / 300)))])
        self.assertTrue(any(e.kind == "memory-growth" for e in grow))
        hog = self.run_minutes(12, lambda t: [unit("app-luma-ari-model-1.scope", 1.0, 3 * GIB)])
        self.assertTrue(any(e.kind == "idle-hog" for e in hog))

    def test_memory_pressure_names_who_holds_it(self):
        events = self.run_minutes(1, lambda t: [unit("a.service", 0, 5 * GIB), unit("b.service", 0, GIB)],
                                  available=GIB // 2, swap_free=GIB // 10)
        pressure = [e for e in events if e.kind == "memory-pressure"]
        self.assertEqual(len(pressure), 1)
        self.assertIn("a.service"[:-8], pressure[0].summary)


class Storage(unittest.TestCase):
    def test_report_totals_cpu_and_events(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "v.db")
            detectors = Detectors()
            import time as clock
            now = clock.time()
            for step in range(20):
                s = sample(now - 300 + step * 15, [unit("busy.service", step * 15.0, 200 << 20)])
                events, cpu = detectors.observe(s)
                store.record(s, cpu, events)
            report = store.report(3600)
            self.assertEqual(report["top_cpu"][0]["unit"], "busy.service")
            self.assertGreater(report["top_cpu"][0]["cpu_seconds"], 200)


class Sampling(unittest.TestCase):
    def test_reads_the_real_session_when_there_is_one(self):
        root = sampler.session_root()
        if not root.is_dir():
            self.skipTest("no user session cgroup here")
        found = sampler.units(root)
        self.assertTrue(found)
        self.assertTrue(all(u.unit.endswith((".scope", ".service", ".container")) for u in found))

    def test_applications_are_counted_separately_from_the_session(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "user@1000.service"
            for leaf in ("app.slice/app-gnome-firefox-1.scope", "session.slice/pipewire.service"):
                (root / leaf).mkdir(parents=True)
                (root / leaf / "memory.current").write_text("1048576\n")
            (root / "memory.current").write_text("2097152\n")
            names = sorted(u.unit for u in sampler.units(root))
        self.assertEqual(names, ["app-gnome-firefox-1.scope", "pipewire.service"])

    def test_android_container_is_counted(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory) / "lxc.payload.waydroid"
            payload.mkdir()
            (payload / "cpu.stat").write_text("usage_usec 5000000\n")
            (payload / "memory.current").write_text("1048576\n")
            found = sampler.containers(Path(directory))
        self.assertEqual([(u.unit, u.cpu_usec, u.memory) for u in found], [("waydroid.lxc", 5000000, 1048576)])



class CrashEvidence(unittest.TestCase):
    """ADR-026 §6: the system's own crash evidence becomes an event exactly once."""

    PREVIOUS = "4b7561bea6f547cc9772a5b4b6ccdee2"
    CURRENT = "742dc8920f82440ca7b2531278a532a6"

    class State(dict):
        def set(self, key, value):
            self[key] = value

    def journal(self, *, shutdown_logged, readable=True, coredumps=(), launch_failures=()):
        import json

        boots = [{"index": -1, "boot_id": self.PREVIOUS, "first_entry": 1, "last_entry": 1789486730716131},
                 {"index": 0, "boot_id": self.CURRENT, "first_entry": 2, "last_entry": 3}]

        def runner(arguments):
            if "--list-boots" in arguments:
                return json.dumps(boots)
            if any(a.startswith("MESSAGE_ID=fc2e22bc") for a in arguments):
                after = next((a.split("=", 1)[1] for a in arguments if a.startswith("--after-cursor=")), None)
                rows = [c for c in coredumps if after is None or c["__CURSOR"] > after]
                return "\n".join(json.dumps(c) for c in rows)
            if any(a.startswith("MESSAGE_ID=5b3c0f1e") for a in arguments):
                after = next((a.split("=", 1)[1] for a in arguments if a.startswith("--after-cursor=")), None)
                rows = [f for f in launch_failures if after is None or f["__CURSOR"] > after]
                return "\n".join(json.dumps(f) for f in rows)
            if any(a.startswith("MESSAGE_ID=98268866") for a in arguments):
                # systemd-logind, not PID 1, logs a requested shutdown.
                return ('{"MESSAGE_ID": "98268866d1d54a499c4e98921d93bc40", "_PID": "1117"}\n'
                        if shutdown_logged and readable else "")
            if "_PID=1" in arguments:
                return '{"MESSAGE_ID": "x", "_PID": "1"}\n' if readable else ""
            if "-n" in arguments:
                return '{"SYSLOG_IDENTIFIER": "gnome-shell"}\n'
            return ""
        return runner

    def watch(self, runner, state=None, **kwargs):
        from luma_vitals.crashes import CrashWatch
        with tempfile.TemporaryDirectory() as directory:
            defaults = {"pstore_root": Path(directory) / "none", "firmware_root": Path(directory) / "none",
                        "boot_id": self.CURRENT, "wtmp": Path(directory) / "none"}
            defaults.update(kwargs)
            return CrashWatch(state if state is not None else self.State(), runner=runner, uid=1000, **defaults)

    def test_a_reset_without_shutdown_is_reported_once_per_boot(self):
        state = self.State()
        watch = self.watch(self.journal(shutdown_logged=False), state)
        events = watch.previous_boot_events()
        self.assertEqual([e.kind for e in events], ["unclean-shutdown"])
        self.assertIn("without shutting down", events[0].summary)
        self.assertEqual(events[0].details["last_sources"], ["gnome-shell"])
        # A second login in the same boot does not repeat it.
        self.assertEqual(self.watch(self.journal(shutdown_logged=False), state).previous_boot_events(), [])

    def test_a_clean_reboot_is_not_an_event(self):
        self.assertEqual(self.watch(self.journal(shutdown_logged=True)).previous_boot_events(), [])

    def test_no_verdict_without_access_to_the_system_journal(self):
        state = self.State()
        self.assertEqual(self.watch(self.journal(shutdown_logged=False, readable=False), state)
                         .previous_boot_events(), [])
        self.assertNotIn("previous-boot-checked", state)

    def test_only_this_persons_crashes_are_named_and_never_by_path(self):
        dumps = [
            {"__CURSOR": "a", "COREDUMP_UID": "1000", "COREDUMP_EXE": "/var/home/nick/private/tool",
             "COREDUMP_SIGNAL": "11", "COREDUMP_USER_UNIT": "app-gnome-org.projectluma.Connect-8995.scope"},
            {"__CURSOR": "b", "COREDUMP_UID": "1001", "COREDUMP_EXE": "/usr/bin/other",
             "COREDUMP_SIGNAL": "6"},
            {"__CURSOR": "c", "COREDUMP_UID": "1000", "COREDUMP_EXE": "/var/home/nick/private/tool",
             "COREDUMP_SIGNAL_NAME": "SIGABRT", "COREDUMP_USER_UNIT": "user@1000.service"},
        ]
        state = self.State()
        watch = self.watch(self.journal(shutdown_logged=True, coredumps=dumps), state)
        events = watch.crash_events()
        self.assertEqual([e.summary.split(" crashed")[0] for e in events], ["org.projectluma.Connect", "tool"])
        self.assertEqual([e.details["signal"] for e in events], ["SIGSEGV", "SIGABRT"])
        self.assertFalse(any("/var/home" in e.summary for e in events))
        self.assertEqual(state["coredump-cursor"], "c")
        self.assertEqual(watch.crash_events(), [])

    def test_an_app_that_could_not_open_is_an_event_with_its_reason(self):
        failures = [{"__CURSOR": "a", "MESSAGE": "CrealityPrint couldn't open: the system library libbz2.so.1.0 is missing",
                     "LUMA_APPLICATION_ID": "appimage-crealityprint-b28f66c25d0c", "LUMA_APPLICATION_NAME": "CrealityPrint",
                     "LUMA_LAUNCH_FAILURE": "missing-library", "LUMA_MISSING": "libbz2.so.1.0",
                     "LUMA_EXIT_STATUS": "127", "LUMA_SECONDS": "0.4",
                     "LUMA_STDERR": "/app/bin/CrealityPrint: error while loading shared libraries: libbz2.so.1.0"}]
        state = self.State()
        watch = self.watch(self.journal(shutdown_logged=True, launch_failures=failures), state)
        events = watch.launch_failure_events()
        self.assertEqual([(e.kind, e.unit) for e in events], [("launch-failure", "appimage-crealityprint-b28f66c25d0c")])
        self.assertIn("libbz2.so.1.0 is missing", events[0].summary)
        self.assertEqual((events[0].details["missing"], events[0].details["exit_status"]), ("libbz2.so.1.0", "127"))
        self.assertEqual(state["launch-failure-cursor"], "a")
        self.assertEqual(watch.launch_failure_events(), [])

    def test_a_crash_loop_is_summarised(self):
        from luma_vitals.crashes import MAX_CRASH_EVENTS
        dumps = [{"__CURSOR": f"{i:03d}", "COREDUMP_UID": "1000", "COREDUMP_EXE": "/opt/app/chrome",
                  "COREDUMP_SIGNAL": "5"} for i in range(40)]
        events = self.watch(self.journal(shutdown_logged=True, coredumps=dumps)).crash_events()
        self.assertEqual(len(events), MAX_CRASH_EVENTS + 1)
        self.assertEqual(events[-1].details["count"], 40 - MAX_CRASH_EVENTS)

    def test_a_new_firmware_error_record_is_reported(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / self.CURRENT
            capture.mkdir()
            (capture / "summary.json").write_text(json.dumps({"new": True, "sha256": "ab", "data_bytes": 27532}))
            state = self.State()
            watch = self.watch(self.journal(shutdown_logged=True), state, firmware_root=Path(directory))
            events = watch.firmware_events()
            self.assertEqual([e.kind for e in events], ["firmware-crash-record"])
            self.assertEqual(watch.firmware_events(), [])
            (capture / "summary.json").write_text(json.dumps({"new": False}))
            self.assertEqual(self.watch(self.journal(shutdown_logged=True), firmware_root=Path(directory))
                             .firmware_events(), [])

    def test_the_boot_capture_keeps_new_records_and_recognises_old_ones(self):
        import importlib.machinery
        import importlib.util
        import json
        import os
        source = Path(__file__).resolve().parents[2] / "src/luma-vitals/crash-evidence/luma-crash-evidence"
        if not source.exists():  # packaged test tree
            source = Path(__file__).resolve().parents[1] / "crash-evidence/luma-crash-evidence"
        with tempfile.TemporaryDirectory() as directory:
            tables, state = Path(directory) / "tables", Path(directory) / "state"
            (tables / "data").mkdir(parents=True)
            (tables / "BERT").write_bytes(b"BERT" + bytes(44))
            (tables / "data" / "BERT").write_bytes(b"crashlog" * 10)
            environment = {"LUMA_CRASH_TABLES": str(tables), "STATE_DIRECTORY": str(state)}

            def capture(boot):
                old = {k: os.environ.get(k) for k in environment}
                os.environ.update(environment)
                try:
                    loader = importlib.machinery.SourceFileLoader(f"capture_{boot}", str(source))
                    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
                    loader.exec_module(module)
                    module.log = lambda *_args, **_fields: None  # never the real journal
                    module.BOOT_ID = Path(directory) / f"boot-{boot}"
                    module.BOOT_ID.write_text(boot + "\n")
                    self.assertEqual(module.main(), 0)
                finally:
                    for key, value in old.items():
                        if value is None:
                            os.environ.pop(key, None)
                        else:
                            os.environ[key] = value
                return json.loads((state / boot / "summary.json").read_text())

            self.assertTrue(capture("first")["new"])
            self.assertFalse(capture("second")["new"])
            self.assertEqual((state / "second" / "BERT.data").read_bytes(), b"crashlog" * 10)
