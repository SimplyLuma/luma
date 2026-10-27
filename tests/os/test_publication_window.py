# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('publication_window', ROOT / 'scripts/os/lib/publication_window.py')
window = importlib.util.module_from_spec(spec)
spec.loader.exec_module(window)
UNIT = 'luma-os-publication-fixture.service'
TIMER = 'luma-approved-os-graph-refresh-nightly.timer'


class Control:
    def __init__(self, result=0):
        self.states = {TIMER: 'active', TIMER[:-6]+'.service': 'inactive'}
        self.calls = []
        self.result = result
        self.stop_failure = False
        self.start_failure = False
    def worker_absent(self, unit): return True
    def state(self, unit): return self.states.get(unit, 'inactive')
    def change(self, action, unit):
        self.calls.append((action, unit))
        self.states[unit] = 'active' if action == 'start' else 'inactive'
        if (action == 'stop' and self.stop_failure) or (action == 'start' and self.start_failure):
            raise RuntimeError('injected state-change failure')
    def stop_worker(self, unit): self.calls.append(('stop-worker', unit))
    def run(self, command):
        self.calls.append(('worker', UNIT))
        if isinstance(self.result, BaseException): raise self.result
        # Real child failure exits the same wait boundary as a failed worker.
        return subprocess.run(['python3', '-c', 'import sys;sys.exit(int(sys.argv[1]))', str(self.result)]).returncode


