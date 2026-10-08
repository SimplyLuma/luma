# SPDX-License-Identifier: Apache-2.0
"""What may make a network account deliver something to another person.

A message delivered twice, or a reaction nobody chose, reaches a real person
and cannot be taken back. Messages therefore has one way to ask a helper to
deliver, and it is narrow:

- A person's action (pressing Send, choosing a reaction, confirming Retry in
  Review Send, replying from a notification) authorizes a request: the store
  records a random token for that message before anything is asked.
- ``AccountStore.claim_send`` turns an authorized token into a ``SendClaim``
  exactly once, in one database transaction, before the helper is asked. A
  token that was ever claimed is never claimed again: a restart, a reconnect,
  a timeout or a queue flush cannot deliver the same request twice.
- ``BridgeProcess.deliver`` is the only call that sends a delivery command
  to a helper, and only with a ``SendClaim``; ``BridgeProcess.request``
  refuses delivery commands outright, logs an error and turns sending off.
  ``tests/messages_outbound_unit.py`` reads the source and fails if anything
  else sends a delivery command.
- The kill switch is one file, ``outbound-disabled`` beside the account
  directories. Vitals, a person or Messages itself creates it; while it exists
  no delivery is claimed or sent, by Messages or by a helper (which reads the
  same file). Too many deliveries in a minute trips it and tells the person.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import logging
from pathlib import Path
import threading
import time

log = logging.getLogger(__name__)

DELIVERY_COMMANDS = frozenset({"message.send", "media.send", "message.react"})
KILL_SWITCH = "outbound-disabled"
BURST_LIMIT = 12
BURST_WINDOW = 60.0

# Helper answers that prove nothing was delivered: the request may be tried
# again later with the same claim released. Every other failure after a
# delivery command was written is uncertain and waits for the person.
NOT_ATTEMPTED = frozenset({"not_attempted", "not_connected", "outbound_disabled", "invalid", "unsupported"})

_ISSUER = object()


@dataclass(frozen=True)
class SendClaim:
    """A person's request, claimed once. Only ``AccountStore.claim_send`` makes one."""
    message_uid: str
    token: str
    kind: str
    issuer: object = field(repr=False, compare=False, default=None)

    @property
    def valid(self) -> bool:
        return self.issuer is _ISSUER and len(self.token) == 32


def issue_claim(message_uid: str, token: str, kind: str) -> SendClaim:
    return SendClaim(message_uid, token, kind, _ISSUER)


def kill_switch(accounts_root: Path) -> Path:
    return Path(accounts_root) / KILL_SWITCH


def disabled_reason(accounts_root: Path) -> str | None:
    try:
        return kill_switch(accounts_root).read_text().strip() or "turned off"
    except FileNotFoundError:
        return None
    except OSError:
        return "the switch could not be read"


def trip(accounts_root: Path, reason: str) -> None:
    """Turn sending off for every account, and say so in the journal Vitals reads."""
    log.error("Messages outbound delivery disabled: %s", reason)
    try:
        Path(accounts_root).mkdir(parents=True, exist_ok=True)
        kill_switch(accounts_root).write_text(reason + "\n")
    except OSError as error:
        log.error("Messages could not write its outbound kill switch: %s", error)


def resume(accounts_root: Path) -> None:
    """A person turns sending back on."""
    try:
        kill_switch(accounts_root).unlink()
    except FileNotFoundError:
        pass
    log.warning("Messages outbound delivery turned back on by the person")


class BurstGuard:
    """Deliveries faster than a person makes them trip the kill switch."""

    def __init__(self, accounts_root: Path, *, limit: int = BURST_LIMIT, window: float = BURST_WINDOW,
                 clock=time.monotonic) -> None:
        self.root, self.limit, self.window, self.clock = Path(accounts_root), limit, window, clock
        self._recent: deque[float] = deque()
        self._lock = threading.Lock()

    def admit(self) -> bool:
        with self._lock:
            now = self.clock()
            while self._recent and now - self._recent[0] > self.window:
                self._recent.popleft()
            if len(self._recent) >= self.limit:
                trip(self.root, f"more than {self.limit} deliveries in {int(self.window)} seconds")
                return False
            self._recent.append(now)
            return True
