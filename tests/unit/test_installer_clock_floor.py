# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the actual native RTC initializer with controlled hardware/time boundaries."""
import ast
import importlib.util
import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "scripts/install/atlas-iso"
spec = importlib.util.spec_from_file_location("runtime_clock", HERE / "prepare-runtime-clock.py")
PATCH = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PATCH)
UPSTREAM = (HERE / "upstream/anaconda-timezone-44.30.py").read_bytes()
PULL_UPSTREAM = (HERE / "upstream/anaconda-ostree-installation-44.30.py").read_bytes()
FLOOR, OLD = 1791246654, 1672544000


def initializer(patched=True, wall=FLOOR, rtc=OLD, floor=FLOOR,
                setter_error=None, ineffective=False, rtc_error=None, utc=True):
    functions = ast.parse(PATCH.patched_source(UPSTREAM).decode() if patched else UPSTREAM.decode())
    functions.body = [n for n in functions.body if isinstance(n, ast.FunctionDef)
                      and n.name in ("time_initialize", "_luma_restore_clock_floor")]
    state = {"wall": wall, "calls": [], "sets": []}
    def stat(path):
        assert path == "/usr/lib/clock-epoch"
        if floor is None:
            raise FileNotFoundError(path)
        if isinstance(floor, Exception):
            raise floor
        return SimpleNamespace(st_mtime=floor)
    def settime(clock, value):
        assert clock == 0
        state["sets"].append(value)
        if setter_error:
            raise setter_error
        if not ineffective:
            state["wall"] = value
    def read_rtc(command, args):
        state["calls"].append((command, args))
        if isinstance(rtc_error, Exception):
            raise rtc_error
        if rtc_error:
            return rtc_error
        state["wall"] = rtc
        return 0
    state["windows_checked"] = False
    def windows():
        state["windows_checked"] = True
        return True
    namespace = dict(os=SimpleNamespace(stat=stat),
                     time=SimpleNamespace(time=lambda: state["wall"], clock_settime=settime,
                                          CLOCK_REALTIME=0, asctime=lambda x: str(x), gmtime=lambda x: x),
                     log=logging.getLogger("clock-test"), arch=SimpleNamespace(is_s390=lambda: False),
                     flags=SimpleNamespace(automatedInstall=False), THREAD_STORAGE="storage", BOOTLOADER="bootloader",
                     thread_manager=SimpleNamespace(wait=lambda _: None),
                     STORAGE=SimpleNamespace(get_proxy=lambda _: SimpleNamespace(DetectWindows=windows)),
                     execWithRedirect=read_rtc)
    exec(compile(functions, "native-anaconda-timezone", "exec"), namespace)
    return lambda: namespace["time_initialize"](SimpleNamespace(IsUTC=utc)), state


