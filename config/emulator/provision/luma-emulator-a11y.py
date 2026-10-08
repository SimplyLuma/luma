#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Dump the guest's accessibility tree as JSON.

This reads the same AT-SPI tree a screen reader would, which is the only
honest way to check that Luma's components expose real names, roles and
relationships rather than looking correct in a screenshot.
"""

from __future__ import annotations

import json
import sys

try:
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
except Exception as error:  # pragma: no cover - reported to the caller as JSON
    print(json.dumps({"error": f"AT-SPI is unavailable in this guest: {error}"}))
    sys.exit(1)


def node(accessible, depth: int, limit: int) -> dict:
    entry = {
        "name": accessible.get_name(),
        "role": accessible.get_role_name(),
        "description": accessible.get_description(),
    }
    try:
        states = accessible.get_state_set()
        entry["states"] = sorted(
            state.value_nick
            for state in (
                Atspi.StateType.ENABLED,
                Atspi.StateType.FOCUSABLE,
                Atspi.StateType.FOCUSED,
                Atspi.StateType.SHOWING,
                Atspi.StateType.SENSITIVE,
            )
            if states.contains(state)
        )
    except Exception:
        entry["states"] = []
    if depth < limit:
        children = []
        for index in range(accessible.get_child_count()):
            child = accessible.get_child_at_index(index)
            if child is not None:
                children.append(node(child, depth + 1, limit))
        if children:
            entry["children"] = children
    return entry


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    Atspi.init()
    desktop = Atspi.get_desktop(0)
    applications = []
    for index in range(desktop.get_child_count()):
        application = desktop.get_child_at_index(index)
        if application is None:
            continue
        name = application.get_name()
        if not name:
            continue
        applications.append(node(application, 0, limit))
    print(json.dumps({"applications": applications}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
