# SPDX-License-Identifier: Apache-2.0
"""Guided scenarios: the prompt, and the rule that names each contact.

A rule sees the contacts of one trial and returns {track key: label}. Rules
are deterministic, so assignments can be recomputed from a recording if a
rule is improved. Contacts a rule can't place stay unlabelled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from .contacts import Track


@dataclass(frozen=True)
class Scenario:
    id: str
    intent: str              # intended or accidental
    title: str
    detail: str
    rule: str
    typing: bool = False
    seconds: float = 6.0     # longest a trial waits before it ends itself


SCENARIOS: list[Scenario] = [
    Scenario("point", "intended", "Move the pointer", "One finger, anywhere, as you normally would.", "all:pointer"),
    Scenario("precision", "intended", "Hit the small target", "Put the pointer on the dot and hold still.", "all:precision"),
    Scenario("scroll", "intended", "Scroll with two fingers", "Up or down, a normal scroll.", "moving:scroll"),
    Scenario("pinch", "intended", "Pinch", "Two fingers apart or together, as if zooming.", "moving:pinch"),
    Scenario("tap", "intended", "Tap once", "A single one-finger tap.", "short:tap"),
    Scenario("tap-2", "intended", "Tap with two fingers", "Both fingers together, once.", "short:tap-2"),
    Scenario("click-thumb", "intended", "Click with your thumb", "Press the pad with your thumb near the bottom.", "click:click-thumb"),
    Scenario("click-drag", "intended", "Click and drag with your thumb",
             "Hold the click with your thumb and move the pointer with a finger.", "clickdrag"),
    Scenario("swipe-3", "intended", "Swipe with three fingers", "Any direction.", "all:swipe-3"),
    Scenario("swipe-4", "intended", "Swipe with four fingers", "Any direction.", "all:swipe-4"),
    Scenario("thumb-rest-scroll", "accidental", "Scroll with your thumb resting",
             "Leave your thumb on the pad and scroll with two fingers.", "rest:thumb-rest:scroll"),
    Scenario("thumb-rest-point", "accidental", "Point with your thumb resting",
             "Leave your thumb on the pad and move the pointer with a finger.", "rest:thumb-rest:pointer"),
    Scenario("palm-typing", "accidental", "Type with your palms down",
             "Type in the box and let your palms rest the way they usually do.", "all:palm-typing", True, 8.0),
    Scenario("thumb-drop", "accidental", "Let your thumb drop mid-drag",
             "Start a drag with one finger, then let your thumb land on the pad.", "drop"),
    Scenario("graze-typing", "accidental", "Type and brush the pad",
             "Type, and lightly brush the pad as if by accident.", "all:graze", True, 8.0),
    Scenario("palm-edge-reach", "accidental", "Reach for a far key",
             "Reach for a key at the edge of the keyboard so your palm's edge meets the pad.", "all:palm-edge", True),
    Scenario("hand-rest", "accidental", "Rest your hand between bursts",
             "Type, put your hand down on the pad for a moment, then type again.", "all:hand-rest", True, 8.0),
]
BY_ID = {s.id: s for s in SCENARIOS}


def _speed(tr: Track) -> float:
    ss = tr.samples
    if len(ss) < 2:
        return 0.0
    path = sum(math.hypot(b.x - a.x, b.y - a.y) for a, b in zip(ss, ss[1:]))
    return path / max(1e-3, (ss[-1].t - ss[0].t) / 1e9)


def _still(tr: Track) -> float:
    ss = tr.samples
    if not ss:
        return 0.0
    return max(math.hypot(s.x - ss[0].x, s.y - ss[0].y) for s in ss)


def assign(rule: str, tracks: list[Track]) -> tuple[dict[str, str], str]:
    """Labels for one trial's contacts, and "ok" or why the trial looked wrong."""
    tracks = [t for t in tracks if t.samples]
    if not tracks:
        return {}, "no contact"
    kind, *args = rule.split(":")
    out: dict[str, str] = {}
    if kind == "all":
        return {t.key: args[0] for t in tracks}, "ok"
    if kind == "moving":
        movers = [t for t in tracks if _still(t) > 3.0]
        for t in movers:
            out[t.key] = args[0]
        return out, "ok" if len(movers) == 2 else f"{len(movers)} moving contacts"
    if kind == "short":
        short = [t for t in tracks if t.duration_ms < 350 and _still(t) < 3.0]
        for t in short:
            out[t.key] = args[0]
        want = 2 if args[0] == "tap-2" else 1
        return out, "ok" if len(short) >= want else "no short tap"
    if kind == "click":
        pressed = [t for t in tracks if any(s.button for s in t.samples)]
        if not pressed:
            return {}, "no click"
        best = max(pressed, key=lambda t: t.samples[0].y)
        return {best.key: args[0]}, "ok"
    if kind == "clickdrag":
        pressed = [t for t in tracks if any(s.button for s in t.samples)]
        if not pressed:
            return {}, "no click"
        thumb = min(pressed, key=_still)
        out[thumb.key] = "drag-thumb"
        for t in tracks:
            if t is not thumb and _still(t) > 3.0:
                out[t.key] = "drag-finger"
        return out, "ok" if len(out) >= 2 else "no drag"
    if kind == "rest":
        rest_label, mover_label = args
        if len(tracks) < 2:
            return {}, "one contact"
        # The resting contact stays longest, moves least and sits lowest.
        def score(t):
            return t.duration_ms / 100 - _still(t) + t.samples[0].y / 10
        rest = max(tracks, key=score)
        out[rest.key] = rest_label
        for t in tracks:
            if t is not rest and _still(t) > 3.0:
                out[t.key] = mover_label
        return out, "ok" if len(out) >= 2 else "nothing moved"
    if kind == "drop":
        if len(tracks) < 2:
            return {}, "one contact"
        first = min(tracks, key=lambda t: t.t_down)
        out[first.key] = "drag-finger"
        for t in tracks:
            if t is not first and t.t_down > first.t_down + 100_000_000:
                out[t.key] = "thumb-drop"
        return out, "ok" if len(out) >= 2 else "no drop"
    raise ValueError(rule)
