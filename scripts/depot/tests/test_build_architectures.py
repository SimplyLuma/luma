"""Host namespace tools and target SDK selection remain independent."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class BuildArchitectures(unittest.TestCase):
    def profile(self, target, tools=None):
        env = dict(os.environ, DEPOT_ARCH=target)
        env.pop('DEPOT_TOOLS_ARCH', None)
        if tools is not None:
            env['DEPOT_TOOLS_ARCH'] = tools
        return subprocess.run(['bash', '-c', '. "$1/scripts/depot/lib.sh"; '
                               'depot_validate_build_architectures && '
                               'printf "%s/%s" "$DEPOT_TOOLS_ARCH" "$DEPOT_ARCH"',
                               'profile', str(ROOT)], env=env, capture_output=True, text=True)

    def test_native_x64_default(self):
        r = self.profile('x86_64'); self.assertEqual((r.returncode, r.stdout), (0, 'x86_64/x86_64'))

    def test_native_arm_default(self):
        r = self.profile('aarch64'); self.assertEqual((r.returncode, r.stdout), (0, 'aarch64/aarch64'))

    def test_explicit_native_pairs(self):
        for cpu in ('x86_64', 'aarch64'):
            with self.subTest(cpu=cpu): self.assertEqual(self.profile(cpu, cpu).returncode, 0)

    def test_native_x64_tools_with_arm_sdk(self):
        r = self.profile('aarch64', 'x86_64')
        self.assertEqual((r.returncode, r.stdout), (0, 'x86_64/aarch64'))

    def test_reverse_and_unknown_pairs_rejected(self):
        for target, tools in [('x86_64', 'aarch64'), ('ppc64le', 'x86_64'),
                              ('aarch64', 'riscv64'), ('bad/cpu', 'x86_64')]:
            with self.subTest(target=target, tools=tools):
                r = self.profile(target, tools); self.assertNotEqual(r.returncode, 0)
                self.assertIn('unsupported tools/target CPU pair', r.stderr)

    def test_actual_tools_cpu_matches_for_native_and_cross_target_exports(self):
        for target, tools, actual, expected in [
            ('x86_64', 'x86_64', 'x86_64', 0),
            ('aarch64', 'aarch64', 'aarch64', 0),
            ('aarch64', 'x86_64', 'x86_64', 0),
            ('aarch64', 'aarch64', 'x86_64', 1),
            ('aarch64', 'x86_64', 'aarch64', 1),
        ]:
            with self.subTest(target=target, tools=tools, actual=actual):
                env = dict(os.environ, DEPOT_ARCH=target, DEPOT_TOOLS_ARCH=tools)
                result = subprocess.run(
                    ['bash', '-c', '. "$1/scripts/depot/lib.sh"; '
                     'depot_validate_build_architectures && '
                     'depot_validate_actual_tools_cpu "$2"', 'profile', str(ROOT), actual],
                    env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, expected)
                if expected:
                    self.assertIn('differs from declared tools CPU', result.stderr)

    def test_invalid_job_limits_fail_before_manifest_or_build(self):
        for jobs in ('0', '-1', '17', '2 3', '2;false'):
            env = dict(os.environ, DEPOT_ARCH='aarch64', DEPOT_TOOLS_ARCH='x86_64', DEPOT_BUILD_JOBS=jobs)
            r = subprocess.run(['bash', str(ROOT/'scripts/depot/build-app.sh'), '/no-such-build-manifest.yml'],
                               env=env, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0); self.assertIn('DEPOT_BUILD_JOBS must be', r.stderr)

    def test_valid_job_limit_reaches_manifest_check(self):
        env = dict(os.environ, DEPOT_ARCH='aarch64', DEPOT_TOOLS_ARCH='x86_64', DEPOT_BUILD_JOBS='2')
        r = subprocess.run(['bash', str(ROOT/'scripts/depot/build-app.sh'), '/no-such-build-manifest.yml'],
                           env=env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0); self.assertIn('no manifest', r.stderr)
        self.assertNotIn('DEPOT_BUILD_JOBS must be', r.stderr)

    def runtime_calls(self, target, tools=None):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp); bin = tmp/'bin'; bin.mkdir(); depot = tmp/'depot'; depot.mkdir()
            (depot/'site').mkdir(); (depot/'site/luma.flatpakrepo').write_text('[Flatpak Repo]\nUrl=https://example.invalid/repo\n')
            log = tmp/'calls'
            mock = bin/'flatpak'; mock.write_text('#!/usr/bin/python3\nimport json,os,sys\nwith open(os.environ["MOCK_LOG"],"a") as f:f.write(json.dumps(sys.argv[1:])+"\\n")\n')
            mock.chmod(0o755)
            env = dict(os.environ, PATH=str(bin)+':'+os.environ['PATH'], DEPOT_ROOT=str(depot),
                       DEPOT_ARCH=target, DEPOT_INSIDE_TOOLS='1', MOCK_LOG=str(log))
            env.pop('DEPOT_TOOLS_ARCH', None)
            if tools is not None: env['DEPOT_TOOLS_ARCH'] = tools
            subprocess.run(['bash', str(ROOT/'scripts/depot/install-build-runtime.sh')], env=env,
                           check=True, capture_output=True, text=True)
            return [json.loads(line) for line in log.read_text().splitlines()]

    def test_sdk_uses_target_but_linter_uses_native_tools(self):
        calls = [c for c in self.runtime_calls('aarch64', 'x86_64') if c[0] == 'install']
        self.assertEqual(len(calls), 2)
        self.assertIn('--arch=aarch64', calls[0]); self.assertIn('org.projectluma.Sdk//44', calls[0])
        self.assertIn('--arch=x86_64', calls[1]); self.assertIn('org.flatpak.Builder', calls[1])

    def test_native_default_runtime_and_linter_still_match(self):
        calls = [c for c in self.runtime_calls('x86_64') if c[0] == 'install']
        self.assertEqual(len(calls), 2)
        self.assertTrue(all('--arch=x86_64' in c for c in calls))


if __name__ == '__main__':
    unittest.main()
