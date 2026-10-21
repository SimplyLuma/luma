# SPDX-License-Identifier: Apache-2.0
"""Actual cold D-Bus directory opening, cwd, argument safety and rejection.

Run only in an owned disposable display/session with the service file below
registered in its private XDG_DATA_HOME. --service runs the production app.
"""
import os
import json
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib
from luma_terminal.terminal import TerminalApplication


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.03)
    raise AssertionError('Timed out waiting for terminal directory opening')


if '--service' in sys.argv:
    app = TerminalApplication()
    def stop():
        for window in app.get_windows():
            window.close()
        app.quit()
        return GLib.SOURCE_REMOVE
    def trace():
        rows = [{'cwd': window.model.active.cwd, 'children': list(window._child_pids.values())}
                for window in app.get_windows()]
        print('OPEN_SERVICE_STATE ' + json.dumps(rows), flush=True)
        return GLib.SOURCE_CONTINUE
    GLib.timeout_add(500, trace)
    GLib.timeout_add_seconds(30, stop)
    raise SystemExit(app.run(['luma-terminal', '--gapplication-service']))


class DirectoryOpening(unittest.TestCase):
    def test_cold_open_and_existing_window_preserve_exact_directories(self):
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        def call(name, path, interface, method, parameters, reply_type=None):
            return bus.call_sync(name, path, interface, method, parameters, reply_type,
                                 Gio.DBusCallFlags.NONE, 10000, None)
        def open_uri(uri):
            call('org.projectluma.Terminal', '/org/projectluma/Terminal',
                 'org.freedesktop.Application', 'Open',
                 GLib.Variant('(asa{sv})', ([uri], {})))
        with tempfile.TemporaryDirectory(prefix='terminal-open-') as temporary:
            root = Path(temporary)
            # URI arguments are data. No shell expansion or source command.
            first = root / 'folder $(touch SHOULD_NOT_EXIST); "quotes"'
            second = root / 'second folder'
            first.mkdir(); second.mkdir()
            open_uri(first.as_uri())
            pid = call('org.freedesktop.DBus', '/org/freedesktop/DBus',
                       'org.freedesktop.DBus', 'GetConnectionUnixProcessID',
                       GLib.Variant('(s)', ('org.projectluma.Terminal',))).unpack()[0]
            self.assertNotEqual(pid, os.getpid())
            def directories():
                found = set()
                # VTE forks from a GLib worker, so inspect each task's children.
                children = set()
                for task in Path(f'/proc/{pid}/task').iterdir():
                    try:
                        children.update((task / 'children').read_text().split())
                    except FileNotFoundError:
                        pass
                for child in children:
                    try:
                        found.add(os.readlink(f'/proc/{child}/cwd'))
                    except FileNotFoundError:
                        pass
                return found
            try:
                wait_for(lambda: str(first) in directories())
            except AssertionError as error:
                raise AssertionError(f'{error}; pid={pid}, observed={directories()}') from error
            open_uri(second.as_uri())
            wait_for(lambda: {str(first), str(second)} <= directories())
            original = directories()
            for uri in ['sftp://example.invalid/folder', (root / 'missing').as_uri(), Path(__file__).as_uri()]:
                open_uri(uri)
            time.sleep(.3)
            self.assertEqual(directories(), original)
            self.assertFalse((root / 'SHOULD_NOT_EXIST').exists())
            self.assertFalse(Path('/source/SHOULD_NOT_EXIST').exists())
            # Exact owned service PID; fixture/container cleanup reaps all work.
            os.kill(pid, signal.SIGTERM)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], verbosity=2)
