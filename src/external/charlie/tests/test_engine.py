# SPDX-License-Identifier: Apache-2.0
import unittest
import imaplib
import smtplib
from unittest.mock import Mock, patch

from charlie_luma.engine import (
    AuthenticationFailure,
    ImapSmtpTransport,
    NetworkFailureKind,
    network_failure_kind,
)
from charlie_luma.fixtures import DEMO_ACCOUNT
from charlie_luma.model import ServerConfig


class _ListClient:
    def list(self):
        return "OK", [b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"']


class EngineTests(unittest.TestCase):
    def test_special_use_sent_mailbox_is_discovered(self):
        self.assertEqual(
            ImapSmtpTransport._sent_mailbox(_ListClient(), DEMO_ACCOUNT),
            "[Gmail]/Sent Mail",
        )
        self.assertEqual(
            ImapSmtpTransport._mailbox_argument("[Gmail]/Sent Mail"),
            '"[Gmail]/Sent Mail"',
        )

    def test_imap_abort_is_connection_failure_not_authentication(self):
        self.assertTrue(issubclass(imaplib.IMAP4.abort, imaplib.IMAP4.error))
        self.assertEqual(
            network_failure_kind(imaplib.IMAP4.abort("socket closed")),
            NetworkFailureKind.CONNECTION,
        )

    def test_only_explicit_credential_failures_are_authentication(self):
        for error in (
            AuthenticationFailure("rejected"),
            PermissionError("missing"),
            smtplib.SMTPAuthenticationError(535, b"rejected"),
        ):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(
                    network_failure_kind(error), NetworkFailureKind.AUTHENTICATION
                )
        self.assertEqual(
            network_failure_kind(imaplib.IMAP4.error("unexpected response")),
            NetworkFailureKind.SERVER,
        )

    def test_imap_login_wraps_rejection_but_not_connection_abort(self):
        transport = ImapSmtpTransport(lambda _account, _kind: "app-password")
        config = ServerConfig("imap.example.test")
        with patch("charlie_luma.engine.imaplib.IMAP4_SSL") as client_type:
            client_type.return_value.login.side_effect = imaplib.IMAP4.error("rejected")
            with self.assertRaises(AuthenticationFailure):
                transport._imap(DEMO_ACCOUNT, config)

            client_type.return_value.login.side_effect = imaplib.IMAP4.abort("socket closed")
            with self.assertRaises(imaplib.IMAP4.abort):
                transport._imap(DEMO_ACCOUNT, config)

    def test_temporary_login_no_is_not_reported_as_bad_credentials(self):
        transport = ImapSmtpTransport(lambda _account, _kind: "app-password")
        config = ServerConfig("imap.example.test")
        with patch("charlie_luma.engine.imaplib.IMAP4_SSL") as client_type:
            client_type.return_value.login.side_effect = imaplib.IMAP4.error(
                "[UNAVAILABLE] Temporary System Error"
            )
            with self.assertRaises(imaplib.IMAP4.error) as caught:
                transport._imap(DEMO_ACCOUNT, config)
        self.assertNotIsInstance(caught.exception, AuthenticationFailure)

    def test_gmail_retries_once_with_a_forced_token_refresh(self):
        account = DEMO_ACCOUNT.__class__(
            DEMO_ACCOUNT.id, DEMO_ACCOUNT.display_name, DEMO_ACCOUNT.address,
            provider="gmail",
        )
        lookups: list[bool] = []

        def token(_account, force_refresh=False):
            lookups.append(force_refresh)
            return "fresh" if force_refresh else "stale"

        transport = ImapSmtpTransport(lambda _account, _kind: None, token)
        config = ServerConfig("imap.example.test")
        with patch("charlie_luma.engine.imaplib.IMAP4_SSL") as client_type:
            stale, fresh = client_type.side_effect = (Mock(), Mock())
            stale.authenticate.side_effect = imaplib.IMAP4.error(
                "[AUTHENTICATIONFAILED] Invalid credentials"
            )
            result = transport._imap(account, config)
        self.assertIs(result, fresh)
        self.assertEqual(lookups, [False, True])


if __name__ == "__main__":
    unittest.main()
