# SPDX-License-Identifier: Apache-2.0
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from ari_ui.live_data import live_capacity, stored_messages, stored_receipt


class StoredConversation(unittest.TestCase):
    def test_receipts_remain_beside_their_own_reply(self):
        record = {'messages': [
            {'role': 'user', 'content': 'First request'},
            {'role': 'assistant', 'content': 'First reply', 'meta': {'steps': ['one']}},
            {'role': 'user', 'content': 'Second request'},
            {'role': 'assistant', 'content': 'Second reply', 'meta': {'steps': ['two', 'one']}}],
            'steps': [{'id': 'one'}, {'id': 'two'}, {'id': 'legacy'}]}
        self.assertEqual(stored_messages(record), [
            {'u': 'First request'}, {'a': 'First reply'}, {'ch': 'one'},
            {'u': 'Second request'}, {'a': 'Second reply'}, {'ch': 'two'}, {'ch': 'legacy'}])

    def test_signature_uses_recorded_model_and_measured_elapsed_time(self):
        model = {'id': 'actual-cloud-model', 'n': 'Actual model', 'local': False, 'via': 'OpenRouter'}
        record = {'messages': [{'role': 'assistant', 'content': 'Reply', 'meta': {'model_info': model, 'elapsed': 3.1}}]}
        self.assertEqual(stored_messages(record), [{'a': 'Reply', 'm': model['id'], 'model_info': model, 's': 3.1}])
        self.assertEqual(stored_messages({'messages': [{'role': 'assistant', 'content': 'Old reply'}]}), [{'a': 'Old reply'}])

    def test_persisted_revert_and_keep_have_correct_undo_state(self):
        step = {'summary': 'Display changed', 'tool': 'set_refresh_rate', 'created': 1, 'undo': {'tool': 'set_refresh_rate'}}
        reverted = stored_receipt({**step, 'state': 'reverted'}, 'chat', 'Today')
        self.assertEqual(reverted['nodes'][0]['st'], 'undone')
        self.assertEqual(reverted['undo'], 'undone')
        kept = stored_receipt({**step, 'state': 'kept'}, 'chat', 'Today')
        self.assertEqual(kept['nodes'][0]['st'], 'done')
        self.assertEqual(kept['undo'], 'can')
        self.assertTrue(kept['kept'])
        self.assertEqual(kept['cats'], ['Settings'])


class HostCapacity(unittest.TestCase):
    def test_capacity_reads_reported_memory_and_disk_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory, product = root / "meminfo", root / "product"
            memory.write_text("MemTotal: 33554432 kB\nMemAvailable: 20971520 kB\n")
            product.write_text("ThinkPad Test\n")
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            with patch("ari_ui.live_data.shutil.disk_usage", return_value=SimpleNamespace(free=412e9)) as disk:
                value = live_capacity(meminfo=memory, product=product, storage=root / "absent/models")
            self.assertEqual(value, {"mem": 32, "sys": 12, "free": 20, "disk": 412, "n": "ThinkPad Test"})
            disk.assert_called_once_with(root)
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)

    def test_missing_readings_are_not_invented(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("ari_ui.live_data.shutil.disk_usage", side_effect=OSError("unavailable")):
                self.assertEqual(live_capacity(meminfo=root / "missing", product=root / "missing", storage=root), {})

    def test_unknown_available_memory_retains_only_reported_total(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "meminfo"
            for available in ("", "MemAvailable: 40000000 kB\n", "MemAvailable: -1 kB\n"):
                memory.write_text("MemTotal: 33554432 kB\n" + available)
                with patch("ari_ui.live_data.shutil.disk_usage", side_effect=OSError()):
                    self.assertEqual(live_capacity(meminfo=memory, product=root / "missing", storage=root), {"mem": 32})

    def test_malformed_readings_do_not_create_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "meminfo"
            for contents in ("MemTotal: invalid kB\n", "MemTotal: 0 kB\n", "MemTotal: 32 MB\n"):
                memory.write_text(contents)
                with patch("ari_ui.live_data.shutil.disk_usage", side_effect=OSError()):
                    self.assertEqual(live_capacity(meminfo=memory, product=root / "missing", storage=root), {})


if __name__ == "__main__":
    unittest.main()
