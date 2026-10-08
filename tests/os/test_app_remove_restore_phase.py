# SPDX-License-Identifier: Apache-2.0
"""Phase controls for the real removal gate's non-root readability query.

Only account selection and child-command construction are isolated here.
Actual removal, GPG restore and 27-ref readability remain VM gates.
"""
from pathlib import Path
import ast
import json
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'tests/os/gate/app-remove-restore.py'


def controls(path=SCRIPT):
    tree = ast.parse(path.read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in ('require', 'readability_account', 'ordinary_refs')]
    assert len(functions) == 3
    namespace = {'Path': Path, 'pwd': types.SimpleNamespace(), 'json': json,
                 'os': types.SimpleNamespace(), 'tempfile': types.SimpleNamespace(),
                 'shlex': __import__('shlex')}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


def account(name, uid, gid=1000, shell='/bin/bash'):
    return types.SimpleNamespace(pw_name=name, pw_uid=uid, pw_gid=gid, pw_shell=shell)


class RemovalReadabilityPhaseTests(unittest.TestCase):
    def setUp(self):
        self.module = controls()
        self.nobody = account('nobody', 65534, 65534, '/usr/sbin/nologin')
        self.module['pwd'].getpwall = lambda: [account('root', 0, 0), self.nobody]
        self.module['pwd'].getpwnam = lambda name: self.nobody

    def test_preaccount_selects_real_nonroot_system_identity(self):
        self.assertIs(self.module['readability_account'](True), self.nobody)

    def test_preaccount_refuses_existing_uid1000_even_without_login_shell(self):
        self.module['pwd'].getpwall = lambda: [account('service', 1000, shell='/sbin/nologin')]
        with self.assertRaisesRegex(ValueError, 'before UID1000'):
            self.module['readability_account'](True)

    def test_preaccount_refuses_other_interactive_uid(self):
        self.module['pwd'].getpwall = lambda: [account('bob', 1001)]
        with self.assertRaisesRegex(ValueError, 'interactive account'):
            self.module['readability_account'](True)

    def test_preaccount_refuses_high_uid_interactive_directory_account(self):
        self.module['pwd'].getpwall = lambda: [account('directory-user', 70000)]
        with self.assertRaisesRegex(ValueError, 'interactive account'):
            self.module['readability_account'](True)

    def test_preaccount_refuses_root_or_root_group_substitution(self):
        for wrong in [account('nobody', 0, 0), account('nobody', 65534, 0)]:
            self.module['pwd'].getpwnam = lambda name: wrong
            with self.assertRaisesRegex(ValueError, 'unprivileged nobody'):
                self.module['readability_account'](True)

    def test_default_requires_actual_uid1000_and_never_falls_back(self):
        def missing(uid):
            raise KeyError(uid)
        self.module['pwd'].getpwuid = missing
        with self.assertRaises(KeyError):
            self.module['readability_account']()
        alice = account('alice', 1000)
        self.module['pwd'].getpwuid = lambda uid: alice
        self.assertIs(self.module['readability_account'](), alice)

    def test_default_refuses_root_identity(self):
        self.module['pwd'].getpwuid = lambda uid: account('root', 0, 0)
        with self.assertRaisesRegex(ValueError, 'UID1000'):
            self.module['readability_account']()

    def test_preaccount_query_drops_identity_and_uses_only_owned_home(self):
        calls = []
        self.module['tempfile'].TemporaryDirectory = lambda **kwargs: __import__('contextlib').nullcontext('/var/tmp/owned-test-home')
        self.module['os'].chown = lambda *args: calls.append(('chown', args))
        def command(*args):
            calls.append(('command', args))
            return '{"app/org.projectluma.Leaf/x86_64/beta":{"commit":"real-head","origin":"luma"}}'
        self.module['command'] = command
        result = self.module['ordinary_refs'](self.nobody, True)
        self.assertEqual(calls[0], ('chown', ('/var/tmp/owned-test-home', 65534, 65534)))
        argv = calls[1][1]
        self.assertEqual(argv[:5], ('runuser', '-u', 'nobody', '--', 'env'))
        self.assertIn('HOME=/var/tmp/owned-test-home', argv)
        self.assertIn('assert os.geteuid()==65534 and os.geteuid()!=0', argv[-1])
        self.assertIn('Flatpak.Installation.new_system(None)', argv[-1])
        self.assertEqual(result['app/org.projectluma.Leaf/x86_64/beta']['origin'], 'luma')

    def test_query_failure_does_not_fall_back_to_root(self):
        self.module['tempfile'].TemporaryDirectory = lambda **kwargs: __import__('contextlib').nullcontext('/var/tmp/owned-test-home')
        self.module['os'].chown = lambda *args: None
        calls = []
        def failing(*args):
            calls.append(args)
            raise RuntimeError('actual child query refused')
        self.module['command'] = failing
        with self.assertRaisesRegex(RuntimeError, 'child query refused'):
            self.module['ordinary_refs'](self.nobody, True)
        self.assertEqual(len(calls), 1)

    def test_fresh_caller_explicit_phase_and_exact_ref_equality_remain(self):
        source = SCRIPT.read_text()
        gate = (ROOT / 'scripts/os/gate.sh').read_text()
        call = 'python3 -B /var/tmp/luma-app-remove-restore.py --pre-account'
        self.assertEqual(gate.count(call), 1)
        phase = gate.split('if [ "$stage" = fresh ]; then', 1)[1].split('\n  fi', 1)[0]
        self.assertIn(call, phase)
        self.assertIn("require(readable == before,", source)
        self.assertIn("ordinary = readability_account(args.pre_account)", source)


if __name__ == '__main__':
    unittest.main()
