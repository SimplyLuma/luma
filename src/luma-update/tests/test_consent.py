# SPDX-License-Identifier: Apache-2.0
"""What a person chooses, and what the agent will admit to.

Updates are optional and visible: automatic downloading can be turned off, one
version can be told to be quiet, nothing is ever installed or restarted without
an explicit Apply(), and Depot can audit where the system comes from and
whether its metadata verified.
"""

import json
import unittest

import fakes
from fakes import graph_doc, release
from luma_update import notifier
from luma_update.engine import UpdateError, automatic_download_allowed, repository_url
from luma_update.config import Settings


class AutomaticDownload(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        self.engine = self.rig.engine()

    def tearDown(self):
        self.rig.close()

    def test_on_by_default_and_published(self):
        self.assertTrue(self.engine.automatic_download_enabled())
        self.assertTrue(self.engine.refresh().automatic_download)

    def test_turning_it_off_stops_the_agent_downloading_on_its_own(self):
        self.engine.set_automatic_download(False)
        self.assertFalse(self.engine.refresh().automatic_download)
        self.engine.automatic()
        self.assertEqual(self.engine.status().state, "available")
        self.assertEqual(self.engine.status().available_version, "1.0.1")
        self.assertFalse([d for d in self.rig.backend.list if d.staged])

    def test_a_person_may_still_download_it(self):
        self.engine.set_automatic_download(False)
        self.engine.check()
        self.engine.download(user_initiated=True)
        self.assertEqual(self.engine.status().state, "staged")

    def test_the_choice_survives_a_restart_of_the_agent(self):
        self.engine.set_automatic_download(False)
        self.assertIs(json.loads(self.rig.paths.state_file.read_text())["automatic_download"], False)
        self.assertFalse(self.rig.engine().automatic_download_enabled())

    def test_the_package_default_applies_until_someone_chooses(self):
        self.rig.settings = Settings(automatic_download=False)
        engine = self.rig.engine()
        self.assertFalse(engine.automatic_download_enabled())
        engine.set_automatic_download(True)
        self.assertTrue(engine.automatic_download_enabled())

    def test_the_policy_helper_takes_the_choice_over_the_default(self):
        allowed, reason = automatic_download_allowed(Settings(), self.rig.probes.network(),
                                                     self.rig.probes.power(), False)
        self.assertFalse(allowed)
        self.assertEqual(reason, "automatic downloads are off")
        self.assertTrue(automatic_download_allowed(Settings(automatic_download=False), self.rig.probes.network(),
                                                   self.rig.probes.power(), True)[0])


class IgnoreOneVersion(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        self.engine = self.rig.engine()
        self.engine.check()

    def tearDown(self):
        self.rig.close()

    def test_an_ignored_version_is_still_offered_but_never_downloaded_alone(self):
        self.engine.ignore_version("1.0.1")
        status = self.engine.status()
        self.assertEqual(status.ignored_version, "1.0.1")
        self.assertEqual(status.available_version, "1.0.1")
        self.engine.automatic()
        self.assertFalse([d for d in self.rig.backend.list if d.staged])
        self.assertEqual(self.engine.status().state, "available")

    def test_it_pins_the_commit_so_a_reused_version_string_is_not_silently_ignored(self):
        self.engine.ignore_version("1.0.1")
        ignored = json.loads(self.rig.paths.state_file.read_text())["ignored"]
        self.assertEqual(ignored["commit"], self.engine.status().available_commit)
        self.assertFalse(self.engine.is_ignored("1.0.1", "some-other-commit"))

    def test_downloading_it_is_the_way_back(self):
        self.engine.ignore_version("1.0.1")
        self.engine.download(user_initiated=True)
        self.assertEqual(self.engine.status().ignored_version, "")

    def test_clearing_it_is_the_other_way_back(self):
        self.engine.ignore_version("1.0.1")
        self.engine.clear_ignored_version()
        self.assertEqual(self.engine.status().ignored_version, "")
        self.rig.now += 7200
        self.engine.automatic()
        self.assertEqual(self.engine.status().state, "staged")

    def test_a_newer_release_is_offered_normally(self):
        self.engine.ignore_version("1.0.1")
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2), release("1.0.2", 3)]))
        self.rig.now += 7200
        self.engine.automatic()
        self.assertEqual(self.engine.status().staged_version, "1.0.2")

    def test_an_empty_version_is_refused(self):
        with self.assertRaises(UpdateError) as caught:
            self.engine.ignore_version("  ")
        self.assertEqual(caught.exception.error_class, "invalid-argument")

    def test_the_notifier_says_nothing_about_it(self):
        status = {"state": "staged", "staged_version": "1.0.1", "staged_commit": "c1", "ignored_version": "1.0.1"}
        plan, _state = notifier.decide(status, notifier.NotifierState(), 0.0)
        self.assertFalse(plan.show_ready)

    def test_the_notifier_withdraws_one_already_on_screen(self):
        state = notifier.NotifierState(ready_commit="c1", ready_shown_at=1.0)
        status = {"state": "staged", "staged_version": "1.0.1", "staged_commit": "c1", "ignored_version": "1.0.1"}
        plan, state = notifier.decide(status, state, 2.0)
        self.assertTrue(plan.withdraw_ready)
        self.assertEqual(state.ready_commit, "")

    def test_the_notifier_still_speaks_for_a_different_version(self):
        status = {"state": "staged", "staged_version": "1.0.2", "staged_commit": "c2", "ignored_version": "1.0.1"}
        plan, _state = notifier.decide(status, notifier.NotifierState(), 0.0)
        self.assertTrue(plan.show_ready)


