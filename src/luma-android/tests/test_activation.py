# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import sys
import unittest
import ast
import threading
import logging
from unittest import mock
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from luma_android import activation
from luma_android.errors import LumaAndroidError

class AndroidActivationTests(unittest.TestCase):
    def test_installed_native_caller_restores_only_after_launch(self):
        events=[]
        engine=SimpleNamespace(live_launchable_packages=lambda: ['org.example.App'],
            launch=lambda package: events.append(('launch', package)))
        with mock.patch.object(activation, 'native_caller', return_value=(42,'100')), \
             mock.patch.object(activation, 'authorize_from_broker', side_effect=lambda conn,sender: events.append(('admit',sender))), \
             mock.patch.object(activation, 'restore_from_broker', side_effect=lambda conn,pkg,**kwargs: events.append(('restore',pkg,kwargs['caller'])) or True):
            self.assertTrue(activation.activate_for_caller(None, ':1.42', 'org.example.App', engine, launch=True))
        self.assertEqual(events, [('admit',':1.42'), ('launch','org.example.App'), ('restore','org.example.App',':1.42')])

    def test_malformed_and_uninstalled_never_launch_or_restore(self):
        engine=mock.Mock(); engine.live_launchable_packages.return_value=[]
        with mock.patch.object(activation,'native_caller',return_value=(42,'100')), mock.patch.object(activation,'restore_from_broker') as restore:
            for value in ['../org.foo','org..foo','org.foo;true','a'*256, None, 'one', 'org.notinstalled.App']:
                with self.assertRaises(LumaAndroidError):
                    activation.activate_for_caller(None, ':1.42', value, engine, launch=True)
            restore.assert_not_called(); engine.launch.assert_not_called()

    def test_launch_failure_never_restores(self):
        engine=mock.Mock(); engine.live_launchable_packages.return_value=['org.example.App']
        engine.launch.side_effect=LumaAndroidError('failed')
        with mock.patch.object(activation,'native_caller',return_value=(42,'100')), mock.patch.object(activation,'authorize_from_broker'), mock.patch.object(activation,'restore_from_broker') as restore:
            with self.assertRaises(LumaAndroidError):
                activation.activate_for_caller(None, ':1.42', 'org.example.App', engine, launch=True)
            restore.assert_not_called()

    def test_shell_native_refusal_never_launches(self):
        engine=mock.Mock(); engine.live_launchable_packages.return_value=['org.example.App']
        with mock.patch.object(activation,'native_caller',return_value=(42,'100')), \
             mock.patch.object(activation,'authorize_from_broker',side_effect=LumaAndroidError('refused')), \
             mock.patch.object(activation,'restore_from_broker') as restore:
            with self.assertRaises(LumaAndroidError):
                activation.activate_for_caller(None, ':1.42', 'org.example.App', engine, launch=True)
            engine.launch.assert_not_called(); restore.assert_not_called()

    def test_vanished_or_replaced_native_caller_never_restores(self):
        engine=mock.Mock(); engine.live_launchable_packages.return_value=['org.example.App']
        for after in [(43,'100'), (42,'101')]:
            with mock.patch.object(activation,'native_caller',side_effect=[(42,'100'),after]), mock.patch.object(activation,'restore_from_broker') as restore:
                with self.assertRaises(LumaAndroidError):
                    activation.activate_for_caller(None, ':1.42', 'org.example.App', engine)
                restore.assert_not_called()

    def test_no_cli_restoration_before_engine_launch(self):
        # The CLI is the actual first-class launcher. A failed Android launch
        # must never reach the broker even when a stale host window exists.
        from luma_android import cli
        with mock.patch.object(cli, 'WaydroidEngine') as factory, \
             mock.patch.object(activation, 'request_existing_restore') as restore, \
             mock.patch.object(cli, '_report_launch_failure'):
            factory.return_value.launch.side_effect=LumaAndroidError('failure')
            self.assertEqual(cli.main(['launch','org.example.App']), 1)
            restore.assert_not_called()

class ActivationAdmissionTests(unittest.TestCase):
    def test_bounded_worker_admission_and_unexpected_exception_release(self):
        # Exercise the maintained handler directly without constructing the
        # session service or starting an Android runtime during a unit check.
        source = Path(activation.__file__).with_name('service.py').read_text()
        tree = ast.parse(source)
        function = next(node for node in ast.walk(tree)
                        if isinstance(node, ast.FunctionDef) and node.name == 'request_activation')
        work = []; callbacks = []
        namespace = {'GLib': SimpleNamespace(idle_add=lambda fn,*args: callbacks.append((fn,args)),
                     Variant=lambda *args: args, SOURCE_REMOVE=False),
                     'activate_for_caller': mock.Mock(side_effect=ValueError('malformed provider JSON')),
                     'logging': logging}
        exec(compile(ast.Module(body=[function],type_ignores=[]), 'production-activation-handler', 'exec'), namespace)
        service = SimpleNamespace(activation_slots=threading.BoundedSemaphore(2), engine=object(),
            activation_workers=SimpleNamespace(submit=lambda fn: work.append(fn)))
        calls = [mock.Mock() for _ in range(4)]
        request = namespace['request_activation']
        with self.assertLogs(level='ERROR'):
            request(service,None,':1.42','org.example.App',calls[0])
            request(service,None,':1.42','org.example.App',calls[1])
            request(service,None,':1.42','org.example.App',calls[2])
            self.assertEqual(len(work),2)
            calls[2].return_dbus_error.assert_called_once_with('org.projectluma.Error.Busy',mock.ANY)
            for worker in work: worker()
        for callback,args in callbacks: callback(*args)
        for call in calls[:2]:
            call.return_dbus_error.assert_called_once_with('org.projectluma.Error.Failed',mock.ANY)
        self.assertTrue(service.activation_slots.acquire(blocking=False))
        self.assertTrue(service.activation_slots.acquire(blocking=False))
        self.assertFalse(service.activation_slots.acquire(blocking=False))

if __name__=='__main__': unittest.main()
