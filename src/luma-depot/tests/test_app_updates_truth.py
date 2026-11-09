# SPDX-License-Identifier: Apache-2.0
"""Depot's Updates page tells the truth about app updates, and the background run can be followed."""
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from luma_depot import autoupdate, native
from luma_depot.providers import Result

try:  # the page and the application need the platform's typelibs (the package build has them)
    from luma_depot.pages.updates import UpdatesPage
    from luma_depot.window import DepotApplication
except (ImportError, ValueError):
    UpdatesPage = DepotApplication = None


class FirstCheckAfterBoot(unittest.TestCase):
    """The monotonic clock starts at boot: "never checked" must not read as "checked just now"."""

    def test_a_host_started_moments_after_boot_still_checks(self):
        installation = native.NativeInstallation()
        found = {'io.luma.Monitor': {'version': '2', 'installed_commit': 'a' * 64}}
        with (patch.object(native.time, 'monotonic', return_value=42.0),
              patch.object(installation, '_check_updates', return_value=found) as check):
            self.assertEqual(installation._pending_updates(), found)
            self.assertEqual(installation._pending_updates(), found)  # then cached for ten minutes
        check.assert_called_once()

    def test_a_forced_check_after_a_cached_one(self):
        installation = native.NativeInstallation()
        with (patch.object(native.time, 'monotonic', return_value=42.0),
              patch.object(installation, '_check_updates', side_effect=[{}, {'x': {}}]) as check):
            installation._pending_updates()
            installation.force_update_check = True
            self.assertEqual(installation._pending_updates(), {'x': {}})
        self.assertEqual(check.call_count, 2)


class Window:
    def __init__(self):
        self.jobs, self.install_listeners, self.started = {}, [], []
        self.provisioning = None
        self.destroyed = False

    def _update(self, app_id, **_kwargs):
        self.started.append(app_id)
        return True

    def get_visible(self):
        return False

    def destroy(self):
        self.destroyed = True


class Application:
    def __init__(self, *windows):
        self.windows = list(windows)
        self.held = 0

    def get_windows(self):
        return list(self.windows)

    def hold(self):
        self.held += 1

    def release(self):
        self.held -= 1

    def send_notification(self, *_args):
        pass


class Follower:
    def __init__(self):
        self.seen = []

    def app_updates_changed(self, run):
        self.seen.append((run.active, sorted(run.running), list(run.queue)))


class BackgroundRun(unittest.TestCase):
    def setUp(self):
        self.window, self.shell = Window(), Follower()
        self.application = Application(self.shell)
        self.run = autoupdate.AppUpdateRun(self.application, self.window)
        self.run.active = True
        self.run.pending = {a: SimpleNamespace(app_id=a, name=a, release='2', commit='b' * 64,
                                               installed_commit='a' * 64) for a in ('monitor', 'viola', 'tide')}
        self.run.queue = ['monitor', 'viola', 'tide']
        idle = patch.object(autoupdate.GLib, 'idle_add', side_effect=lambda f, *a: f(*a))
        idle.start()
        self.addCleanup(idle.stop)
        quiet = patch.multiple(autoupdate.AppUpdateRun, _refresh_firmware_blocklist=lambda s: None,
                               _remind_security_firmware=lambda s: None)
        quiet.start()
        self.addCleanup(quiet.stop)

    def test_one_at_a_time_and_followed_to_the_end(self):
        self.run._next()
        self.assertEqual(self.window.started, ['monitor'])
        self.assertTrue(self.run.busy('viola'))
        self.run.update_finished('monitor', Result(value=None), False)
        self.assertEqual(self.window.started, ['monitor', 'viola'])
        self.run.update_finished('viola', Result(value=None), False)
        with patch.object(autoupdate.depot_autoupdate, 'unannounced', return_value=()):
            self.run.update_finished('tide', Result(value=None), False)
        self.assertFalse(self.run.active)
        self.assertEqual([p.app_id for p in self.run.completed], ['monitor', 'viola', 'tide'])
        self.assertEqual(self.shell.seen[0], (True, ['monitor'], ['viola', 'tide']))
        self.assertEqual(self.shell.seen[-1][0], False)  # the shell hears the run is over
        self.assertTrue(self.window.destroyed)

    def test_update_now_starts_the_rest_together(self):
        self.run._next()
        self.run.hurry()
        self.assertEqual(self.window.started, ['monitor', 'viola', 'tide'])
        self.assertEqual(self.run.queue, [])
        self.assertEqual(self.shell.seen[-1], (True, ['monitor', 'tide', 'viola'], []))

    def test_an_update_already_running_is_not_started_twice(self):
        self.window.jobs['monitor'] = SimpleNamespace(failed=False)
        self.run._next()
        self.assertEqual(self.window.started, ['viola'])

    def test_settings_from_the_host_are_waited_for(self):
        self.window.host_client = object()
        self.window._settings_ready = False
        waits = []
        with patch.object(autoupdate.GLib, 'timeout_add', side_effect=lambda ms, f: waits.append(ms)):
            self.run._checked(Result(value=()))
        self.assertEqual(waits, [500])
        self.assertTrue(self.run.active)


