#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Execute production Bash cleanliness functions against real Git fixtures.

Repo enumeration is an explicit dependency fixture. This is not a full Android
source/image build. The preserved old function demonstrates the false-clean
Git-error control; no Android tree is changed.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = 'scripts/android/prepare-prairie-waydroid-tree.sh'


def functions(source):
    return source[source.index('dirty_projects() {'):source.index('\nofficial_input_sha() {')]


class Cleanliness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='luma-source-cleanliness-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        self.project = self.base / 'source' / 'project'
        self.project.mkdir(parents=True)
        self.git = shutil.which('git')
        for args in (('init', '-q'), ('config', 'user.email', 'fixture@invalid.example'),
                     ('config', 'user.name', 'Synthetic fixture')):
            subprocess.run([self.git, '-C', str(self.project), *args], check=True)
        (self.project / 'tracked').write_text('synthetic original\n')
        subprocess.run([self.git, '-C', str(self.project), 'add', 'tracked'], check=True)
        subprocess.run([self.git, '-C', str(self.project), 'commit', '-q', '-m', 'Fixture'], check=True)
        repo = self.bin / 'repo'
        repo.write_text('''#!/usr/bin/env python3
import os,pathlib,subprocess,sys
args=sys.argv[1:]
if os.environ.get('FAIL_REPO'):sys.exit(71)
assert args[0]=='forall'
if '-j' in args:pathlib.Path(os.environ['JOB_RECORD']).write_text(args[args.index('-j')+1])
command=args[args.index('-c')+1]
env=dict(os.environ,REPO_PATH='project')
result=subprocess.run(['bash','-c',command],cwd=pathlib.Path.cwd()/'project',env=env)
sys.exit(result.returncode)
''')
        repo.chmod(0o700)
        git = self.bin / 'git'
        git.write_text('#!/bin/bash\nif [[ -n ${FAIL_GIT:-} ]]; then exit 128; fi\nexec ' + self.git + ' "$@"\n')
        git.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        JOB_RECORD=str(self.base / 'jobs'))
        self.source = (ROOT / SCRIPT).read_text()

    def check_tree(self, source=None, **env):
        body = functions(source or self.source)
        if 'tree_is_clean()' not in body:
            body += '\ntree_is_clean() { test -z "$(dirty_projects)"; }\n'
        return subprocess.run(['bash', '-c', 'set -euo pipefail\nsource_root="$1"\n' + body + '\ntree_is_clean',
                               'fixture', str(self.project.parent)], env=dict(self.env, **env),
                              capture_output=True, text=True, timeout=10)

    def test_clean_source_and_bounded_default(self):
        self.assertEqual(self.check_tree().returncode, 0)
        self.assertEqual((self.base / 'jobs').read_text(), '2')

    def test_tracked_change_untracked_file_and_deletion_are_rejected(self):
        tracked = self.project / 'tracked'
        tracked.write_text('changed\n')
        self.assertEqual(self.check_tree().returncode, 1)
        tracked.write_text('synthetic original\n')
        extra = self.project / 'untracked'
        extra.write_text('new source\n')
        self.assertEqual(self.check_tree().returncode, 1)
        extra.unlink()
        tracked.unlink()
        self.assertEqual(self.check_tree().returncode, 1)

    def test_git_failure_is_not_a_clean_tree(self):
        self.assertEqual(self.check_tree(FAIL_GIT='1').returncode, 2)
        original = '''dirty_projects() {
  (
    cd "$source_root"
    repo forall -c 'test -z "$(git status --porcelain)" || printf "%s\\n" "$REPO_PATH"'
  )
}
official_input_sha() {
'''
        self.assertEqual(self.check_tree(original, FAIL_GIT='1').returncode, 0,
                         'Preserved old implementation must demonstrate the false-clean control')

    def test_repo_failure_is_not_a_clean_tree(self):
        self.assertEqual(self.check_tree(FAIL_REPO='1').returncode, 2)

    def test_explicit_concurrency_is_bounded(self):
        self.assertEqual(self.check_tree(LUMA_ANDROID_VERIFY_JOBS='8').returncode, 0)
        self.assertEqual((self.base / 'jobs').read_text(), '8')
        for value in ('0', '9', '-1', 'anything'):
            self.assertEqual(self.check_tree(LUMA_ANDROID_VERIFY_JOBS=value).returncode, 2)

    def test_every_destructive_and_incremental_caller_propagates_failure(self):
        self.assertEqual(self.source.count('tree_is_clean || {'), 4)
        self.assertNotIn('test -z "$(dirty_projects)"', self.source)


if __name__ == '__main__':
    unittest.main()