@unittest.skipUnless(os.geteuid()==0,'publication supervision uses protected root-owned state')
class PublicationWindow(unittest.TestCase):
    def setUp(self):
        self.lock_directory = tempfile.TemporaryDirectory()
        self.original_window_lock = window.WINDOW_LOCK
        window.WINDOW_LOCK = Path(self.lock_directory.name)/'window.lock'
        self.addCleanup(self.lock_directory.cleanup)
        self.addCleanup(setattr, window, 'WINDOW_LOCK', self.original_window_lock)

    def test_second_attempt_and_recovery_refused_before_timer_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)/'interrupted'; p.mkdir(mode=0o700)
            window.save(p, 'STARTED.json', {'worker_unit':UNIT,'timer_states':{TIMER:'active'}})
            second = Control()
            # The shared lock must be checked before even querying timer state.
            def state_is_forbidden(unit):
                self.fail('competing window queried timer or worker state')
            second.state = state_is_forbidden
            with window.publication_lock():
                with self.assertRaisesRegex(ValueError, 'another publication window'):
                    window.supervise(second, UNIT, [TIMER], ['fixture'], Path(root)/'second')
                with self.assertRaisesRegex(ValueError, 'another publication window'):
                    window.recover(second, p)
            self.assertFalse(second.calls)
            self.assertFalse((Path(root)/'second').exists())
            # After release recovery takes shared then attempt lock and succeeds.
            window.recover(Control(), p)
            self.assertTrue((p/'RECOVERED.json').exists())

    def test_shared_lock_rejects_symlink_and_unprotected_file(self):
        target = window.WINDOW_LOCK.with_name('target')
        target.write_text('')
        window.WINDOW_LOCK.symlink_to(target)
        with self.assertRaises(OSError):
            with window.publication_lock(): self.fail('symlink accepted')
        window.WINDOW_LOCK.unlink()
        window.WINDOW_LOCK.write_text('');window.WINDOW_LOCK.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'private root-owned'):
            with window.publication_lock(): self.fail('readable lock accepted')

    def test_metadata_preflight_refuses_timestamp_difference_and_symlinks(self):
        with tempfile.TemporaryDirectory() as root:
            left,right=Path(root)/'local.json',Path(root)/'verified.json'
            left.write_text('{"generated_at":"old"}')
            right.write_text('{"generated_at":"new"}')
            with self.assertRaisesRegex(ValueError,'staged predecessor'):
                window.require_equal_files([(left,right)])
            left.write_bytes(right.read_bytes());window.require_equal_files([(left,right)])
            left.unlink();left.symlink_to(right)
            with self.assertRaises(ValueError):window.require_equal_files([(left,right)])

    def test_existing_runner_forwards_selected_config_through_real_shell(self):
        source=(ROOT/'scripts/os/run-capped.sh').read_text()
        block=source[source.index('forwarded=()'):source.index('output=(')]
        command=block+'\nprintf "%s\\0" "${forwarded[@]}"'
        environment=os.environ|{'LUMA_OS_PROFILE':'arm-preview','LUMA_OS_KEYS':'/keys with spaces','LUMA_OS_SLICE':'selected.slice','UNRELATED_TOKEN':'not forwarded'}
        args=subprocess.check_output(['bash','-c',command],env=environment).decode().split('\0')
        self.assertIn('--setenv=LUMA_OS_KEYS=/keys with spaces',args)
        self.assertIn('--setenv=LUMA_OS_SLICE=selected.slice',args)
        self.assertIn('--setenv=LUMA_OS_PROFILE=arm-preview',args)
        self.assertFalse(any('UNRELATED_TOKEN' in value for value in args))

    def test_worker_native_cap_private_mounts_explicit_environment(self):
        command = window.worker_command(UNIT, ['true'], {'LUMA_OS_PROFILE':'arm-preview',
           'LUMA_TARGET_ARCHITECTURE':'aarch64','EXPLICIT_PROOF':'some path', 'UNRELATED_TOKEN':'not forwarded'},4096,7200,['EXPLICIT_PROOF'])
        self.assertIn('--property=MemoryMax=4096M',command)
        self.assertIn('--property=PrivateMounts=yes',command)
        self.assertIn('--property=KillMode=control-group',command)
        self.assertIn('--setenv=EXPLICIT_PROOF=some path',command)
        self.assertIn('--setenv=LUMA_TARGET_ARCHITECTURE=aarch64',command)
        self.assertFalse(any('UNRELATED_TOKEN' in arg for arg in command))
        for env in ['MISSING', 'LUMA_OS_SIGNING_LOCK_FD', 'not-an-env']:
            with self.subTest(env=env), self.assertRaises(ValueError):
                window.worker_command(UNIT,['true'],{},4096,7200,[env])

    def test_failed_worker_restores_timers_without_publication_pass(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'attempt';control=Control(137)
            self.assertEqual(window.supervise(control,UNIT,[TIMER],['fixture'],p),137)
            self.assertEqual(control.state(TIMER),'active')
            receipt=json.loads((p/'RESTORED.json').read_text())
            self.assertEqual(receipt['worker_exit'],137)
            self.assertEqual(receipt['result'],'TIMERS_RESTORED')
            self.assertEqual((p/'RESTORED.json').stat().st_mode&0o777,0o400)
            self.assertNotIn('PASS',receipt['result'])

    def test_stop_failure_after_state_change_still_restores(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'attempt';control=Control();control.stop_failure=True
            with self.assertRaises(RuntimeError):window.supervise(control,UNIT,[TIMER],['fixture'],p)
            self.assertEqual(control.state(TIMER),'active')
            self.assertNotIn(('worker',UNIT),control.calls)

    def test_interruption_quiesces_worker_before_resuming_timers(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'attempt';control=Control(KeyboardInterrupt())
            with self.assertRaises(KeyboardInterrupt):window.supervise(control,UNIT,[TIMER],['fixture'],p)
            self.assertLess(control.calls.index(('stop-worker',UNIT)),control.calls.index(('start',TIMER)))

    def test_active_service_refused_without_mutation(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'attempt';control=Control();control.states[TIMER[:-6]+'.service']='active'
            with self.assertRaises(ValueError):window.supervise(control,UNIT,[TIMER],['fixture'],p)
            self.assertFalse(p.exists());self.assertFalse(control.calls)

    def test_nonzero_client_quiesces_worker_before_timer_restore(self):
        with tempfile.TemporaryDirectory() as root:
            control=Control(17);p=Path(root)/'attempt'
            self.assertEqual(window.supervise(control,UNIT,[TIMER],['fixture'],p),17)
            self.assertLess(control.calls.index(('stop-worker',UNIT)),control.calls.index(('start',TIMER)))

    def test_nonzero_client_stops_real_control_live_unit_before_timer_restore(self):
        with tempfile.TemporaryDirectory() as root:
            control = Control(17)
            control.states[UNIT] = 'inactive'
            control.stop_worker = lambda unit: window.SystemControl.stop_worker(control, unit)
            original_run = control.run
            def failed_client(command):
                control.states[UNIT] = 'active'
                return original_run(command)
            control.run = failed_client
            control.worker_absent = lambda unit: control.state(unit) == 'inactive'
            self.assertEqual(window.supervise(control, UNIT, [TIMER], ['fixture'], Path(root)/'attempt'), 17)
            self.assertEqual(control.state(UNIT), 'inactive')
            self.assertLess(control.calls.index(('stop', UNIT)), control.calls.index(('start', TIMER)))

    def test_unstoppable_real_control_worker_leaves_timers_paused(self):
        with tempfile.TemporaryDirectory() as root:
            control = Control(17)
            control.stop_worker = lambda unit: window.SystemControl.stop_worker(control, unit)
            control.worker_absent = lambda unit: control.state(unit) == 'inactive'
            original_change = control.change
            def change(action, unit):
                original_change(action, unit)
                if unit == UNIT: control.states[UNIT] = 'active'
            control.change = change
            def failed_client(command):
                control.states[UNIT] = 'active'
                return 17
            control.run = failed_client
            p = Path(root)/'attempt'
            with self.assertRaisesRegex(RuntimeError, 'still active'):
                window.supervise(control, UNIT, [TIMER], ['fixture'], p)
            self.assertEqual(control.state(TIMER), 'inactive')
            self.assertFalse((p/'RESTORED.json').exists())

    def test_recovery_refuses_live_supervisor_before_worker_start(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)/'attempt'; p.mkdir(mode=0o700)
            window.save(p, 'STARTED.json', {'worker_unit': UNIT, 'timer_states': {TIMER:'active'}})
            control = Control()
            with window.attempt_lock(p):
                with self.assertRaisesRegex(ValueError, 'still supervised'):
                    window.recover(control, p)
            self.assertFalse(control.calls)
            window.recover(control, p)
            self.assertTrue((p/'RECOVERED.json').exists())

    def test_service_racing_timer_pause_refuses_worker(self):
        with tempfile.TemporaryDirectory() as root:
            control=Control();original=control.change
            def racing(action,unit):
                original(action,unit)
                if action=='stop':control.states[TIMER[:-6]+'.service']='active'
            control.change=racing
            with self.assertRaisesRegex(RuntimeError,'raced'):
                window.supervise(control,UNIT,[TIMER],['fixture'],Path(root)/'attempt')
            self.assertNotIn(('worker',UNIT),control.calls)
            self.assertEqual(control.state(TIMER),'active')

    def test_metadata_rechecked_inside_paused_window_before_worker(self):
        with tempfile.TemporaryDirectory() as root:
            control=Control();p=Path(root)/'attempt'
            def preflight():
                self.assertEqual(control.state(TIMER),'inactive')
                raise ValueError('catalog changed before mutation')
            with self.assertRaises(ValueError):
                window.supervise(control,UNIT,[TIMER],['fixture'],p,preflight)
            self.assertNotIn(('worker',UNIT),control.calls)
            self.assertEqual(control.state(TIMER),'active')

    def test_writable_state_ancestor_refused_before_timer_mutation(self):
        with tempfile.TemporaryDirectory() as root:
            control=Control();Path(root).chmod(0o777)
            with self.assertRaises(ValueError):
                window.supervise(control,UNIT,[TIMER],['fixture'],Path(root)/'attempt')
            self.assertFalse(control.calls)

    def test_restore_failure_is_explicit(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'attempt';control=Control();control.start_failure=True
            with self.assertRaises(RuntimeError):window.supervise(control,UNIT,[TIMER],['fixture'],p)
            self.assertEqual(json.loads((p/'RESTORED.json').read_text())['result'],'RESTORE_FAILED')

    @unittest.skipUnless(os.geteuid()==0,'protected-state recovery requires actual root ownership')
    def test_interrupted_supervisor_recovery_refuses_live_worker(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'attempt';p.mkdir(mode=0o700)
            window.save(p,'STARTED.json',{'worker_unit':UNIT,'timer_states':{TIMER:'active'}})
            control=Control();control.states[UNIT]='active'
            with self.assertRaises(ValueError):window.recover(control,p)
            self.assertFalse(control.calls)
            control.states[UNIT]='inactive';window.recover(control,p)
            self.assertEqual(json.loads((p/'RECOVERED.json').read_text())['result'],'RECOVERED_INTERRUPTED_SUPERVISION_NOT_PUBLICATION_PASS')


if __name__=='__main__':unittest.main()
