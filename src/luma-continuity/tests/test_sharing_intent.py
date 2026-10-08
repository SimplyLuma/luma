import tempfile
from pathlib import Path
from dataclasses import replace
import unittest
from luma_continuity.relay_binding import RelayBinding
from luma_continuity.sharing_intent import SharingIntent


class SharingIntentTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.directory=Path(self.temporary.name)
        self.binding=RelayBinding('account','a'*64,'e'*32,'00000000-0000-4000-8000-000000000001','00000000-0000-4000-8000-000000000002','receiver')
        self.intent=SharingIntent(self.directory,[self.binding])

    def test_restart_loads_only_same_explicit_binding(self):
        self.intent.save({self.binding.peer})
        self.assertEqual(SharingIntent(self.directory,[self.binding]).load(),{self.binding.peer})
        for changes in ({'epoch':'f'*32},{'account':'other'},{'pair_id':'00000000-0000-4000-8000-000000000003'}):
            self.assertEqual(SharingIntent(self.directory,[replace(self.binding,**changes)]).load(),set())

    def test_explicit_stop_survives_restart(self):
        self.intent.save({self.binding.peer});self.intent.save(set())
        self.assertEqual(SharingIntent(self.directory,[self.binding]).load(),set())

    def test_no_writable_public_or_unbound_intent(self):
        with self.assertRaises(PermissionError):self.intent.save({'b'*64})
        self.intent.save({self.binding.peer});self.intent.path.chmod(0o644)
        with self.assertRaises(PermissionError):self.intent.load()
