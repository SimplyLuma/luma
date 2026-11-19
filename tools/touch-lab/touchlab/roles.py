# SPDX-License-Identifier: Apache-2.0
"""Contact roles: what each contact is doing, not just keep or drop.

The model labels every contact with one role. What the system does with a
contact follows from its role (ACTIONS), so a resting thumb neither moves
the pointer nor breaks a scroll, and a pressing thumb still clicks.
"""

from __future__ import annotations

ROLES = ("point", "second", "thumb-rest", "thumb-click", "palm", "brush")
INDEX = {r: i for i, r in enumerate(ROLES)}

NAMES = {
    "point": "Pointing finger",
    "second": "Second finger",
    "thumb-rest": "Resting thumb",
    "thumb-click": "Clicking thumb",
    "palm": "Palm",
    "brush": "Brush",
}
DESCRIPTIONS = {
    "point": "The finger that moves the pointer, taps or drags",
    "second": "A finger joining the pointing finger: scroll, pinch, two-finger tap, swipe",
    "thumb-rest": "A thumb (or a holding finger on a screen) resting while other fingers work",
    "thumb-click": "A thumb pressing the pad to click",
    "palm": "A palm or the heel of the hand",
    "brush": "A graze, knuckle or accidental brush",
}
# Touchscreens have no click and the thumb is often the pointing digit.
NAMES_TOUCHSCREEN = {**NAMES, "point": "Touching finger", "thumb-rest": "Holding finger",
                     "thumb-click": "Pressing thumb"}

# What libinput is asked to do with each role (plugins/luma-touch-roles.lua).
#   track:  the touch moves the pointer / joins gestures
#   count:  it counts for clickfinger (1 finger = left, 2 = right) and taps
#   button: a physical or haptic click while it is down still clicks
ACTIONS = {
    "point": {"track": True, "count": True, "button": True},
    "second": {"track": True, "count": True, "button": True},
    "thumb-rest": {"track": False, "count": False, "button": True},
    "thumb-click": {"track": False, "count": False, "button": True},
    "palm": {"track": False, "count": False, "button": False},
    "brush": {"track": False, "count": False, "button": False},
}
# Roles libinput must not track: the plugin sets MT_TOOL_PALM on their slot.
# libinput then drops them from motion, taps, gestures and the clickfinger
# count (max(1, n) keeps a lone pressing thumb a left click), and releases
# them again if the role changes back (tool-palm is reversible).
SUPPRESS = frozenset(r for r, a in ACTIONS.items() if not a["track"])
INTENDED = frozenset(("point", "second", "thumb-click"))

# Colours in the live test view (LumaUI roles).
COLOURS = {
    "point": "luma_accent",
    "second": "luma_green",
    "thumb-rest": "luma_violet",
    "thumb-click": "luma_amber",
    "palm": "luma_red",
    "brush": "luma_ink",
}

# Phase 1 labels mapped to roles (multi-finger labels: the first finger down
# points, the rest are second fingers; see from_label()).
FROM_LABEL = {
    "pointer": "point", "precision": "point", "tap": "point", "click": "point", "drag-finger": "point",
    "scroll": "second", "pinch": "second", "tap-2": "second", "swipe-3": "second", "swipe-4": "second",
    "click-thumb": "thumb-click", "drag-thumb": "thumb-click",
    "thumb-rest": "thumb-rest", "thumb-drop": "thumb-rest",
    "palm-typing": "palm", "palm-edge": "palm", "hand-rest": "palm",
    "graze": "brush",
}
MULTI_FINGER = {"scroll", "pinch", "tap-2", "swipe-3", "swipe-4"}


def from_label(label: str | None, first_of_group: bool = False) -> str | None:
    if not label:
        return None
    if label in ROLES:
        return label
    role = FROM_LABEL.get(label)
    if role == "second" and first_of_group:
        return "point"
    return role


def label_class(role: str) -> str:
    return "intended" if role in INTENDED else "accidental"


def name(role: str, surface_class: str = "") -> str:
    return (NAMES_TOUCHSCREEN if surface_class == "touchscreen" else NAMES).get(role, role)
