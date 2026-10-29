# SPDX-License-Identifier: Apache-2.0
"""Single-dispatch reply policy for one authenticated notification incarnation.

The transport supplies the actual Shell owner and resolves an opaque grant to
an Android action. No message text is retained here. A missing/late Binder ACK
means uncertain delivery; it never authorizes an automatic second dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import logging
import re
import threading
from typing import Callable
import uuid


LOGGER = logging.getLogger(__name__)


class ReplyError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ReplyGrant:
    notification_id: int
    token: str
    action: str
    # Distinguishes a replaced notification/listener even if IDs are reused.
    generation: int
    endpoint: object = None


@dataclass
class _Request:
    grant: ReplyGrant
    digest: bytes
    receipt: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: str = 'pending'
    waiters: list = field(default_factory=list)


class ReplyLedger:
    """Bound reply dispatch, retries and ambiguous completion in memory.

    Grants belong to one producer connection and must be revoked at replacement,
    withdrawal, listener/server reset and shutdown. New connections must issue
    new unpredictable tokens. This is not a durable Android messaging outbox.
    ``dispatch`` starts one async operation and calls done(True) on an explicit
    Android acceptance ACK, or done(None/False) after failure/timeout. A late
    acceptance may reconcile an uncertain receipt; it cannot send text twice.
    """
    MAX_REQUESTS = 4096
    MAX_PENDING = 8
    MAX_WAITERS = 8

    def __init__(self, resolve: Callable, dispatch: Callable):
        self.resolve = resolve
        self.dispatch = dispatch
        self.requests: dict[tuple[str, str], _Request] = {}
        self.pending = 0
        self.revision = 0
        self.lock = threading.RLock()

    @staticmethod
    def validate(token, request_id, text):
        if not isinstance(token, str) or not re.fullmatch('[0-9a-f]{32}', token):
            raise ReplyError('InvalidInput')
        try:
            if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
                raise ValueError()
        except (ValueError, AttributeError):
            raise ReplyError('InvalidInput') from None
        if (not isinstance(text, str) or not text.strip() or '\x00' in text):
            raise ReplyError('InvalidInput')
        try:
            if len(text.encode('utf-8')) > 4096:
                raise ReplyError('InvalidInput')
        except UnicodeEncodeError:
            raise ReplyError('InvalidInput') from None

    def request(self, sender, owner, notification_id, token, request_id, text, complete):
        # Caller identity is a bus credential; no grant or payload inspection
        # occurs before it matches the currently observed unique Shell owner.
        if (not isinstance(owner, str) or not owner.startswith(':') or sender != owner):
            raise ReplyError('Unauthorized')
        self.validate(token, request_id, text)
        if (isinstance(notification_id, bool) or not isinstance(notification_id, int)
                or not 0 < notification_id <= 0xffffffff):
            raise ReplyError('InvalidInput')
        digest = hashlib.sha256(text.encode('utf-8')).digest()
        key = token, request_id
        # Never call the bridge resolver under the ledger lock: withdrawal
        # already holds the bridge lock before revoking ledger grants.
        with self.lock:
            revision = self.revision
        grant = self.resolve(notification_id, token)
        if (not isinstance(grant, ReplyGrant) or grant.notification_id != notification_id
                or grant.token != token or not grant.action):
            raise ReplyError('StaleNotification')
        with self.lock:
            if self.revision != revision:
                raise ReplyError('NotReady')
            existing = self.requests.get(key)
            if existing is not None:
                if existing.grant != grant or existing.digest != digest:
                    raise ReplyError('InvalidInput')
                if existing.state == 'pending':
                    if len(existing.waiters) >= self.MAX_WAITERS:
                        raise ReplyError('Busy')
                    existing.waiters.append(complete)
                    return existing.receipt
                result = existing.receipt, existing.state
            else:
                if len(self.requests) >= self.MAX_REQUESTS or self.pending >= self.MAX_PENDING:
                    raise ReplyError('Busy')
                entry = _Request(grant, digest, waiters=[complete])
                self.requests[key] = entry
                self.pending += 1
                result = None
        if result is not None:
            complete(*result)
            return result[0]

        def done(accepted):
            with self.lock:
                if entry.state == 'sent':
                    return
                first = entry.state == 'pending'
                entry.state = 'sent' if accepted is True else 'uncertain'
                if first:
                    self.pending -= 1
                waiters, entry.waiters = entry.waiters, []
                result = entry.receipt, entry.state
            for waiter in waiters:
                try:
                    waiter(*result)
                except Exception:
                    LOGGER.warning('Could not return an Android reply receipt')

        try:
            # Admission commits the single-dispatch decision. Revocation blocks
            # new requests; already-admitted operations may complete. An exception
            # may occur after IPC dispatch, so retries only read the receipt.
            self.dispatch(grant, request_id, text, done)
        except Exception:
            done(None)
        return entry.receipt

    def revoke(self, token):
        # Outstanding callbacks retain only their receipt, not the reply text.
        # Already-dispatched work cannot be unsent; its ACK may still complete
        # the original caller, while no new caller can resolve this grant.
        with self.lock:
            self.revision += 1
            for key in [key for key in self.requests if key[0] == token]:
                self.requests.pop(key)
