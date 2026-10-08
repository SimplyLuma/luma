# SPDX-License-Identifier: Apache-2.0
"""Charlie's windowless background mail agent (ADR-033, category ``mail``).

``org.projectluma.Charlie --agent`` runs this module without importing the GTK
application. It keeps one IMAP connection per enabled account, waits in IMAP
IDLE where the server offers it (polls every ten minutes where it does not),
brings new Inbox mail into the local store so the window shows it without a
sync, posts one notification per new unread message and publishes the unread
count for live extensions.

Privacy: logs carry a short hash of the opaque account id and counts only;
never addresses, subjects, bodies, server responses or exception text.
Notification text is the sender and subject, replaced by "Charlie" / "New
mail" while the screen is locked and details are not allowed there.

Agent state (the UIDVALIDITY and the last UID already announced per account)
lives in ``$XDG_STATE_HOME/charlie/agent.json`` rather than in ``mail.db``: it
is disposable (losing it only re-baselines without notifying), it belongs to
the agent alone, and a new table would bump the store's schema version, which
an older Charlie window refuses to open.

With no configured accounts the agent publishes an unread count of zero and
exits cleanly after a short grace period instead of idling. A clean exit is
not a crash: luma-background starts the agent again at its next wake, and the
window requests background activity (which starts an allowed agent) after an
account is added, so an idle process that costs memory buys nothing.

Bus contract: ``org.projectluma.BackgroundAgent1`` from ``background.py``
(published values ``unread-count`` and ``unread-by-account``) plus
``org.projectluma.Charlie.MailAgent1`` (``CheckNow``, ``Reload``).
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
import imaplib
import json
import logging
import os
from pathlib import Path
import random
import re
import socket
import ssl
import sys
import threading
import time
from typing import Callable, Iterable, Protocol
from urllib.parse import quote

from . import APP_ID
from .engine import AuthenticationFailure, ImapSmtpTransport
from .model import Account, ServerConfig

LOG = logging.getLogger("charlie.agent")

AGENT_NAME = f"{APP_ID}.Agent"
MAIL_AGENT_PATH = "/org/projectluma/Charlie/MailAgent"
MAIL_AGENT_INTERFACE = "org.projectluma.Charlie.MailAgent1"
MAIL_AGENT_XML = f"""<node><interface name="{MAIL_AGENT_INTERFACE}">
  <method name="CheckNow"/>
  <method name="Reload"/>
