# SPDX-License-Identifier: Apache-2.0
"""Every Depot window Depot can open reaches the journal as "shown"."""
import ast
import contextlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

HERE = Path(__file__).resolve()
if (HERE.parents[2] / "luma-depot").is_dir():
    sys.path.insert(0, str(HERE.parents[2] / "luma-depot"))
from luma_depot import host_wire, window as depot_window


def built_windows():
    """The window classes DepotApplication.window constructs."""
    tree = ast.parse(Path(importlib.util.find_spec('luma_depot.window').origin).read_text())
    app = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'DepotApplication')
    method = next(n for n in app.body if isinstance(n, ast.FunctionDef) and n.name == 'window')
    return sorted({node.value.func.id for node in ast.walk(method)
                   if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                   and isinstance(node.value.func, ast.Name)
                   and any(isinstance(t, ast.Name) and t.id == 'window' for t in node.targets)})


class FakeWindow:
    def __init__(self, mapped=False):
        self.mapped, self.handlers = mapped, {}

    def connect(self, signal, handler):
        self.handlers.setdefault(signal, []).append(handler)

    def get_mapped(self):
        return self.mapped

    def map(self):
        self.mapped = True
        for handler in self.handlers.get('map', ()):
            handler(self)


class ShownIsRecorded(unittest.TestCase):
    @contextlib.contextmanager
    def watch(self, window):
        app = NS(started=0.0)
        with mock.patch.object(depot_window, 'lifecycle') as emit, \
             mock.patch.object(depot_window.GLib, 'timeout_add_seconds') as late:
            depot_window.DepotApplication._watch(app, window)
            yield emit
            late.assert_called_once()

    def shown(self, emit):
        return [c for c in emit.call_args_list if c.args == ('shown',)]

    def test_the_window_classes_are_the_ones_watched_here(self):
        self.assertEqual(built_windows(), ['DepotShell', 'DepotWindow', 'FailureWindow'])

    def test_every_window_class_is_accepted_by_the_host(self):
        for name in built_windows():
            values = {'event': 'shown', 'detail': '', 'seconds': 1.5, 'window': name}
            with self.subTest(window=name):
                self.assertEqual(host_wire.request('Lifecycle', json.dumps(values)), values)

    def test_shown_once_when_the_window_maps_after_the_watch(self):
        for name in built_windows():
            window = type(name, (FakeWindow,), {})()
            with self.subTest(window=name):
                with self.watch(window) as emit:
                    self.assertFalse(self.shown(emit))
                    window.map(); window.map()
                    (call,) = self.shown(emit)
                    self.assertEqual(call.kwargs['window'], name)

    def test_shown_at_once_when_the_window_is_already_mapped(self):
        for name in built_windows():
            window = type(name, (FakeWindow,), {})(mapped=True)
            with self.subTest(window=name):
                with self.watch(window) as emit:
                    (call,) = self.shown(emit)
                    self.assertEqual(call.kwargs['window'], name)
                    window.map()
                    self.assertEqual(len(self.shown(emit)), 1)


if __name__ == '__main__':
    unittest.main()
