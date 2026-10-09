# SPDX-License-Identifier: Apache-2.0
"""What the agent remembers between runs, in ``/var/lib/luma-update``.

* ``wariness``: this device's place in staged rollouts, a random number in
  [0, 1) created once. It never leaves the computer.
* ``state.json``: the newest graph time seen per channel, the pending update
  transition (so the next boot can tell booted from rolled back), commits not
  to retry, the channel a person asked for, a queue of anonymous reports, the
  countme windows, and the last error. No identifiers of any kind.

Writes are atomic (write, fsync, rename) so a power cut leaves either the old
or the new file, never half of one.
"""

from __future__ import annotations

import contextlib
import copy
import fcntl
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading

__all__ = ("StateStore", "load_wariness", "atomic_write", "mark_active", "mark_permanent", "barrier_retry_at",
           "barrier_retry_due")

MAX_DO_NOT_RETRY = 64
MAX_QUEUED_REPORTS = 50

#: A commit that failed its health checks and was rolled back is never offered
#: again (unless it is a barrier, see ``barrier_retry_at``). Every other mark --
#: a staged update lost before it finalized, a person going back, a person
#: booting the previous entry -- is not evidence the release is broken, so it
#: expires after a week.
PERMANENT_REASONS = ("rolled-back-after-failed-boot",)
RETRY_MARK_SECONDS = 7 * 24 * 3600
#: A barrier must be installed before anything newer, so even a permanent mark
#: on it only delays the next attempt: one day after the first failure, three
#: after the second, a week after every later one. A person's own rollback of a
#: barrier waits the whole week.
BARRIER_BACKOFF_SECONDS = (24 * 3600, 3 * 24 * 3600, 7 * 24 * 3600)

DEFAULT_STATE = {
    "version": 1,
    "graph_generated_at": {},      # channel -> epoch seconds of the newest verified graph
    "last_check": 0,               # epoch seconds of the last successful check
    "last_attempt": 0,             # epoch seconds of the last check attempt
    "last_error": "",
    "last_error_class": "",
    "channel_chosen_by_person": False,
    "channel_default_policy": "",
    "requested_channel": "",       # a channel the person asked for, until reached
    "switch_now": False,
    "pending": None,               # {from_version, from_commit, to_version, to_commit, channel, kind, staged_at}
    "do_not_retry": [],            # [{commit, version, reason, at, attempts, expires_at (0: permanent)}]
    "finalize_failures": {},       # commit -> times a staged commit never became a deployment
    "rollback_notice": None,       # {from_version, to_version, at, id}
    "rollback_restart": None,      # {from_commit, to_commit}: the rollback Rollback() made, for Apply()
    "reports": [],                 # queued anonymous events
    "countme": {"first_window": None, "counted_window": None},
    "available": None,             # last selected target, for status after restart
    "kargs_added": [],             # kernel arguments this agent added from kargs.d, on the booted system
    "automatic_download": None,    # the person's choice; None follows update.conf's default
    "ignored": None,               # {version, commit, at}: a version whose reminders are off
    "last_check_reason": "",       # why the last check ended as it did (a graph decision reason)
    "graph_signature": None,       # {key_id, at, channel} of the last graph signature that verified
    "adopting": False,             # a person asked an unmanaged computer to follow requested_channel
    "booted_display_name": None,   # {commit, name}: the graph's name for the booted release (presentation only)
    "removed_packages": None,
    "kept_packages": None,         # {to_version, packages}: added packages newer than the release kept
    "attempt_booted": "",          # base commit booted when last_attempt/last_error were recorded      # {to_version, packages}: added packages the last staged release ships itself
}


def atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        try:
            directory = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def load_wariness(path: Path) -> float:
    """Read this device's wariness, creating it on first use."""
    try:
        value = float(path.read_text(encoding="ascii").strip())
        if 0.0 <= value < 1.0:
            return value
    except (OSError, ValueError, UnicodeDecodeError):
        pass
    # 53 random bits give a uniform double in [0, 1).
    value = secrets.randbits(53) / float(1 << 53)
    atomic_write(path, f"{value!r}\n".encode("ascii"), 0o600)
    return value


