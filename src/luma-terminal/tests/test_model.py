"""Session behavior that must remain sound independent of GTK/VTE."""

import unittest

from luma_terminal.model import TerminalModel, history_suggestion, short_path
from luma_terminal.profiles import Profile, argv_for_profile


class TerminalModelTest(unittest.TestCase):
    def test_split_and_close_preserve_surviving_pane_and_directory(self):
        model = TerminalModel("/tmp/luma")
        first = model.active.id
        second = model.split_active("row").id
        model.update_cwd(second, "/tmp/luma/src")
        third = model.split_active("col").id
        self.assertEqual([pane.id for pane in model.current.panes], [first, second, third])
        model.close_pane(second)
        self.assertEqual([pane.id for pane in model.current.panes], [first, third])
        self.assertEqual(model.current.panes[1].cwd, "/tmp/luma/src")

    def test_new_session_inherits_chosen_cwd_but_keeps_older_one(self):
        model = TerminalModel("/tmp/one")
        first = model.current.id
        second = model.new_session(model.active.cwd).id
        model.update_cwd(model.active.id, "/tmp/two")
        model.activate_session(first)
        self.assertEqual(model.active.cwd, "/tmp/one")
        model.activate_session(second)
        self.assertEqual(model.active.cwd, "/tmp/two")

    def test_last_session_replaced_and_rename_reset(self):
        model = TerminalModel("/tmp/one")
        old = model.current.id
        model.rename_session(old, " Build ")
        self.assertEqual(model.current.display_name, "Build")
        model.rename_session(old, "  ")
        self.assertEqual(model.current.display_name, "one")
        model.close_session(old)
        self.assertEqual(len(model.sessions), 1)
        self.assertNotEqual(model.current.id, old)
        self.assertEqual(model.active.cwd, "/tmp/one")

    def test_fixture_home_names_new_and_replacement_sessions(self):
        model = TerminalModel("/home/nick", home="/home/nick")
        self.assertEqual(model.current.display_name, "Home")
        old = model.current.id
        self.assertEqual(model.new_session("/home/nick").display_name, "Home")
        model.close_session(old)
        remaining = model.current.id
        model.close_session(remaining)
        self.assertEqual(model.current.display_name, "Home")
        self.assertEqual(model.active.cwd, "/home/nick")

    def test_history_suggestion_uses_newest_prefix(self):
        self.assertEqual(history_suggestion("git s", ["git show", "git status"]), "tatus")
        self.assertEqual(history_suggestion("", ["git status"]), "")
        self.assertEqual(short_path("/home/nick/src", "/home/nick"), "~/src")
        self.assertEqual(short_path("/home/nick2", "/home/nick"), "/home/nick2")

    def test_existing_profile_command_is_argument_vector(self):
        profile = Profile("a" * 32, "Build", False, "python3 -c 'print(1 + 1)'", 10000, True, "session")
        self.assertEqual(argv_for_profile(profile, "/bin/bash"),
                         ["python3", "-c", "print(1 + 1)"])
        self.assertEqual(argv_for_profile(None, "/bin/bash"), ["/bin/bash"])


if __name__ == "__main__":
    unittest.main()
