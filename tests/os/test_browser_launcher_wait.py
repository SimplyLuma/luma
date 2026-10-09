# SPDX-License-Identifier: Apache-2.0
"""Real Gio launch completion with output still owned by a GUI descendant."""
from pathlib import Path
import tempfile
import time
import unittest

try:
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
except (ImportError, AttributeError, ValueError):
    Gio = GLib = None


@unittest.skipIf(Gio is None, 'Requires the shipped Gio introspection runtime')
class BrowserLauncherWaitTests(unittest.TestCase):
    def launch(self, directory, program):
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.NONE)
        launcher.set_stdout_file_path(str(directory / 'stdout'))
        launcher.set_stderr_file_path(str(directory / 'stderr'))
        child = launcher.spawnv(['/usr/bin/python3', '-c', program])
        loop = GLib.MainLoop()
        result = []

        def finished(process, response):
            result.append(process.wait_finish(response))
            loop.quit()

        timeout = GLib.timeout_add(1500, lambda: (loop.quit(), False)[1])
        started = time.monotonic()
        child.wait_async(None, finished)
        loop.run()
        if result:
            GLib.source_remove(timeout)
        self.assertEqual(result, [True], 'Launcher exit must not wait for descendant output EOF')
        self.assertLess(time.monotonic() - started, 1.5)
        return child

    def test_launcher_finishes_while_descendant_owns_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            program = (
                'import subprocess,sys;'
                'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(2)"]);'
                'print(p.pid,flush=True)')
            child = self.launch(directory, program)
            self.assertTrue(child.get_successful())
            descendant = Path('/proc') / (directory / 'stdout').read_text().strip()
            self.assertTrue(descendant.exists(), 'The output-owning descendant must still be alive')
            # The owned fixture exits naturally; no unrelated process receives a signal.
            time.sleep(2.1)

    def test_launch_failure_keeps_its_real_exit_status_and_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            child = self.launch(directory, 'import sys;print("launch refused",file=sys.stderr);sys.exit(17)')
            self.assertFalse(child.get_successful())
            self.assertEqual(child.get_exit_status(), 17)
            self.assertIn('launch refused', (directory / 'stderr').read_text())


if __name__ == '__main__':
    unittest.main()