</interface></node>"""
APP_PATH = "/org/projectluma/Charlie"
NOTIFICATION_CATEGORY = "email.arrived"

IDLE_SECONDS = 25 * 60
POLL_SECONDS = 10 * 60
BACKOFF_START = 60.0
BACKOFF_MAX = 30 * 60.0
MAX_FETCH_PER_PASS = 25
MAX_BODY_BYTES = 8 * 1024 * 1024
GROUP_THRESHOLD = 3
EMPTY_EXIT_SECONDS = 30
STOP_SECONDS = 4.0
HEALTHY_SESSION_SECONDS = 60.0
KEEPALIVE_IDLE_SECONDS = 240
TRACKED_NOTIFICATIONS = 50
IDLE_TRIGGERS = frozenset({"EXISTS", "RECENT", "FETCH", "EXPUNGE"})

_UID = re.compile(rb"\bUID (\d+)")
_FLAGS = re.compile(rb"\bFLAGS \(([^)]*)\)")
_SIZE = re.compile(rb"\bRFC822\.SIZE (\d+)")
_UNSEEN = re.compile(rb"\bUNSEEN (\d+)")
_STATUS_UIDNEXT = re.compile(rb"\bUIDNEXT (\d+)")
_STATUS_UIDVALIDITY = re.compile(rb"\bUIDVALIDITY (\d+)")


def account_tag(account_id: str) -> str:
    """A log-safe handle for an account (the id is already opaque; hash it anyway)."""
    return sha256(account_id.encode("utf-8")).hexdigest()[:10]


def backoff_delay(failures: int, jitter: float | None = None) -> float:
    """Seconds to wait after ``failures`` consecutive failures: 1 min doubling to 30 min, ±20 % jitter."""
    if failures <= 0:
        return 0.0
    base = min(BACKOFF_MAX, BACKOFF_START * (2 ** min(failures - 1, 16)))
    jitter = random.random() if jitter is None else min(1.0, max(0.0, jitter))
    return max(BACKOFF_START * 0.8, min(BACKOFF_MAX, base * (0.8 + 0.4 * jitter)))


def _xdg(variable: str, fallback: str) -> Path:
    value = os.environ.get(variable, "")
    return Path(value) if value and os.path.isabs(value) else Path.home() / fallback


def default_store_path() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "charlie" / "mail.db"


def default_state_path() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / "charlie" / "agent.json"


class CredentialsUnavailable(Exception):
    """No credential could be read (keyring locked, token service unreachable): retry later."""


# ---------------------------------------------------------------------------
# Per-account state
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MailboxMark:
    uidvalidity: int
    last_uid: int


class AgentState:
    """UIDVALIDITY and last announced UID per account, in a private JSON file."""

    VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._marks: dict[str, MailboxMark] = {}
        try:
            data = json.loads(path.read_text())
            if data.get("version") == self.VERSION:
                for account_id, value in data.get("accounts", {}).items():
                    self._marks[str(account_id)] = MailboxMark(int(value["uidvalidity"]), int(value["last_uid"]))
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self._marks = {}

    def get(self, account_id: str) -> MailboxMark | None:
        with self._lock:
            return self._marks.get(account_id)

    def set(self, account_id: str, mark: MailboxMark) -> None:
        with self._lock:
            if self._marks.get(account_id) == mark:
                return
            self._marks[account_id] = mark
            self._write()

    def forget(self, account_ids: Iterable[str]) -> None:
        with self._lock:
            removed = [self._marks.pop(account_id, None) for account_id in account_ids]
            if any(removed):
                self._write()

    def _write(self) -> None:
        payload = json.dumps({
            "version": self.VERSION,
            "accounts": {key: {"uidvalidity": mark.uidvalidity, "last_uid": mark.last_uid}
                         for key, mark in sorted(self._marks.items())},
        }, separators=(",", ":")).encode("utf-8")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise


# ---------------------------------------------------------------------------
# IMAP helpers (all run on an account's worker thread)
# ---------------------------------------------------------------------------


def _ok(status: str, what: str) -> None:
    if status != "OK":
        raise imaplib.IMAP4.error(f"{what} was refused")


def _integer(values, default: int = 0) -> int:
    for value in values or ():
        if isinstance(value, bytes) and value.strip().isdigit():
            return int(value)
    return default


def refresh_capabilities(client: imaplib.IMAP4) -> None:
    """Servers such as Dovecot only list IDLE after authentication."""
    if "IDLE" in client.capabilities:
        return
    status, data = client.capability()
    if status == "OK" and data and isinstance(data[-1], bytes):
        client.capabilities = tuple(data[-1].decode("ascii", "replace").upper().split())


def examine_inbox(client: imaplib.IMAP4) -> tuple[int, int]:
    """EXAMINE INBOX; returns (UIDVALIDITY, UIDNEXT)."""
    status, _ = client.select("INBOX", readonly=True)
    _ok(status, "EXAMINE")
    uidvalidity = _integer(client.response("UIDVALIDITY")[1])
    uidnext = _integer(client.response("UIDNEXT")[1])
    client.response("RECENT")
    if not uidvalidity or not uidnext:
        status, data = client.status("INBOX", "(UIDNEXT UIDVALIDITY)")
        _ok(status, "STATUS")
        line = b" ".join(item for item in data if isinstance(item, bytes))
        if not uidnext and (match := _STATUS_UIDNEXT.search(line)):
            uidnext = int(match.group(1))
        if not uidvalidity and (match := _STATUS_UIDVALIDITY.search(line)):
            uidvalidity = int(match.group(1))
    if not uidvalidity:
        raise imaplib.IMAP4.error("server reported no UIDVALIDITY")
    return uidvalidity, max(1, uidnext)


def search_uids(client: imaplib.IMAP4, *criteria: str) -> list[int]:
    status, data = client.uid("SEARCH", *criteria)
    _ok(status, "UID SEARCH")
    return sorted({int(token) for item in data if isinstance(item, bytes) for token in item.split() if token.isdigit()})


def uids_after(client: imaplib.IMAP4, last_uid: int) -> list[int]:
    # "n:*" always matches the highest UID, even when it is below n.
    return [uid for uid in search_uids(client, "UID", f"{last_uid + 1}:*") if uid > last_uid]


def _responses(data) -> Iterable[bytes]:
    """Flatten imaplib FETCH data to the protocol text of each message response."""
    pending = b""
    for item in data or ():
        if isinstance(item, tuple):
            pending += item[0] + b" "
        elif isinstance(item, bytes):
            pending += item
            yield pending
            pending = b""
    if pending:
        yield pending


def fetch_flags_and_sizes(client: imaplib.IMAP4, uids: list[int]) -> dict[int, tuple[bytes, int]]:
    if not uids:
        return {}
    status, data = client.uid("FETCH", ",".join(map(str, uids)), "(UID FLAGS RFC822.SIZE)")
    _ok(status, "UID FETCH")
    result: dict[int, tuple[bytes, int]] = {}
    for text in _responses(data):
        uid, flags = _UID.search(text), _FLAGS.search(text)
        if uid is None or flags is None:
            continue
        size = _SIZE.search(text)
        result[int(uid.group(1))] = (flags.group(1), int(size.group(1)) if size else 0)
    return result


def fetch_literal(client: imaplib.IMAP4, uid: int, section: str) -> bytes | None:
    status, data = client.uid("FETCH", str(uid), f"({section})")
    _ok(status, "UID FETCH")
    for item in data or ():
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], bytes):
            match = _UID.search(item[0])
            if match is None or int(match.group(1)) == uid:
                return item[1]
    return None


def unseen_count(client: imaplib.IMAP4) -> int:
    status, data = client.status("INBOX", "(UNSEEN)")
    _ok(status, "STATUS")
    line = b" ".join(item for item in data if isinstance(item, bytes))
    match = _UNSEEN.search(line)
    return int(match.group(1)) if match else 0


def enable_keepalive(client: imaplib.IMAP4) -> None:
    """Notice a connection a NAT or suspended network dropped within minutes, not at the next IDLE renewal."""
    sock = getattr(client, "sock", None)
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for option, value in (("TCP_KEEPIDLE", KEEPALIVE_IDLE_SECONDS), ("TCP_KEEPINTVL", 30), ("TCP_KEEPCNT", 4)):
            if hasattr(socket, option):
                sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, option), value)
    except OSError:
        pass


def break_connection(client: imaplib.IMAP4 | None) -> None:
    """Wake a thread blocked reading ``client`` (IDLE, FETCH) from any other thread."""
    sock = getattr(client, "sock", None)
    if sock is None:
        return
    try:
        # The plain socket call: SSLSocket.shutdown would also drop its TLS
        # object under the reading thread.
        socket.socket.shutdown(sock, socket.SHUT_RDWR)
    except OSError:
        pass


def close_connection(client: imaplib.IMAP4 | None, *, logout: bool = False) -> None:
    if client is None:
        return
    if logout:
        try:
            client.logout()
            return
        except (imaplib.IMAP4.error, OSError, ValueError):
            pass
    try:
        client.shutdown()
    except (OSError, ValueError, AttributeError):
        pass


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


class NotifierLike(Protocol):
    def notify(self, key: str, summary: str, body: str = "", **options) -> int: ...
    def withdraw(self, key: str) -> None: ...
    def keys(self) -> list[str]: ...


@dataclass(frozen=True, slots=True)
class NewMail:
    message_id: str
    account_id: str
    uid: int
    sender: str
    subject: str


class SecretLookup(Protocol):
    def lookup(self, account_id: str, kind: str) -> str | None: ...
    def store(self, account_id: str, kind: str, value: str) -> None: ...


class MailAgentCore:
    """Account workers, notifications and published counts, independent of the bus.

    ``dispatch`` runs a callable on the thread that owns ``notifier`` and
    ``publish`` (the GLib main loop in the agent, inline in tests).
    """

    def __init__(self, *, store_path: Path, state_path: Path, secrets: SecretLookup,
                 notifier: NotifierLike, publish: Callable[[str, object], None],
                 dispatch: Callable[[Callable[[], None]], None], launcher=None, oauth=None,
                 on_accounts: Callable[[int], None] | None = None,
                 ssl_context: Callable[[], ssl.SSLContext] = ssl.create_default_context) -> None:
        self.store_path = store_path
        self.state = AgentState(state_path)
        self.secrets = secrets
        self.notifier = notifier
        self.publish = publish
        self.dispatch = dispatch
        self.launcher = launcher
        self._oauth = oauth
        self.on_accounts = on_accounts or (lambda _count: None)
        self.ssl_context = ssl_context
        self.idle_seconds = IDLE_SECONDS
        self.poll_seconds = POLL_SECONDS
        self._store = None
        self._store_lock = threading.RLock()
        self._secret_lock = threading.Lock()
        self._workers: dict[str, AccountWorker] = {}
        self._unread: dict[str, int] = {}
        self._unread_lock = threading.Lock()
        self._stopping = False

    # -- accounts --------------------------------------------------------------
    def store(self):
        """The agent's own sqlite connection; ``None`` until Charlie has created its database."""
        with self._store_lock:
            if self._store is None and not self._stopping and self.store_path.exists():
                from .store import MailStore

                self._store = MailStore(self.store_path)
            return self._store

    def configured(self) -> list[tuple[Account, ServerConfig]]:
        with self._store_lock:
            store = self.store()
            if store is None:
                return []
            return [(account, config) for account in store.accounts()
                    if account.enabled and account.provider != "demo"
                    and (config := store.server_config(account.id)) is not None and config.imap_host]

    def reload(self) -> int:
        """Match workers to the configured accounts; returns how many there are.

        Unchanged, healthy workers keep their connection. Changed accounts and
        accounts stopped by an authentication failure get a new worker, which
        waits for its predecessor to finish so the two never overlap.
        """
        if self._stopping:
            return 0
        wanted = {account.id: (account, config) for account, config in self.configured()}
        predecessors: dict[str, AccountWorker] = {}
        for account_id, worker in list(self._workers.items()):
            target = wanted.get(account_id)
            if target is not None and (worker.account, worker.config) == target and worker.is_alive():
                continue
            worker.stop()
            del self._workers[account_id]
            if target is None:
                self._forget(account_id)
            else:
                predecessors[account_id] = worker
        for account_id, (account, config) in wanted.items():
            if account_id not in self._workers:
                worker = AccountWorker(self, account, config, predecessor=predecessors.get(account_id))
                self._workers[account_id] = worker
                worker.start()
        LOG.info("Watching %d account(s)", len(wanted))
        self._publish_counts()
        self.on_accounts(len(wanted))
        return len(wanted)

    def _forget(self, account_id: str) -> None:
        self.state.forget((account_id,))
        with self._unread_lock:
            self._unread.pop(account_id, None)
        for prefix in (f"message-{account_id}:", f"new-{account_id}", f"auth-{account_id}"):
            self._withdraw_prefix(prefix)

    def _withdraw_prefix(self, prefix: str) -> None:
        for key in list(self.notifier.keys()):
            if key.startswith(prefix):
                self.notifier.withdraw(key)

    def check_now(self) -> None:
        for worker in list(self._workers.values()):
            worker.wake()

    def wake(self, event: str) -> None:
        """``login`` and ``request`` need nothing: starting already loaded the accounts."""
        if event in {"network", "resume", "unlock", "schedule"}:
            # Connections held across suspend or a network change are dead.
            self.check_now()

    def stop(self, timeout: float = STOP_SECONDS) -> None:
        self._stopping = True
        workers = list(self._workers.values())
        self._workers.clear()
        for worker in workers:
            worker.stop()
        deadline = time.monotonic() + timeout
        for worker in workers:
            worker.join(max(0.0, deadline - time.monotonic()))
        with self._store_lock:
            if self._store is not None and not any(worker.is_alive() for worker in workers):
                self._store.close()
                self._store = None

    def workers(self) -> dict[str, "AccountWorker"]:
        return dict(self._workers)

    # -- credentials -------------------------------------------------------------
    def oauth(self, account: Account, token_json: str):
        if self._oauth is not None:
            return self._oauth
        from .oauth import GmailOAuth, MicrosoftOAuth

        if account.provider == "microsoft":
            return MicrosoftOAuth.from_token_json(token_json)
        return GmailOAuth()

    def credentials(
        self, account: Account, force_refresh: bool = False
    ) -> tuple[str | None, str | None]:
        """Return credentials only while that account still exists, across owners."""
        from .account_lifecycle import account_lock
        with account_lock(self.store_path, account.id):
            with self._store_lock:
                store = self.store()
                current = next((value for value in store.accounts() if value.id == account.id), None) if store else None
            if current is None or not current.enabled or current.provider != account.provider:
                raise CredentialsUnavailable("Mail account is unavailable")
            return self._credentials_locked(current, force_refresh)

    def _credentials_locked(self, account, force_refresh=False):
        with self._secret_lock:
            token_json = (
                self.secrets.lookup(account.id, "oauth-token")
                if account.provider in {"gmail", "microsoft"} else None
            )
            password = None if token_json else self.secrets.lookup(account.id, "password")
        if token_json:
            from .oauth import OAuthError, failure_requires_sign_in, GoogleOAuthUnavailable, require_google_oauth

            try:
                if account.provider == "gmail":
                    require_google_oauth()
                access_token, refreshed = self.oauth(account, token_json).access_token(
                    token_json, force_refresh=force_refresh
                )
            except GoogleOAuthUnavailable as error:
                raise CredentialsUnavailable(str(error)) from None
            except OAuthError as error:
                if failure_requires_sign_in(error):
                    raise AuthenticationFailure(f"{account.provider.title()} refused the saved sign-in") from None
                raise CredentialsUnavailable(f"{account.provider.title()} could not be reached") from None
            if refreshed:
                with self._secret_lock:
                    self.secrets.store(account.id, "oauth-token", refreshed)
            if not access_token:
                raise AuthenticationFailure(f"{account.provider.title()} returned no access token")
            return access_token, None
        if not password:
            raise CredentialsUnavailable("No credential is available")
        return None, password

    def connect(self, account: Account, config: ServerConfig) -> imaplib.IMAP4_SSL:
        token, password = self.credentials(account)
        transport = ImapSmtpTransport(lambda _account_id, kind: password if kind == "password" else None,
                                      (
                                          lambda _account, force_refresh=False: (
                                              self.credentials(account, force_refresh=True)[0]
                                              if force_refresh else token
                                          )
                                      ) if token else None,
                                      ssl_context=self.ssl_context)
        client = transport._imap(account, config)
        enable_keepalive(client)
        try:
            refresh_capabilities(client)
        except (imaplib.IMAP4.error, OSError):
            close_connection(client)
            raise
        return client

    # -- results from workers ---------------------------------------------------------
    def upsert(self, messages) -> None:
        with self._store_lock:
            store = self.store()
            if store is None:
                return
            for message in messages:
                store.upsert_message(message)

    def set_read_locally(self, message_id: str) -> None:
        with self._store_lock:
            store = self.store()
            if store is not None:
                store.set_read((message_id,), True)

    def set_unread(self, account_id: str, count: int) -> None:
        with self._unread_lock:
            if self._unread.get(account_id) == count:
                return
            self._unread[account_id] = max(0, int(count))
        self.dispatch(self._publish_counts)

    def _publish_counts(self) -> None:
        with self._unread_lock:
            active = {key: value for key, value in self._unread.items() if key in self._workers}
        self.publish("unread-count", sum(active.values()))
        self.publish("unread-by-account", dict(active))

    def announce(self, account: Account, items: list[NewMail], extra_unread: int = 0) -> list[tuple[str, list[int]]]:
        """Post notifications for new unread mail; returns (key, uids) for later withdrawal."""
        total = len(items) + max(0, extra_unread)
        if total == 0:
            return []
        if total > GROUP_THRESHOLD:
            senders = list(dict.fromkeys(item.sender for item in items))[:GROUP_THRESHOLD]
            key = f"new-{account.id}"
            self.dispatch(lambda: self.notifier.notify(
                key, f"{total} new messages", ", ".join(senders),
                actions=[("default", "Open Charlie")], on_action=lambda _action: self.open_app(key),
                category=NOTIFICATION_CATEGORY, public_summary="Charlie", public_body="New mail"))
            return [(key, [item.uid for item in items])]
        posted = []
        for item in items:
            key = f"message-{item.message_id}"
            self.dispatch(lambda item=item, key=key: self.notifier.notify(
                key, item.sender, item.subject,
                actions=[("default", "Open"), ("mark-read", "Mark Read")],
                on_action=lambda action, item=item, key=key: self.message_action(item, key, action),
                category=NOTIFICATION_CATEGORY, public_summary="Charlie", public_body="New mail"))
            posted.append((key, [item.uid]))
        return posted

    def withdraw(self, key: str) -> None:
        self.dispatch(lambda: self.notifier.withdraw(key))

    def authentication_failed(self, account: Account) -> None:
        key = f"auth-{account.id}"
        LOG.warning("Account %s was refused; waiting for Reload", account_tag(account.id))
        self.dispatch(lambda: self.notifier.notify(
            key, f"Sign in to {account.address} again",
            "Charlie can't check this account until you sign in again.",
            actions=[("default", "Open Charlie")], on_action=lambda _action: self.open_app(key),
            category="email", public_summary="Charlie", public_body="An account needs attention"))

    def authentication_succeeded(self, account: Account) -> None:
        """Retire this account's stable auth alert after a proven login.

        The desktop notification can outlive the agent process that posted it,
        so this deliberately withdraws the deterministic ID even when the new
        notifier has no in-memory record of that alert.
        """
        key = f"auth-{account.id}"
        self.dispatch(lambda: self.notifier.withdraw(key))

    # -- notification actions (main thread) -----------------------------------------------
    def _token(self, key: str) -> str:
        getter = getattr(self.notifier, "activation_token", None)
        return getter(key) if callable(getter) else ""

    def open_app(self, key: str) -> None:
        if self.launcher is not None:
            self.launcher.activate(self._token(key))
        self.notifier.withdraw(key)

    def message_action(self, item: NewMail, key: str, action: str) -> None:
        if action == "mark-read":
            threading.Thread(target=self._mark_read, args=(item, key), name="charlie-mark-read",
                             daemon=True).start()
        elif action == "default":
            if self.launcher is not None:
                self.launcher.open_message(item.message_id, self._token(key))
            self.notifier.withdraw(key)

    def _mark_read(self, item: NewMail, key: str) -> None:
        worker = self._workers.get(item.account_id)
        if worker is None:
            return
        client = None
        try:
            client = self.connect(worker.account, worker.config)
            status, _ = client.select("INBOX")
            _ok(status, "SELECT")
            status, _ = client.uid("STORE", str(item.uid), "+FLAGS", "(\\Seen)")
            _ok(status, "UID STORE")
            self.set_read_locally(item.message_id)
            worker.forget_notification(item.uid)
            self.withdraw(key)
            self.set_unread(item.account_id, unseen_count(client))
            LOG.info("Marked one message read for %s", account_tag(item.account_id))
        except Exception as error:
            LOG.warning("Mark read failed for %s: %s", account_tag(item.account_id), type(error).__name__)
        finally:
            close_connection(client, logout=True)


