# SPDX-License-Identifier: Apache-2.0
"""Desktop launches preserve a custom workspace through the real GLib parser."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from luma_tester.desktop import install


class DesktopTests(unittest.TestCase):
    def test_registered_launcher_preserves_workspace_and_literal_paths(self):
        try:
            import gi
            gi.require_version('GioUnix', '2.0')
            from gi.repository import GioUnix
        except (ImportError, ValueError):self.skipTest('GioUnix required')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);capture=root/'argv.json'
            executable=root/'Tester 100% "$`\\.AppImage'
            executable.write_text('#!/usr/bin/python3\nimport json,sys\nfrom pathlib import Path\nPath('+repr(str(capture))+').write_text(json.dumps(sys.argv[1:]))\n')
            executable.chmod(0o700)
            workspace=root/'My 100% "quoted" `$\\ workspace'
            entry=install(executable,root/'applications-data',state=workspace)
            launcher=GioUnix.DesktopAppInfo.new_from_filename(str(entry))
            self.assertIsNotNone(launcher)
            self.assertTrue(launcher.launch([],None))
            deadline=time.monotonic()+5
            while not capture.exists() and time.monotonic()<deadline:time.sleep(.05)
            self.assertEqual(json.loads(capture.read_text()),['--appimage-extract-and-run','--state',str(workspace.resolve()),'gui'])