class InstallerClockFloor(unittest.TestCase):
    def test_successful_stale_rtc_rewinds_upstream_but_is_restored_by_patch(self):
        run, old = initializer(patched=False)
        run()
        self.assertEqual(old["wall"], OLD)
        run, fixed = initializer()
        run()
        self.assertEqual(fixed["calls"], [("hwclock", ["--hctosys", "--utc"])])
        self.assertEqual(fixed["wall"], FLOOR)
        self.assertEqual(fixed["sets"], [FLOOR])

    def test_local_rtc_detection_and_nonzero_hwclock_preserved(self):
        run, state = initializer(utc=False)
        run()
        self.assertTrue(state["windows_checked"])
        self.assertEqual(state["calls"], [("hwclock", ["--hctosys", "--localtime"])])
        self.assertEqual(state["wall"], FLOOR)
        run, state = initializer(wall=OLD, rtc_error=1)
        run()
        self.assertEqual(state["wall"], FLOOR)

    def test_missing_floor_and_current_or_future_clock_keep_upstream_behavior(self):
        run, state = initializer(floor=None)
        run()
        self.assertEqual(state["wall"], OLD)
        self.assertEqual(state["sets"], [])
        for rtc in (FLOOR, FLOOR + 100000000):
            run, state = initializer(rtc=rtc)
            run()
            self.assertEqual(state["wall"], rtc)
            self.assertEqual(state["sets"], [])

    def test_clock_failures_are_not_reported_as_success(self):
        for options, error in (({"floor": PermissionError("stat")}, PermissionError),
                               ({"setter_error": PermissionError("set")}, PermissionError),
                               ({"ineffective": True}, RuntimeError)):
            run, _ = initializer(**options)
            with self.assertRaises(error):
                run()
        run, state = initializer(wall=OLD, rtc_error=OSError("RTC unavailable"))
        with self.assertRaises(OSError):
            run()
        self.assertEqual(state["wall"], FLOOR)

    def test_changed_upstream_source_is_refused(self):
        with self.assertRaisesRegex(ValueError, "unsupported Anaconda"):
            PATCH.patched_source(UPSTREAM + b"\n")
        with self.assertRaisesRegex(ValueError, "unsupported Anaconda"):
            PATCH.patched_pull_source(PULL_UPSTREAM + b"\n")

    def test_manual_backdate_is_corrected_before_actual_native_signed_pull(self):
        def pull(patched):
            tree = ast.parse(PATCH.patched_pull_source(PULL_UPSTREAM).decode()
                             if patched else PULL_UPSTREAM.decode())
            owner = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                         and n.name == "PullRemoteAndDeleteTask")
            owner.bases = []
            owner.body = [n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == "run"]
            module = ast.Module(body=[owner], type_ignores=[])
            state = {"wall": OLD, "pulls": [], "floor_checks": 0}
            def restore():
                state["floor_checks"] += 1
                state["wall"] = max(state["wall"], FLOOR)
            def signed_pull(remote, options, progress, cancellable):
                state["pulls"].append((remote, options, state["wall"]))
                if state["wall"] < FLOOR:
                    raise RuntimeError("release key is in the future")
            repo = SimpleNamespace(set_disable_fsync=lambda _: None, pull_with_options=signed_pull,
                                   remote_delete=lambda *a: None)
            context = SimpleNamespace(push_thread_default=lambda: None, pop_thread_default=lambda: None)
            progress = SimpleNamespace(connect=lambda *a: None, get_status=lambda: "")
            namespace = dict(create_new_context=lambda: context,
                RpmOstree=SimpleNamespace(varsubst_basearch=lambda ref: ref), _=lambda text: text,
                OSTree=SimpleNamespace(AsyncProgress=SimpleNamespace(new=lambda: progress),
                    RepoPullFlags=SimpleNamespace(UNTRUSTED=99), check_version=lambda *a: False,
                    Sysroot=SimpleNamespace(new=lambda _: SimpleNamespace(load=lambda _: None,
                                                                         get_repo=lambda _: (True, repo)))),
                Gio=SimpleNamespace(File=SimpleNamespace(new_for_path=lambda path: path)),
                conf=SimpleNamespace(target=SimpleNamespace(physical_root="/mnt/sysroot")),
                Variant=lambda kind, data: (kind, data), GError=OSError, PayloadInstallationError=ValueError,
                log=logging.getLogger("pull-test"), _luma_restore_clock_floor=restore)
            exec(compile(module, "native-anaconda-signed-pull", "exec"), namespace)
            task = namespace["PullRemoteAndDeleteTask"]()
            task._data = SimpleNamespace(ref="luma/ref", remote="luma")
            task.report_progress = lambda _: None
            task._pull_progress_cb = lambda _: None
            return task.run, state
        run, original = pull(False)
        with self.assertRaisesRegex(RuntimeError, "future"):
            run()
        self.assertEqual(original["floor_checks"], 0)
        run, fixed = pull(True)
        run()
        self.assertEqual(fixed["floor_checks"], 1)
        self.assertEqual(fixed["pulls"], [("luma", ("a{sv}", {"refs": ("as", ["luma/ref"]),
                                          "flags": ("i", 99)}), FLOOR)])

    def test_final_stage_proof_requires_both_complete_native_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "usr/lib64/python3.14/site-packages/pyanaconda"
            base.mkdir(parents=True)
            clock = base / "timezone.py"
            pull = base / "modules/payloads/payload/rpm_ostree/installation.py"
            pull.parent.mkdir(parents=True)
            clock.write_bytes(UPSTREAM)
            pull.write_bytes(PULL_UPSTREAM + b"\n")
            with self.assertRaises(ValueError):
                PATCH.prepare(root)
            self.assertEqual(clock.read_bytes(), UPSTREAM)
            pull.write_bytes(PULL_UPSTREAM)
            PATCH.prepare(root)
            PATCH.verify(root)
            pull.write_bytes(PULL_UPSTREAM)
            with self.assertRaisesRegex(ValueError, "missing from final stage 2"):
                PATCH.verify(root)


if __name__ == "__main__":
    unittest.main()
