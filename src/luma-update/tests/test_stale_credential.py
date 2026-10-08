# SPDX-License-Identifier: Apache-2.0
"""A stale or missing preview credential never blocks a public channel.

Every channel is public (ADR-030 section 4). A computer enrolled before that
keeps a credential in its mirror list; once Hub revokes it, or the record is
gone while the mirror list still names the preview repository, the pull must
fall back to the public repository instead of failing every update."""

import json
import unittest

import fakes
from fakes import commit, graph_doc, release
from luma_update import preview
from luma_update.engine import classify_error
from luma_update.errors import BusyError, TransactionError

CREDENTIAL = "Hub_issued-credential_0123456789abcdefghijk"
PUBLIC = "https://dl.simplyluma.com/os/repo"
PREVIEW = f"https://dl.simplyluma.com/os/preview/{CREDENTIAL}/repo"
# Exactly what luma-updated received on a real gate VM (20260921T191930Z) when the preview
# repository answered 404 for its config: rpm-ostree's Finished(false, message) carries
# libostree's text alone, and the client raises it as TransactionError ("transaction").
REFUSED = "No valid mirrors were found in mirrorlist 'file:///etc/luma/update-mirrorlist'"
# libostree's fetcher for a plain (non-mirrorlist) remote, same format as the gate's 404 line.
FORBIDDEN = f"While fetching {PREVIEW}/config: Server returned HTTP 403"
UNAUTHORIZED = f"While fetching {PREVIEW}/config: Server returned HTTP 401"
# A missing object is a broken repository, not a refusal: never a reason to switch repositories.
MISSING_OBJECT = ("While fetching http://192.168.1.1:49923/objects/03/356dd1689389a323b3b943e7dc12a3bed8c2aebc"
                  "6530372dc7b39ebe0f3903.commit: Server returned HTTP 404")


class PreviewRefuses(fakes.FakeRpmOstree):
    """rpm-ostree whose pull fails whenever the mirror list names the preview repository
    (and, with ``public_down``, the public one too)."""

    def __init__(self, mirrorlist, public_down=False, refusal=REFUSED):
        super().__init__(booted_version="1.1.0-nightly.1", booted_commit=commit(7),
                         origin="luma:luma/1/x86_64/nightly")
        self.mirrorlist, self.public_down, self.pulled_from = mirrorlist, public_down, []
        self.refusal = refusal

    def update_deployment(self, **kwargs):
        source = self.mirrorlist.read_text().strip()
        self.pulled_from.append(source)
        if "/os/preview/" in source or self.public_down:
            self.calls.append(("update", kwargs["revision"]))
            raise TransactionError(self.refusal)  # the real client's error, class "transaction"
        super().update_deployment(**kwargs)


