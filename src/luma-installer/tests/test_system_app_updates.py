# SPDX-License-Identifier: Apache-2.0
"""App updates downloaded with a staged Luma update, deployed on its first boot,
and Luma's own system apps kept current in the session."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from luma_installer import system_app_updates as sau

OLD_OS, NEW_OS, OTHER_OS = 'a' * 64, 'b' * 64, 'c' * 64
MONITOR = 'app/io.luma.Monitor/x86_64/beta'
VIOLA = 'app/com.rhyme.viola/x86_64/beta'
DEPOT = 'app/org.projectluma.Depot/x86_64/beta'
PLATFORM = 'runtime/org.projectluma.Platform/x86_64/44'
BASE = """[Application]
name={name}
runtime=org.projectluma.Platform/x86_64/44

[Context]
shared=ipc;
sockets=wayland;fallback-x11;
"""
WIDER = BASE + "filesystems=home;\n"
CONFIG = """[core]
repo_version=1
mode=bare-user-only

[remote "luma"]
url=https://dl.simplyluma.com/repo/
gpg-verify=true
gpg-verify-summary=true
xa.title=Luma
"""


def c(n):
    return f'{n:064x}'


class Flatpak:
    """A system installation on disk plus the commands this module runs against it."""

    def __init__(self, root: Path):
        self.root = root
        (root / 'repo').mkdir(parents=True)
        (root / 'repo/config').write_text(CONFIG)
        self.remote = {}       # ref -> (commit, metadata) on the luma remote
        self.objects = {}      # commit -> metadata in the local repository
        self.deployments = [{'booted': True, 'checksum': OLD_OS}]
        self.calls = []
        self.fail_pull = set()
        self.fail_deploy = False
        self.updates = []

    def install(self, ref, commit, metadata):
        kind, name, arch, branch = ref.split('/')
        where = self.root / kind / name / arch / branch
        (where / commit).mkdir(parents=True, exist_ok=True)
        (where / commit / 'metadata').write_text(metadata.format(name=name))
        active = where / 'active'
        if active.is_symlink():
            active.unlink()
        active.symlink_to(commit)
        self.objects[commit] = metadata.format(name=name)
        self._local(ref, commit)

    def _local(self, ref, commit):
        path = self.root / 'repo/refs/remotes/luma' / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(commit + '\n')

    def publish(self, ref, commit, metadata=BASE):
        self.remote[ref] = (commit, metadata.format(name=ref.split('/')[1]))

    def stage(self, identity=NEW_OS):
        self.deployments = [{'staged': True, 'booted': False, 'checksum': identity},
                            *[d for d in self.deployments if not d.get('staged')]]

    def update(self, ref, expected, before, installation):
        """refresh's libflatpak update: the newest commit, refused unless it is the one compared."""
        self.updates.append((ref, expected, before))
        commit = self.remote[ref][0]
        if ref in self.fail_pull:
            return 'error: failed'
        if commit != expected or sau.installed_commit(ref, installation) != before:
            return 'Transaction refused'
        self.objects[commit] = self.remote[ref][1]
        self._local(ref, commit)
        self.install(ref, commit, self.objects[commit].replace('{', '{{').replace('}', '}}'))
        return ''

    def boot(self, identity):
        self.deployments = [{'booted': d['checksum'] == identity, 'checksum': d['checksum']}
                            for d in self.deployments]
        self.deployments.sort(key=lambda d: not d['booted'])

    def run(self, argv, **_kwargs):
        self.calls.append(argv)
        ok = lambda out='': subprocess.CompletedProcess(argv, 0, out, '')  # noqa: E731
        failed = subprocess.CompletedProcess(argv, 1, '', 'error: failed')
        if argv[:2] == ['rpm-ostree', 'status']:
            return ok(json.dumps({'deployments': self.deployments}))
        if argv[:2] == ['flatpak', 'remote-ls']:
            return ok(''.join(f'{ref}\n' for ref, (commit, _m) in self.remote.items()
                              if sau.installed_commit(ref, self.root) not in ('', commit)))
        if argv[:2] == ['flatpak', 'remote-info']:
            commit, metadata = self.remote[argv[-1]]
            return ok(commit + '\n' if argv[3] == '--show-commit' else metadata)
        if argv[:2] == ['flatpak', 'update'] and '--no-deploy' in argv:
            for ref in argv[6:]:
                if ref not in self.fail_pull:
                    commit, metadata = self.remote[ref]
                    self.objects[commit] = metadata
                    self._local(ref, commit)
            return failed if self.fail_pull & set(argv[6:]) else ok()
        if argv[:2] == ['flatpak', 'update'] and '--no-pull' in argv:
            if self.fail_deploy:
                return failed
            for ref in argv[6:]:
                commit = (self.root / 'repo/refs/remotes/luma' / ref).read_text().strip()
                self.install(ref, commit, self.objects[commit].replace('{', '{{').replace('}', '}}'))
            return ok()
        if argv[0] == 'ostree' and argv[2] == 'cat':
            return ok(self.objects.get(argv[3], ''))
        return failed


