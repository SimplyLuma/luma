import contextlib
import io
from pathlib import Path
import runpy
import unittest

helper = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'bin/luma-waydroid-package-session'))

class RemovalPolicy(unittest.TestCase):
    def test_retention_is_explicit_and_scoped_to_user_zero(self):
        self.assertEqual(helper['removal_command'](['remove-package', 'org.example.App', 'keep-data']),
            ['/system/bin/pm', 'uninstall', '-k', '--user', '0', 'org.example.App'])
        self.assertEqual(helper['removal_command'](['remove-package', 'org.example.App', 'delete-data']),
            ['/system/bin/pm', 'uninstall', '--user', '0', 'org.example.App'])

    def test_arguments_cannot_escape_the_removal_verb(self):
        for args in (['remove-package'], ['remove-package', '--user', 'keep-data'],
                     ['remove-package', 'org.example;id', 'keep-data'],
                     ['remove-package', 'org.example', 'keep-data', '--user', '10'],
                     ['remove-package', 'org.example', 'anything'],
                     ['remove-package', 'org.'+'a'*260, 'keep-data']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit): helper['removal_command'](args)

if __name__ == '__main__': unittest.main()
