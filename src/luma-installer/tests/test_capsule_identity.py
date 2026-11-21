"""Every installed application runs under its own SELinux category pair."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from luma_installer import capsule_identity as identity
from luma_installer import launcher
from luma_installer.desktop import read_record, write_record


class Pairs(unittest.TestCase):
    def test_index_and_pair_are_inverse_over_the_whole_space(self):
        self.assertEqual(identity.index_pair(0), (0, 1))
        self.assertEqual(identity.index_pair(identity.PAIRS - 1), (1022, 1023))
        for index in (0, 1, 1022, 1023, 1024, 2045, 261888, identity.PAIRS - 2, identity.PAIRS - 1):
            pair = identity.index_pair(index)
            self.assertLess(pair[0], pair[1])
            self.assertEqual(identity.pair_index(pair), index)

    def test_levels_parse_only_in_the_form_they_are_written(self):
        self.assertEqual(identity.parse_level("s0:c3,c900"), (3, 900))
        for value in ("s0:c900,c3", "s0:c3,c3", "s0:c3,c1024", "s0:c3", "s0-s0:c0.c1023", "", None, 7):
            self.assertIsNone(identity.parse_level(value))

    def test_a_taken_pair_moves_to_the_next_free_one(self):
        self.assertEqual(identity.choose((4, 9), []), (4, 9))
        self.assertEqual(identity.choose((4, 9), [(4, 9)]), (4, 10))
        self.assertEqual(identity.choose((4, 1023), [(4, 1023)]), (5, 6))
        self.assertEqual(identity.choose((1022, 1023), [(1022, 1023), (0, 1)]), (0, 2))


class Allocation(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch.dict(os.environ, {"XDG_DATA_HOME": self.directory.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_existing_record_keeps_the_pair_its_files_already_carry(self):
        write_record("one", {"format": "deb", "sha256": "a" * 64})
        level = identity.level(read_record("one"))
        self.assertEqual(identity.parse_level(level), identity.legacy_pair("a" * 64))
        self.assertEqual(read_record("one")["mcs_level"], level)
        self.assertEqual(identity.level(read_record("one")), level)

    def test_two_records_whose_hash_gave_one_pair_are_separated(self):
        write_record("one", {"format": "deb", "sha256": "a" * 64})
        write_record("two", {"format": "rpm", "sha256": "b" * 64})
        with patch.object(identity, "legacy_pair", return_value=(10, 20)):
            first = identity.level(read_record("one"))
            second = identity.level(read_record("two"))
        self.assertEqual(first, "s0:c10,c20")
        self.assertEqual(second, "s0:c10,c21")
        self.assertEqual(read_record("two")["mcs_level"], second)

    def test_every_application_gets_a_different_pair(self):
        with patch.object(identity, "legacy_pair", return_value=(0, 1022)):
            levels = set()
            for number in range(40):
                write_record(f"app-{number}", {"format": "deb", "sha256": f"{number:064x}"})
                levels.add(identity.level(read_record(f"app-{number}")))
        self.assertEqual(len(levels), 40)

    def test_a_stale_copy_of_the_record_does_not_allocate_twice(self):
        write_record("one", {"format": "deb", "sha256": "a" * 64})
        stale = read_record("one")
        first = identity.level(read_record("one"))
        with patch.object(identity, "choose", side_effect=AssertionError("allocated again")):
            self.assertEqual(identity.level(stale), first)

    def test_fields_another_writer_added_survive_allocation(self):
        write_record("one", {"format": "deb", "sha256": "a" * 64})
        stale = read_record("one")
        write_record("one", {**stale, "mime_types": ["x-scheme-handler/one"]})
        identity.level(stale)
        self.assertEqual(read_record("one")["mime_types"], ["x-scheme-handler/one"])

    def test_an_application_installed_before_marking_may_find_its_older_keyring_entries_for_a_while(self):
        write_record("old", {"format": "deb", "sha256": "d" * 64})
        record, adopt = identity.keyring_adoption(read_record("old"), now=1000)
        self.assertTrue(adopt)
        self.assertEqual(read_record("old")["keyring"], {"adopt_until": 1000 + identity.ADOPTION_SECONDS})
        self.assertEqual(record["keyring"], read_record("old")["keyring"])
        # The clock does not start again on a later start.
        self.assertTrue(identity.keyring_adoption(read_record("old"), now=5000)[1])
        self.assertEqual(read_record("old")["keyring"]["adopt_until"], 1000 + identity.ADOPTION_SECONDS)
        self.assertFalse(identity.keyring_adoption(read_record("old"), now=1001 + identity.ADOPTION_SECONDS)[1])

    def test_an_application_installed_now_has_nothing_older_to_find(self):
        write_record("new", {"format": "deb", "sha256": "e" * 64, "keyring": identity.new_keyring_state()})
        record, adopt = identity.keyring_adoption(read_record("new"), now=1000)
        self.assertFalse(adopt)
        self.assertEqual(read_record("new")["keyring"], {"adopt_until": 0})
        self.assertEqual(identity.keyring_tag(record), "e" * 64)

    def test_the_capsule_runs_at_the_allocated_level(self):
        home = Path(self.directory.name)
        write_record("fixture", {"format": "deb", "sha256": "c" * 64, "image": "localhost/fixture",
                                 "command": "/usr/bin/true", "mcs_level": "s0:c7,c8"})
        with patch("pathlib.Path.home", return_value=home), \
             patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.directory.name}), \
             patch.object(launcher, "_running_capsule", return_value=False), \
             patch.object(launcher, "_start_bus_proxy", return_value=(None, None)), \
             patch.object(launcher, "_person_folders", return_value=[]), \
             patch.object(launcher, "_guarded", return_value=0) as run:
            launcher._launch_deb(read_record("fixture"))
        arguments = run.call_args.args[1]
        self.assertIn("label=level:s0:c7,c8", arguments)


if __name__ == "__main__":
    unittest.main()
