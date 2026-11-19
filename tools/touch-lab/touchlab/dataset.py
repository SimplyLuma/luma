# SPDX-License-Identifier: Apache-2.0
"""One session joined into contacts with labels, keyboard activity and libinput's decisions."""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from . import contacts, libinput_log
from .labels import CLASSES
from .roles import MULTI_FINGER, ROLES, from_label
from .session import Session


@dataclass
class Joined:
    session: Session
    tracks: list[contacts.Track]
    decisions: list[libinput_log.Decision]
    keys: list[int]                       # key-press times, every keyboard
    keys_internal: list[int]              # key-press times, built-in keyboard only
    labels: list[dict]
    marks: list[dict]
    scenarios: list[dict]                 # {"scenario", "trial", "intent", "t0", "t1", "mode"}
    runs: list[dict]
    status: list[list]
    pad_mm: tuple[float, float]
    builder: contacts.TrackBuilder = field(repr=False, default=None)

    def keys_before(self, t: int, internal_only: bool = False) -> int | None:
        seq = self.keys_internal if internal_only else self.keys
        i = bisect.bisect_right(seq, t)
        return seq[i - 1] if i else None

    def keys_between(self, a: int, b: int, internal_only: bool = False) -> int:
        seq = self.keys_internal if internal_only else self.keys
        return bisect.bisect_right(seq, b) - bisect.bisect_left(seq, a)


def join(session: Session) -> Joined:
    recs = session.records()
    decisions = libinput_log.parse(recs, "l")
    absinfo = session.meta.get("devices", {}).get("touchpad", {}).get("evdev_description", {}).get("absinfo")
    tb = contacts.build(recs, session.descriptor, decisions, absinfo)
    internal = {k["index"] for k in session.keyboards() if k.get("internal")}
    keys = [r[0] for r in recs if r[1] == "k"]
    keys_internal = [r[0] for r in recs if r[1] == "k" and r[2] in internal]
    labels = session.labels()
    # "Again" and "Stop" discard a trial; its contact labels written before that no longer count.
    discards = {(l.get("run"), l.get("scenario"), l.get("trial")): l["t"] for l in labels
                if l.get("kind") == "scenario" and l.get("phase") == "discard"}
    labels = [l for l in labels if not (l.get("kind") in ("contact", "scenario") and l.get("phase") != "discard"
              and l["t"] <= discards.get((l.get("run"), l.get("scenario"), l.get("trial")), -1))]
    marks = [l for l in labels if l.get("kind") == "mark"]
    scen, runs, open_s, open_r = [], [], {}, {}
    for l in labels:
        if l.get("kind") == "scenario":
            key = (l.get("run"), l.get("scenario"), l.get("trial"))
            if l.get("phase") == "begin":
                open_s[key] = l
            elif key in open_s:
                b = open_s.pop(key)
                scen.append({"scenario": l["scenario"], "trial": l.get("trial"), "intent": b.get("intent"),
                             "run": l.get("run"), "t0": b["t"], "t1": l["t"],
                             "outcome": l.get("phase") if l.get("phase") != "end" else l.get("outcome", "done")})
        elif l.get("kind") == "run":
            if l.get("phase") == "begin":
                open_r[l.get("run")] = l
            elif l.get("run") in open_r:
                b = open_r.pop(l.get("run"))
                runs.append({"run": l.get("run"), "mode": b.get("mode"), "t0": b["t"], "t1": l["t"]})
    tracks = tb.tracks
    by_cid: dict[int, list[contacts.Track]] = {}
    for tr in tracks:
        by_cid.setdefault(tr.cid, []).append(tr)
    groups: dict[tuple, list] = {}
    for l in labels:
        if l.get("kind") != "contact":
            continue
        best = None
        for tr in by_cid.get(l.get("contact_id"), []):
            gap = abs(tr.t_down - l.get("down", 0))
            if gap < 40_000_000 and (best is None or gap < best[0]):
                best = (gap, tr)
        if best:
            tr = best[1]
            tr.label, tr.label_class = l.get("label"), l.get("class") or CLASSES.get(l.get("label"))
            tr.label_how, tr.scenario = l.get("how"), l.get("scenario")
            # Phase 1 synthetic labels carry a body-part "role" (finger/thumb/palm); only roles.ROLES count.
            tr.role = l.get("role") if l.get("role") in ROLES else from_label(l.get("label"))
            groups.setdefault((l.get("run"), l.get("scenario"), l.get("trial")), []).append(tr)
    # Phase 1 multi-finger labels: the first finger down in a trial points, the others join it.
    for members in groups.values():
        multi = sorted((t for t in members if t.label in MULTI_FINGER), key=lambda t: t.t_down)
        if multi:
            multi[0].role = "point"
    for s in scen:
        for tr in tracks:
            if tr.scenario is None and s["t0"] <= tr.t_down <= s["t1"]:
                tr.scenario = s["scenario"]
    for m in marks:
        lo, hi = m["t"] - m.get("window_ns", 3_000_000_000), m["t"]
        for tr in tracks:
            end = tr.t_up or tr.t_down
            if tr.t_down <= hi and end >= lo:
                tr.marks.append(m.get("mark"))
    if isinstance(tb, contacts.EvdevTrackBuilder):
        w, h = tb.pad_mm
    else:
        w, h = (tb.layout.x_mm or 1.0, tb.layout.y_mm or 1.0) if tb.layout else (1.0, 1.0)
    return Joined(session, tracks, decisions, keys, keys_internal, labels, marks, scen, runs,
                  [r for r in recs if r[1] == "s"], (w, h), tb)
