#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Construct and map the real Luma window for package/runtime qualification."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib

from charlie_luma.application import CharlieApplication


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="charlie-runtime-") as directory:
        os.environ["XDG_DATA_HOME"] = directory
        application = CharlieApplication(data_home=Path(directory), seed_demo=True)
        application.did_initial_sync = True
        original_window = {"value": None}

        def inspect_reactivation() -> bool:
            window = application.window
            if window is None or window is not original_window["value"]:
                raise RuntimeError("close replaced or destroyed the primary window")
            application.activate()
            if application.window is not window:
                raise RuntimeError("activation did not reuse the resident window")
            application.quit()
            return GLib.SOURCE_REMOVE

        def inspect() -> bool:
            window = application.window
            if window is None or not window.get_mapped():
                raise RuntimeError("Charlie window did not map")
            if window.get_title() != "Charlie":
                raise RuntimeError("Charlie window identity is missing")
            if window.message_list.get_row_at_index(0) is None:
                raise RuntimeError("Demo mailbox did not render")
            if window.current_conversation is None:
                raise RuntimeError("Conversation selection did not reach reader")
            original_window["value"] = window
            window.close()
            GLib.timeout_add(200, inspect_reactivation)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(900, inspect)
        return application.run(["org.projectluma.Charlie"])


if __name__ == "__main__":
    raise SystemExit(main())
