# SPDX-License-Identifier: Apache-2.0
import unittest

from luma_android.apk import architecture_notice


class ArchitectureNoticeTest(unittest.TestCase):
    def test_foreign_build_notice_names_host_and_package(self):
        notice = architecture_notice(("arm64-v8a",), "x86_64")
        self.assertIn("x86 (Intel/AMD)", notice)
        self.assertIn("ARM64", notice)
        self.assertIn("universal", notice)
        self.assertIn("only if Android supports", notice)
        reverse = architecture_notice(("x86_64",), "aarch64")
        self.assertIn("computer uses ARM", reverse)
        self.assertIn("Intel/AMD 64-bit", reverse)

    def test_neutral_and_matching_family_do_not_warn(self):
        for machine, abis in [
            ("x86_64", ()), ("x86_64", ("x86",)),
            ("amd64", ("arm64-v8a", "x86_64")),
            ("aarch64", ("armeabi-v7a",)), ("arm64", ("arm64-v8a",)),
            ("unknown", ("arm64-v8a",)),
        ]:
            with self.subTest(machine=machine, abis=abis):
                self.assertEqual(architecture_notice(abis, machine), "")


if __name__ == "__main__":
    unittest.main()
