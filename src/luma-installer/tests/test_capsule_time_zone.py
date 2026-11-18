import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from luma_installer import launcher

RECORD = {'application_id': 'rpm-google-chrome-abc', 'format': 'rpm', 'sha256': 'b' * 64,
          'image': 'localhost/luma-rpm-google-chrome:abc', 'command': 'google-chrome', 'chromium': True}


def _zone_tree(root: Path, link: str | None, zones=('America/Chicago', 'Europe/Berlin')) -> tuple[Path, Path]:
    etc, zoneinfo = root / 'etc', root / 'usr/share/zoneinfo'
    etc.mkdir(parents=True)
    for zone in zones:
        (zoneinfo / zone).parent.mkdir(parents=True, exist_ok=True)
        (zoneinfo / zone).write_bytes(b'TZif2')
    if link is not None:
        os.symlink(link, etc / 'localtime')
    return etc, zoneinfo


class HostTimeZone(unittest.TestCase):
    def zone(self, link, **kwargs):
        with tempfile.TemporaryDirectory() as root:
            etc, zoneinfo = _zone_tree(Path(root), link, **kwargs)
            return launcher.host_time_zone(etc, zoneinfo)

    def test_relative_link_as_timedatectl_writes_it(self):
        self.assertEqual(self.zone('../usr/share/zoneinfo/America/Chicago'), 'America/Chicago')

    def test_absolute_link(self):
        self.assertEqual(self.zone('/usr/share/zoneinfo/Europe/Berlin'), 'Europe/Berlin')

    def test_zone_missing_from_the_database_is_unknown(self):
        self.assertEqual(self.zone('../usr/share/zoneinfo/Asia/Tokyo'), '')

    def test_link_outside_the_database_is_unknown(self):
        self.assertEqual(self.zone('/srv/clock'), '')
        self.assertEqual(self.zone('../usr/share/zoneinfo/../../../etc/passwd'), '')

    def test_copied_file_or_no_file_is_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            etc, zoneinfo = _zone_tree(Path(root), None)
            self.assertEqual(launcher.host_time_zone(etc, zoneinfo), '')
            (etc / 'localtime').write_bytes(b'TZif2')
            self.assertEqual(launcher.host_time_zone(etc, zoneinfo), '')


class CapsuleLaunchArguments(unittest.TestCase):
    def launch(self, zone, environment=None):
        launched = []
        followed = []
        with tempfile.TemporaryDirectory() as home, \
             patch.dict(os.environ, environment or {}, clear=True), \
             patch('luma_installer.launcher._running_capsule', return_value=False), \
             patch('luma_installer.launcher._start_bus_proxy', return_value=(None, None)), \
             patch('luma_installer.launcher._private_home', return_value=Path(home)), \
             patch('luma_installer.launcher._person_folders', return_value=[]), \
             patch('luma_installer.launcher.host_time_zone', return_value=zone), \
             patch('luma_installer.launcher._follow_time_zone', side_effect=lambda *a: followed.append(a)), \
             patch('luma_installer.launcher._guarded', side_effect=lambda record, argv, env=None: launched.append(argv) or 0):
            self.assertEqual(launcher._launch_deb(dict(RECORD)), 0)
        argv = launched[0]
        return argv[:argv.index(RECORD['image'])], followed

    def test_capsule_runs_in_the_host_zone_by_name(self):
        options, followed = self.launch('America/Chicago')
        self.assertIn('--tz=America/Chicago', options)
        self.assertEqual(followed, [('luma-run-rpm-google-chrome-abc', 'America/Chicago')])

    def test_host_zone_database_is_shared_read_only(self):
        with tempfile.TemporaryDirectory() as zoneinfo, patch.object(launcher, 'ZONEINFO', Path(zoneinfo)):
            options, _followed = self.launch('America/Chicago')
        volume = options.index(f'{zoneinfo}:{zoneinfo}:ro')
        self.assertEqual(options[volume - 1], '--volume')

    def test_tz_is_not_set_so_zone_changes_reach_the_application(self):
        options, _followed = self.launch('America/Chicago')
        self.assertFalse([value for value in options if value.startswith('TZ=')])

    def test_unknown_zone_copies_the_host_file(self):
        options, followed = self.launch('')
        self.assertIn('--tz=local', options)
        self.assertEqual(followed, [])

    def test_person_set_tz_is_kept_and_not_overridden(self):
        options, followed = self.launch('America/Chicago', {'TZ': 'Europe/Berlin'})
        self.assertEqual(options[options.index('TZ=Europe/Berlin') - 1], '--env')
        self.assertEqual(followed, [])

    def test_joining_a_running_capsule_keeps_its_zone(self):
        launched = []
        with patch('luma_installer.launcher._running_capsule', return_value=True), \
             patch('luma_installer.launcher.runtime_policy.primary_argument_count', return_value=0), \
             patch('luma_installer.launcher._follow_time_zone') as follow, \
             patch('luma_installer.launcher._guarded', side_effect=lambda record, argv, env=None: launched.append(argv) or 0):
            launcher._launch_deb(dict(RECORD))
        self.assertEqual(launched[0][:3], ['podman', 'exec', 'luma-run-rpm-google-chrome-abc'])
        self.assertFalse([value for value in launched[0] if value.startswith('--tz')])
        follow.assert_not_called()


