"""Exercise guard admission and emitted limits without running host operations."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class MemoryAdmissionTests(unittest.TestCase):
    def run_guard(self, profile=None, state="shut off", available_gib=48):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src/ProjectLuma").mkdir(parents=True)
            (root / "logs").mkdir()
            commands = root / "bin"
            commands.mkdir()
            stubs = {
                "id": 'printf "0\\n"',
                "virsh": 'printf "%s\\n" "$TEST_VM_STATE"',
                "awk": 'printf "%s\\n" "$TEST_AVAILABLE"',
                "systemd-run": 'printf "%s\\n" "$@" > "$TEST_ARGUMENTS"',
                "journalctl": "exit 0",
            }
            for name, body in stubs.items():
                path = commands / name
                path.write_text("#!/bin/sh\n" + body + "\n")
                path.chmod(0o755)
            arguments = root / "arguments"
            env = dict(os.environ, PATH=f"{commands}:{os.environ['PATH']}",
                       LUMA_BUILD_ROOT=str(root), TEST_VM_STATE=state,
                       TEST_AVAILABLE=str(available_gib * 1024**3),
                       TEST_ARGUMENTS=str(arguments))
            env.pop("LUMA_BUILD_MEMORY_PROFILE", None)
            if profile is not None:
                env["LUMA_BUILD_MEMORY_PROFILE"] = profile
            result = subprocess.run(
                ["bash", str(ROOT / "scripts/build-host/luma-build-run"), "true"],
                env=env, capture_output=True, text=True)
            return result, arguments.read_text() if arguments.exists() else ""

    def test_default_keeps_shared_limits_even_with_vm_off(self):
        result, args = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--property=MemoryHigh=16G\n", args)
        self.assertIn("--property=MemoryMax=20G\n", args)

    def test_explicit_standalone_keeps_hard_and_swap_limits(self):
        result, args = self.run_guard("standalone")
        self.assertEqual(result.returncode, 0, result.stderr)
        for option in ("MemoryHigh=32G", "MemoryMax=36G", "MemorySwapMax=2G",
                       "CPUQuota=1400%", "Nice=10"):
            self.assertIn(f"--property={option}\n", args)

    def test_refuses_standalone_while_vm_running(self):
        result, args = self.run_guard("standalone", state="running")
        self.assertEqual(result.returncode, 75)
        self.assertIn("requires", result.stderr)
        self.assertEqual(args, "")

    def test_refuses_insufficient_headroom(self):
        for profile, available in (("standalone", 39), ("shared", 7)):
            with self.subTest(profile=profile):
                result, args = self.run_guard(profile, available_gib=available)
                self.assertEqual(result.returncode, 75)
                self.assertEqual(args, "")

    def test_invalid_profile_and_unknown_vm_state_fail_closed(self):
        for profile, state in (("unbounded", "shut off"), ("standalone", "paused")):
            result, args = self.run_guard(profile, state=state)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(args, "")


if __name__ == "__main__":
    unittest.main()
