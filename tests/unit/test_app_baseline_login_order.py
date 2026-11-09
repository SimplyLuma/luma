# SPDX-License-Identifier: Apache-2.0
"""The first-boot app baseline may delay the login screen briefly, never block it.

GDM once Requires= luma-app-baseline.service, whose first offline import could
outlast its start timeout on a slow device: the import failed and the person
got no login screen at all.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / 'src/luma-installer/data'
UNIT = DATA / 'luma-app-baseline.service'
DROPIN = DATA / 'luma-app-baseline-display-manager.conf'
OS_HANDOFF = REPO / 'image/luma-desktop/rootfs/usr/lib/systemd/system/luma-app-baseline.service.d/10-luma-os-handoff.conf'
SPEC = REPO / 'packaging/rpm/luma-application-installer.spec'
STRONG = ('Requires', 'Requisite', 'BindsTo', 'PartOf', 'Upholds')


def tracked():
    return subprocess.run(['git', 'ls-files'], cwd=REPO, check=True, capture_output=True,
                          text=True).stdout.splitlines()


def settings(text):
    result = {}
    for line in text.splitlines():
        match = re.match(r'\s*([A-Za-z]+)\s*=\s*(.*)$', line)
        if match:
            result.setdefault(match.group(1), []).extend(match.group(2).split())
    return result


def seconds(value):
    total = 0
    for number, unit in re.findall(r'(\d+)\s*(min|s|h)?', value):
        total += int(number) * {'min': 60, 'h': 3600}.get(unit, 1)
    return total


class LoginOrder(unittest.TestCase):
    def test_no_display_manager_unit_strongly_depends_on_the_baseline(self):
        dropins = [REPO / path for path in tracked()
                   if re.search(r'(^|/)(display-manager|gdm)\.service\.d/[^/]+\.conf$', path)]
        for path in dropins + [DROPIN]:
            values = settings(path.read_text())
            for key in STRONG:
                self.assertNotIn('luma-app-baseline.service', values.get(key, []), f'{path}: {key}=')
        values = settings(DROPIN.read_text())
        self.assertEqual(values.get('Wants'), ['luma-app-baseline.service'])
        self.assertEqual(values.get('After'), ['luma-app-baseline.service'])
        self.assertIn('%{_unitdir}/display-manager.service.d/20-luma-app-baseline.conf', SPEC.read_text())

    def test_one_source_for_the_unit_and_the_display_manager_ordering(self):
        files = tracked()
        self.assertEqual([path for path in files if path.endswith('/luma-app-baseline.service')],
                         ['src/luma-installer/data/luma-app-baseline.service'])
        mentions = [path for path in files if re.search(r'\.service\.d/[^/]+\.conf$', path)
                    and 'luma-app-baseline' in (REPO / path).read_text()]
        self.assertEqual(mentions, [str(OS_HANDOFF.relative_to(REPO))])
        self.assertEqual(settings(OS_HANDOFF.read_text()), {'ExecStart': ['/usr/libexec/luma-app-baseline']})

    def test_baseline_reports_ready_within_a_bound_and_runs_to_completion(self):
        values = settings(UNIT.read_text())
        self.assertEqual(values.get('Type'), ['notify'])
        self.assertLessEqual(seconds(' '.join(values['TimeoutStartSec'])), 600)
        self.assertNotIn('RuntimeMaxSec', values)
        self.assertNotIn('TimeoutSec', values)

    @unittest.skipUnless(shutil.which('systemd-analyze'), 'systemd-analyze is not installed')
    def test_systemd_loads_the_units_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            system = root / 'usr/lib/systemd/system'
            (system / 'display-manager.service.d').mkdir(parents=True)
            (system / 'luma-app-baseline.service.d').mkdir()
            shutil.copy(UNIT, system)
            shutil.copy(DROPIN, system / 'display-manager.service.d/20-luma-app-baseline.conf')
            shutil.copy(OS_HANDOFF, system / 'luma-app-baseline.service.d')
            (system / 'gdm.service').write_text('[Unit]\nDescription=GDM\n[Service]\nExecStart=/usr/sbin/gdm\n'
                                                '[Install]\nAlias=display-manager.service\n')
            os.symlink('gdm.service', system / 'display-manager.service')
            for program in ('usr/bin/luma-seed-app-baseline', 'usr/libexec/luma-app-baseline',
                            'usr/bin/python3', 'usr/sbin/gdm'):
                path = root / program
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('#!/bin/sh\n')
                path.chmod(0o755)
            result = subprocess.run(['systemd-analyze', 'verify', '--man=no', '--recursive-errors=no',
                                     f'--root={root}', str(system / 'luma-app-baseline.service'),
                                     str(system / 'gdm.service')],
                                    capture_output=True, text=True)
            problems = [line for line in result.stderr.splitlines()
                        if 'luma-app-baseline' in line or 'gdm.service' in line or 'display-manager' in line]
            self.assertEqual(problems, [], result.stderr)


if __name__ == '__main__':
    unittest.main()
