# SPDX-License-Identifier: Apache-2.0
"""The active session: a dozen short drills where the drill defines the role.

Every drill asks for one posture for 30 to 60 seconds, so each contact in it
gets a role without the user labelling anything: the rule only has to tell
apart the few roles the drill allows (which contact is the resting thumb,
which finger joined second). Rules are deterministic and run again offline
on the recording, so an improved rule relabels old sessions. A contact a
rule can't place stays unlabelled rather than guessed.

Drill text adapts to the surface class (touchscreens have no click and no
keyboard beside them).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .contacts import Track
from .roles import ROLES

CATEGORIES = ("Pointing", "Thumb", "Two fingers", "Typing", "Accidental")

SENTENCES = (
    "The quick brown fox jumps over the lazy dog.",
    "Pack my box with five dozen liquor jugs.",
    "How vexingly quick daft zebras jump.",
    "Sphinx of black quartz, judge my vow.",
)


@dataclass(frozen=True)
class Variant:
    title: str
    text: str
    rule: str
    roles: tuple


@dataclass(frozen=True)
class Drill:
    id: str
    category: str
    seconds: int
    pad: Variant                       # touchpads (every class but touchscreen)
    screen: Variant | None = None      # touchscreen variant; None means the pad text works as is
    typing: bool = False
    targets: bool = False
    needs: tuple = ()                  # mask bits the pad variant needs ("button", "keyboard")

    def variant(self, surface_class: str) -> Variant:
        if surface_class == "touchscreen" and self.screen is not None:
            return self.screen
        return self.pad

    def available(self, surface_class: str, mask: dict | None = None) -> bool:
        if surface_class == "touchscreen":
            return True
        return all((mask or {}).get(b, 1) for b in self.needs)


V = Variant
DRILLS: tuple[Drill, ...] = (
    Drill("point-slow", "Pointing", 40,
          V("Point, slowly", "Move the pointer with one finger in long, slow strokes. Lift and start again "
            "wherever you like.", "all:point", ("point",)),
          V("Drag, slowly", "Drag one finger slowly around the screen. Lift and start again anywhere.",
            "all:point", ("point",))),
    Drill("point-precise", "Pointing", 40,
          V("Hit the dots", "Put the pointer on each dot and hold it there until the next one appears.",
            "all:point", ("point",)),
          V("Touch the dots", "Touch each dot and hold until the next one appears.", "all:point", ("point",)),
          targets=True),
    Drill("thumb-rest-point", "Thumb", 60,
          V("Thumb down, point", "Rest your thumb on the pad the way you usually do, and point with your index "
            "or middle finger. Lift and land the pointing finger now and then; keep the thumb down.",
            "rest:point", ("thumb-rest", "point")),
          V("Hold and drag", "Hold the phone in one hand with your fingers round the edge, and drag with a finger "
            "of the other hand.", "rest:point", ("thumb-rest", "point"))),
    Drill("thumb-rest-tap", "Thumb", 45,
          V("Thumb down, tap", "Keep your thumb resting on the pad and tap with a finger, about once a second.",
            "rest:point", ("thumb-rest", "point")),
          V("Hold and tap", "Hold the phone in one hand and tap with that hand's thumb, about once a second.",
            "rest:point", ("thumb-rest", "point"))),
    Drill("thumb-click", "Thumb", 45,
          V("Click with your thumb", "Rest your index finger on the pad and click by pressing with your thumb, "
            "about once a second.", "click", ("thumb-click", "point")),
          V("Reach with your thumb", "Hold the phone in one hand and reach the far corner with your thumb, "
            "then come back.", "rest:point", ("thumb-rest", "point")),
          needs=("button",)),
    Drill("thumb-click-drag", "Thumb", 50,
          V("Thumb clicks, finger drags", "Press and hold the click with your thumb, drag with a finger, "
            "let go. Again, in different directions.", "clickdrag", ("thumb-click", "point")),
          V("Hold, then drag", "Touch and hold an empty spot, then drag something across the screen.",
            "all:point", ("point",)),
          needs=("button",)),
    Drill("scroll", "Two fingers", 40,
          V("Scroll with two fingers", "Scroll up and down with two fingers, short and long scrolls.",
            "group", ("point", "second")),
          V("Pinch and spread", "Zoom in and out with two fingers.", "group", ("point", "second"))),
    Drill("scroll-thumb-down", "Two fingers", 50,
          V("Scroll with your thumb down", "Rest your thumb on the pad and scroll with two fingers above it.",
            "rest:group", ("thumb-rest", "point", "second")),
          V("Hold and scroll", "Hold the phone with your fingers round the edge and scroll with the other hand.",
            "rest:point", ("thumb-rest", "point"))),
    Drill("typing", "Typing", 60,
          V("Type the sentence", "Type the sentence below as you normally would and let your hands rest where "
            "they usually rest. Only the timing of key presses is kept, never the keys.",
            "accidental", ("palm", "brush")),
          V("Type the sentence", "Type the sentence on the on-screen keyboard while holding the phone as usual.",
            "rest:point", ("thumb-rest", "point")),
          typing=True, needs=("keyboard",)),
    Drill("palm-heel", "Accidental", 40,
          V("Heel of the hand", "Put the heel of your hand down on the pad, lift it, and again, the way it lands "
            "when you type.", "all:palm", ("palm",)),
          V("Palm on the corner", "Lay the base of your palm across the bottom corner of the screen, lift, and "
            "again.", "all:palm", ("palm",))),
    Drill("knuckle-brush", "Accidental", 35,
          V("Brush with a knuckle", "Brush the pad lightly with a knuckle or the side of a finger: quick, short, "
            "anywhere.", "all:brush", ("brush",)),
          V("Brush the edge", "Brush the screen's edge lightly with the side of a finger, quick and short.",
            "all:brush", ("brush",))),
    Drill("reach-grip", "Accidental", 50,
          V("Reach across and grip", "Reach for a far key so your hand crosses the pad, then grip the laptop's "
            "front edge as if picking it up. Alternate.", "accidental", ("palm", "brush")),
          V("Pick it up", "Pick the phone up, put it down and grip it as if carrying it.", "accidental",
            ("palm", "brush"))),
)
BY_ID = {d.id: d for d in DRILLS}
TEST_ROUND_SECONDS = 60
FOLLOW_UP_SECONDS = 30


def plan(surface_class: str, mask: dict | None = None) -> list[Drill]:
    """The drills for a surface, in session order (easy first, accidental last)."""
    return [d for d in DRILLS if d.available(surface_class, mask)]


def session_seconds(surface_class: str, mask: dict | None = None) -> int:
    return sum(d.seconds for d in plan(surface_class, mask)) + TEST_ROUND_SECONDS + FOLLOW_UP_SECONDS


def sentence(rng: random.Random | None = None) -> str:
    return (rng or random).choice(SENTENCES)


# ── Rules ───────────────────────────────────────────────────────────────────

def _disp(tr: Track) -> float:
    ss = tr.samples
    return max((math.hypot(s.x - ss[0].x, s.y - ss[0].y) for s in ss), default=0.0)


def _end(tr: Track) -> int:
    return tr.t_up if tr.t_up is not None else (tr.samples[-1].t if tr.samples else tr.t_down)


def _overlap(a: Track, b: Track) -> bool:
    return a.t_down < _end(b) and b.t_down < _end(a)


def _edge_mm(tr: Track, pad_mm) -> float:
    w, h = pad_mm
    s = tr.samples[0]
    return min(s.x, w - s.x, s.y, h - s.y)


def _rest_candidates(tracks: list[Track], pad_mm, surface_class: str) -> list[Track]:
    """Contacts that held still for a while, best resting candidate first."""
    cands = [t for t in tracks if t.duration_ms >= 350 and _disp(t) < 4.5]
    if surface_class == "touchscreen":
        key = lambda t: t.duration_ms / 200 - _edge_mm(t, pad_mm)          # long, at the edge
    else:
        key = lambda t: t.duration_ms / 200 + t.samples[0].y / 6 - _disp(t)  # long, low on the pad, still
    return sorted(cands, key=key, reverse=True)


def _rest_split(tracks, pad_mm, surface_class):
    """Resting contacts: the best still candidate of each time-overlapping cluster that also overlaps a
    contact that does something else."""
    rest = []
    for c in _rest_candidates(tracks, pad_mm, surface_class):
        if any(_overlap(c, r) for r in rest):
            continue
        others = [t for t in tracks if t is not c and _overlap(c, t)]
        if others and any(t.duration_ms < c.duration_ms for t in others):
            rest.append(c)
    return rest


def _groups(tracks: list[Track]) -> list[list[Track]]:
    """Contacts that overlap in time, chained."""
    out: list[list[Track]] = []
    for t in sorted(tracks, key=lambda t: t.t_down):
        if out and any(_overlap(t, o) for o in out[-1]):
            out[-1].append(t)
        else:
            out.append([t])
    return out


def _press_starts(tracks: list[Track]) -> list[int]:
    """Times the pad's button went down (one per press, from any contact's samples)."""
    times = sorted({s.t for t in tracks for s in t.samples if s.button})
    out = []
    for t in times:
        if not out or t - last > 40_000_000:
            out.append(t)
        last = t
    return out


def _accidental(tr: Track) -> str:
    peak = max((s.force for s in tr.samples), default=0.0)
    return "palm" if tr.duration_ms >= 220 or (peak > 0.3 and tr.duration_ms >= 120) else "brush"


def assign(rule: str, tracks: list[Track], pad_mm=(149.6, 73.8), surface_class: str = "force-pad") \
        -> tuple[dict[str, str], str]:
    """{track key: role} for one drill's contacts, and "ok" or why the drill looked wrong."""
    tracks = [t for t in tracks if t.samples]
    if not tracks:
        return {}, "no contact"
    out: dict[str, str] = {}
    kind, _, arg = rule.partition(":")
    if kind == "all":
        return {t.key: arg for t in tracks}, "ok"
    if kind == "accidental":
        return {t.key: _accidental(t) for t in tracks}, "ok"
    if kind == "group":
        for g in _groups(tracks):
            for i, t in enumerate(sorted(g, key=lambda t: t.t_down)):
                out[t.key] = "point" if i == 0 else "second"
        return out, "ok" if any(v == "second" for v in out.values()) else "never two fingers"
    if kind == "rest":
        rest = _rest_split(tracks, pad_mm, surface_class)
        for r in rest:
            out[r.key] = "thumb-rest"
        others = [t for t in tracks if t.key not in out]
        if arg == "group":
            # Only fingers that overlapped the resting thumb are known to be a scroll pair.
            for g in _groups(others):
                for i, t in enumerate(sorted(g, key=lambda t: t.t_down)):
                    out[t.key] = "point" if i == 0 else "second"
        else:
            for t in others:
                # A still contact that never overlapped a resting one is ambiguous: leave it out.
                if rest or _disp(t) > 2.0 or t.duration_ms < 350:
                    out[t.key] = "point"
        return out, "ok" if rest else "no resting contact"
    if kind in ("click", "clickdrag"):
        pressed = [t for t in tracks if any(s.button for s in t.samples)]
        if not pressed:
            return {t.key: "point" for t in tracks if _disp(t) > 3.0}, "no click"
        thumbs = []
        for tp in _press_starts(tracks):
            down = [t for t in tracks if t.t_down <= tp <= _end(t)]
            if not down:
                continue
            # The pressing thumb: landed just before the press, low on the pad, still.
            th = max(down, key=lambda t: t.samples[0].y / 6 - (tp - t.t_down) / 3e8 - _disp(t))
            if th not in thumbs:
                thumbs.append(th)
        for t in tracks:
            if t in thumbs:
                out[t.key] = "thumb-click"
            elif kind == "click" or _disp(t) > 3.0 or any(_overlap(t, th) for th in thumbs):
                out[t.key] = "point"
        return out, "ok"
    raise ValueError(rule)


