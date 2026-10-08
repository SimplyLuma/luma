"""A name server that blinks must not end an install the person approved."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from luma_installer import network
from luma_installer.errors import InstallerError


class GioError(Exception):
    """Shaped like a GLib.Error: a domain, a code and a message attribute."""

    def __init__(self, message, domain="g-resolver-error-quark"):
        super().__init__(message)
        self.message = message
        self.domain = domain


RESOLVER = GioError("Error resolving 'dl.flathub.org': Name or service not known")


class Classification(unittest.TestCase):
    def test_the_reported_failure_is_treated_as_transient(self):
        self.assertTrue(network.is_transient(RESOLVER))
        self.assertTrue(network.is_transient(
            InstallerError("Could not resolve host: registry.fedoraproject.org")))
        self.assertTrue(network.is_transient(GioError("Connection timed out", "g-io-error-quark")))

    def test_a_real_package_failure_is_not_retried_as_a_network_blip(self):
        for message in ("dpkg: error processing archive",
                        "No space left on device",
                        "Signature verification failed"):
            with self.subTest(message=message):
                self.assertFalse(network.is_transient(InstallerError(message)))

    def test_cancellation_is_not_a_transient_fault(self):
        self.assertFalse(network.is_transient(GioError("Operation was cancelled", "g-io-error-quark")))

    def test_the_message_shown_is_ours_and_actionable(self):
        sentence = network.friendly(RESOLVER, "this application's source")
        self.assertNotIn("g-resolver", sentence)
        self.assertNotIn("Name or service not known", sentence)
        self.assertIn("network connection", sentence)
        self.assertTrue(sentence.endswith("try again."))

    def test_an_unrelated_failure_keeps_its_own_report(self):
        self.assertEqual(network.friendly(InstallerError("dpkg: error processing archive")), "")


class Retrying(unittest.TestCase):
    def test_a_transient_fetch_is_retried_before_anything_is_said(self):
        attempts = []
        slept = []

        def work():
            attempts.append(1)
            if len(attempts) < 3:
                raise RESOLVER
            return "installed"

        self.assertEqual(network.retrying(work, sleep=slept.append), "installed")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(slept, [1.0, 3.0])

    def test_backoff_grows_and_the_attempts_are_bounded(self):
        slept = []
        with self.assertRaises(GioError):
            network.retrying(self._always_failing(), sleep=slept.append)
        self.assertEqual(slept, [1.0, 3.0])

    def test_a_real_failure_is_surfaced_at_once(self):
        attempts = []

        def work():
            attempts.append(1)
            raise InstallerError("No space left on device")

        with self.assertRaisesRegex(InstallerError, "No space left"):
            network.retrying(work, sleep=lambda _seconds: None)
        self.assertEqual(len(attempts), 1)

    def test_a_caller_can_refuse_a_retry_it_knows_is_unsafe(self):
        attempts = []

        def work():
            attempts.append(1)
            raise RESOLVER

        with self.assertRaises(GioError):
            network.retrying(work, sleep=lambda _seconds: None, transient=lambda _error: False)
        self.assertEqual(len(attempts), 1)

    def _always_failing(self):
        def work():
            raise RESOLVER
        return work


class BackendFetches(unittest.TestCase):
    def test_a_capsule_fetch_survives_one_blink(self):
        from luma_installer import backends
        calls = []

        def run(arguments, timeout=0):
            calls.append(arguments)
            if len(calls) == 1:
                raise InstallerError(
                    "Temporary failure resolving 'deb.debian.org'")
            return SimpleNamespace(stdout="ok")

        with patch("luma_installer.backends._run", side_effect=run), \
             patch("luma_installer.network.time.sleep") as sleep:
            result = backends._fetch(["podman", "exec", "capsule", "apt-get", "update"])
        self.assertEqual(result.stdout, "ok")
        self.assertEqual(len(calls), 2)
        sleep.assert_called_once_with(1.0)

    def test_a_persistent_outage_is_explained_not_quoted(self):
        from luma_installer import backends

        def run(arguments, timeout=0):
            raise InstallerError("E: Could not resolve host 'deb.debian.org'")

        with patch("luma_installer.backends._run", side_effect=run), \
             patch("luma_installer.network.time.sleep"):
            with self.assertRaises(InstallerError) as caught:
                backends._fetch(["podman", "exec", "capsule", "apt-get", "update"],
                                source="the Debian package index")
        message = str(caught.exception)
        self.assertNotIn("deb.debian.org", message)
        self.assertIn("the Debian package index", message)
        self.assertIn("network connection", message)

    def test_a_package_failure_keeps_its_own_words(self):
        from luma_installer import backends

        def run(arguments, timeout=0):
            raise InstallerError("dpkg: dependency problems prevent configuration")

        with patch("luma_installer.backends._run", side_effect=run), \
             patch("luma_installer.network.time.sleep") as sleep:
            with self.assertRaisesRegex(InstallerError, "dependency problems"):
                backends._fetch(["podman", "exec", "capsule", "apt-get", "update"])
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
