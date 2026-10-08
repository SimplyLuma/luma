from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .apk import ApkInspection


def receipt_root() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    root = data_home / "luma-android" / "receipts"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def write_install_receipt(inspection: ApkInspection, result: str) -> Path:
    root = receipt_root()
    payload = {
        "schema": 1,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
        "apk": json.loads(inspection.to_json()),
    }
    fd, temporary = tempfile.mkstemp(prefix=".receipt-", dir=root, text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        destination = root / f"{inspection.sha256}.json"
        os.replace(temporary, destination)
        return destination
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
