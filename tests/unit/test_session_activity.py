# SPDX-License-Identifier: Apache-2.0
"""Exercise the actual asynchronous portal protocol on an isolated session bus."""
import importlib.util
from pathlib import Path
import time
import unittest
from gi.repository import Gio, GLib

module_path = Path(__file__).resolve().parents[2] / 'src/luma-platform/appkit/luma_appkit/session_activity.py'
spec = importlib.util.spec_from_file_location('session_activity', module_path)
activity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(activity)

XML = '''<node><interface name="org.freedesktop.portal.Inhibit">
<method name="CreateMonitor"><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
<method name="QueryEndResponse"><arg type="o" direction="in"/></method>
</interface></node>'''


def until(predicate):
    deadline = time.monotonic() + 3
    context = GLib.MainContext.default()
    while not predicate() and time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(.002)
    if not predicate():
        raise AssertionError('portal operation did not finish')


class PortalActivityTests(unittest.TestCase):
    def setUp(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.reg = self.bus.register_object(activity._PATH,
            Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], self.called, None, None)
        self.monitors = 0
        self.attempts = 0
        self.acks = 0
        self.fail = False
        self.owner = Gio.bus_own_name_on_connection(self.bus, activity._NAME,
            Gio.BusNameOwnerFlags.NONE, None, None)
        self.monitor = activity.SessionActivity()
        self.disconnects = []

    def tearDown(self):
        for disconnect in self.disconnects:
            disconnect()
        if self.owner:
            Gio.bus_unown_name(self.owner)
        self.bus.unregister_object(self.reg)
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)

    def called(self, bus, sender, path, interface, method, parameters, invocation):
        if method == 'QueryEndResponse':
            self.acks += 1
            invocation.return_value(None)
            return
        if self.fail:
            self.attempts += 1
            invocation.return_dbus_error('org.freedesktop.portal.Error.Failed', 'fixture unavailable')
            return
        self.monitors += 1
        _, options = parameters.unpack()
        suffix = sender.lstrip(':').replace('.', '_') + '/' + options['handle_token']
        request = activity._PATH + '/request/' + suffix
        self.session = activity._PATH + '/session/' + suffix
        invocation.return_value(GLib.Variant('(o)', (request,)))
        bus.emit_signal(sender, request, 'org.freedesktop.portal.Request', 'Response',
            GLib.Variant('(ua{sv})', (0, {'session_handle': GLib.Variant('s', self.session)})))
        self.send(True)

    def send(self, hidden, state=1, session=None):
        self.bus.emit_signal(None, activity._PATH, activity._INTERFACE, 'StateChanged',
            GLib.Variant('(oa{sv})', (session or self.session, {
                'screensaver-active': GLib.Variant('b', hidden),
                'session-state': GLib.Variant('u', state)})))

    def subscribe(self, values):
        disconnect = self.monitor.subscribe(values.append)
        self.disconnects.append(disconnect)
        return disconnect

    def test_one_monitor_shared_unlock_logout_and_final_cleanup(self):
        first, second = [], []
        disconnect = self.subscribe(first)
        until(lambda: first == [False, True])
        other = self.subscribe(second)
        self.assertEqual(second, [True])
        self.assertEqual(self.monitors, 1)
        self.send(False, state=2)
        until(lambda: first[-1] is False and self.acks == 1)
        self.assertEqual(second, [True, False])
        disconnect()
        self.send(True)
        until(lambda: second[-1] is True)
        self.assertEqual(first, [False, True, False])
        other(); other()
        self.assertEqual(self.monitor._subscriptions, [])
        self.assertEqual(self.monitor._watch, 0)

    def test_portal_loss_restores_visibility_and_reappearance_recovers(self):
        values = []
        self.subscribe(values)
        until(lambda: values[-1] is True)
        Gio.bus_unown_name(self.owner); self.owner = 0
        until(lambda: values[-1] is False)
        self.owner = Gio.bus_own_name_on_connection(self.bus, activity._NAME,
            Gio.BusNameOwnerFlags.NONE, None, None)
        until(lambda: values[-1] is True and self.monitors == 2)

    def test_other_sessions_ignored_and_closed_monitor_fails_open(self):
        values = []
        self.subscribe(values)
        until(lambda: values[-1] is True)
        self.send(False, session='/org/example/other')
        self.bus.emit_signal(None, self.session, 'org.freedesktop.portal.Session',
                             'Closed', GLib.Variant('(a{sv})', ({},)))
        until(lambda: values[-1] is False)
        self.assertEqual(values, [False, True, False])

    def test_unavailable_monitor_does_not_disable_rendering_or_poll(self):
        self.fail = True
        values = []
        self.subscribe(values)
        until(lambda: self.attempts == 1 and self.monitor._bus is None)
        self.assertEqual(values, [False])
        self.assertEqual(self.monitors, 0)


if __name__ == '__main__':
    unittest.main()
