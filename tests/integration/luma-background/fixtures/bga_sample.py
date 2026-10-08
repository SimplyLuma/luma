# SPDX-License-Identifier: MPL-2.0
"""A sample kit app with a background agent, as a third-party developer writes one.

The same executable is the app (no --agent) and its agent (--agent). The agent
counts once a second, publishes the count, records every wake, feeds a Live
Extension from its own values, and can be told to misbehave (crash, exhaust
memory) through a file, so the integration test can watch the system react.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from luma_appkit.background import (
    Agent, LiveExtensionBinding, from_agent, request_background, run_agent,
)

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

CONTROL = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "bga-control"


class SampleAgent(Agent):
    def on_start(self):
        self.count = 0
        self.publish("count", 0)
        self.publish("pid", os.getpid())
        self.publish("wakes", [])
        self.extension = LiveExtensionBinding(
            self,
            extension_id=self.app_id + ".Count",
            category="generic",
            title=self.app_id.rsplit(".", 1)[-1],
            subtitle=lambda values: f"{values['count']} so far",
            progress=from_agent(self.agent_id, "fraction"),
            privacy="public",
            expires_in=timedelta(minutes=5),
        )
        GLib.timeout_add(1000, self.tick)

    def tick(self):
        self.count += 1
        self.publish("count", self.count)
        self.publish("fraction", (self.count % 100) / 100)
        publisher = self.extension.publisher
        self.publish("live-extension", bool(publisher and publisher.publication_id))
        name = self.app_id.rsplit(".", 1)[-1].lower()
        order = CONTROL / f"{name}.order"
        if order.exists():
            command = order.read_text().strip()
            order.unlink()
            if command == "crash":
                os.kill(os.getpid(), 11)
            elif command == "hog":
                # One allocation straight past memory.max plus swap.max.
                self.hog = b"\x01" * (320 * 1024 * 1024)
            elif command == "quit":
                self.quit(0)
        return GLib.SOURCE_CONTINUE

    def on_wake(self, wake):
        record = f"{wake.reason}:{wake.schedule}:{int(wake.missed)}"
        self.publish("wakes", [*self.values.get("wakes", []), record])
        if wake.reason == "login" and self.app_id.endswith(".Chatter"):
            self.schedule("soon", at=datetime.now(timezone.utc) + timedelta(seconds=20))


def app_main(app_id: str) -> int:
    """The app's "window": ask for background activity, then stay open until killed."""

    loop = GLib.MainLoop()
    result = {}

    def answered(outcome):
        result["outcome"] = outcome
        print(f"request_background: allowed={outcome.allowed} autostart={outcome.autostart} "
              f"response={outcome.response}", flush=True)

    request_background(app_id, reason="Count while closed", autostart=True, callback=answered)
    GLib.timeout_add_seconds(600, loop.quit)
    loop.run()
    return 0


def main(app_id: str) -> int:
    agent_class = type("Agent", (SampleAgent,), {"app_id": app_id, "agent_id": app_id + ".Agent"})
    status = run_agent(agent_class)
    if status is not None:
        return status
    return app_main(app_id)
