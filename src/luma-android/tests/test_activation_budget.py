# SPDX-License-Identifier: Apache-2.0
"""Contract tests. Actual native D-Bus latency is qualified separately."""
import inspect
import ast
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from luma_android import activation, activation_budget as budget, live_registry


class CallTimeout(Exception):
    pass


class Variant:
    def __init__(self, signature, values):
        self.values = values

    def unpack(self):
        return self.values


class BudgetTransport:
    """Execute the real broker call graph at its declared worst IO costs.

    This is a deterministic transport deadline model, not a claimed native
    D-Bus wall-clock measurement. Authority is independently tested elsewhere.
    """
    def __init__(self):
        self.calls = []
        self.work_ms = 0

    def call_sync(self, dest, path, interface, method, args, reply, flags, timeout, cancel):
        self.calls.append((dest, method, timeout))
        if dest == activation.ANDROID_NAME:
            engine = SimpleNamespace(live_launchable_packages=self.registry)
            value = activation.activate_for_caller(self, ':1.42', args.unpack()[0],
                                                   engine, close=method == 'CloseExisting')
            if self.work_ms >= timeout:
                raise CallTimeout('client deadline expired before the valid broker reply')
            return Variant('(b)', (value,))
        self.work_ms += timeout
        if method == 'GetConnectionUnixUser':
            return Variant('(u)', (1000,))
        if method == 'GetConnectionUnixProcessID':
            return Variant('(u)', (42,))
        if dest == activation.SHELL_NAME:
            return Variant('(b)', (True,))
        raise AssertionError((dest, method))

    def registry(self):
        self.work_ms += (budget.REGISTRY_QUERY_SECONDS + budget.REGISTRY_CLEANUP_SECONDS) * 1000
        return {'org.example.App'}


def repository(connection):
    return SimpleNamespace(
        Gio=SimpleNamespace(bus_get_sync=lambda *_:connection,
            BusType=SimpleNamespace(SESSION=0), DBusCallFlags=SimpleNamespace(NONE=0)),
        GLib=SimpleNamespace(Variant=Variant, VariantType=SimpleNamespace(new=lambda x:x),
                             Error=CallTimeout))


class ActivationBudgetTests(unittest.TestCase):
    def request(self, connection, **kwargs):
        with mock.patch.dict(sys.modules, {'gi.repository':repository(connection)}), \
             mock.patch.object(activation.os, 'getuid', return_value=1000), \
             mock.patch.object(activation, '_start', return_value='owned-start'):
            return activation.request_existing_restore('org.example.App', **kwargs)

    def test_restore_and_close_keep_client_through_the_actual_broker_call_graph(self):
        for close in (False, True):
            with self.subTest(close=close):
                connection = BudgetTransport()
                self.assertTrue(self.request(connection, close=close))
                self.assertEqual(connection.work_ms, budget.EXISTING_WORK_TIMEOUT_MS)
                self.assertEqual(connection.calls[0], (activation.ANDROID_NAME,
                    'CloseExisting' if close else 'ActivateExisting', budget.EXISTING_CLIENT_TIMEOUT_MS))
                self.assertEqual([c[1] for c in connection.calls[1:]],
                    ['GetConnectionUnixUser','GetConnectionUnixProcessID']*2 +
                    ['CloseAndroidApplicationForCaller' if close else 'ActivateAndroidApplicationForCaller'])
                self.assertGreater(connection.calls[0][2], connection.work_ms)

    def test_existing_methods_exclude_the_separate_prelaunch_shell_check(self):
        # Exercise the actual service dispatcher, including its explicit launch
        # flag; neither existing-only operation inherits Launch's extra RPC.
        tree = ast.parse(Path(activation.__file__).with_name('service.py').read_text())
        function = next(n for n in ast.walk(tree)
                        if isinstance(n,ast.FunctionDef) and n.name == 'on_method_call')
        namespace = {'LumaAndroidError':RuntimeError}
        module = ast.Module(body=[ast.ImportFrom(module='__future__',
            names=[ast.alias(name='annotations')],level=0),function],type_ignores=[])
        exec(compile(ast.fix_missing_locations(module),'actual-android-service-dispatch','exec'),namespace)
        service = SimpleNamespace(request_activation=mock.Mock())
        dispatch = namespace['on_method_call']
        for method,close in (('ActivateExisting',False),('CloseExisting',True)):
            invocation = mock.Mock()
            dispatch(service,None,':1.42',activation.ANDROID_PATH,
                     activation.ANDROID_NAME,method,Variant('(s)',('org.example.App',)),invocation)
            service.request_activation.assert_called_with(None,':1.42','org.example.App',
                                                           invocation,launch=False,close=close)
        invocation = mock.Mock()
        dispatch(service,None,':1.42',activation.ANDROID_PATH,activation.ANDROID_NAME,
                 'Launch',Variant('(s)',('org.example.App',)),invocation)
        service.request_activation.assert_called_with(None,':1.42','org.example.App',
                                                       invocation,launch=True,close=False)

    def test_original_15s_deadline_rejects_valid_bounded_work(self):
        # This is the actual old value, exercised through the production client
        # and broker functions, rather than only comparing two constants.
        with mock.patch.object(activation, 'EXISTING_CLIENT_TIMEOUT_MS', 15000), \
             self.assertLogs('luma_android.activation', level='WARNING'):
            self.assertFalse(self.request(BudgetTransport()))

    def test_unresponsive_transport_uses_finite_deadline_and_preserves_failure(self):
        connection = mock.Mock()
        connection.call_sync.side_effect = CallTimeout('no broker reply')
        with self.assertLogs('luma_android.activation', level='WARNING'):
            self.assertFalse(self.request(connection))
        self.assertEqual(connection.call_sync.call_args.args[-2], budget.EXISTING_CLIENT_TIMEOUT_MS)
        self.assertGreater(budget.EXISTING_CLIENT_TIMEOUT_MS, 0)
        self.assertLessEqual(budget.EXISTING_CLIENT_TIMEOUT_MS, 45000)

    def test_live_query_and_cleanup_consume_the_same_declared_bounds(self):
        self.assertEqual(inspect.signature(live_registry.bounded_output).parameters['timeout'].default,
                         budget.REGISTRY_QUERY_SECONDS)
        actual_wait = live_registry.subprocess.Popen.wait
        waits = []
        def wait(process, timeout=None):
            waits.append(timeout)
            return actual_wait(process, timeout=timeout)
        with mock.patch.object(live_registry.subprocess.Popen, 'wait', wait):
            self.assertEqual(live_registry.bounded_output([sys.executable,'-I','-c','print("live")']), 'live\n')
        self.assertIn(budget.REGISTRY_CLEANUP_SECONDS, waits)


if __name__ == '__main__':
    unittest.main()