class RunnerWindow(unittest.TestCase):
    def test_the_redesign_gets_a_hidden_window_that_is_never_shown(self):
        class DepotWindow:
            def __init__(self, application):
                application.windows.append(self)

        application = Application(object())  # the redesigned shell
        module = SimpleNamespace(DepotWindow=DepotWindow, use_redesign=lambda: True)
        with patch.dict(sys.modules, {'luma_depot.window': module}):
            window = autoupdate._runner_window(application)
            self.assertTrue(window.background_runner)
            self.assertIs(autoupdate._runner_window(application), window)


@unittest.skipIf(DepotApplication is None, 'needs the platform typelibs')
class ShownWindow(unittest.TestCase):
    def test_opening_depot_never_presents_the_hidden_runner(self):
        runner = SimpleNamespace(background_runner=True)
        shell = SimpleNamespace()
        app = SimpleNamespace(props=SimpleNamespace(active_window=runner), get_windows=lambda: [runner, shell])
        self.assertIs(DepotApplication.shown_window(app), shell)
        app = SimpleNamespace(props=SimpleNamespace(active_window=runner), get_windows=lambda: [runner])
        self.assertIsNone(DepotApplication.shown_window(app))


@unittest.skipIf(UpdatesPage is None, 'needs the platform typelibs')
class PageStatus(unittest.TestCase):
    def page(self, run=None, live=True):
        installer = SimpleNamespace(force_update_check=False, installed=lambda cb: self.calls.append(cb))
        store = SimpleNamespace(installer=installer if live else None, _installed=self.loaded.append)
        shell = SimpleNamespace(store=store, app_update_run=lambda: run, place='other', item=None)
        return UpdatesPage(shell)

    def setUp(self):
        self.calls, self.loaded = [], []

    def test_never_up_to_date_before_a_check_comes_back(self):
        page = self.page()
        self.assertEqual(page.apps_status(), 'checking')
        page.check_apps()
        self.assertTrue(page.shell.store.installer.force_update_check)
        self.assertEqual(page.apps_status(), 'checking')
        self.calls[0](Result(value=()))
        self.assertEqual((page.apps_status(), len(self.loaded)), ('current', 1))

    def test_a_check_that_failed_says_so(self):
        page = self.page()
        page.check_apps()
        self.calls[0](Result(error=Exception('offline')))
        self.assertEqual(page.apps_status(), 'failed')

    def test_the_background_run_shows(self):
        self.assertEqual(self.page(run=SimpleNamespace(active=True)).apps_status(), 'running')

    def test_the_sample_needs_no_check(self):
        self.assertEqual(self.page(live=False).apps_status(), 'current')


if __name__ == '__main__':
    unittest.main()
