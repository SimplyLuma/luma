#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the real GI ref parser at the signed runtime observer boundary."""
import importlib.util
from pathlib import Path
import unittest
import gi

gi.require_version('Flatpak', '1.0')
from gi.repository import GLib

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/os/qa/viola-native/read-native-state.py'
SPEC = importlib.util.spec_from_file_location('native_state_observer', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
REF = 'runtime/org.projectluma.Platform/aarch64/44'
COMMIT = '30f7e30d5d68e20c6c39bcb933508f3706446b39f0aaf2a353a1917544cdc925'


class SignedRuntimeIdentityTest(unittest.TestCase):
    def deployment(self):
        return {'runtime_ref': REF, 'runtime_commit': COMMIT, 'refs': {REF: COMMIT}}

    def test_full_flatpak_info_ref_and_exact_signed_commit(self):
        MODULE.validate_runtime_identity(REF, COMMIT, self.deployment())

    def test_kind_name_arch_branch_and_short_preferences_are_rejected(self):
        for value in ('org.projectluma.Platform/aarch64/44',
                      'app/org.projectluma.Platform/aarch64/44',
                      'runtime/org.projectluma.Sdk/aarch64/44',
                      'runtime/org.projectluma.Platform/x86_64/44',
                      'runtime/org.projectluma.Platform/aarch64/45'):
            with self.subTest(value=value), self.assertRaises((AssertionError, GLib.Error)):
                MODULE.validate_runtime_identity(value, COMMIT, self.deployment())

    def test_changed_commit_and_disagreeing_signed_map_are_rejected(self):
        with self.assertRaises(AssertionError):
            MODULE.validate_runtime_identity(REF, 'a' * 64, self.deployment())
        deployment = self.deployment()
        deployment['refs'][REF] = 'b' * 64
        with self.assertRaises(AssertionError):
            MODULE.validate_runtime_identity(REF, COMMIT, deployment)
        deployment = self.deployment()
        deployment['runtime_ref'] = 'runtime/org.projectluma.Sdk/aarch64/44'
        with self.assertRaises(AssertionError):
            MODULE.validate_runtime_identity(REF, COMMIT, deployment)


if __name__ == '__main__':
    unittest.main()
