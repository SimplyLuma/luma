"""Transient network faults, and the words a person can act on.

A name server that blinks must not end an installation the person already
approved: a fetch that fails this way is retried a couple of times with a short
backoff before anything is said. When it still fails, what reaches the screen is
a sentence about this computer's network — not a GIO error string.

Nothing here logs or repeats a package's contents, a URL's query, or any
credential: classification reads the message, and the message shown is ours.
"""
from __future__ import annotations

import time
from typing import Callable

# Two retries after the first attempt: enough for a name server that blinks,
# short enough that a genuinely offline machine is told quickly.
ATTEMPTS = 3
BACKOFF = (1.0, 3.0)

# Domains whose errors are, by construction, about reaching the other end.
_TRANSIENT_DOMAINS = {"g-resolver-error-quark"}

_TRANSIENT_TEXT = (
    "could not resolve",
    "couldn't resolve",
    "resolve host",
    "name or service not known",
    "temporary failure in name resolution",
    "no address associated with hostname",
    "nodename nor servname provided",
    "network is unreachable",
    "network is down",
    "no route to host",
    "connection refused",
    "connection reset",
    "connection timed out",
    "connection closed",
    "timed out",
    "timeout was reached",
    "operation timed out",
    "temporary failure",
    "try again",
    "temporarily unavailable",
    "failed to connect",
    "unable to connect",
    "server returned status 429",
    "server returned status 50",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway",
    "transfer closed",
    "recv failure",
    "send failure",
    "ssl connect error",
    "i/o error",
)

_NAME_TEXT = ("resolve", "name or service not known", "name resolution",
              "no address associated with hostname")


def error_text(error: object) -> str:
    """The comparable text of an exception, GLib.Error included."""
    message = getattr(error, "message", None)
    if not isinstance(message, str) or not message:
        message = str(error)
    return message


def is_transient(error: object) -> bool:
    """True when retrying the same fetch is worth doing before saying anything."""
    domain = getattr(error, "domain", "")
    if isinstance(domain, str) and domain in _TRANSIENT_DOMAINS:
        return True
    text = error_text(error).casefold()
    return any(marker in text for marker in _TRANSIENT_TEXT)


def friendly(error: object, source: str = "the application source") -> str:
    """An actionable sentence for a failed fetch, never a raw backend string."""
    text = error_text(error).casefold()
    domain = getattr(error, "domain", "")
    if (isinstance(domain, str) and domain in _TRANSIENT_DOMAINS) or any(m in text for m in _NAME_TEXT):
        return (f"Luma could not look up {source} on the network. "
                "Check this computer's network connection, then try again.")
    if is_transient(error):
        return (f"Luma could not reach {source}. "
                "The connection did not hold. Check the network, then try again.")
    return ""


def retrying(work: Callable[[], object], *, attempts: int = ATTEMPTS,
             backoff: tuple[float, ...] = BACKOFF, sleep: Callable[[float], None] | None = None,
             transient: Callable[[object], bool] = is_transient,
             before_retry: Callable[[int, BaseException], None] | None = None):
    """Run `work`, retrying only faults that a second attempt can fix.

    `work` must be safe to run again: this wraps fetches and resolvable
    metadata reads, never a step that has already changed the system.
    """
    pause = sleep if sleep is not None else time.sleep
    last: BaseException | None = None
    for attempt in range(max(1, attempts)):
        try:
            return work()
        except Exception as error:  # noqa: BLE001 - classified immediately below
            if not transient(error) or attempt == max(1, attempts) - 1:
                raise
            last = error
            if before_retry is not None:
                before_retry(attempt + 1, error)
            pause(backoff[min(attempt, len(backoff) - 1)])
    raise last  # pragma: no cover - loop either returns or raises
