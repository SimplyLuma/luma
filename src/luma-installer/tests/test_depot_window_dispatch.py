# SPDX-License-Identifier: Apache-2.0
# Private source integration: actual Gtk/Gio imported; controller with controlled provider.
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
if (HERE.parents[2] / "luma-depot").is_dir():
    sys.path.insert(0, str(HERE.parents[2] / "luma-depot"))
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest import mock
from luma_depot.window import DepotWindow, Job
from luma_depot import autoupdate
from luma_depot.providers import Result
from luma_installer import depot_autoupdate as policy
from gi.repository import Gio, GLib

APP = 'catalog:tide'
OLD, NEW = 'a'*64, 'b'*64

class DispatchSafety(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {'XDG_STATE_HOME': self.temp.name})
        self.env.start()
        self.record = NS(app_id=APP, app=NS(name='Tide'), has_update=True,
            update_commit=NEW, commit=OLD, update_version='2', permission_changes=())
        self.installer = NS(update=mock.Mock())
        self.view = NS(jobs={}, installed={APP:self.record}, installer=self.installer,
            _asks_for_more=DepotWindow._asks_for_more, _report=mock.Mock(), go=mock.Mock(),
            _remember_before_update=mock.Mock(), render=mock.Mock(), _progress=mock.Mock(),
            _update_done=mock.Mock())
    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()
    def run_update(self, **kwargs):
        args = dict(expected_commit=NEW, expected_installed_commit=OLD)
        args.update(kwargs)
        return DepotWindow._update(self.view, APP, **args)
    def grow(self):
        self.record.permission_changes = (NS(change='widened', key='files.music'),)
    def assert_no_dispatch(self):
        self.installer.update.assert_not_called()
        self.assertEqual(self.view.jobs, {})
    def test_stale_displayed_target_never_approves_current_build(self):
        self.grow()
        self.record.update_commit = 'c'*64
        self.assertFalse(self.run_update(approve=True))
        self.assert_no_dispatch()
        self.assertEqual(policy.load()['approved'], [])
    def test_stale_displayed_baseline_never_approves(self):
        self.grow()
        self.record.commit = 'c'*64
        self.assertFalse(self.run_update(approve=True))
        self.assert_no_dispatch()
        self.assertEqual(policy.load()['approved'], [])
    def test_bulk_retry_without_consent_refuses_and_opens_review(self):
        self.grow()
        self.assertFalse(self.run_update(approve=False))
        self.assert_no_dispatch()
        self.view.go.assert_called_once_with('app', APP)
        self.assertEqual(policy.load()['approved'], [])
    def test_only_explicit_current_permission_review_approves_and_dispatches(self):
        self.grow()
        self.assertTrue(self.run_update(approve=True))
        pending=policy.Pending(APP,'Tide','2',True,NEW,OLD)
        self.assertFalse(policy.is_held(pending,policy.load()))
        self.assertTrue(policy.is_held(policy.Pending(APP,'Tide','2',True,NEW,'c'*64),policy.load()))
        self.installer.update.assert_called_once()
        args, kwargs=self.installer.update.call_args
        self.assertEqual(kwargs, {'expected_commit':NEW,'expected_installed_commit':OLD})
        self.assertIsInstance(args[3], Gio.Cancellable)
    def test_approved_retry_retains_exact_target_and_baseline(self):
        self.grow()
        policy.approve(policy.Pending(APP,'Tide','2',True,NEW,OLD))
        self.assertTrue(self.run_update(approve=False))
        self.installer.update.assert_called_once()
    def test_signed_ui_delegates_explicit_consent_without_writing_local_authority(self):
        self.grow();self.installer.requires_permission_approval=True
        self.installer.permission_state={'approved':[]}
        with mock.patch.object(policy,'approve',side_effect=AssertionError('UI must not write host authority')):
            self.assertTrue(self.run_update(approve=True))
        self.assertTrue(self.installer.update.call_args.kwargs['permission_approval'])
        self.assertEqual(policy.load()['approved'],[])
    def test_copied_ui_approval_cannot_replace_current_host_consent(self):
        self.grow();self.installer.requires_permission_approval=True
        self.installer.permission_state={'approved':[]}
        policy.approve(policy.Pending(APP,'Tide','2',True,NEW,OLD))
        self.assertFalse(self.run_update(approve=False,automatic=True))
        self.assert_no_dispatch()
    def test_failed_persistence_never_dispatches(self):
        self.grow()
        with mock.patch.object(policy,'approve',side_effect=OSError('private fixture failure')):
            self.assertFalse(self.run_update(approve=True))
        self.assert_no_dispatch()
    def test_automatic_hold_returns_false_without_interactive_navigation(self):
        self.grow()
        self.assertFalse(self.run_update(approve=False,automatic=True))
        self.assert_no_dispatch()
        self.view.go.assert_not_called()
        self.view._report.assert_not_called()

class CancelledTerminal(unittest.TestCase):
    def setUp(self):
        self.original=Job(APP,'update',Gio.Cancellable())
        self.retry=Job(APP,'update',Gio.Cancellable())
        self.listener=NS(update_finished=mock.Mock(),install_finished=mock.Mock())
        self.view=NS(jobs={APP:self.original},installer=NS(installed=mock.Mock(),delivers_cancelled_terminal=True),
            install_listeners=[self.listener],render=mock.Mock(),_installed_loaded=mock.Mock(),_progress=mock.Mock())
        self.view._current_transaction=lambda job,result=None:DepotWindow._current_transaction(self.view,job,result)
    def test_cancel_retains_job_until_one_actual_terminal_reply(self):
        DepotWindow._cancel(self.view,APP)
        self.assertIs(self.view.jobs[APP],self.original)
        self.listener.update_finished.assert_not_called()
        DepotWindow._update_done(self.view,APP,Result(value=None),self.original)
        self.assertNotIn(APP,self.view.jobs)
        self.listener.update_finished.assert_called_once()
        self.assertTrue(self.listener.update_finished.call_args.args[2])
        self.view.installer.installed.assert_called_once()
    def test_late_success_refreshes_actual_state_without_mutating_retry_or_double_notice(self):
        self.view.jobs[APP]=self.retry
        DepotWindow._update_done(self.view,APP,Result(value=None),self.original)
        self.assertIs(self.view.jobs[APP],self.retry)
        self.assertEqual(self.retry.failed,'')
        self.listener.update_finished.assert_not_called()
        self.view.installer.installed.assert_called_once()
    def test_late_failure_and_progress_cannot_touch_retry(self):
        from luma_depot.providers import Result,ProviderError,Progress
        self.view.jobs[APP]=self.retry
        DepotWindow._update_done(self.view,APP,Result(error=ProviderError('old transaction')),self.original)
        DepotWindow._progress_for_job(self.view,self.original,Progress(APP,.5,0,0,'Updating'))
        self.assertIs(self.view.jobs[APP],self.retry);self.assertEqual(self.retry.failed,'')
        self.listener.update_finished.assert_not_called()
        self.view.installer.installed.assert_not_called();self.view._progress.assert_not_called()
    def test_refused_automatic_dispatch_does_not_wait_for_nonexistent_job(self):
        view=NS(jobs={}, _update=mock.Mock(return_value=False))
        runner=autoupdate.AppUpdateRun(NS(),view)
        runner.pending={APP:policy.Pending(APP,'Tide','2',False,NEW,OLD)}
        runner.queue=[APP]
        with mock.patch.object(GLib,'idle_add') as idle:
            runner._next()
        self.assertEqual(runner.current,'')
        idle.assert_called_once_with(runner._next)

if __name__ == '__main__': unittest.main()
