# SPDX-License-Identifier: Apache-2.0
"""Parse `libinput debug-events --verbose` output into decisions.

Event lines ("POINTER_MOTION", "GESTURE_SWIPE_BEGIN", ...) say what libinput
did; debug lines ("palm: touch 0 ... palm detected (edge)", "thumb: touch
1, THUMB_STATE_FINGER → THUMB_STATE_SUPPRESSED", "tap: ...") say why. Debug
lines name libinput's touch index, which is the evdev slot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

EVENT = re.compile(r"^\s*-?(event\d+)\s+([A-Z_]+)\s+(?:(\d+)\s+)?([+-]?\d+\.\d+)s\s*(.*)$")
DEBUG = re.compile(r"^\s*(?:\.\.\.|\d+:)?\s*(event\d+)\s+-\s+(.*)$")
TOUCH = re.compile(r"touch (-?\d+)")
STATE = re.compile(r"(\w+_STATE_\w+)\s*→\s*(?:\w+_EVENT_\w+\s*→\s*)?(\w+_STATE_\w+)")
PALM = re.compile(r"palm: touch (\d+) \((\w+)\), palm detected \(([^)]+)\)")
PALM_END = re.compile(r"palm: touch (\d+) \((\w+)\), palm (?:released|ended|cleared)(?: \(([^)]*)\))?")
THUMB = re.compile(r"thumb: touch (\d+), (THUMB_STATE_\w+) → (THUMB_STATE_\w+)")
NUM = r"[+-]?\s*\d+\.\d+"
MOTION = re.compile(rf"({NUM})/\s*({NUM})")
SCROLL = re.compile(rf"vert\s+({NUM})/[^ ]*\s+horiz\s+({NUM})")
BUTTON = re.compile(r"(BTN_\w+) \((\d+)\) (pressed|released)")


@dataclass
class Decision:
    t: int                 # recorder arrival time (ns, CLOCK_MONOTONIC)
    kind: str              # motion, scroll, button, gesture, palm, thumb, tap, dwt, hold, ...
    device: str
    touch: int | None = None
    detail: dict = field(default_factory=dict)
    text: str = ""


def parse_line(t: int, text: str) -> Decision | None:
    m = EVENT.match(text)
    if m:
        device, name, _repeat, rel, rest = m.groups()
        d = Decision(t, name.lower(), device, text=text, detail={"rel_s": float(rel)})
        if name == "POINTER_MOTION":
            mm = MOTION.search(rest)
            if mm:
                d.detail.update(dx=float(mm.group(1).replace(" ", "")), dy=float(mm.group(2).replace(" ", "")))
            d.kind = "motion"
        elif name.startswith("POINTER_SCROLL"):
            sm = SCROLL.search(rest)
            if sm:
                d.detail.update(v=float(sm.group(1).replace(" ", "")), h=float(sm.group(2).replace(" ", "")))
            d.kind = "scroll"
        elif name == "POINTER_BUTTON":
            bm = BUTTON.search(rest)
            if bm:
                d.detail.update(button=bm.group(1), pressed=bm.group(3) == "pressed")
            d.kind = "button"
        elif name.startswith("GESTURE_"):
            parts = name.split("_")             # GESTURE_SWIPE_BEGIN
            d.kind = "gesture"
            d.detail.update(gesture=parts[1].lower(), phase=parts[2].lower(),
                            fingers=int(rest.split()[0]) if rest.split() and rest.split()[0].isdigit() else None,
                            cancelled="cancelled" in rest)
        elif name in ("DEVICE_ADDED", "DEVICE_REMOVED"):
            d.kind = name.lower()
            d.detail["text"] = rest
        return d
    m = DEBUG.match(text)
    if not m:
        return None
    device, body = m.groups()
    pm = PALM.search(body)
    if pm:
        return Decision(t, "palm", device, int(pm.group(1)),
                        {"phase": pm.group(2), "reason": pm.group(3)}, text)
    pe = PALM_END.search(body)
    if pe:
        return Decision(t, "palm-end", device, int(pe.group(1)), {"phase": pe.group(2)}, text)
    th = THUMB.search(body)
    if th:
        return Decision(t, "thumb", device, int(th.group(1)), {"from": th.group(2), "to": th.group(3)}, text)
    if "is speed-based thumb" in body or "is a thumb" in body:
        tm = TOUCH.search(body)
        return Decision(t, "thumb-why", device, int(tm.group(1)) if tm else None, {"why": body}, text)
    if body.startswith("tap:"):
        tm = TOUCH.search(body)
        st = STATE.search(body)
        return Decision(t, "tap", device, int(tm.group(1)) if tm else None,
                        {"from": st.group(1) if st else None, "to": st.group(2) if st else None,
                         "event": (re.search(r"TAP_EVENT_\w+", body) or [None])[0]}, text)
    if body.startswith("gesture:"):
        st = STATE.search(body)
        return Decision(t, "gesture-state", device, None,
                        {"to": st.group(2) if st else None,
                         "fingers": (re.search(r"\[(\d)fg\]", body) or [None, None])[1]}, text)
    if "dwt" in body or "typing" in body:
        return Decision(t, "dwt", device, None, {"text": body}, text)
    if body.startswith("button state:"):
        tm = TOUCH.search(body)
        to = re.search(r"to (BUTTON_STATE_\w+)", body)
        return Decision(t, "button-state", device, int(tm.group(1)) if tm else None,
                        {"to": to.group(1) if to else None}, text)
    if "jump" in body:
        tm = TOUCH.search(body)
        return Decision(t, "jump", device, int(tm.group(1)) if tm else None, {"text": body}, text)
    return Decision(t, "log", device, None, {"text": body}, text)


def parse(records: list[list], tag: str = "l") -> list[Decision]:
    out = []
    for r in records:
        if r[1] != tag:
            continue
        d = parse_line(r[0], r[2])
        if d:
            out.append(d)
    return out


def event_time_offset(decisions: list[Decision]) -> int | None:
    """libinput's own clock (relative ms) against arrival; the smallest gap is the true offset."""
    best = None
    for d in decisions:
        rel = d.detail.get("rel_s")
        if rel is None:
            continue
        off = d.t - int(rel * 1e9)
        best = off if best is None or off < best else best
    return best


SUPPRESSING_THUMB = {"THUMB_STATE_SUPPRESSED", "THUMB_STATE_JAILED", "THUMB_STATE_PINCH",
                     "THUMB_STATE_REVIVED_JAILED"}
