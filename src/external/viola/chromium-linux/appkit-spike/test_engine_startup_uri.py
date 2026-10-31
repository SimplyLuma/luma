# SPDX-License-Identifier: GPL-3.0-only
"""Real singleton helper regression for a slow initial browser dispatch."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from engine_pipe import EnginePipe

class StartupUriTest(unittest.TestCase):
    def test_initial_uri_survives_a_helper_slower_than_interactive_deadline(self):
        # The released helper was killed at12s, dropping its initial URI. Run a
        # real delayed child; successful forwarding must preserve the exact URI.
        with tempfile.TemporaryDirectory(prefix='viola-startup-uri-') as folder:
            root = Path(folder)
            child = root / 'singleton'
            child.write_text('#!' + sys.executable + '\n'
                'import pathlib, sys, time\n'
                'time.sleep(13)\n'
                'pathlib.Path(__file__).with_suffix(".forwarded").write_text(sys.argv[-1])\n')
            child.chmod(0o700)
            engine = EnginePipe.__new__(EnginePipe)
            engine.executable, engine.profile = child, root / 'profile'
            engine.process = SimpleNamespace(poll=lambda: None)
            engine.display = None
            url = 'http://127.0.0.1:43210/initial-page?unique=slow-helper'
            with (root / 'helper.log').open('wb') as engine.log:
                self.assertTrue(engine.open_external(url, startup=True))
            self.assertEqual(child.with_suffix('.forwarded').read_text(), url)

    def test_invalid_uri_never_starts_helper_even_during_startup(self):
        engine = EnginePipe.__new__(EnginePipe)
        # No process or executable exists: validation must precede dispatch.
        self.assertFalse(engine.open_external('javascript:alert(1)', startup=True))

if __name__ == '__main__':
    unittest.main()
