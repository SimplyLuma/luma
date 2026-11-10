"""Exercise the actual adapter gate without opening the modem."""
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/mobile/luma-fp6-carrier-pdc"

class KernelAdmission(unittest.TestCase):
    def allowed(self, release):
        result = subprocess.run(["bash", "-c", 'source "$1"; supported_kernel "$2"',
            "carrier-test", str(SCRIPT), release], capture_output=True, timeout=5)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        return result.returncode == 0

    def test_exact_native_releases(self):
        for release in ("7.1.2-luma-fp-ims1", "7.1.2-luma-fp-ims1.gmu1", "7.1.2-luma-fp-ims1.gmu2", "7.1.2-luma-fp-ims1.gmu2.selinux1"):
            with self.subTest(release=release): self.assertTrue(self.allowed(release))

    def test_other_releases_are_rejected(self):
        for release in ("", "7.1.2", "7.1.2-luma-fp-ims1.gmu2.selinux1-extra", "7.1.2-luma-fp-ims1.gmu2.selinux2", "7.1.2-luma-fp-ims1.gmu3", "7.1.2-luma-fp-ims1.gmu2-extra",
                        "7.1.2-luma-fp-ims10", "7.1.2-luma-fp-ims1.android", "6.6.0-android", "$(id)"):
            with self.subTest(release=release): self.assertFalse(self.allowed(release))

if __name__ == "__main__": unittest.main()
