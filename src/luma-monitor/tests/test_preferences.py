# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import tempfile
import unittest

from luma_monitor.preferences import update_preferences


class PreferencesTests(unittest.TestCase):
    def test_edit_preserves_unknown_fields_and_backs_up_original_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'preferences.json'
            original=b'{"tab":"cpu","other":{"retain":[1,2]},"width":500}\n'
            path.write_bytes(original)
            update_preferences(path,{'tab':'memory','height':700})
            self.assertEqual(json.loads(path.read_bytes()),{'tab':'memory','other':{'retain':[1,2]},'width':500,'height':700})
            self.assertEqual(path.with_suffix('.json.bak').read_bytes(),original)

    def test_later_edits_keep_the_first_backup_and_previous_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'preferences.json';original=b'{"width":500}'
            path.write_bytes(original)
            update_preferences(path,{'tab':'disk'})
            update_preferences(path,{'height':700})
            self.assertEqual(json.loads(path.read_bytes()),{'width':500,'tab':'disk','height':700})
            self.assertEqual(path.with_suffix('.json.bak').read_bytes(),original)

    def test_damaged_or_non_object_preferences_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'preferences.json'
            for original in (b'broken',b'[]'):
                with self.subTest(original=original):
                    path.write_bytes(original)
                    with self.assertRaises(ValueError):update_preferences(path,{'tab':'cpu'})
                    self.assertEqual(path.read_bytes(),original)
                    self.assertFalse(path.with_suffix('.json.bak').exists())