def _count(value, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default


def mark_permanent(mark: dict) -> bool:
    expires = mark.get("expires_at")
    if isinstance(expires, (int, float)) and not isinstance(expires, bool):
        return expires == 0
    # Written by 1.0.0-1.luma.1/.2, before marks expired: keep the old meaning
    # only for a failed health check.
    return mark.get("reason") in PERMANENT_REASONS


def _mark_time(mark: dict) -> float:
    at = mark.get("at")
    return float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else 0.0


def mark_active(mark: dict, now: float) -> bool:
    if mark_permanent(mark):
        return True
    at = _mark_time(mark)
    expires = mark.get("expires_at")
    if not isinstance(expires, (int, float)) or isinstance(expires, bool) or expires <= 0:
        expires = at + RETRY_MARK_SECONDS
    # A mark dated after now was made while the clock ran ahead; it has expired.
    return at <= now < expires


def barrier_retry_at(mark: dict) -> float:
    """When a marked barrier may be tried again."""
    at = _mark_time(mark)
    if mark.get("reason") == "rolled-back-by-person":
        return at + RETRY_MARK_SECONDS
    attempts = _count(mark.get("attempts"), 1)
    return at + BARRIER_BACKOFF_SECONDS[min(attempts, len(BARRIER_BACKOFF_SECONDS)) - 1]


def barrier_retry_due(mark: dict, now: float) -> bool:
    at = _mark_time(mark)
    return now < at or now >= barrier_retry_at(mark)


#: One lock for every StateStore in the process. The daemon runs operations on
#: worker threads while the main loop answers D-Bus calls, and a store's
#: in-memory copy is shared between them; flock only separates processes.
_PROCESS_LOCK = threading.RLock()


class StateStore:
    """The state file. Mutate only inside ``with store.locked():``.

    The daemon, the greenboot hooks and the boot reconciler are separate
    processes; the lock reloads the file on entry and saves it on a clean exit,
    so none of them overwrites another's change. Within a process, threads are
    serialized by a process-wide lock held for the whole locked section, and
    nesting is counted per thread, so a second thread always waits for the
    first to save rather than joining its section.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._local = threading.local()
        with _PROCESS_LOCK:
            self.data = self._load()

    @property
    def _depth(self) -> int:
        return getattr(self._local, "depth", 0)

    @contextlib.contextmanager
    def locked(self):
        with _PROCESS_LOCK:
            depth = self._depth
            if depth:
                self._local.depth = depth + 1
                try:
                    yield self
                finally:
                    self._local.depth = depth
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path.parent / ".state.lock", "a+b") as lock:
                os.chmod(lock.name, 0o600)
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    self.data = self._load()
                    self._local.depth = 1
                    try:
                        yield self
                        self.save()
                    finally:
                        self._local.depth = 0
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def reload(self) -> None:
        """Re-read the file, unless this thread is inside a locked section
        (whose copy is already current and not yet saved)."""
        with _PROCESS_LOCK:
            if not self._depth:
                self.data = self._load()

    def snapshot(self) -> dict:
        """A private copy of the current state, safe to read on any thread."""
        with _PROCESS_LOCK:
            if not self._depth:
                self.data = self._load()
            return copy.deepcopy(self.data)

    def _load(self) -> dict:
        state = copy.deepcopy(DEFAULT_STATE)
        try:
            with self.path.open("rb") as stream:
                loaded = json.loads(stream.read(4 * 1024 * 1024).decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return state
        if isinstance(loaded, dict):
            for key, default in DEFAULT_STATE.items():
                value = loaded.get(key, default)
                if default is None or isinstance(value, type(default)) or (
                        isinstance(default, int) and isinstance(value, (int, float)) and not isinstance(value, bool)):
                    state[key] = value
        return state

    def save(self) -> None:
        atomic_write(self.path, (json.dumps(self.data, indent=2, sort_keys=True) + "\n").encode("utf-8"), 0o600)

    # ── Convenience accessors ────────────────────────────────────────────

    def graph_seen(self, channel: str) -> float | None:
        value = self.data["graph_generated_at"].get(channel)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def record_graph(self, channel: str, generated_at: float) -> None:
        current = self.graph_seen(channel)
        if current is None or generated_at > current:
            self.data["graph_generated_at"][channel] = generated_at

    def retry_marks(self) -> list[dict]:
        return [item for item in self.data["do_not_retry"]
                if isinstance(item, dict) and isinstance(item.get("commit"), str)]

    def retry_mark(self, commit: str) -> dict | None:
        return next((item for item in self.retry_marks() if item["commit"] == commit), None)

    def do_not_retry(self, now: float) -> frozenset[str]:
        """Commits not to offer at ``now``: permanent marks and unexpired ones."""
        return frozenset(item["commit"] for item in self.retry_marks() if mark_active(item, now))

    def add_do_not_retry(self, commit: str, version: str, reason: str, at: float) -> None:
        """Mark a commit. Only a failed health check marks it for good; every
        other reason expires after ``RETRY_MARK_SECONDS``. Marking a commit
        again counts the attempt and never shortens a permanent mark."""
        previous = self.retry_mark(commit)
        permanent = reason in PERMANENT_REASONS or bool(previous and mark_permanent(previous))
        attempts = (_count(previous.get("attempts"), 1) + 1) if previous else 1
        entries = [item for item in self.retry_marks() if item["commit"] != commit and mark_active(item, at)]
        entries.append({"commit": commit, "version": version, "reason": reason, "at": int(at),
                        "attempts": attempts, "expires_at": 0 if permanent else int(at) + RETRY_MARK_SECONDS})
        self.data["do_not_retry"] = entries[-MAX_DO_NOT_RETRY:]

    def remove_do_not_retry(self, commit: str, reason: str | None = None) -> None:
        self.data["do_not_retry"] = [item for item in self.retry_marks()
                                     if not (item["commit"] == commit and (reason is None or item.get("reason") == reason))]

    def queue_report(self, report: dict) -> None:
        reports = [item for item in self.data["reports"] if isinstance(item, dict)]
        reports.append(report)
        self.data["reports"] = reports[-MAX_QUEUED_REPORTS:]
