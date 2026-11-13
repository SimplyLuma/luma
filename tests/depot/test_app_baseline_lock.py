# SPDX-License-Identifier: Apache-2.0
import datetime
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
APP = 'org.projectluma.Clock'


def load(repo):
    spec = importlib.util.spec_from_file_location('app_baseline_lock', repo / 'scripts/depot/app-baseline-lock.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Lock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        for name in ('LICENSE.md', 'scripts/depot/seal-first-party-source.py', 'scripts/depot/app-baseline-lock.py',
                     'packaging/flatpak/first-party-updates.json',
                     f'assets/icon-theme/Prairie/scalable/apps/{APP}.svg'):
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, self.repo / name)
        for name in (f'packaging/flatpak/apps/{APP}', 'src/prairie-core'):
            shutil.copytree(ROOT / name, self.repo / name, ignore=shutil.ignore_patterns('__pycache__'))
        (self.repo / 'config/os').mkdir(parents=True)
        (self.repo / 'config/os/first-party-app-baseline.json').write_text(json.dumps(
            {'schema': 'org.projectluma.os-app-baseline/v1', 'applications': [APP]}))
        self.lock = load(self.repo)
        self.row = {'version': self.lock.metainfo_version(self.repo, APP),
                    'source_sha256': self.lock.fingerprint(self.repo, APP),
                    'commits': {'aarch64': 'a' * 64, 'x86_64': 'b' * 64}}
        self.write_lock()

    def tearDown(self):
        self.tmp.cleanup()

    def write_lock(self):
        (self.repo / 'config/os/app-baseline-lock.json').write_text(json.dumps({
            'schema': 'org.projectluma.os-app-baseline-lock/v1',
            'baselines': {'aarch64': {'manifest_sha256': 'c' * 64}, 'x86_64': {'manifest_sha256': 'd' * 64}},
            'applications': {APP: self.row}}))

    def test_matching_source_passes(self):
        self.assertTrue(self.lock.check(self.repo))

    def test_changed_app_source_fails_until_rebuilt(self):
        clock = self.repo / 'src/prairie-core/prairie_apps/clock.py'
        clock.write_text(clock.read_text() + '\n# changed\n')
        self.assertFalse(self.lock.check(self.repo))
        self.row['source_sha256'] = self.lock.fingerprint(self.repo, APP)
        self.write_lock()
        self.assertTrue(self.lock.check(self.repo))

    def test_recipe_change_counts_as_source(self):
        recipe = self.repo / f'packaging/flatpak/apps/{APP}/{APP}.yml'
        recipe.write_text(recipe.read_text() + '\n')
        self.assertFalse(self.lock.check(self.repo))

    def test_tests_do_not_change_the_fingerprint(self):
        test = self.repo / 'src/prairie-core/tests/test_new_case.py'
        test.parent.mkdir(exist_ok=True)
        test.write_text('pass\n')
        self.assertTrue(self.lock.check(self.repo))

    def test_version_must_match_metainfo(self):
        self.row['version'] = '0.0.1'
        self.write_lock()
        self.assertFalse(self.lock.check(self.repo))

    def test_pending_rebuild_expires(self):
        clock = self.repo / 'src/prairie-core/prairie_apps/clock.py'
        clock.write_text(clock.read_text() + '\n# changed\n')
        self.row['pending'] = {'reason': 'rebuild in progress', 'expires': '2026-11-16'}
        self.write_lock()
        self.assertTrue(self.lock.check(self.repo, today=datetime.date(2026, 11, 15)))
        self.assertFalse(self.lock.check(self.repo, today=datetime.date(2026, 11, 16)))

    def test_image_build_refuses_an_unpinned_baseline(self):
        baseline = self.repo / 'baseline'
        baseline.mkdir()
        (baseline / 'manifest.json').write_text(json.dumps({'architecture': 'x86_64', 'bundles': []}))
        self.assertFalse(self.lock.verify_baseline(self.repo, 'x86_64', baseline))


class RepositoryLock(unittest.TestCase):
    def test_every_shipped_app_matches_its_baseline_bundle(self):
        self.assertTrue(load(ROOT).check(ROOT))


if __name__ == '__main__':
    unittest.main()
