# SPDX-License-Identifier: Apache-2.0
"""Leaving early updates revokes only a credential Hub issued to this one device.

Staff install media (Atlas) carry one preview credential per batch and write
luma-update's enrollment record with "source": "staff-media". Revoking that
credential at Hub from one computer would cut off every computer in the batch;
staff issue and revoke batch credentials on the download server. A record
without a per-device Hub credential id is treated the same way. Leaving never
waits on or fails because of Hub.
"""

import json
import stat
import unittest

import fakes
from fakes import commit, graph_doc, release
from luma_update import preview

CREDENTIAL = "Zq9_staff-Batch-0123456789abcdef"
PUBLIC = "https://dl.simplyluma.com/os/repo"
PREVIEW = f"https://dl.simplyluma.com/os/preview/{CREDENTIAL}/repo"


class ExplodingHttp(fakes.FakeHttp):
    """A Hub client that fails in a way HttpError does not cover."""

    def request_json(self, method, url, payload, headers=None, max_bytes=65536):
        self.posts.append((method, url, payload, dict(headers or {})))
        raise RuntimeError("socket closed mid-response")


class Leaving(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        self.rig.publish(graph_doc([release("1.1.0-beta.1", 5)], channel="beta"), channel="beta")
        self.rig.backend = fakes.FakeRpmOstree(booted_version="1.1.0-beta.1", booted_commit=commit(5),
                                               origin="luma:luma/1/x86_64/beta")
        remotes = self.rig.paths.ostree_remotes_dir
        remotes.mkdir(parents=True)
        # The image's mirror-list layout, as Atlas leaves it on a staff medium's install.
        self.image = ('[remote "luma"]\nurl=mirrorlist=file://%s\ngpg-verify=true\n'
                      'collection-id=org.projectluma.OS\n' % self.rig.paths.update_mirrorlist)
        self.conf = remotes / "luma.conf"
        self.conf.write_text(self.image)
        mirrorlist = self.rig.paths.update_mirrorlist
        mirrorlist.parent.mkdir(parents=True, exist_ok=True)
        mirrorlist.write_text(PREVIEW + "\n")
        mirrorlist.chmod(0o600)
        # What the DELETE saw on disk when it was sent.
        self.at_delete = []
        http = self.rig.http
        original = http.request_json

        def recording(method, url, payload, headers=None, max_bytes=65536):
            if method == "DELETE":
                self.at_delete.append((self.rig.paths.preview_credential.exists(),
                                       mirrorlist.read_text()))
            return original(method, url, payload, headers, max_bytes)

        http.request_json = recording

    def tearDown(self):
        self.rig.close()

    def write_record(self, record):
        path = self.rig.paths.preview_credential
        if isinstance(record, bytes):
            path.write_bytes(record)
        else:
            path.write_text(record if isinstance(record, str) else json.dumps(record) + "\n")
        path.chmod(0o600)

    def deletes(self):
        return [(url, headers) for (method, url, _p, headers) in self.rig.http.posts if method == "DELETE"]

    def leave(self):
        engine = self.rig.engine()
        with self.assertLogs("luma-update", level="INFO") as logs:
            engine.leave_preview()
        text = "\n".join(logs.output)
        self.assertNotIn(CREDENTIAL, text)
        return engine, text

    def assert_left(self, engine):
        self.assertFalse(self.rig.paths.preview_credential.exists())
        self.assertFalse(self.rig.paths.preview_mirrorlist.exists())
        self.assertEqual(self.rig.paths.update_mirrorlist.read_text(), PUBLIC + "\n")
        self.assertEqual(stat.S_IMODE(self.rig.paths.update_mirrorlist.stat().st_mode), 0o600)
        self.assertEqual(self.conf.read_text(), self.image)
        status = engine.status()
        self.assertEqual((status.channel, status.preview_enrolled), ("stable", False))

    # ── Records that are never revoked ───────────────────────────────────

    def test_staff_media_record_is_removed_locally_and_never_revoked(self):
        # Exactly what Atlas's kickstart writes (f1f82756).
        self.write_record({"credential": CREDENTIAL, "channel": "beta", "channels": ["beta"],
                           "issued_at": 1789000000, "source": "staff-media"})
        engine, text = self.leave()
        self.assertEqual(self.deletes(), [])
        self.assert_left(engine)
        self.assertIn("shared by its batch", text)

    def test_staff_media_record_with_an_id_is_still_never_revoked(self):
        self.write_record({"credential": CREDENTIAL, "channel": "beta", "channels": ["beta"],
                           "source": "staff-media", "hub_credential_id": "batch-7"})
        engine, _text = self.leave()
        self.assertEqual(self.deletes(), [])
        self.assert_left(engine)

    def test_records_without_a_per_device_hub_id_are_never_revoked(self):
        for record in (
            {"credential": CREDENTIAL, "channel": "beta", "channels": ["beta"]},  # 1.luma.4 and earlier
            {"credential": CREDENTIAL, "channel": "beta", "source": "hub"},
            {"credential": CREDENTIAL, "channel": "beta", "source": "hub", "hub_credential_id": ""},
            {"credential": CREDENTIAL, "channel": "beta", "source": "hub", "hub_credential_id": 7},
            {"credential": CREDENTIAL, "channel": "beta", "source": "hub", "hub_credential_id": "a/b"},
            {"credential": CREDENTIAL, "channel": "beta", "source": "someone-else", "hub_credential_id": "d-1"},
        ):
            with self.subTest(record=record):
                self.rig.http.posts.clear()
                self.rig.paths.update_mirrorlist.write_text(PREVIEW + "\n")
                self.write_record(record)
                engine, text = self.leave()
                self.assertEqual(self.deletes(), [])
                self.assert_left(engine)
                self.assertIn("no per-device Luma Hub id", text)

    def test_missing_record_leaves_without_calling_hub(self):
        engine = self.rig.engine()
        engine.leave_preview()
        self.assertEqual(self.deletes(), [])
        self.assert_left(engine)

    def test_corrupt_record_is_removed_and_never_revoked(self):
        for corrupt in ('{"credential": "' + CREDENTIAL, b"\xff\xfe not UTF-8", b"", "[]",
                        json.dumps({"credential": "short", "source": "hub", "hub_credential_id": "d-1"}),
                        json.dumps({"source": "hub", "hub_credential_id": "d-1"})):
            with self.subTest(corrupt=corrupt):
                self.rig.http.posts.clear()
                self.rig.paths.update_mirrorlist.write_text(PREVIEW + "\n")
                self.write_record(corrupt)
                engine = self.rig.engine()
                engine.leave_preview()
                self.assertEqual(self.deletes(), [])
                self.assert_left(engine)

    # ── Per-device Hub credentials ───────────────────────────────────────

    def device_record(self):
        self.write_record({"credential": CREDENTIAL, "channel": "beta", "channels": ["beta"],
                           "issued_at": 1789000000, "source": "hub", "hub_credential_id": "dev_01HZX9"})

    def test_per_device_credential_is_revoked_after_leaving_locally(self):
        self.device_record()
        engine, text = self.leave()
        url = self.rig.settings.preview_credentials_url + "/current"
        self.assertEqual(self.deletes(), [(url, {"Authorization": f"Bearer {CREDENTIAL}"})])
        # By the time Hub is asked, the record is gone and the mirror list is public.
        self.assertEqual(self.at_delete, [(False, PUBLIC + "\n")])
        self.assert_left(engine)
        self.assertIn("Luma Hub revoked", text)

    def test_hub_unreachable_never_blocks_leaving(self):
        self.device_record()
        self.rig.http.offline = True
        engine, text = self.leave()
        self.assert_left(engine)
        self.assertIn("could not be reached", text)

    def test_hub_not_built_or_refusing_never_blocks_leaving(self):
        url = self.rig.settings.preview_credentials_url + "/current"
        for status, expected in ((404, "nothing to revoke"), (410, "nothing to revoke"),
                                 (500, "did not revoke"), (401, "did not revoke")):
            with self.subTest(status=status):
                self.rig.http.posts.clear()
                self.rig.paths.update_mirrorlist.write_text(PREVIEW + "\n")
                self.device_record()
                self.rig.http.responses[url] = (status, {})
                engine, text = self.leave()
                self.assertEqual(len(self.deletes()), 1)
                self.assert_left(engine)
                self.assertIn(expected, text)

    def test_unexpected_client_failure_never_blocks_leaving(self):
        self.device_record()
        self.rig.http = ExplodingHttp()
        engine, text = self.leave()
        self.assert_left(engine)
        self.assertEqual(len(self.deletes()), 1)
        self.assertIn("RuntimeError", text)
        self.assertNotIn("socket closed", text)

    def test_revocation_still_runs_when_a_later_step_fails(self):
        self.device_record()
        engine = self.rig.engine()
        engine.refresh = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("status write failed"))
        with self.assertRaises(RuntimeError):
            engine.leave_preview()
        self.assertEqual(len(self.deletes()), 1)
        self.assertFalse(self.rig.paths.preview_credential.exists())