def check_roles(labels: dict[str, str], allowed: tuple) -> bool:
    return all(v in allowed and v in ROLES for v in labels.values())


# ── Follow-up: the next drill targets the least certain role ─────────────────

@dataclass
class RoundStats:
    """What the test round saw: per role, predicted confidence and corrections."""
    confidence: dict = field(default_factory=dict)    # role -> [max prob of contacts predicted or corrected to it]
    corrections: dict = field(default_factory=dict)   # (predicted, corrected) -> count

    def add(self, predicted: str, prob: float, corrected: str | None = None) -> None:
        role = corrected or predicted
        self.confidence.setdefault(role, []).append(prob if not corrected else 0.0)
        if corrected and corrected != predicted:
            key = (predicted, corrected)
            self.corrections[key] = self.corrections.get(key, 0) + 1

    def weakest(self) -> list[str]:
        """Roles, least certain first (corrections count as zero confidence)."""
        mean = {r: sum(v) / len(v) for r, v in self.confidence.items() if v}
        return sorted(mean, key=lambda r: mean[r])


def follow_up(stats: RoundStats, surface_class: str, mask: dict | None = None) -> Drill | None:
    """The drill that best exercises the least certain role (the confused pair if there is one)."""
    avail = plan(surface_class, mask)
    if stats.corrections:
        (a, b), _ = max(stats.corrections.items(), key=lambda kv: kv[1])
        both = [d for d in avail if {a, b} <= set(d.variant(surface_class).roles)]
        if both:
            return min(both, key=lambda d: len(d.variant(surface_class).roles))
        want = b
    else:
        weak = stats.weakest()
        if not weak:
            return None
        want = weak[0]
    have = [d for d in avail if want in d.variant(surface_class).roles]
    return min(have, key=lambda d: len(d.variant(surface_class).roles)) if have else None


def tracks_from_contacts(contacts) -> list[Track]:
    """rfeatures.Contact objects as contacts.Track, so the rules run on synthetic scenes too."""
    from .contacts import Sample
    out = []
    for i, c in enumerate(contacts):
        tr = Track(i, int(c.t_down * 1e6), None if c.t_up is None else int(c.t_up * 1e6))
        for k in range(len(c.t)):
            p = c.p[k]
            tr.samples.append(Sample(int(c.t[k] * 1e6), c.x[k], c.y[k], p if p == p else 0.0,
                                     None if c.conf[k] != c.conf[k] else c.conf[k] > 0.5, 0.0, bool(c.btn[k])))
        out.append(tr)
    return out
