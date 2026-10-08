# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from pathlib import Path
from luma_monitor.model import Sampler, Identity, stat_record, identity_candidates, filesystems
from unittest.mock import patch
from types import SimpleNamespace


def process_stat(pid, ticks=10, start=50):
    tail = ['0'] * 22
    tail[0], tail[1], tail[11], tail[12], tail[19] = 'S', '1', str(ticks), '0', str(start)
    return f'{pid} (name (with) spaces) ' + ' '.join(tail)


class Samples(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.proc, self.sys = self.root / 'proc', self.root / 'sys'
        self.proc.mkdir(); self.sys.mkdir()
        self.write(self.proc / 'stat', 'cpu 10 0 10 80 0 0 0 0 0 0\ncpu0 10 0 10 80 0 0 0 0\n')
        self.write(self.proc / 'uptime', '100 200')
        self.write(self.proc / 'meminfo', 'MemTotal: 10000 kB\nMemAvailable: 4000 kB\nCached: 1000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n')
        self.make_process(42, 10)
        self.make_process(43, 5)
        self.sampler = Sampler(self.proc, self.sys, resolve=lambda *_: Identity('example.desktop', 'Example'))
        self.sampler.hz = 100

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def make_process(self, pid, ticks, start=50):
        root = self.proc / str(pid)
        self.write(root / 'stat', process_stat(pid, ticks, start))
        self.write(root / 'status', 'Uid:\t1000 1000 1000 1000\nThreads: 2\nRssFile: 20 kB\n')
        self.write(root / 'cgroup', '0::/user.slice/app-example-1.scope\n')
        self.write(root / 'cmdline', '/usr/bin/example\0')
        self.write(root / 'smaps_rollup', 'Pss: 100 kB\nRss: 999 kB\n')
        self.write(root / 'io', 'read_bytes: 1000\nwrite_bytes: 500\n')

    def test_real_deltas_identity_grouping_and_pss(self):
        first = self.sampler.sample(memory=True, now=10)
        self.assertIsNone(first['cpu'])
        self.assertEqual(first['rows'][0]['memory'], 200 * 1024)
        self.assertEqual(len(first['rows']), 1)
        self.write(self.proc / 'stat', 'cpu 50 0 20 130 0 0 0 0 0 0\ncpu0 50 0 20 130 0 0 0 0\n')
        self.write(self.proc / '42/stat', process_stat(42, 30))
        self.write(self.proc / '43/stat', process_stat(43, 15))
        self.write(self.proc / '42/io', 'read_bytes: 1300\nwrite_bytes: 700\n')
        second = self.sampler.sample(memory=True, now=11)
        self.assertAlmostEqual(second['cpu'], 50)
        self.assertAlmostEqual(second['apps_cpu'], 30)
        self.assertAlmostEqual(second['system_cpu'], 20)
        self.assertEqual(second['rows'][0]['reading'], 300)
        self.assertIsNone(second['rows'][0]['receiving'])
        self.assertEqual(second['rows'][0]['threads'], 4)

    def test_dbus_activated_app_remains_identifiable_without_window(self):
        self.assertEqual(identity_candidates('/user.slice/app.slice/dbus-:1.2-org.projectluma.Tide@1.service'), ('org.projectluma.Tide',))
        self.assertEqual(identity_candidates('/user.slice/dbus-unrelated.service'), ())

    def test_unreadable_memory_and_io_stay_unavailable(self):
        self.sampler.sample(memory=True, now=10)
        (self.proc / '42/smaps_rollup').unlink()
        (self.proc / '42/io').unlink()
        result = self.sampler.sample(memory=True, now=11)
        self.assertIsNone(result['rows'][0]['memory'])
        self.assertIsNone(result['rows'][0]['reading'])

    def test_pid_reuse_and_pause_do_not_create_spikes(self):
        self.sampler.sample(now=10)
        self.make_process(42, 400000, start=70)
        self.write(self.proc / 'stat', 'cpu 50 0 20 130 0 0 0 0\n')
        result = self.sampler.sample(now=11)
        self.assertIsNone(next(p.cpu for p in result['processes'] if p.pid == 42))
        self.sampler.reset()
        self.assertIsNone(self.sampler.sample(now=100)['cpu'])

    def test_disk_partitions_are_not_counted_twice(self):
        for name in ('vda', 'vda1'):
            (self.sys / 'class/block' / name).mkdir(parents=True)
        self.write(self.sys / 'class/block/vda1/partition', '1')
        self.write(self.proc / 'diskstats', '252 0 vda 1 0 2 0 1 0 3 0 0 0 0\n252 1 vda1 1 0 2 0 1 0 3 0 0 0 0\n')
        self.sampler.sample(now=10)
        self.write(self.proc / 'diskstats', '252 0 vda 1 0 4 0 1 0 7 0 0 0 0\n252 1 vda1 1 0 4 0 1 0 7 0 0 0 0\n')
        self.assertEqual(self.sampler.sample(now=11)['disk_rates'], (1024, 2048))

    def test_parent_attribution_and_kernel_threads(self):
        self.sampler.resolve = lambda group, pid, command: Identity('terminal.desktop', 'Terminal') if pid == 42 else None
        child = process_stat(43, 5).split(') ')
        tail = child[-1].split(); tail[1] = '42'
        self.write(self.proc / '43/stat', ') '.join(child[:-1]) + ') ' + ' '.join(tail))
        self.make_process(2, 0)
        result = self.sampler.sample(now=10)
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(result['rows'][0]['name'], 'Terminal')
        self.assertEqual(len(result['rows'][0]['members']), 2)
        self.assertEqual(result['background_count'], 1)
        self.assertEqual(len(result['processes']), 3)

    def test_child_rss_never_substitutes_for_app_pss(self):
        self.sampler.resolve = lambda group, pid, command: Identity('terminal.desktop', 'Terminal') if pid == 42 else None
        text = process_stat(43, 5); close = text.rfind(')'); tail = text[close + 2:].split(); tail[1] = '42'
        self.write(self.proc / '43/stat', text[:close + 2] + ' '.join(tail))
        (self.proc / '43/smaps_rollup').unlink()
        with (self.proc / '43/status').open('a') as output: output.write('VmRSS: 999 kB\n')
        result = self.sampler.sample(memory=True, now=10)
        self.assertIsNone(result['rows'][0]['memory'])

    def test_cache_is_cgroup_file_charge_not_rss(self):
        self.write(self.sys / 'fs/cgroup/user.slice/app-example-1.scope/memory.stat', 'anon 1000\nfile 4096\n')
        result = self.sampler.sample(memory=True, now=10)
        self.assertEqual(result['rows'][0]['cached'], 4096)

    def test_parent_cgroup_cache_includes_child_charge_once(self):
        self.write(self.proc / '43/cgroup', '0::/user.slice/app-example-1.scope/worker\n')
        self.write(self.sys / 'fs/cgroup/user.slice/app-example-1.scope/memory.stat', 'file 4096\n')
        self.write(self.sys / 'fs/cgroup/user.slice/app-example-1.scope/worker/memory.stat', 'file 2048\n')
        self.assertEqual(self.sampler.sample(memory=True, now=10)['rows'][0]['cached'], 4096)

    def test_snap_and_service_instance_identity(self):
        self.assertIn('spotify_spotify', identity_candidates('/snap.spotify.spotify-1234abcd.scope'))
        self.assertIn('org.example.App', identity_candidates('/app-org.example.App@abc.service'))

    def test_network_excludes_virtual_interfaces(self):
        for interface in ('eth0', 'lo', 'tun0', 'veth0'):
            self.write(self.sys / f'class/net/{interface}/statistics/rx_bytes', '100')
            self.write(self.sys / f'class/net/{interface}/statistics/tx_bytes', '50')
        (self.sys / 'class/net/eth0/device').mkdir()
        result = self.sampler.sample(now=10)
        self.assertEqual(result['network_received'], 100)

    def test_silverblue_home_and_hidden_system_mounts(self):
        (self.sys / 'dev/block/8:1').mkdir(parents=True)
        self.write(self.proc / 'self/mountinfo',
            '1 0 8:1 / / rw - btrfs /dev/test rw\n'
            '2 0 8:1 /home /var/home rw - btrfs /dev/test rw\n'
            '3 0 8:1 /containers /var/lib/containers rw - btrfs /dev/test rw\n')
        with patch('luma_monitor.model.os.statvfs', return_value=SimpleNamespace(f_blocks=100, f_frsize=1024, f_bavail=50, f_bfree=50)):
            rows = filesystems(self.proc, self.sys)
        self.assertEqual([(r['name'], r['mount']) for r in rows], [('System', '/'), ('Home', '/var/home')])

    def test_stat_name_and_scope_escaping(self):
        self.assertEqual(stat_record(process_stat(1))['comm'], 'name (with) spaces')
        candidates = identity_candidates(r'/user.slice/app-flatpak-org.example.App-123.scope')
        self.assertIn('org.example.App', candidates)
        self.assertIn('org.example.App-name', identity_candidates(r'/app-gnome-org.example.App\x2dname-12.scope'))


if __name__ == '__main__':
    unittest.main()