class AccountWorker(threading.Thread):
    """One account's connection: IDLE (or polling), incremental checks, backoff."""

    def __init__(self, core: MailAgentCore, account: Account, config: ServerConfig,
                 predecessor: threading.Thread | None = None) -> None:
        super().__init__(name=f"charlie-agent-{account_tag(account.id)}", daemon=True)
        self.core, self.account, self.config = core, account, config
        self.tag = account_tag(account.id)
        self.predecessor = predecessor
        self.stopped_for_authentication = False
        self.connections = 0
        self.checks = 0
        self._stopping = threading.Event()
        self._woken = threading.Event()
        self._client_lock = threading.Lock()
        self._client: imaplib.IMAP4 | None = None
        self._notified: OrderedDict[int, str] = OrderedDict()
        self._notified_lock = threading.Lock()

    # -- control (any thread) ------------------------------------------------------
    def wake(self) -> None:
        self._woken.set()
        with self._client_lock:
            break_connection(self._client)

    def stop(self) -> None:
        self._stopping.set()
        self.wake()

    def forget_notification(self, uid: int) -> None:
        with self._notified_lock:
            self._notified.pop(uid, None)

    # -- thread ----------------------------------------------------------------------
    def run(self) -> None:
        if self.predecessor is not None:
            self.predecessor.join(STOP_SECONDS)
            self.predecessor = None
        failures = 0
        while not self._stopping.is_set():
            self._woken.clear()
            client = None
            healthy_since = 0.0
            try:
                client = self.core.connect(self.account, self.config)
                self.connections += 1
                with self._client_lock:
                    self._client = client
                if self._stopping.is_set():
                    break
                self.core.authentication_succeeded(self.account)
                self.check(client)
                healthy_since = time.monotonic()
                failures = 0
                if "IDLE" in client.capabilities:
                    self._idle(client)
                else:
                    self._detach()
                    close_connection(client, logout=True)
                    client = None
                    self._sleep(self.core.poll_seconds)
            except AuthenticationFailure:
                self.stopped_for_authentication = True
                if not self._stopping.is_set():
                    self.core.authentication_failed(self.account)
                return
            except Exception as error:
                if self._stopping.is_set():
                    break
                self._detach()
                close_connection(client)
                client = None
                if self._woken.is_set():
                    failures = 0
                    continue
                if healthy_since and time.monotonic() - healthy_since >= HEALTHY_SESSION_SECONDS:
                    # A long, healthy session ended (server timeout, dropped NAT): reconnect now.
                    LOG.info("Account %s: connection closed (%s); reconnecting", self.tag, type(error).__name__)
                    failures = 0
                    continue
                failures += 1
                delay = backoff_delay(failures)
                LOG.info("Account %s: %s; retrying in %d s", self.tag, type(error).__name__, delay)
                LOG.debug("Account %s failure detail", self.tag, exc_info=True)
                if self._sleep(delay):
                    failures = 0
            finally:
                self._detach()
                close_connection(client)

    def _detach(self) -> None:
        with self._client_lock:
            self._client = None

    def _sleep(self, seconds: float) -> bool:
        """Wait, returning True when woken early."""
        return self._woken.wait(seconds)

    def _idle(self, client: imaplib.IMAP4) -> None:
        rechecks = 0
        while not self._woken.is_set():
            # Changes reported while the last check ran would be lost to IDLE.
            pending = any(client.untagged_responses.get(name) for name in IDLE_TRIGGERS)
            client.untagged_responses.clear()
            if pending and rechecks < 3:
                rechecks += 1
                self.check(client)
                continue
            rechecks = 0
            with client.idle(duration=self.core.idle_seconds) as idler:
                for response, _data in idler:
                    if response in IDLE_TRIGGERS or self._woken.is_set():
                        break
            if self._woken.is_set():
                return
            self.check(client)

    # -- the incremental check -------------------------------------------------------------
    def check(self, client: imaplib.IMAP4) -> None:
        self.checks += 1
        account = self.account
        uidvalidity, uidnext = examine_inbox(client)
        mark = self.core.state.get(account.id)
        if mark is None or mark.uidvalidity != uidvalidity:
            # First run or a rebuilt mailbox: never flood with old mail.
            self.core.state.set(account.id, MailboxMark(uidvalidity, uidnext - 1))
            with self._notified_lock:
                self._notified.clear()
            LOG.info("Account %s: baseline set", self.tag)
        elif uidnext - 1 > mark.last_uid:
            new = uids_after(client, mark.last_uid)
            if new:
                self._process(client, mark, new)
        self._withdraw_read(client)
        self.core.set_unread(account.id, unseen_count(client))

    def _process(self, client: imaplib.IMAP4, mark: MailboxMark, new: list[int]) -> None:
        from .mime import parse_message

        account = self.account
        batch, skipped = new[-MAX_FETCH_PER_PASS:], new[:-MAX_FETCH_PER_PASS]
        own = account.address.casefold()
        items: list[NewMail] = []
        stored = 0
        for uid, (flags, size) in sorted(fetch_flags_and_sizes(client, batch).items()):
            complete = size <= MAX_BODY_BYTES
            raw = fetch_literal(client, uid, "BODY.PEEK[]" if complete else "BODY.PEEK[HEADER]")
            if raw is None:
                continue
            unread = b"\\Seen" not in flags
            message = parse_message(raw, account_id=account.id, folder="inbox", uid=uid, unread=unread,
                                    flagged=b"\\Flagged" in flags, outgoing=False)
            del raw
            if complete:
                self.core.upsert((message,))
                stored += 1
            if unread and message.sender_address.casefold() != own:
                items.append(NewMail(message.id, account.id, uid, message.sender_label[:120],
                                     " ".join(message.subject.split())[:200] or "(No subject)"))
            del message
        extra = 0
        if skipped:
            extra = len(search_uids(client, "UID", f"{skipped[0]}:{skipped[-1]}", "UNSEEN"))
        self.core.state.set(account.id, MailboxMark(mark.uidvalidity, max(new)))
        LOG.info("Account %s: %d new, %d stored, %d to announce", self.tag, len(new), stored, len(items) + extra)
        for key, uids in self.core.announce(account, items, extra):
            with self._notified_lock:
                for uid in uids:
                    self._notified[uid] = key
                while len(self._notified) > TRACKED_NOTIFICATIONS:
                    self._notified.popitem(last=False)

    def _withdraw_read(self, client: imaplib.IMAP4) -> None:
        """Withdraw notifications for mail that has since been read or removed elsewhere."""
        with self._notified_lock:
            tracked = dict(self._notified)
        if not tracked:
            return
        flags = fetch_flags_and_sizes(client, sorted(tracked))
        done = {uid for uid in tracked if uid not in flags or b"\\Seen" in flags[uid][0]}
        if not done:
            return
        with self._notified_lock:
            for uid in done:
                self._notified.pop(uid, None)
            remaining = set(self._notified.values())
        for key in {tracked[uid] for uid in done} - remaining:
            self.core.withdraw(key)