class Case(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / 'home'
        self.home.mkdir()
        self.state = Path(temp.name) / 'state'
        self.flatpak = Flatpak(Path(temp.name) / 'flatpak')
        self.flatpak.install(MONITOR, c(1), BASE)
        self.flatpak.install(VIOLA, c(2), BASE)
        self.flatpak.install(PLATFORM, c(3), BASE)
        self.environments = [{'HOME': str(self.home)}]
        host = patch('luma_installer.system_app_updates.needs_newer_host', return_value=False)
        host.start()
        self.addCleanup(host.stop)

    def prefetch(self):
        return sau.prefetch(runner=self.flatpak.run, installation=self.flatpak.root, state=self.state,
                            environments=self.environments, now=lambda: 1000)

    def deploy(self, now=2000):
        return sau.deploy(runner=self.flatpak.run, installation=self.flatpak.root, state=self.state,
                          now=lambda: now)

    def deployed(self, ref):
        return sau.installed_commit(ref, self.flatpak.root)


class Prefetching(Case):
    def test_downloads_for_the_staged_deployment_without_deploying(self):
        self.flatpak.publish(MONITOR, c(11))
        self.flatpak.publish(PLATFORM, c(13))
        self.flatpak.stage()
        outcome = self.prefetch()
        self.assertEqual((outcome['result'], outcome['refs']), ('prepared', 2))
        self.assertEqual((self.deployed(MONITOR), self.deployed(PLATFORM)), (c(1), c(3)))
        doc = sau.load(self.state)
        self.assertEqual(doc['deployment'], NEW_OS)
        # the runtime comes first, so an app's new runtime is there before the app
        self.assertEqual([(r['ref'], r['from'], r['to']) for r in doc['refs']],
                         [(PLATFORM, c(3), c(13)), (MONITOR, c(1), c(11))])
        pulls = [argv for argv in self.flatpak.calls if argv[:2] == ['flatpak', 'update']]
        self.assertTrue(all('--no-deploy' in argv and '--system' in argv for argv in pulls))

    def test_an_update_that_asks_for_more_waits_for_the_person(self):
        self.flatpak.publish(MONITOR, c(11), WIDER)
        self.flatpak.publish(VIOLA, c(12))
        self.flatpak.stage()
        self.prefetch()
        doc = sau.load(self.state)
        self.assertEqual([r['ref'] for r in doc['refs']], [VIOLA])
        self.assertEqual(doc['held'], [{'ref': MONITOR, 'reason': 'asks-for-more'}])
        self.assertNotIn(MONITOR, [a[-1] for a in self.flatpak.calls if a[:2] == ['flatpak', 'update']])

    def test_an_app_taken_back_stays_where_the_person_left_it(self):
        history = self.home / '.local/state/luma/depot/app-history.json'
        history.parent.mkdir(parents=True)
        history.write_text(json.dumps({'entries': [], 'paused': [{
            'app_id': 'catalog:monitor', 'name': 'Monitor', 'reverted_from_version': '2',
            'reverted_from_commit': c(9), 'at': int(time.time())}]}))
        self.flatpak.publish(MONITOR, c(11))
        self.flatpak.stage()
        catalogue = SimpleNamespace(applications=[SimpleNamespace(id='monitor', source_id='io.luma.Monitor',
                                                                  backend='flatpak')])
        with patch('luma_installer.depot_catalog.local_catalog', return_value=catalogue):
            self.prefetch()
        self.assertEqual(sau.load(self.state)['held'], [{'ref': MONITOR, 'reason': 'taken-back'}])

    def test_automatic_app_updates_off_downloads_nothing(self):
        settings = self.home / '.config/luma/depot/settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'app_updates': False}))
        self.flatpak.publish(MONITOR, c(11))
        self.flatpak.stage()
        self.assertEqual(self.prefetch()['result'], 'app-updates-off')
        self.assertIsNone(sau.load(self.state))

    def test_only_a_signed_luma_remote(self):
        config = self.flatpak.root / 'repo/config'
        for broken in (CONFIG.replace('gpg-verify=true', 'gpg-verify=false'),
                       CONFIG.replace('gpg-verify-summary=true', 'gpg-verify-summary=false'),
                       CONFIG.replace('dl.simplyluma.com', 'dl.example.com'),
                       CONFIG.replace('[remote "luma"]', '[remote "other"]')):
            config.write_text(broken)
            self.flatpak.publish(MONITOR, c(11))
            self.flatpak.stage()
            self.assertEqual(self.prefetch()['result'], 'remote-not-trusted')
        self.assertFalse(any(a[:2] == ['flatpak', 'update'] for a in self.flatpak.calls))

    def test_nothing_staged_records_nothing(self):
        self.flatpak.publish(MONITOR, c(11))
        self.assertEqual(self.prefetch()['result'], 'nothing-staged')
        self.assertIsNone(sau.load(self.state))

    def test_a_download_cut_short_keeps_what_arrived(self):
        self.flatpak.publish(MONITOR, c(11))
        self.flatpak.publish(VIOLA, c(12))
        self.flatpak.fail_pull = {VIOLA}
        self.flatpak.stage()
        outcome = self.prefetch()
        self.assertEqual((outcome['refs'], outcome['missed']), (1, 1))
        self.assertEqual([r['ref'] for r in sau.load(self.state)['refs']], [MONITOR])


class Deploying(Case):
    def staged_with(self, *refs):
        for index, ref in enumerate(refs):
            self.flatpak.publish(ref, c(20 + index))
        self.flatpak.stage()
        self.prefetch()

    def test_first_boot_of_the_update_deploys_from_the_local_repository(self):
        self.staged_with(MONITOR, PLATFORM)
        self.flatpak.boot(NEW_OS)
        outcome = self.deploy()
        self.assertEqual(outcome['result'], 'deployed')
        self.assertEqual((self.deployed(MONITOR), self.deployed(PLATFORM)), (c(20), c(21)))
        deploys = [a for a in self.flatpak.calls if a[:2] == ['flatpak', 'update'] and '--no-pull' in a]
        self.assertEqual(len(deploys), 1)
        self.assertIsNone(sau.load(self.state))  # once per deployment
        self.assertEqual(self.deploy()['result'], 'none')

    def test_booting_the_previous_deployment_deploys_nothing(self):
        self.staged_with(MONITOR)
        self.flatpak.boot(OLD_OS)  # chosen in the boot menu, or greenboot fell back
        self.assertEqual(self.deploy()['result'], 'waiting')
        self.assertEqual(self.deployed(MONITOR), c(1))
        self.assertIsNotNone(sau.load(self.state))  # the update still boots next time
        self.flatpak.boot(NEW_OS)
        self.assertEqual(self.deploy()['result'], 'deployed')

    def test_a_deployment_that_is_gone_drops_the_record(self):
        self.staged_with(MONITOR)
        self.flatpak.deployments = [{'booted': True, 'checksum': OLD_OS}]  # the staged update was removed
        self.assertEqual(self.deploy()['result'], 'dropped')
        self.assertIsNone(sau.load(self.state))
        self.assertEqual(self.deployed(MONITOR), c(1))

    def test_an_old_record_is_dropped(self):
        self.staged_with(MONITOR)
        self.flatpak.boot(OLD_OS)
        self.assertEqual(self.deploy(now=1000 + sau.KEEP_SECONDS + 1)['result'], 'dropped')

    def test_another_update_staged_since_is_not_this_one(self):
        self.staged_with(MONITOR)
        self.flatpak.stage(OTHER_OS)
        self.flatpak.deployments = [d for d in self.flatpak.deployments if d['checksum'] != NEW_OS]
        self.flatpak.boot(OTHER_OS)
        self.assertEqual(self.deploy()['result'], 'dropped')
        self.assertEqual(self.deployed(MONITOR), c(1))

    def test_an_app_depot_already_updated_is_left_alone(self):
        self.staged_with(MONITOR, VIOLA)
        self.flatpak.install(MONITOR, c(30), BASE)  # Depot moved it on before the restart
        self.flatpak.boot(NEW_OS)
        outcome = self.deploy()
        self.assertEqual(outcome['deployed'], [VIOLA])
        self.assertEqual(outcome['skipped'], [{'ref': MONITOR, 'reason': 'changed'}])
        self.assertEqual(self.deployed(MONITOR), c(30))

    def test_a_newer_host_requirement_waits(self):
        self.staged_with(MONITOR)
        self.flatpak.boot(NEW_OS)
        with patch('luma_installer.system_app_updates.needs_newer_host', return_value=True):
            outcome = self.deploy()
        self.assertEqual(outcome['skipped'], [{'ref': MONITOR, 'reason': 'needs-newer-host'}])
        self.assertEqual(self.deployed(MONITOR), c(1))

    def test_a_commit_that_widens_permissions_is_never_deployed(self):
        self.staged_with(MONITOR)
        record = sau.load(self.state)['refs'][0]
        self.flatpak.objects[record['to']] = WIDER.format(name='io.luma.Monitor')  # not what was compared
        self.flatpak.boot(NEW_OS)
        self.assertEqual(self.deploy()['skipped'], [{'ref': MONITOR, 'reason': 'asks-for-more'}])

    def test_a_failed_deploy_never_fails_the_boot(self):
        self.staged_with(MONITOR)
        self.flatpak.boot(NEW_OS)
        self.flatpak.fail_deploy = True
        outcome = self.deploy()
        self.assertEqual((outcome['result'], outcome['failed']), ('deployed', [MONITOR]))
        with patch.object(sau, 'deploy', side_effect=RuntimeError('boom')):
            self.assertEqual(sau.main(['deploy']), 0)

    def test_a_malformed_record_is_ignored(self):
        self.state.mkdir()
        sau.manifest_path(self.state).write_text('{"schema": "x"}')
        self.assertEqual(self.deploy()['result'], 'none')


class Refreshing(Case):
    """Every six hours and soon after sign-in, without a restart, catalogued or not."""

    def setUp(self):
        super().setUp()
        self.flatpak.install(DEPOT, c(4), BASE)

    def refresh(self, *, settled=True, reason='', update=None):
        return sau.refresh(runner=self.flatpak.run, installation=self.flatpak.root,
                           environments=self.environments, settled=lambda: settled, waits=lambda _e: reason,
                           update=update or self.flatpak.update)

    def updates(self):
        return self.flatpak.updates

    def test_system_apps_and_runtimes_update_in_place(self):
        self.flatpak.publish(DEPOT, c(14))
        self.flatpak.publish(VIOLA, c(12))
        self.flatpak.publish(PLATFORM, c(13))
        outcome = self.refresh()
        self.assertEqual(outcome['result'], 'updated')
        self.assertEqual(outcome['updated'], [PLATFORM, VIOLA, DEPOT])  # the runtime first
        self.assertEqual((self.deployed(DEPOT), self.deployed(VIOLA), self.deployed(PLATFORM)), (c(14), c(12), c(13)))
        # each only to the commit that was compared, from the one installed
        self.assertEqual(self.updates(), [(PLATFORM, c(13), c(3)), (VIOLA, c(12), c(2)), (DEPOT, c(14), c(4))])
        self.assertEqual(self.refresh()['result'], 'up-to-date')

    def test_a_remote_that_moved_on_is_not_taken_blind(self):
        self.flatpak.publish(DEPOT, c(14))

        def moved(ref, expected, before, installation):
            self.flatpak.publish(DEPOT, c(15), WIDER)  # between the comparison and the transaction
            return self.flatpak.update(ref, expected, before, installation)
        outcome = self.refresh(update=moved)
        self.assertEqual(outcome['failed'], [DEPOT])
        self.assertEqual(self.deployed(DEPOT), c(4))

    def test_held_like_depot_holds(self):
        self.flatpak.publish(DEPOT, c(14), WIDER)
        self.flatpak.publish(VIOLA, c(12))
        with patch('luma_installer.system_app_updates.needs_newer_host', side_effect=lambda m: True):
            outcome = self.refresh()
        self.assertEqual(outcome['held'], [{'ref': VIOLA, 'reason': 'needs-newer-host'},
                                           {'ref': DEPOT, 'reason': 'asks-for-more'}])
        self.assertEqual(self.updates(), [])
        self.assertEqual((self.deployed(DEPOT), self.deployed(VIOLA)), (c(4), c(2)))

    def test_an_app_taken_back_stays(self):
        history = self.home / '.local/state/luma/depot/app-history.json'
        history.parent.mkdir(parents=True)
        history.write_text(json.dumps({'entries': [], 'paused': [{
            'app_id': 'catalog:viola', 'name': 'Viola', 'reverted_from_version': '2',
            'reverted_from_commit': c(9), 'at': int(time.time())}]}))
        self.flatpak.publish(VIOLA, c(12))
        catalogue = SimpleNamespace(applications=[SimpleNamespace(id='viola', source_id='com.rhyme.viola',
                                                                  backend='flatpak')])
        with patch('luma_installer.depot_catalog.local_catalog', return_value=catalogue):
            outcome = self.refresh()
        self.assertEqual(outcome['held'], [{'ref': VIOLA, 'reason': 'taken-back'}])
        self.assertEqual(self.deployed(VIOLA), c(2))

    def test_waits_for_the_offline_baseline(self):
        self.flatpak.publish(DEPOT, c(14))
        self.assertEqual(self.refresh(settled=False), {'result': 'baseline-installing'})
        self.assertEqual(self.flatpak.calls, [])

    def test_defers_when_depot_would(self):
        self.flatpak.publish(DEPOT, c(14))
        outcome = self.refresh(reason='This connection is metered.')
        self.assertEqual(outcome, {'result': 'deferred', 'reason': 'This connection is metered.'})
        self.assertEqual(self.updates(), [])

    def test_only_a_signed_luma_remote(self):
        (self.flatpak.root / 'repo/config').write_text(CONFIG.replace('gpg-verify=true', 'gpg-verify=false'))
        self.flatpak.publish(DEPOT, c(14))
        self.assertEqual(self.refresh()['result'], 'remote-not-trusted')
        self.assertEqual(self.updates(), [])

    def test_a_failed_update_is_reported_and_the_rest_go_on(self):
        self.flatpak.publish(DEPOT, c(14))
        self.flatpak.publish(VIOLA, c(12))
        self.flatpak.fail_pull = {DEPOT}
        outcome = self.refresh()
        self.assertEqual((outcome['updated'], outcome['failed']), ([VIOLA], [DEPOT]))
        self.assertEqual(self.deployed(DEPOT), c(4))
        def crash(*_args):
            raise RuntimeError('no bus')
        self.assertEqual(self.refresh(update=crash)['failed'], [DEPOT])  # Viola is current by now
        with patch.object(sau, 'refresh', side_effect=RuntimeError('boom')):
            self.assertEqual(sau.main(['refresh']), 0)

    def test_the_boot_deploy_of_a_staged_update_leaves_a_refreshed_app_alone(self):
        self.flatpak.publish(DEPOT, c(20))
        self.flatpak.stage()
        self.prefetch()
        self.flatpak.publish(DEPOT, c(21))  # newer still, taken in the session
        self.refresh()
        self.flatpak.boot(NEW_OS)
        outcome = self.deploy()
        self.assertEqual(outcome['skipped'], [{'ref': DEPOT, 'reason': 'changed'}])
        self.assertEqual(self.deployed(DEPOT), c(21))


class Baseline(unittest.TestCase):
    def test_waits_for_the_import_then_goes_on(self):
        states = iter(['installing', 'installing', 'done'])
        slept = []
        self.assertTrue(sau.baseline_settled(read=lambda: {'state': next(states)}, sleep=slept.append,
                                             clock=lambda: 0))
        self.assertEqual(len(slept), 2)

    def test_no_baseline_on_this_device(self):
        self.assertTrue(sau.baseline_settled(read=lambda: None, sleep=self.fail))

    def test_gives_up_after_the_wait(self):
        clock = iter(range(0, 10000, 600))
        self.assertFalse(sau.baseline_settled(read=lambda: {'state': 'installing'}, sleep=lambda _s: None,
                                              clock=lambda: next(clock), wait=1200))


class Deferring(unittest.TestCase):
    """Depot's own rule decides, so the two never disagree."""

    def reason(self, *, metered=False, power_saver=False, low=False, off=False):
        from gi.repository import Gio
        monitor = SimpleNamespace(get_network_metered=lambda: metered)
        profile = SimpleNamespace(get_power_saver_enabled=lambda: power_saver)
        with patch.object(Gio.NetworkMonitor, 'get_default', return_value=monitor), \
                patch.object(Gio.PowerProfileMonitor, 'dup_default', return_value=profile), \
                patch('luma_depot.system_updates.battery_low', return_value=low), \
                patch('luma_installer.system_app_updates.updates_off', return_value=off):
            return sau.deferred([])

    def test_conditions(self):
        self.assertEqual(self.reason(), '')
        self.assertEqual(self.reason(off=True), 'Automatic app updates are off.')
        self.assertEqual(self.reason(metered=True), 'This connection is metered.')
        self.assertEqual(self.reason(power_saver=True), 'Power saving is on.')
        self.assertEqual(self.reason(low=True), 'The battery is low.')


class SignIn(unittest.TestCase):
    def runner(self, system='', user=None, offline=0, refs=''):
        calls, state = [], {'offline': offline}

        def run(argv, **_kwargs):
            calls.append(argv)
            if argv[1] == 'remotes':
                remotes = 'luma\nflathub\n' if argv[2] == '--system' or user is not None else 'flathub\n'
                return subprocess.CompletedProcess(argv, 0, remotes, '')
            if argv[1] == 'remote-ls':
                if state['offline']:
                    if argv[2] == '--user' or user is None:
                        state['offline'] -= 1
                    return subprocess.CompletedProcess(argv, 1, '', 'error: Unable to load summary')
                if '--columns=ref' in argv:
                    return subprocess.CompletedProcess(argv, 0, refs, '')
                return subprocess.CompletedProcess(argv, 0, system if argv[2] == '--system' else user or '', '')
            return subprocess.CompletedProcess(argv, 0, '', '')
        return run, calls

    def setUp(self):
        catalogued = patch('luma_installer.depot_flatpak._catalogued', return_value={
            'monitor': ('io.luma.Monitor', 'luma', 'beta'), 'viola': ('com.rhyme.viola', 'luma', 'beta'),
            'vlc': ('org.videolan.VLC', 'flathub', 'stable')})
        catalogued.start()
        self.addCleanup(catalogued.stop)

    def test_waiting_updates_start_depot_at_once(self):
        run, calls = self.runner(system='io.luma.Monitor\norg.projectluma.Depot\n')
        outcome = sau.pending(runner=run, sleep=lambda _s: None)
        self.assertEqual(outcome, {'result': 'started', 'apps': ['io.luma.Monitor']})
        self.assertIn(['systemctl', '--user', 'start', '--no-block', sau.DEPOT_UNIT], calls)

    def test_a_system_app_outside_the_catalogue_starts_it_too(self):
        run, calls = self.runner(system='org.projectluma.Depot\n', refs=DEPOT + '\n' + PLATFORM + '\n')
        outcome = sau.pending(runner=run, sleep=lambda _s: None)
        self.assertEqual(outcome, {'result': 'started', 'apps': ['org.projectluma.Depot', 'org.projectluma.Platform']})
        self.assertIn(['systemctl', '--user', 'start', '--no-block', sau.DEPOT_UNIT], calls)

    def test_nothing_waiting_keeps_the_timer(self):
        run, calls = self.runner(system='org.projectluma.Depot\n')  # not one Depot updates
        self.assertEqual(sau.pending(runner=run, sleep=lambda _s: None), {'result': 'up-to-date'})
        self.assertFalse(any(a[0] == 'systemctl' for a in calls))

    def test_waits_for_the_network_after_sign_in(self):
        slept = []
        run, calls = self.runner(system='com.rhyme.viola\n', offline=2)
        outcome = sau.pending(runner=run, sleep=slept.append)
        self.assertEqual((outcome['result'], len(slept)), ('started', 2))

    def test_offline_gives_up_quietly(self):
        run, calls = self.runner(system='com.rhyme.viola\n', offline=99)
        self.assertEqual(sau.pending(runner=run, attempts=3, sleep=lambda _s: None), {'result': 'unavailable'})
        self.assertFalse(any(a[0] == 'systemctl' for a in calls))


class Deployments(unittest.TestCase):
    def test_a_layered_deployment_is_known_by_its_base(self):
        self.assertEqual(sau._identity({'checksum': 'd' * 64, 'base-checksum': NEW_OS}), NEW_OS)
        self.assertEqual(sau.staged_identity([{'booted': True, 'checksum': OLD_OS},
                                              {'staged': True, 'checksum': NEW_OS}]), NEW_OS)
        # finalized at shutdown: the new default, not yet booted
        self.assertEqual(sau.staged_identity([{'booted': False, 'checksum': NEW_OS},
                                              {'booted': True, 'checksum': OLD_OS}]), NEW_OS)
        self.assertEqual(sau.staged_identity([{'booted': True, 'checksum': OLD_OS}]), '')


if __name__ == '__main__':
    unittest.main()
