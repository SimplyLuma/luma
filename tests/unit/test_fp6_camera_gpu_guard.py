# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD_PATH = (
    REPO_ROOT
    / "config/mobile/fp6-physical/overlay/usr/local/libexec/luma-fp6-camera-gpu-guard"
)
loader = importlib.machinery.SourceFileLoader("luma_fp6_camera_gpu_guard", str(GUARD_PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None
guard = importlib.util.module_from_spec(spec)
loader.exec_module(guard)


class Fp6CameraGpuGuardTests(unittest.TestCase):
    def test_fp6_compatible_requires_exact_nul_separated_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            compatible = Path(temporary) / "compatible"
            compatible.write_bytes(b"qcom,milos\0fairphone,fp6\0")
            self.assertTrue(guard.is_fp6(compatible))
            compatible.write_bytes(b"qcom,milos\0fairphone,fp6-pro\0")
            self.assertFalse(guard.is_fp6(compatible))

    def test_camera_pid_selection_ignores_root_and_unrelated_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            proc_root = Path(temporary)
            fixtures = {
                "101": (1000, b"/usr/bin/python3\0/usr/bin/prairie-camera\0"),
                "102": (0, b"/usr/bin/prairie-camera\0"),
                "103": (1000, b"/usr/bin/prairie-notes\0"),
                "104": (1000, b"/usr/bin/python3\0/home/luma/camera-preview/prairie-camera\0"),
            }
            for pid, (uid, command) in fixtures.items():
                process = proc_root / pid
                process.mkdir()
                (process / "status").write_text(
                    f"Name:\ttest\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n",
                    encoding="utf-8",
                )
                (process / "cmdline").write_bytes(command)
            self.assertEqual(guard.camera_pids(proc_root), (101, 104))

    def test_fault_record_accepts_only_known_bounded_kernel_signatures(self) -> None:
        self.assertIsNone(guard.fault_record(b"camera frame timeout\n"))
        record = guard.fault_record(
            b"noise\nmsm_dpu: hangcheck detected gpu lockup rb 0!\nmore\n"
        )
        self.assertEqual(record, b"msm_dpu: hangcheck detected gpu lockup rb 0!")
        self.assertLessEqual(len(record or b""), 512)


if __name__ == "__main__":
    unittest.main()