# ---------------------------------------------------------------------------
# The process: bus, notifications, launching Charlie
# ---------------------------------------------------------------------------


class CharlieLauncher:
    """Opens Charlie's UI process; the agent itself never opens a window."""

    def __init__(self, connection) -> None:
        self.connection = connection

    def _platform(self, token: str):
        from gi.repository import GLib

        return {"activation-token": GLib.Variant("s", token)} if token else {}

    def activate(self, token: str = "") -> None:
        from gi.repository import Gio, GLib

        self.connection.call(APP_ID, APP_PATH, "org.freedesktop.Application", "Activate",
                             GLib.Variant("(a{sv})", (self._platform(token),)), None, Gio.DBusCallFlags.NONE,
                             30000, None, self._finished, None)

    def open_message(self, message_id: str, token: str = "") -> None:
        from gi.repository import Gio, GLib

        parameters = GLib.Variant("(sava{sv})", ("open-message", [GLib.Variant("s", message_id)],
                                                 self._platform(token)))
        self.connection.call(APP_ID, APP_PATH, "org.freedesktop.Application", "ActivateAction", parameters, None,
                             Gio.DBusCallFlags.NONE, 30000, None, self._finished,
                             f"charlie://message/{quote(message_id, safe='')}")

    def _finished(self, connection, result, uri) -> None:
        from gi.repository import Gio, GLib

        try:
            connection.call_finish(result)
            return
        except GLib.Error as error:
            LOG.info("Charlie did not answer on the bus (%s); launching it", error.code)
        info = Gio.DesktopAppInfo.new(f"{APP_ID}.desktop")
        if info is None:
            return
        try:
            info.launch_uris([uri] if uri else [], None)
        except GLib.Error as error:
            LOG.warning("Charlie could not be launched (%s)", error.code)


