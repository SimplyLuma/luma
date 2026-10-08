# SPDX-License-Identifier: Apache-2.0
"""Beta and Nightly, as a person reaches them.

Every channel is public (ADR-030 section 4, 2026-09-16): any Luma install
follows beta or nightly by choosing it, with no account and no credential, and
goes back to Official the same way. Hub enrollment stays for compatibility:
its answers become plain errors, and a revoked credential shows up as a
refusal rather than a mystery.
"""

import json
import os
from pathlib import Path
import unittest

import fakes
from fakes import commit, graph_doc, release
from luma_update import preview
from luma_update.engine import FRIENDLY, UpdateError, Unmanaged, classify_error

CREDENTIAL = "Hub_issued-credential_0123456789abcdefghijk"  # 43 characters, like Hub's
URL = "https://hub.simplyluma.com/api/updates/preview-credentials"
PUBLIC = "https://dl.simplyluma.com/os/repo"


class Base(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.addCleanup(self.rig.close)
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        self.rig.publish(graph_doc([release("1.1.0-nightly.1", 7), release("1.1.0-nightly.2", 8)],
                                   channel="nightly"), channel="nightly")
        self.rig.publish(graph_doc([release("1.1.0-beta.1", 5)], channel="beta"), channel="beta")
        remotes = self.rig.paths.ostree_remotes_dir
        remotes.mkdir(parents=True)
        (remotes / "luma.conf").write_text(
            '[remote "luma"]\nurl=mirrorlist=file://%s\ngpg-verify=true\n' % self.rig.paths.update_mirrorlist)
        mirrorlist = self.rig.paths.update_mirrorlist
        mirrorlist.parent.mkdir(parents=True, exist_ok=True)
        mirrorlist.write_text(PUBLIC + "\n")
        mirrorlist.chmod(0o600)

    def hub_answers(self, status, body=None):
        self.rig.http.responses[URL] = (status, body if body is not None else {})

    def record(self):
        return json.loads(self.rig.paths.preview_credential.read_text())

    def mirror(self):
        return self.rig.paths.update_mirrorlist.read_text().strip()


class ThroughHub(Base):
    def test_hub_enrollment_follows_the_channel_and_says_who_set_it_up(self):
        self.hub_answers(201, {"credential": CREDENTIAL, "channels": ["beta", "nightly"], "credential_id": "pc_1"})
        engine = self.rig.engine()
        engine.enroll_preview("nightly", "connect-device-token-0123456789")
        status = engine.status()
        self.assertTrue(status.preview_enrolled)
        self.assertEqual(status.preview_source, "hub")
        self.assertEqual(status.channel, "nightly")
        self.assertIn("nightly", status.available_channels)
        self.assertEqual(self.record()["hub_credential_id"], "pc_1")
        self.assertEqual(self.mirror(), f"https://dl.simplyluma.com/os/preview/{CREDENTIAL}/repo")
        method, url, payload, headers = self.rig.http.posts[-1]
        self.assertEqual((method, url, payload), ("POST", URL, {"channel": "nightly", "arch": "x86_64"}))
        self.assertEqual(headers["Authorization"], "Bearer connect-device-token-0123456789")

    def test_a_signed_out_device_is_asked_to_sign_in_again(self):
        self.hub_answers(401)
        with self.assertRaises(UpdateError) as caught:
            self.rig.engine().enroll_preview("nightly", "connect-device-token-0123456789")
        self.assertEqual(caught.exception.error_class, "sign-in-required")
        self.assertIn("Sign in to Luma Connect again", str(caught.exception))
        self.assertFalse(self.rig.paths.preview_credential.exists())
        self.assertEqual(self.mirror(), PUBLIC)

    def test_an_account_without_access_is_not_entitled(self):
        self.hub_answers(403)
        with self.assertRaises(UpdateError) as caught:
            self.rig.engine().enroll_preview("beta", "connect-device-token-0123456789")
        self.assertEqual(caught.exception.error_class, "not-entitled")

    def test_rate_limits_and_hub_failures_read_as_what_to_do(self):
        for status, words in ((429, "Wait a few minutes"), (503, "Try again later")):
            self.hub_answers(status)
            with self.assertRaises(UpdateError) as caught:
                self.rig.engine().enroll_preview("beta", "connect-device-token-0123456789")
            self.assertIn(words, str(caught.exception))
            self.assertFalse(self.rig.paths.preview_credential.exists())

    def test_leaving_goes_back_to_official_and_revokes_at_hub(self):
        self.hub_answers(201, {"credential": CREDENTIAL, "channels": ["beta", "nightly"], "credential_id": "pc_1"})
        engine = self.rig.engine()
        engine.enroll_preview("nightly", "connect-device-token-0123456789")
        self.rig.http.responses[URL + "/current"] = (204, {})
        with self.assertLogs("luma-update", level="INFO"):
            engine.leave_preview()
        status = engine.status()
        self.assertFalse(status.preview_enrolled)
        self.assertEqual(status.preview_source, "")
        self.assertEqual(self.mirror(), PUBLIC)
        deletes = [p for p in self.rig.http.posts if p[0] == "DELETE"]
        self.assertEqual(deletes[-1][3]["Authorization"], f"Bearer {CREDENTIAL}")


class HubEnrollmentFromAComputerThatFollowsNothing(Base):
    def setUp(self):
        super().setUp()
        self.rig.backend.list[0].origin = "fedora:fedora/44/x86_64/silverblue"

    def test_enrolling_adopts_the_channel_in_one_step(self):
        self.hub_answers(201, {"credential": CREDENTIAL, "channels": ["beta", "nightly"], "credential_id": "pc_2"})
        engine = self.rig.engine()
        engine.enroll_preview("nightly", "connect-device-token-0123456789")
        status = engine.status()
        self.assertEqual(status.state, "staged")
        self.assertEqual(status.staged_version, "1.1.0-nightly.2")
        staged = next(d for d in self.rig.backend.list if d.staged)
        self.assertEqual(staged.origin, "luma:luma/1/x86_64/nightly")

    def test_a_computer_that_cannot_be_adopted_is_refused_before_hub_is_asked(self):
        for item in self.rig.paths.ostree_remotes_dir.glob("*.conf"):
            item.unlink()
        with self.assertRaises(Unmanaged):
            self.rig.engine().enroll_preview("nightly", "connect-device-token-0123456789")
        self.assertEqual(self.rig.http.posts, [])
        self.assertFalse(self.rig.paths.preview_credential.exists())

class PublicChannels(Base):
    def test_every_channel_is_on_offer(self):
        self.assertEqual(self.rig.engine().refresh().available_channels, ["stable", "beta", "nightly"])

    def test_choosing_nightly_needs_nothing_and_stages_its_newest_release(self):
        engine = self.rig.engine()
        engine.set_channel("nightly")
        engine.check()
        engine.download(user_initiated=True)
        status = engine.status()
        self.assertEqual((status.channel, status.state, status.staged_version), ("nightly", "staged", "1.1.0-nightly.2"))
        staged = next(d for d in self.rig.backend.list if d.staged)
        self.assertEqual(staged.origin, "luma:luma/1/x86_64/nightly")
        self.assertFalse(status.preview_enrolled)
        self.assertEqual(self.mirror(), PUBLIC, "the public repository serves every channel")
        self.assertEqual(self.rig.http.posts, [], "no Hub, no credential")

    def test_an_install_that_follows_nightly_without_a_credential_updates(self):
        # A TEST2-era install: it follows the nightly ref and was never given a credential.
        self.rig.backend = fakes.FakeRpmOstree(booted_version="1.1.0-nightly.1", booted_commit=commit(7),
                                               origin="luma:luma/1/x86_64/nightly")
        engine = self.rig.engine()
        engine.check()
        status = engine.status()
        self.assertEqual(status.last_error_class, "")
        self.assertIn("1.1.0-nightly.2", (status.available_version, status.staged_version))

    def test_back_to_official(self):
        engine = self.rig.engine()
        engine.set_channel("nightly")
        engine.set_channel("stable")
        self.assertEqual(engine.status().channel, "stable")
        self.assertEqual(self.mirror(), PUBLIC)
        self.assertEqual(self.rig.http.posts, [])

    def test_older_records_say_unknown(self):
        self.assertEqual(preview.record_source({"credential": CREDENTIAL}), "unknown")
        self.assertEqual(preview.record_source({"credential": CREDENTIAL, "source": "staff-media"}), "staff-media")
        self.assertEqual(preview.record_source(None), "")


class RevokedAccess(unittest.TestCase):
    def test_a_refused_pull_is_named(self):
        for text in ("Server returned HTTP 403", "While pulling: 403 Forbidden", "Server returned HTTP 401"):
            self.assertEqual(classify_error(Exception(text)), "access-denied")
        self.assertIn("leave early updates", FRIENDLY["access-denied"])
        self.assertEqual(classify_error(Exception("Server returned HTTP 404")), "unknown")
        # What libostree actually says when the mirror list's only URL refuses (observed on a VM).
        mirrors = Exception("No valid mirrors were found in mirrorlist 'file:///etc/luma/update-mirrorlist'")
        self.assertEqual(classify_error(mirrors, credential_installed=True), "access-denied")
        self.assertEqual(classify_error(mirrors), "network")


if __name__ == "__main__":
    unittest.main()