class StaleCredential(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.addCleanup(self.rig.close)
        self.rig.publish(graph_doc([release("1.1.0-nightly.1", 7), release("1.1.0-nightly.2", 8)],
                                   channel="nightly"), channel="nightly")
        remotes = self.rig.paths.ostree_remotes_dir
        remotes.mkdir(parents=True)
        (remotes / "luma.conf").write_text(
            '[remote "luma"]\nurl=mirrorlist=file://%s\ngpg-verify=true\n' % self.rig.paths.update_mirrorlist)
        self.mirror = self.rig.paths.update_mirrorlist
        self.mirror.parent.mkdir(parents=True, exist_ok=True)
        self.mirror.write_text(PREVIEW + "\n")
        self.mirror.chmod(0o600)

    def enrolled(self):
        self.rig.paths.preview_credential.write_text(json.dumps(
            {"credential": CREDENTIAL, "channel": "nightly", "channels": ["nightly"], "source": "hub"}))
        self.rig.paths.preview_credential.chmod(0o600)

    def run_update(self, **kwargs):
        self.rig.backend = PreviewRefuses(self.mirror, **kwargs)
        engine = self.rig.engine()
        engine.check()
        engine.download(user_initiated=True)
        return engine.status()

    def test_a_revoked_credential_falls_back_to_the_public_repository(self):
        self.enrolled()
        status = self.run_update()
        self.assertEqual((status.state, status.staged_commit, status.last_error), ("staged", commit(8), ""))
        self.assertEqual(self.rig.backend.pulled_from, [PREVIEW, PUBLIC])
        self.assertEqual(self.mirror.read_text().strip(), PUBLIC)
        self.assertIsNone(preview.read_credential(self.rig.paths))
        self.assertFalse(status.preview_enrolled)
        self.assertEqual(self.rig.http.posts, [], "nothing is revoked at Hub")

    def test_an_http_403_or_401_from_the_preview_repository_falls_back(self):
        for refusal in (FORBIDDEN, UNAUTHORIZED):
            with self.subTest(refusal=refusal):
                self.mirror.write_text(PREVIEW + "\n")
                self.enrolled()
                status = self.run_update(refusal=refusal)
                self.assertEqual((status.state, status.staged_commit), ("staged", commit(8)))
                self.assertEqual(self.rig.backend.pulled_from, [PREVIEW, PUBLIC])

    def test_other_pull_failures_do_not_switch_repositories(self):
        self.enrolled()
        status = self.run_update(refusal=MISSING_OBJECT)
        self.assertEqual(status.staged_commit, "")
        self.assertEqual(status.last_error_class, "transaction")
        self.assertEqual(self.rig.backend.pulled_from, [PREVIEW], "a missing object is not a refusal")
        self.assertEqual(preview.read_credential(self.rig.paths)["credential"], CREDENTIAL)

    def test_when_the_public_repository_fails_too_the_credential_is_put_back(self):
        self.enrolled()
        status = self.run_update(public_down=True)
        self.assertEqual(status.staged_commit, "")
        self.assertEqual(self.rig.backend.pulled_from, [PREVIEW, PUBLIC])
        self.assertEqual(self.mirror.read_text().strip(), PREVIEW)
        self.assertEqual(self.mirror.stat().st_mode & 0o777, 0o600)
        self.assertEqual(preview.read_credential(self.rig.paths)["credential"], CREDENTIAL)

    def test_a_mirror_list_without_a_credential_record_uses_the_public_repository(self):
        status = self.run_update()
        self.assertEqual((status.state, status.staged_commit), ("staged", commit(8)))
        self.assertEqual(self.rig.backend.pulled_from, [PUBLIC], "reset before the pull, not after a failure")
        self.assertEqual(self.mirror.read_text().strip(), PUBLIC)

    def test_a_public_mirror_list_is_left_alone(self):
        self.mirror.write_text(PUBLIC + "\n")
        status = self.run_update(public_down=True)
        self.assertEqual(status.staged_commit, "")
        self.assertEqual(self.rig.backend.pulled_from, [PUBLIC], "no second attempt when nothing is preview")


class WrappedErrors(unittest.TestCase):
    """classify_error on the exact objects the rpm-ostree client raises."""

    def test_refusals_inside_a_transaction_error(self):
        for text in (REFUSED, FORBIDDEN, UNAUTHORIZED):
            with self.subTest(text=text):
                self.assertEqual(classify_error(TransactionError(text), credential_installed=True), "access-denied")
        self.assertEqual(classify_error(TransactionError(REFUSED), credential_installed=False), "network")

    def test_other_transaction_failures_stay_transaction(self):
        for text in (MISSING_OBJECT, "rpm-ostree transaction failed",
                     "cannot install both viola-1-2.x86_64 from @System and viola-1-1.x86_64 from @commandline"):
            with self.subTest(text=text):
                self.assertEqual(classify_error(TransactionError(text), credential_installed=True), "transaction")

    def test_a_more_specific_class_is_kept(self):
        self.assertEqual(classify_error(BusyError(REFUSED), credential_installed=True), "busy")


if __name__ == "__main__":
    unittest.main()