AGENT_REASON = "Get new mail when Charlie is closed"


class AgentRuntime:
    """Wires MailAgentCore to the background agent, the session bus and GLib."""

    def __init__(self, agent, connection) -> None:
        self.agent = agent
        self.connection = connection
        self.core: MailAgentCore | None = None
        self.notifier = None
        self.environment = None
        self._registration = 0
        self._exit_source = 0

    def start(self) -> None:
        from gi.repository import Gio, GLib

        from .background import KIT_BACKGROUND, EnvironmentWakes, Notifier
        from .secrets import SecretStore

        connection = self.connection
        LOG.info("Agent contract from %s; %s", "the kit" if KIT_BACKGROUND else "Charlie's built-in module",
                 "managed by luma-background" if self.agent.managed else "unmanaged")
        self.notifier = Notifier(connection, APP_ID, "Charlie")

        def dispatch(callback: Callable[[], None]) -> None:
            GLib.idle_add(lambda: (callback(), GLib.SOURCE_REMOVE)[1])

        self.core = MailAgentCore(
            store_path=default_store_path(), state_path=default_state_path(), secrets=SecretStore(),
            notifier=self.notifier, publish=self.agent.publish, dispatch=dispatch,
            launcher=CharlieLauncher(connection), on_accounts=self._accounts_changed)
        node = Gio.DBusNodeInfo.new_for_xml(MAIL_AGENT_XML)
        self._registration = connection.register_object(MAIL_AGENT_PATH, node.interfaces[0], self._method, None,
                                                        None)
        if not self.agent.managed:
            # Without luma-background nobody else delivers these wakes.
            self.environment = EnvironmentWakes(connection, self.wake)
        self.core.reload()

    def wake(self, reason: str) -> None:
        if self.core is not None:
            self.core.wake(reason)

    def stop(self) -> None:
        from gi.repository import GLib

        if self._exit_source:
            GLib.source_remove(self._exit_source)
            self._exit_source = 0
        if self.environment is not None:
            self.environment.close()
            self.environment = None
        if self.core is not None:
            self.core.stop()
        if self.connection is not None and self._registration:
            self.connection.unregister_object(self._registration)
            self._registration = 0
        if self.notifier is not None:
            self.notifier.close()

    def _accounts_changed(self, count: int) -> None:
        from gi.repository import GLib

        if count:
            if self._exit_source:
                GLib.source_remove(self._exit_source)
                self._exit_source = 0
        elif not self._exit_source:
            self._exit_source = GLib.timeout_add_seconds(EMPTY_EXIT_SECONDS, self._exit_empty)

    def _exit_empty(self) -> bool:
        from gi.repository import GLib

        self._exit_source = 0
        LOG.info("No accounts; exiting until the next wake")
        self.agent.quit()
        return GLib.SOURCE_REMOVE

    def _method(self, _connection, _sender, _path, _interface, method, _parameters, invocation) -> None:
        from gi.repository import GLib

        if method == "CheckNow":
            invocation.return_value(None)
            GLib.idle_add(lambda: (self.core.check_now(), GLib.SOURCE_REMOVE)[1])
        elif method == "Reload":
            invocation.return_value(None)
            GLib.idle_add(lambda: (self.core.reload(), GLib.SOURCE_REMOVE)[1])
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)


