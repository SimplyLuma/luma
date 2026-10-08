# SPDX-License-Identifier: Apache-2.0
"""Private, durable ICS backups before Calendar's first write of each kind."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import threading

_lock = threading.Lock()
_backed_up = set()


def ensure_backup(source_uid, kind, export, *, root=None):
    if os.environ.get("LUMA_CALENDAR_FIXTURE"):
        raise RuntimeError("Fixture mode cannot back up or access real calendars.")
    root = Path(root) if root is not None else Path(os.environ.get("XDG_STATE_HOME", str(Path.home()/".local/state"))) / "luma/calendar-backups"
    key = (str(root), source_uid, kind)
    with _lock:
        if key in _backed_up:
            return None
        text = export(source_uid)
        if "BEGIN:VCALENDAR" not in text or "END:VCALENDAR" not in text:
            raise ValueError("Calendar backup did not contain a complete calendar.")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        digest = hashlib.sha256((source_uid+":"+kind).encode()).hexdigest()[:16]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        path = root / f"{stamp}-{digest}.ics"
        descriptor = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor,"w") as output:
                output.write(text)
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        _backed_up.add(key)
        return path
