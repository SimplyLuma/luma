# SPDX-License-Identifier: Apache-2.0
"""Asynchronous mail protocol boundary.

Network work happens in bounded worker threads. The GTK layer receives results
through callbacks and never owns a socket or waits on one.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from enum import Enum
from base64 import b64encode
import imaplib
import re
import ssl
import sys
import threading
from typing import Callable, Protocol

# MIME parsing and SMTP are imported where they are used: the background mail
# agent imports this module and should not carry them until mail arrives.
from .model import Account, Draft, Message, ServerConfig
from .store import MailStore


class CredentialLookup(Protocol):
    def __call__(self, account_id: str, kind: str) -> str | None: ...


class OAuthAccessTokenLookup(Protocol):
    def __call__(self, account: Account, force_refresh: bool = False) -> str | None: ...


class SyncCancelled(Exception):
    pass


class AuthenticationFailure(Exception):
    """A credential rejection, distinct from an IMAP transport abort."""


class NetworkFailureKind(Enum):
    AUTHENTICATION = "authentication"
    CONNECTION = "connection"
    SERVER = "server"


def network_failure_kind(error: Exception) -> NetworkFailureKind:
    """Classify failures without treating IMAP socket aborts as bad credentials.

    ``imaplib.IMAP4.abort`` inherits from ``imaplib.IMAP4.error`` even though it
    represents a broken connection. Authentication is therefore wrapped at the
    login boundary instead of inferred from that overly broad base class.
    """
    smtp = sys.modules.get("smtplib")  # an SMTP error can only exist once smtplib is loaded
    smtp_authentication = (smtp.SMTPAuthenticationError,) if smtp is not None else ()
    if isinstance(error, (AuthenticationFailure, PermissionError, *smtp_authentication)):
        return NetworkFailureKind.AUTHENTICATION
    if isinstance(error, (imaplib.IMAP4.abort, TimeoutError, OSError)):
        return NetworkFailureKind.CONNECTION
    return NetworkFailureKind.SERVER


class ImapSmtpTransport:
    def __init__(
        self,
        credentials: CredentialLookup,
        oauth_access_token: OAuthAccessTokenLookup | None = None,
        ssl_context: Callable[[], ssl.SSLContext] = ssl.create_default_context,
    ) -> None:
        self.credentials = credentials
        self.oauth_access_token = oauth_access_token
        # Always a verifying context; tests pass one that trusts a local CA.
        self.ssl_context = ssl_context

    def _oauth_token(self, account: Account, force_refresh: bool = False) -> str | None:
        if account.provider not in {"gmail", "microsoft"} or self.oauth_access_token is None:
            return None
        return self.oauth_access_token(account, force_refresh)

    @staticmethod
    def _authentication_rejected(error: imaplib.IMAP4.error) -> bool:
        """Recognize explicit credential refusal without promoting every NO.

        IMAP servers also use NO for rate limiting, maintenance and temporary
        account/backend trouble. Those are retryable server failures.
        """

        detail = str(error).casefold()
        return any(marker in detail for marker in (
            "authenticationfailed",
            "authentication failed",
            "authorizationfailed",
            "invalid credentials",
            "invalid login",
            "login failed",
            "password not accepted",
            "application-specific password",
            "credentials rejected",
            "rejected",
        ))

    def _imap(self, account: Account, config: ServerConfig) -> imaplib.IMAP4_SSL:
        oauth_token = self._oauth_token(account)
        password = None if oauth_token else self.credentials(account.id, "password")
        if not oauth_token and not password:
            raise AuthenticationFailure("Account credentials are unavailable")
        for attempt in range(2):
            client = imaplib.IMAP4_SSL(
                config.imap_host,
                config.imap_port,
                ssl_context=self.ssl_context(),
                timeout=30,
            )
            try:
                if oauth_token:
                    auth = f"user={config.username or account.address}\x01auth=Bearer {oauth_token}\x01\x01"
                    client.authenticate("XOAUTH2", lambda _challenge: auth.encode("utf-8"))
                else:
                    client.login(config.username or account.address, password)
                return client
            except imaplib.IMAP4.abort:
                self._discard(client)
                raise
            except imaplib.IMAP4.error as error:
                self._discard(client)
                if not self._authentication_rejected(error):
                    raise
                if oauth_token and attempt == 0:
                    oauth_token = self._oauth_token(account, force_refresh=True)
                    if oauth_token:
                        continue
                raise AuthenticationFailure(
                    "The mail server rejected the account credentials"
                ) from error
            except BaseException:
                self._discard(client)
                raise
        raise AuthenticationFailure("The mail server rejected the account credentials")

    @staticmethod
    def _discard(client: imaplib.IMAP4) -> None:
        """Close a connection that failed to authenticate instead of leaking its socket."""
        try:
            client.shutdown()
        except (OSError, ValueError, AttributeError):
            pass

    @staticmethod
    def _mailbox_argument(name: str) -> str:
        return name if name.upper() == "INBOX" else f'"{name.replace(chr(34), chr(92) + chr(34))}"'

    @staticmethod
    def _sent_mailbox(client: imaplib.IMAP4_SSL, account: Account) -> str:
        try:
            status, rows = client.list()
        except imaplib.IMAP4.error:
            status, rows = "NO", []
        if status == "OK":
            for raw in rows or ():
                value = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                if "\\Sent" not in value:
                    continue
                match = re.search(r'(?:"((?:[^"\\]|\\.)*)"|([^ ]+))$', value)
                if match:
                    return (match.group(1) or match.group(2)).replace('\\"', '"')
        return "[Gmail]/Sent Mail" if account.provider == "gmail" else "Sent"

    def _fetch_mailbox(
        self,
        client: imaplib.IMAP4_SSL,
        account: Account,
        mailbox: str,
        folder: str,
        *,
        cancel: threading.Event,
        limit: int,
    ) -> tuple[Message, ...]:
        from .mime import parse_message

        status, _ = client.select(self._mailbox_argument(mailbox), readonly=True)
        if status != "OK":
            return ()
        status, values = client.uid("search", None, "ALL")
        if status != "OK":
            return ()
        uids = values[0].split()[-limit:]
        messages: list[Message] = []
        for raw_uid in reversed(uids):
            if cancel.is_set():
                raise SyncCancelled()
            status, rows = client.uid("fetch", raw_uid, "(FLAGS BODY.PEEK[])")
            if status != "OK" or not rows or not isinstance(rows[0], tuple):
                continue
            flags = rows[0][0] if isinstance(rows[0][0], bytes) else b""
            messages.append(parse_message(
                rows[0][1],
                account_id=account.id,
                folder=folder,
                uid=int(raw_uid),
                unread=folder == "inbox" and b"\\Seen" not in flags,
                flagged=b"\\Flagged" in flags,
                outgoing=folder == "sent",
            ))
        return tuple(messages)

    def fetch_mail(
        self,
        account: Account,
        config: ServerConfig,
        *,
        cancel: threading.Event,
        limit: int = 100,
    ) -> tuple[Message, ...]:
        client = self._imap(account, config)
        try:
            inbox = self._fetch_mailbox(
                client, account, "INBOX", "inbox", cancel=cancel, limit=limit
            )
            sent = self._fetch_mailbox(
                client, account, self._sent_mailbox(client, account), "sent",
                cancel=cancel, limit=limit,
            )
            return (*inbox, *sent)
        finally:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass

    def set_seen(
        self, account: Account, config: ServerConfig,
        messages: tuple[Message, ...], seen: bool
    ) -> None:
        uids = tuple(message.uid for message in messages if message.folder == "inbox")
        if not uids:
            return
        client = self._imap(account, config)
        try:
            client.select("INBOX")
            operation = "+FLAGS.SILENT" if seen else "-FLAGS.SILENT"
            status, _ = client.uid("store", ",".join(map(str, uids)), operation, "(\\Seen)")
            if status != "OK":
                raise RuntimeError("Server did not accept the read-state change")
        finally:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass

    def send(self, account: Account, config: ServerConfig, draft: Draft) -> None:
        from email.message import EmailMessage
        import smtplib

        oauth_token = self._oauth_token(account)
        password = None if oauth_token else self.credentials(account.id, "password")
        if not oauth_token and not password:
            raise PermissionError("Account credentials are unavailable")
        message = EmailMessage()
        message["From"] = f"{account.display_name} <{account.address}>"
        message["To"] = ", ".join(draft.to)
        if draft.cc:
            message["Cc"] = ", ".join(draft.cc)
        message["Subject"] = draft.subject
        if draft.in_reply_to:
            message["In-Reply-To"] = draft.in_reply_to
        if draft.references:
            message["References"] = " ".join(dict.fromkeys(draft.references))
        message.set_content(draft.body)
        recipients = draft.to + draft.cc + draft.bcc
        context = self.ssl_context()
        if config.use_starttls:
            client: smtplib.SMTP = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30)
            client.starttls(context=context)
        else:
            client = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=30, context=context)
        try:
            if oauth_token:
                auth = f"user={config.username or account.address}\x01auth=Bearer {oauth_token}\x01\x01"
                response, _ = client.docmd(
                    "AUTH", "XOAUTH2 " + b64encode(auth.encode("utf-8")).decode("ascii")
                )
                if response != 235:
                    raise smtplib.SMTPAuthenticationError(response, b"OAuth authentication failed")
            else:
                client.login(config.username or account.address, password)
            client.send_message(message, to_addrs=recipients)
        finally:
            client.quit()


class MailEngine:
    """Coordinates local truth and bounded protocol tasks."""

    def __init__(self, store: MailStore, transport: ImapSmtpTransport | None = None) -> None:
        self.store = store
        self.transport = transport
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="charlie-mail")
        self._cancel = threading.Event()

    def sync(
        self,
        account: Account,
        config: ServerConfig,
        complete: Callable[[tuple[Message, ...] | None, Exception | None], None],
    ) -> Future:
        if self.transport is None:
            raise RuntimeError("No network transport is configured")
        self._cancel.clear()

        def work() -> tuple[Message, ...]:
            messages = self.transport.fetch_mail(account, config, cancel=self._cancel)
            for message in messages:
                self.store.upsert_message(message)
            return messages

        future = self._pool.submit(work)

        def done(result: Future) -> None:
            try:
                complete(result.result(), None)
            except Exception as error:  # callbacks receive a redacted exception class/message
                complete(None, error)

        future.add_done_callback(done)
        return future

    def mark_read(
        self,
        messages: tuple[Message, ...],
        read: bool,
        configs: dict[str, ServerConfig] | None = None,
    ) -> Future | None:
        """Commit local state immediately, then reconcile server state."""
        self.store.set_read(tuple(message.id for message in messages), read)
        if self.transport is None or not configs:
            return None

        def work() -> None:
            for account_id in dict.fromkeys(message.account_id for message in messages):
                account = next((item for item in self.store.accounts() if item.id == account_id), None)
                config = configs.get(account_id)
                if account is None or config is None:
                    continue
                selected = tuple(message for message in messages if message.account_id == account_id)
                self.transport.set_seen(account, config, selected, read)

        return self._pool.submit(work)

    def send(
        self,
        account: Account,
        config: ServerConfig,
        draft: Draft,
        complete: Callable[[Exception | None], None],
    ) -> Future:
        if self.transport is None:
            raise RuntimeError("No network transport is configured")

        future = self._pool.submit(self.transport.send, account, config, draft)

        def done(result: Future) -> None:
            try:
                result.result()
                complete(None)
            except Exception as error:
                complete(error)

        future.add_done_callback(done)
        return future

    def close(self) -> None:
        self._cancel.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