def agent_class():
    """Charlie's agent on the kit's ``Agent`` when available, else on the built-in one."""
    from .background import AgentBase

    class CharlieMailAgent(AgentBase):
        app_id = APP_ID
        agent_id = AGENT_NAME

        def __init__(self, connection) -> None:
            super().__init__(connection=connection)
            self.runtime = AgentRuntime(self, connection)

        def on_start(self) -> None:
            self.runtime.start()

        def on_wake(self, wake) -> None:
            LOG.info("Wake: %s%s", wake.reason, " (missed)" if getattr(wake, "missed", False) else "")
            self.runtime.wake(wake.reason)

        def on_stop(self) -> None:
            self.runtime.stop()

    return CharlieMailAgent


def main(argv: list[str] | None = None) -> int:
    from .background import configure_logging, service_installed

    arguments = list(sys.argv if argv is None else argv)[1:]
    autostart = "--autostart" in arguments
    unknown = [argument for argument in arguments if argument not in {"--agent", "--autostart"}]
    if unknown or "--agent" not in arguments:
        print("usage: org.projectluma.Charlie --agent [--autostart]", file=sys.stderr)
        return 2
    configure_logging()
    from gi.repository import Gio, GLib

    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        LOG.error("No session bus")
        return 1
    if autostart and service_installed(connection):
        # The session autostart entry is only for sessions without
        # luma-background; with it, the service starts allowed agents itself.
        LOG.info("luma-background is installed; leaving the start to it")
        return 0
    return agent_class()(connection).run()


if __name__ == "__main__":
    raise SystemExit(main())
