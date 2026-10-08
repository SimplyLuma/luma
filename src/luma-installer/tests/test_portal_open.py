import os
from pathlib import Path
import subprocess
import tempfile
import unittest

class PortalOpen(unittest.TestCase):
    def test_uri_is_one_literal_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'gdbus'
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n')
            fake.chmod(0o755)
            uri = 'https://example.test/?value=$(touch%20no)&a=two words'
            result = subprocess.run(['sh', str(Path(__file__).parents[1] / 'bin/luma-capsule-open'), uri], env={**os.environ, 'PATH':str(root)+os.pathsep+os.environ['PATH'], 'CAPTURE':str(root/'args')})
            self.assertEqual(result.returncode, 0)
            args = (root/'args').read_text().splitlines()
            self.assertIn('org.freedesktop.portal.OpenURI.OpenURI', args)
            self.assertEqual(args[-2], uri)

    def test_option_is_not_a_uri(self):
        result = subprocess.run(['sh', str(Path(__file__).parents[1] / 'bin/luma-capsule-open'), '--help'], capture_output=True)
        self.assertEqual(result.returncode, 2)
