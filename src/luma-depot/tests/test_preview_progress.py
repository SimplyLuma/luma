# SPDX-License-Identifier: Apache-2.0
"""Execute the shipped callback without a display; GTK geometry has its own gate."""
import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest


def shipped_method(name):
    source = Path(importlib.util.find_spec('luma_depot.window').origin).read_text()
    tree = ast.parse(source)
    window = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'DepotWindow')
    method = next(n for n in window.body if isinstance(n, ast.FunctionDef) and n.name == name)
    namespace = {'GLib': SimpleNamespace(SOURCE_CONTINUE=True, SOURCE_REMOVE=False), 'Progress': object}
    exec(compile(ast.Module(body=[method], type_ignores=[]), 'shipped-window-callback', 'exec'), namespace)
    return namespace[name]


class ProgressCallback(unittest.TestCase):
    def test_progress_preserves_page_and_updates_all_current_controls(self):
        callback = shipped_method('_progress')
        controls = [[], []]
        job = SimpleNamespace(progress=None)
        window = SimpleNamespace(jobs={'test': job}, _progress_views={'test': [x.append for x in controls]},
                                 _refresh_sidebar_counts=lambda: None)
        def rebuild():
            self.fail('installation progress rebuilt the whole page')
        window.render = rebuild
        for fraction in (.1, .5, .99):
            value = SimpleNamespace(app_id='test', fraction=fraction)
            self.assertFalse(callback(window, value))
            self.assertIs(job.progress, value)
        self.assertEqual([[v.fraction for v in x] for x in controls], [[.1, .5, .99], [.1, .5, .99]])

    def test_cancelled_worker_cannot_touch_controls(self):
        callback = shipped_method('_progress')
        def forbidden(*_args):
            self.fail('cancelled worker touched current page')
        window = SimpleNamespace(jobs={}, _progress_views={'test': [forbidden]},
                                 render=forbidden, _refresh_sidebar_counts=forbidden)
        self.assertFalse(callback(window, SimpleNamespace(app_id='test', fraction=.5)))


if __name__ == '__main__':
    unittest.main()