class Enrollment(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.rig.publish(graph_doc([release("1.0.0", 1)]))
        remotes = self.rig.paths.ostree_remotes_dir
        remotes.mkdir(parents=True)
        (remotes / "luma.conf").write_text('[remote "luma"]\nurl=mirrorlist=file://%s\n'
                                           % self.rig.paths.update_mirrorlist)
        self.rig.paths.update_mirrorlist.parent.mkdir(parents=True, exist_ok=True)
        self.rig.paths.update_mirrorlist.write_text(PUBLIC + "\n")

    def tearDown(self):
        self.rig.close()

    def record(self):
        return json.loads(self.rig.paths.preview_credential.read_text())

    def test_enrollment_records_hub_source_and_device_id(self):
        self.rig.http.responses[self.rig.settings.preview_credentials_url] = (
            201, {"credential": CREDENTIAL, "channels": ["beta"], "credential_id": "dev_01HZX9"})
        self.rig.engine().enroll_preview("beta", "connect-device-token-123")
        record = self.record()
        self.assertEqual((record["source"], record["hub_credential_id"]), ("hub", "dev_01HZX9"))
        self.assertTrue(preview.revocable(record))

    def test_enrollment_without_an_id_is_not_revocable_and_says_so(self):
        self.rig.http.responses[self.rig.settings.preview_credentials_url] = (
            201, {"credential": CREDENTIAL, "channels": ["beta"], "credential_id": "not an id!"})
        with self.assertLogs("luma-update", level="WARNING") as logs:
            self.rig.engine().enroll_preview("beta", "connect-device-token-123")
        self.assertIn("will not revoke", "\n".join(logs.output))
        record = self.record()
        self.assertEqual(record["source"], "hub")
        self.assertNotIn("hub_credential_id", record)
        self.assertFalse(preview.revocable(record))


class Revocable(unittest.TestCase):
    def test_only_hub_records_with_a_device_id(self):
        base = {"credential": CREDENTIAL}
        self.assertTrue(preview.revocable({**base, "source": "hub", "hub_credential_id": "d-1_X"}))
        for record in (None, [], "hub", {}, base, {**base, "source": "staff-media"},
                       {**base, "source": "staff-media", "hub_credential_id": "d-1"},
                       {**base, "source": "hub"}, {**base, "source": "HUB", "hub_credential_id": "d-1"},
                       {**base, "source": "hub", "hub_credential_id": "x" * 129},
                       {"credential": "short", "source": "hub", "hub_credential_id": "d-1"}):
            with self.subTest(record=record):
                self.assertFalse(preview.revocable(record))

    def test_skipped_revocation_sends_nothing(self):
        http = fakes.FakeHttp()
        self.assertEqual(preview.revoke_credential(http, fakes.Settings(), None), "skipped")
        self.assertEqual(preview.revoke_credential(
            http, fakes.Settings(), {"credential": CREDENTIAL, "source": "staff-media"}), "skipped")
        self.assertEqual(http.posts, [])


if __name__ == "__main__":
    unittest.main()
