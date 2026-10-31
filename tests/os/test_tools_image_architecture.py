"""Release tooling executes on the signer/builder host, independently of its target."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PINS = dict(line.split('=', 1) for line in (ROOT / 'config/desktop/inputs.env').read_text().splitlines()
            if line.startswith('FEDORA_RPM_BUILD_CONTAINER'))


class ToolsArchitectureTests(unittest.TestCase):
    def run_tools(self, host, target, image_arch, cached=False):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'calls'
            script = r'''
source "$TEST_ROOT/scripts/os/lib/common.sh"
uname() { printf '%s\n' "$TEST_HOST"; }
LUMA_OS_ARCH=$TEST_TARGET
luma_os_podman() {
  printf '%s\n' "$*" >> "$TEST_LOG"
  case "$1 $2" in
    'image exists') [ "$TEST_CACHED" = 1 ] ;;
    'image inspect') printf '%s\n' "$TEST_IMAGE_ARCH" ;;
    'build --arch') return 0 ;;
    *) return 23 ;;
  esac
}
luma_os_tools_image
'''
            env = dict(os.environ, TEST_ROOT=str(ROOT), TEST_HOST=host,
                       TEST_TARGET=target, TEST_IMAGE_ARCH=image_arch,
                       TEST_CACHED=str(int(cached)), TEST_LOG=str(log))
            env.pop('LUMA_TARGET_ARCHITECTURE', None)
            result = subprocess.run(['bash', '-e', '-c', script], env=env,
                                    capture_output=True, text=True)
            return result, log.read_text() if log.exists() else ''

    def test_arm_tools_on_arm_host_exporting_x86(self):
        result, calls = self.run_tools('aarch64', 'x86_64', 'arm64')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('build --arch aarch64 ', calls)
        self.assertIn('FEDORA_IMAGE=' + PINS['FEDORA_RPM_BUILD_CONTAINER_AARCH64'], calls)

    def test_x86_signer_tools_for_arm_target(self):
        result, calls = self.run_tools('x86_64', 'aarch64', 'amd64')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('build --arch x86_64 ', calls)
        self.assertIn('FEDORA_IMAGE=' + PINS['FEDORA_RPM_BUILD_CONTAINER'], calls)

    def test_architecture_pins_cannot_share_cached_tag(self):
        arm, _ = self.run_tools('aarch64', 'aarch64', 'arm64')
        x86, _ = self.run_tools('x86_64', 'aarch64', 'amd64')
        self.assertEqual(arm.returncode, 0, arm.stderr)
        self.assertEqual(x86.returncode, 0, x86.stderr)
        self.assertNotEqual(arm.stdout.strip(), x86.stdout.strip())

    def test_wrong_architecture_cached_image_is_refused(self):
        result, calls = self.run_tools('aarch64', 'aarch64', 'amd64', cached=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('differs from executing host architecture', result.stderr)
        self.assertNotIn('build --arch', calls)

    def test_unknown_host_is_refused_before_podman(self):
        result, calls = self.run_tools('riscv64', 'aarch64', 'arm64')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unsupported release tools host', result.stderr)
        self.assertEqual(calls, '')


if __name__ == '__main__':
    unittest.main()