class WhereItComesFrom(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        self.engine = self.rig.engine()
        self.remotes = self.rig.paths.ostree_remotes_dir
        self.remotes.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.rig.close()

    def write_remote(self, url):
        (self.remotes / "luma.conf").write_text(
            f'[remote "luma"]\nurl={url}\ngpg-verify=true\n')

    def test_a_plain_url_remote_is_reported_as_it_is(self):
        self.write_remote("https://dl.example.test/os/repo")
        self.assertEqual(repository_url(self.rig.paths, self.rig.settings), "https://dl.example.test/os/repo")

    def test_a_mirror_list_is_followed_not_guessed(self):
        listing = self.rig.root / "etc/luma/update-mirrorlist"
        listing.parent.mkdir(parents=True, exist_ok=True)
        listing.write_text("# where the system comes from\nhttps://os.r2.example.test/repo\n")
        self.write_remote(f"mirrorlist=file://{listing}")
        self.assertEqual(repository_url(self.rig.paths, self.rig.settings), "https://os.r2.example.test/repo")

    def test_a_preview_credential_never_reaches_the_published_status(self):
        listing = self.rig.root / "etc/luma/update-mirrorlist"
        listing.parent.mkdir(parents=True, exist_ok=True)
        listing.write_text("https://dl.example.test/os/preview/sup3rsecretcredential00/repo\n")
        self.write_remote(f"mirrorlist=file://{listing}")
        url = repository_url(self.rig.paths, self.rig.settings)
        self.assertNotIn("sup3rsecretcredential00", url)
        self.assertIn("<credential>", url)

    def test_no_remote_says_nothing_rather_than_inventing_a_host(self):
        self.assertEqual(repository_url(self.rig.paths, self.rig.settings), "")

    def test_the_graph_signature_is_published_after_a_check(self):
        self.engine.check()
        status = self.engine.status()
        self.assertTrue(status.signature_verified)
        self.assertEqual(status.signing_key_id, fakes.KEY_ID[::-1].hex().upper())
        self.assertTrue(status.graph_url.endswith("/stable.json"))
        self.assertEqual(status.last_check_reason, "newest-eligible")
        self.assertEqual(status.last_check_attempt, int(self.rig.now))

    def test_a_signature_that_does_not_verify_clears_the_claim(self):
        self.engine.check()
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.2", 3)]), secret=b"\x07" * 32)
        self.engine.check()
        status = self.engine.status()
        self.assertFalse(status.signature_verified)
        self.assertEqual(status.last_check_reason, "signature")


if __name__ == "__main__":
    unittest.main()


class StartFollowingAChannel(unittest.TestCase):
    """An unmanaged computer is never a dead end: it is told why, and where the
    reason allows it, it can start following a Luma channel from here."""

    def setUp(self):
        self.rig = fakes.Rig()
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        self.rig.backend.list[0].origin = "fedora:fedora/44/x86_64/silverblue"
        self.engine = self.rig.engine()
        self.remotes = self.rig.paths.ostree_remotes_dir
        self.remotes.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.rig.close()

    def with_remote(self):
        (self.remotes / "luma.conf").write_text(
            '[remote "luma"]\nurl=https://dl.example.test/os/repo\ngpg-verify=true\n')

    def test_unmanaged_without_the_luma_remote_says_exactly_what_is_missing(self):
        status = self.engine.refresh()
        self.assertFalse(status.managed)
        self.assertFalse(status.adoptable)
        self.assertEqual(status.unmanaged_reason, "no-remote")

    def test_unmanaged_without_a_signing_key_says_so(self):
        self.with_remote()
        for directory in self.rig.paths.graph_key_dirs:
            for key in directory.glob("*.pub"):
                key.unlink()
        status = self.engine.refresh()
        self.assertFalse(status.adoptable)
        self.assertEqual(status.unmanaged_reason, "no-key")

    def test_with_the_remote_and_a_key_it_can_be_adopted(self):
        self.with_remote()
        status = self.engine.refresh()
        self.assertFalse(status.managed)
        self.assertTrue(status.adoptable)
        self.assertEqual(status.unmanaged_reason, "other-origin")

    def test_adopting_stages_the_channel_newest_release_and_rebases(self):
        self.with_remote()
        self.engine.adopt_channel("stable")
        status = self.engine.status()
        self.assertEqual(status.state, "staged")
        self.assertEqual(status.staged_version, "1.0.1")
        staged = next(d for d in self.rig.backend.list if d.staged)
        self.assertEqual(staged.origin, "luma:luma/1/x86_64/stable")

    def test_adopting_replaces_overrides_and_an_ordinary_update_does_not(self):
        self.with_remote()
        self.engine.adopt_channel("stable")
        self.assertIn("no-overrides", self.rig.backend.calls[-1])
        rig = fakes.Rig()
        self.addCleanup(rig.close)
        rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        engine = rig.engine()
        engine.check()
        engine.download(user_initiated=True)
        self.assertNotIn("no-overrides", rig.backend.calls[-1])

    def test_the_running_version_is_kept_so_it_can_be_undone(self):
        self.with_remote()
        self.engine.adopt_channel("stable")
        booted = next(d for d in self.rig.backend.list if d.booted)
        self.assertEqual(booted.origin, "fedora:fedora/44/x86_64/silverblue")
        self.assertEqual(self.engine.status().state, "staged")

    def test_an_unknown_channel_is_refused(self):
        self.with_remote()
        with self.assertRaises(UpdateError) as caught:
            self.engine.adopt_channel("experimental")
        self.assertEqual(caught.exception.error_class, "invalid-argument")

    def test_a_computer_that_cannot_be_adopted_is_refused_with_the_reason(self):
        with self.assertRaises(Exception) as caught:
            self.engine.adopt_channel("stable")
        self.assertIn("no-remote", str(caught.exception))

    def test_a_managed_computer_has_nothing_to_adopt(self):
        rig = fakes.Rig()
        self.addCleanup(rig.close)
        rig.publish(graph_doc([release("1.0.0", 1)]))
        engine = rig.engine()
        with self.assertRaises(Exception) as caught:
            engine.adopt_channel("stable")
        self.assertIn("already follows", str(caught.exception))

    def test_a_preview_channel_needs_no_enrollment(self):
        # Every channel is public (ADR-030 section 4, 2026-09-16).
        self.with_remote()
        self.rig.publish(graph_doc([release("1.1.0-beta.1", 5)], channel="beta"), channel="beta")
        self.engine.adopt_channel("beta")
        self.assertEqual(self.engine.status().state, "staged")
        staged = next(d for d in self.rig.backend.list if d.staged)
        self.assertEqual(staged.origin, "luma:luma/1/x86_64/beta")
        self.assertEqual(self.rig.http.posts, [])

    def test_a_managed_computer_publishes_no_reason_at_all(self):
        rig = fakes.Rig()
        self.addCleanup(rig.close)
        rig.publish(graph_doc([release("1.0.0", 1)]))
        status = rig.engine().refresh()
        self.assertTrue(status.managed)
        self.assertFalse(status.adoptable)
        self.assertEqual(status.unmanaged_reason, "")
