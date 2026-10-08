# SPDX-License-Identifier: Apache-2.0
"""What Depot updated, what can be taken back, and what is paused (per person).

Every app update Depot makes -- automatic or asked for -- is recorded with the
version and the exact build before and after. The Updates page lists the last
90 days as "Recently updated". Within 30 days, the most recent update of an app
can be taken back to the build it replaced, as long as its source still offers
that build. Going back pauses automatic updates for that app until a version
newer than the one it went back from is published; a person can resume them.

State lives in ``$XDG_STATE_HOME/luma/depot/app-history.json`` (0600). This
module decides and records; it installs nothing and has no GLib.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import re
import tempfile
import time

HISTORY_DAYS = 90
REVERT_DAYS = 30
MAX_ENTRIES = 500
_COMMIT = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class Entry:
    app_id: str
    name: str
    from_version: str
    to_version: str
    at: int
    automatic: bool = False
    #: The exact builds, when the source names them (Flatpak commits).
    from_commit: str = ""
    to_commit: str = ""
    #: "update" or "revert".
    kind: str = "update"
    #: What the source says is new in to_version, plain text, possibly empty.
    notes: str = ""


@dataclass(frozen=True)
class Pause:
    app_id: str
    name: str
    #: The version and build that were taken back; only something newer resumes updates.
    reverted_from_version: str
    reverted_from_commit: str
    at: int


@dataclass
class History:
    entries: list[Entry] = field(default_factory=list)
    paused: dict[str, Pause] = field(default_factory=dict)


def state_path(environment=None) -> Path:
    env = os.environ if environment is None else environment
    base = env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME", str(Path.home())), ".local/state")
    return Path(base) / "luma/depot/app-history.json"


def _text(value, limit=256) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _entry(value) -> Entry | None:
    if not isinstance(value, dict) or not _text(value.get("app_id")):
        return None
    at = value.get("at")
    if isinstance(at, bool) or not isinstance(at, int) or at <= 0:
        return None
    commit_from, commit_to = _text(value.get("from_commit"), 64), _text(value.get("to_commit"), 64)
    return Entry(
        app_id=_text(value["app_id"]), name=_text(value.get("name")) or _text(value["app_id"]),
        from_version=_text(value.get("from_version"), 64), to_version=_text(value.get("to_version"), 64),
        at=at, automatic=value.get("automatic") is True,
        from_commit=commit_from if _COMMIT.match(commit_from) else "",
        to_commit=commit_to if _COMMIT.match(commit_to) else "",
        kind="revert" if value.get("kind") == "revert" else "update",
        notes=_text(value.get("notes"), 4000))


def load(environment=None, *, now: float | None = None) -> History:
    try:
        with state_path(environment).open("rb") as stream:
            value = json.loads(stream.read(4 * 1024 * 1024).decode("utf-8"))
    except (OSError, ValueError, UnicodeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    history = History()
    for item in value.get("entries", []) if isinstance(value.get("entries"), list) else []:
        entry = _entry(item)
        if entry is not None:
            history.entries.append(entry)
    for item in value.get("paused", []) if isinstance(value.get("paused"), list) else []:
        if isinstance(item, dict) and _text(item.get("app_id")) and isinstance(item.get("at"), int):
            history.paused[item["app_id"]] = Pause(
                _text(item["app_id"]), _text(item.get("name")) or _text(item["app_id"]),
                _text(item.get("reverted_from_version"), 64), _text(item.get("reverted_from_commit"), 64),
                item["at"])
    prune(history, now=now)
    return history


def prune(history: History, *, now: float | None = None) -> None:
    cutoff = int(now if now is not None else time.time()) - HISTORY_DAYS * 86400
    history.entries = sorted((e for e in history.entries if e.at >= cutoff), key=lambda e: e.at)[-MAX_ENTRIES:]


def save(history: History, environment=None) -> None:
    path = state_path(environment)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = {"entries": [asdict(e) for e in history.entries],
            "paused": [asdict(p) for p in history.paused.values()]}
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".app-history")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=1)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def record(entry: Entry, environment=None, *, now: float | None = None) -> History:
    history = load(environment, now=now)
    history.entries.append(entry)
    prune(history, now=now)
    save(history, environment)
    return history


def recent(history: History) -> list[Entry]:
    """Newest first."""
    return sorted(history.entries, key=lambda e: e.at, reverse=True)


def revertable(history: History, app_id: str, current_commit: str, *, now: float | None = None) -> Entry | None:
    """The update that can be taken back for ``app_id``, or None.

    Only the latest change to the app, only within REVERT_DAYS, only when both
    builds are known, and only while the app is still on the build that update
    installed (a later change, made anywhere, makes the old record stale)."""
    stamp = int(now if now is not None else time.time())
    latest = next((e for e in recent(history) if e.app_id == app_id), None)
    if latest is None or latest.kind != "update" or not latest.from_commit or not latest.to_commit:
        return None
    if stamp - latest.at > REVERT_DAYS * 86400:
        return None
    if current_commit and current_commit != latest.to_commit:
        return None
    return latest


def pause(history: History, entry: Entry, environment=None, *, now: float | None = None) -> None:
    history.paused[entry.app_id] = Pause(entry.app_id, entry.name, entry.to_version, entry.to_commit,
                                         int(now if now is not None else time.time()))
    save(history, environment)


def resume(app_id: str, environment=None) -> History:
    history = load(environment)
    history.paused.pop(app_id, None)
    save(history, environment)
    return history


def _version_key(version: str):
    return [(0, int(part)) if part.isdigit() else (1, part) for part in re.split(r"[.\-+~_ ]+", version or "") if part]


def newer(candidate: str, than: str) -> bool:
    """Whether version ``candidate`` is newer than ``than``. Unknown versions are not newer."""
    if not candidate or not than or candidate == than:
        return False
    return _version_key(candidate) > _version_key(than)


def holds(history: History, app_id: str, update_version: str, update_commit: str = "") -> bool:
    """Whether a pause keeps ``app_id`` from updating to this release automatically."""
    paused = history.paused.get(app_id)
    if paused is None:
        return False
    if update_commit and update_commit == paused.reverted_from_commit:
        return True
    return not newer(update_version, paused.reverted_from_version)


def lift_outdated_pauses(history: History, pending: dict[str, tuple[str, str]], environment=None) -> list[str]:
    """Resume apps whose source now offers something newer than what was taken back.

    ``pending`` maps app id to (update version, update commit). Returns the app ids resumed."""
    lifted = [app_id for app_id, (version, commit) in pending.items()
              if app_id in history.paused and not holds(history, app_id, version, commit)]
    for app_id in lifted:
        history.paused.pop(app_id, None)
    if lifted:
        save(history, environment)
    return lifted