class FollowHostZone(unittest.TestCase):
    def test_capsule_link_follows_each_host_change(self):
        with tempfile.TemporaryDirectory() as root:
            etc, zoneinfo = _zone_tree(Path(root), '../usr/share/zoneinfo/America/Chicago')
            applied = []

            def changes(_etc):
                yield  # an unrelated write in /etc: same zone, nothing to do
                (etc / 'localtime').unlink()
                os.symlink('../usr/share/zoneinfo/Europe/Berlin', etc / 'localtime')
                yield
                yield

            with patch.object(launcher, 'ZONEINFO', zoneinfo):
                launcher._keep_zone('luma-run-x', 'America/Chicago', etc, changes,
                                    lambda instance, zone: applied.append((instance, zone)) or True)
        self.assertEqual(applied, [('luma-run-x', 'Europe/Berlin')])

    def test_failed_update_is_tried_again_on_the_next_change(self):
        with tempfile.TemporaryDirectory() as root:
            etc, zoneinfo = _zone_tree(Path(root), '../usr/share/zoneinfo/Europe/Berlin')
            results = iter((False, True))
            applied = []

            def apply(_instance, zone):
                applied.append(zone)
                return next(results)

            with patch.object(launcher, 'ZONEINFO', zoneinfo):
                launcher._keep_zone('luma-run-x', 'America/Chicago', etc, lambda _e: iter((None, None, None)), apply)
        self.assertEqual(applied, ['Europe/Berlin', 'Europe/Berlin'])

    def test_capsule_link_is_replaced_as_root_inside_the_capsule(self):
        with patch('luma_installer.launcher.subprocess.run') as run:
            run.return_value.returncode = 0
            self.assertTrue(launcher._set_capsule_zone('luma-run-x', 'Europe/Berlin'))
        self.assertEqual(run.call_args[0][0], ['podman', 'exec', '--user', '0', 'luma-run-x', 'ln', '-sfn',
                                               '../usr/share/zoneinfo/Europe/Berlin', '/etc/localtime'])

    @unittest.skipUnless(sys.platform.startswith('linux'), 'inotify')
    def test_host_link_replacement_is_seen(self):
        with tempfile.TemporaryDirectory() as root:
            etc, _zoneinfo = _zone_tree(Path(root), '../usr/share/zoneinfo/America/Chicago')
            seen = threading.Event()
            changes = launcher._host_zone_changes(etc)

            def watch():
                for _change in changes:
                    seen.set()
                    return

            threading.Thread(target=watch, daemon=True).start()
            for _attempt in range(20):
                (etc / 'resolv.conf').write_text('')
                if seen.wait(0.05):
                    break
            self.assertFalse(seen.is_set(), 'an unrelated file in /etc is not a zone change')
            # As timedatectl replaces it: a new link renamed over the old one.
            for _attempt in range(50):
                os.symlink('../usr/share/zoneinfo/Europe/Berlin', etc / '.#localtime')
                os.rename(etc / '.#localtime', etc / 'localtime')
                if seen.wait(0.1):
                    break
            self.assertTrue(seen.is_set())


if __name__ == '__main__':
    unittest.main()
