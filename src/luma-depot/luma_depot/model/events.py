# SPDX-License-Identifier: Apache-2.0
"""Anonymous install counts (Depot platform plan, decision 7).

After an install, update or uninstall finishes, Depot posts
{app_id, version, arch, event, form} to the Hub. No account, no device id; the
Hub keeps no address. It is fire and forget: a failed post changes nothing
for the person, and nothing is retried or queued.
"""
from __future__ import annotations

import json
import os
import platform
import threading
import urllib.request

__all__ = ["ENDPOINT", "payload", "send"]

ENDPOINT = "https://hub.simplyluma.com/api/depot/events"
EVENTS = ("install", "update", "uninstall")


def payload(app_id: str, version: str, event: str, form: str) -> dict:
    arch = {"arm64": "aarch64", "amd64": "x86_64"}.get(platform.machine().lower(), platform.machine().lower())
    return {"app_id": app_id, "version": version, "arch": arch, "event": event, "form": form}


def send(item, event: str) -> None:
    """Post one event for `item` (an Item) in the background; never in a sample or a test run."""
    if event not in EVENTS or os.environ.get("LUMA_DEPOT_FIXTURE") or os.environ.get("LUMA_DEPOT_PREVIEW") \
            or os.environ.get("LUMA_DEPOT_NO_EVENTS") == "1":
        return
    from luma_appkit import lumaui
    form = "tv" if lumaui.tv_posture() else "phone" if lumaui.mobile_form_factor() else "desktop"
    app = getattr(item, "app", None)
    body = json.dumps(payload(getattr(app, "flatpak_id", "") or item.id, getattr(app, "version", "") or "",
                              event, form)).encode()

    def post() -> None:
        request = urllib.request.Request(ENDPOINT, data=body, method="POST",
                                         headers={"Content-Type": "application/json", "User-Agent": "Luma Depot"})
        try:
            urllib.request.urlopen(request, timeout=10).close()
        except Exception:  # noqa: BLE001 - counting must never get in the way
            pass

    threading.Thread(target=post, daemon=True).start()
