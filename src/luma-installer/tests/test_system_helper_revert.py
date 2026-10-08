# SPDX-License-Identifier: Apache-2.0
"""luma-installer-system flatpak-revert refuses anything but an app id and a full commit."""

import io
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

from luma_installer import system_helper


class Refusals(unittest.TestCase):
    def run_helper(self, *args):
        out = io.StringIO()
        with mock.patch.object(os, "geteuid", return_value=0), redirect_stdout(out):
            code = system_helper.main(["flatpak-revert", *args])
        return code, out.getvalue()

    def test_bad_arguments_are_refused_before_flatpak_is_touched(self):
        for app_id, commit in (("../../etc", "a" * 64), ("org.example.Notes", "abc"),
                               ("org.example.Notes", "A" * 64), ("notes", "a" * 64),
                               ("org.example.Notes; rm -rf /", "a" * 64)):
            with mock.patch("luma_installer.depot_flatpak.revert_transaction") as transaction:
                code, output = self.run_helper(app_id, commit)
            self.assertEqual(code, 1)
            self.assertIn("refused:", output)
            transaction.assert_not_called()

    def test_not_root_is_refused(self):
        with mock.patch.object(os, "geteuid", return_value=1000):
            self.assertEqual(system_helper.main(["flatpak-revert", "org.example.Notes", "a" * 64]), 1)


if __name__ == "__main__":
    unittest.main()
