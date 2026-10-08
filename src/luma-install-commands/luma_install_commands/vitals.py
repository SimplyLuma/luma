# SPDX-License-Identifier: MPL-2.0
"""One journal entry per system software change, for Luma Vitals.

Every install, removal and update started from dnf, yum, apt or snap on Luma
is written to the system journal with a catalogued MESSAGE_ID and the fields
Luma Vitals uses (``LUMA_VITALS_KIND``, ``LUMA_VITALS_DETAILS``), so
``journalctl MESSAGE_ID=a08437fa35fc4db9a55f26f08c3e9847`` lists every change
with who asked for it. Logging never blocks the change.
"""

from __future__ import annotations

import json
import os
import subprocess

MESSAGE_ID = "a08437fa35fc4db9a55f26f08c3e9847"
LOGGER = "/usr/bin/logger"


def log(action: str, details: dict, runner=subprocess.run) -> None:
    person = os.environ.get("SUDO_USER") or os.environ.get("USER") or str(os.getuid())
    packages = details.get("packages") or []
    result = details.get("result", "")
    summary = f"{person} ran {details.get('command', action)}"
    if packages:
        summary += " for " + ", ".join(str(p) for p in packages)
    if result:
        summary += f": {result}"
    fields = {
        "MESSAGE": summary,
        "MESSAGE_ID": MESSAGE_ID,
        "SYSLOG_IDENTIFIER": "luma-install-commands",
        "PRIORITY": "3" if result == "failed" else "5",
        "LUMA_VITALS_KIND": "system-software-change",
        "LUMA_VITALS_UNIT": f"{action}",
        "LUMA_VITALS_DETAILS": json.dumps({"action": action, "person": person, **details}, sort_keys=True),
        "LUMA_SOFTWARE_ACTION": action,
        "LUMA_SOFTWARE_PACKAGES": " ".join(str(p) for p in packages),
        "LUMA_SOFTWARE_RESULT": result,
    }
    payload = "".join(f"{key}={str(value).replace(chr(10), ' ')}\n" for key, value in fields.items())
    try:
        runner([LOGGER, "--journald"], input=payload, text=True, stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass
