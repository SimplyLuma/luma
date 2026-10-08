# SPDX-License-Identifier: Apache-2.0
"""The preview credential never reaches the journal or a D-Bus error.

rpm-ostree's messages and errors name the repository URL, and a preview
repository URL contains the device's credential. The agent logged those
messages verbatim ("rpm-ostree: ...") and returned raw error text to D-Bus
callers.
"""

import logging
import unittest

import fakes
from fakes import commit, graph_doc, release
from luma_update import redact
from luma_update.errors import TransactionError

CREDENTIAL = "Zq9_preview-Credential-0123456789abcdef"
URL = f"https://dl.simplyluma.com/os/preview/{CREDENTIAL}/repo"


class Redact(unittest.TestCase):
    def test_preview_paths_and_known_credentials(self):
        self.assertEqual(redact.redact(f"Fetching {URL}/objects/ab/cd.filez: 403", secrets=()),
                         "Fetching https://dl.simplyluma.com/os/preview/<credential>/repo/objects/ab/cd.filez: 403")
        self.assertEqual(redact.redact(f"while fetching 'https://mirror.example/{CREDENTIAL}/x'", secrets=(CREDENTIAL,)),
                         "while fetching 'https://mirror.example/<credential>/x'")
        self.assertEqual(redact.redact("https://dl.simplyluma.com/os/repo/summary", secrets=()),
                         "https://dl.simplyluma.com/os/repo/summary")


class ChattyRpmOstree(fakes.FakeRpmOstree):
    def update_deployment(self, *, message=None, **kwargs):
        if message:
            message(f"Receiving objects: 12% from {URL}")
            message(f"Fetching mirrorlist entry https://cdn.example/{CREDENTIAL}/repo")
        raise TransactionError(f"Server returned HTTP 403 while fetching {URL}/summary.sig")


class EngineLogs(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.rig.backend = ChattyRpmOstree()
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        credential = self.rig.paths.preview_credential
        credential.parent.mkdir(parents=True, exist_ok=True)
        credential.write_text('{"credential": "%s", "channel": "beta", "channels": ["beta"]}' % CREDENTIAL)
        credential.chmod(0o600)

    def tearDown(self):
        self.rig.close()

    def test_rpm_ostree_messages_and_errors_are_redacted_everywhere(self):
        engine = self.rig.engine()
        engine.check()
        with self.assertLogs("luma-update", level="INFO") as captured:
            self.assertFalse(engine.download(user_initiated=True))
        text = "\n".join(captured.output)
        self.assertIn("rpm-ostree: Receiving objects: 12% from https://dl.simplyluma.com/os/preview/<credential>/repo",
                      text)
        self.assertIn("<credential>", text)
        self.assertNotIn(CREDENTIAL, text)
        status = engine.status()
        self.assertNotIn(CREDENTIAL, status.last_error)
        self.assertNotIn(CREDENTIAL, self.rig.paths.status_file.read_text())
        self.assertNotIn(CREDENTIAL, self.rig.paths.state_file.read_text())
        # A 403 inside rpm-ostree's wrapped error, with a credential installed, is a refusal.
        self.assertEqual(status.last_error_class, "access-denied")

    def test_the_filter_formats_arguments_before_redacting(self):
        self.rig.engine()
        logger = logging.getLogger("luma-update")
        with self.assertLogs("luma-update", level="WARNING") as captured:
            logger.warning("%s failed: %r", "pull", TransactionError(URL))
        self.assertEqual(captured.records[0].getMessage(),
                         "pull failed: TransactionError('https://dl.simplyluma.com/os/preview/<credential>/repo')")


try:
    import gi
    gi.require_version("GLib", "2.0")
    from luma_update import daemon as daemonmod
except (ImportError, ValueError):  # no GObject introspection here
    daemonmod = None


@unittest.skipIf(daemonmod is None, "PyGObject is not available")
class DbusErrors(unittest.TestCase):
    def test_error_text_returned_to_callers_is_redacted(self):
        import threading
        from unittest import mock

        class Invocation:
            errors = []

            def return_dbus_error(self, name, message):
                self.errors.append((name, message))

            def return_value(self, value):
                pass

        rig = fakes.Rig()
        try:
            daemon = daemonmod.Daemon.__new__(daemonmod.Daemon)
            daemon.engine = rig.engine()
            daemon.workers = 0
            daemon.workers_lock = threading.Lock()
            daemon.last_activity = 0.0
            invocation = Invocation()
            done = threading.Event()

            def work():
                raise TransactionError(f"error: While pulling {URL}: 403")

            def immediately(callback, *args):
                callback(*args)
                done.set()
            with mock.patch.object(daemonmod.GLib, "idle_add", side_effect=immediately):
                daemon._start(work, invocation, True)
                self.assertTrue(done.wait(5))
            self.assertEqual(invocation.errors, [("org.projectluma.Update1.Error.Failed",
                                                  "error: While pulling https://dl.simplyluma.com/os/preview/"
                                                  "<credential>/repo: 403")])
        finally:
            rig.close()


if __name__ == "__main__":
    unittest.main()
