# SPDX-License-Identifier: Apache-2.0
"""Synthetic touchpad recordings.

Scripted hands (fingers, thumbs and palms with paths, force curves and a
firmware-confidence story) are rendered into the same raw streams the
recorder writes: HID reports encoded with the real descriptor, evdev frames
shaped like hid-multitouch's, libinput debug-events lines, key-press times
and labels. The viewer, feature extractor and tests run on these before any
real recording exists; tests/uhid_touchpad.py plays the same reports through
a kernel to check the shapes.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import random
from dataclasses import dataclass, field
from typing import Callable

from . import hid
from .labels import CLASSES, MARK_WINDOW_NS

FRAME_S = 1 / 120
DESCRIPTOR_PATH = os.path.join(os.path.dirname(__file__), "data", "xps14-2c2f-0034.desc.hex")

# Kernel codes used in the evdev rendering.
ABS_X, ABS_Y, ABS_PRESSURE = 0x00, 0x01, 0x18
ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y = 0x2F, 0x35, 0x36
ABS_MT_TOOL_TYPE, ABS_MT_TRACKING_ID, ABS_MT_PRESSURE = 0x37, 0x39, 0x3A
BTN_LEFT, BTN_TOOL_FINGER, BTN_TOUCH = 0x110, 0x145, 0x14A
BTN_TOOL_QUINTTAP, BTN_TOOL_DOUBLETAP, BTN_TOOL_TRIPLETAP, BTN_TOOL_QUADTAP = 0x148, 0x14D, 0x14E, 0x14F
MSC_TIMESTAMP = 0x05
MT_TOOL_FINGER, MT_TOOL_PALM = 0, 2


def load_descriptor() -> bytes:
    with open(DESCRIPTOR_PATH, encoding="ascii") as fh:
        return bytes.fromhex(fh.read().strip())


@dataclass
class Actor:
    label: str
    role: str                                   # finger, thumb or palm
    t0: float
    dur: float
    path: Callable[[float], tuple[float, float]]
    force: Callable[[float], float]             # 0..1 of full scale, by fraction of the contact
    conf: Callable[[float], bool] = lambda u: True
    press: Callable[[float], bool] = lambda u: False
    ident: int = -1

    @property
    def t1(self) -> float:
        return self.t0 + self.dur


@dataclass
class Trial:
    scenario: str
    intent: str
    t0: float
    t1: float
    actors: list[Actor]
    keys: list[float] = field(default_factory=list)


# ── Paths and curves ────────────────────────────────────────────────────────

def line(a, b, ease=True):
    def f(u):
        if ease:
            u = 0.5 - 0.5 * math.cos(math.pi * u)
        return a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u
    return f


def still(p, wobble=0.15, rng=None):
    rng = rng or random.Random(0)
    phase = rng.random() * 6.28
    return lambda u: (p[0] + wobble * math.sin(phase + u * 9), p[1] + wobble * math.cos(phase + u * 7))


def ramp(level, rise=0.12, fall=0.1, bump=None):
    def f(u):
        v = level * min(1.0, u / rise if rise else 1.0, (1 - u) / fall if fall else 1.0)
        if bump and bump[0] <= u <= bump[1]:
            mid = (bump[0] + bump[1]) / 2
            v += bump[2] * (1 - abs(u - mid) / ((bump[1] - bump[0]) / 2))
        return max(0.0, v)
    return f


# ── Scenarios ───────────────────────────────────────────────────────────────

W, H = 149.6, 73.8


def _p(rng, xr=(0.2, 0.8), yr=(0.2, 0.7)):
    return (W * rng.uniform(*xr), H * rng.uniform(*yr))


def scenario_actors(name: str, t: float, rng: random.Random) -> Trial:
    a: list[Actor] = []
    keys: list[float] = []
    if name == "point":
        s = _p(rng)
        a.append(Actor("pointer", "finger", t, rng.uniform(0.5, 0.9), line(s, _p(rng)), ramp(0.12)))
    elif name == "precision":
        s = _p(rng)
        e = (s[0] + rng.uniform(-8, 8), s[1] + rng.uniform(-5, 5))
        a.append(Actor("precision", "finger", t, rng.uniform(0.7, 1.2), line(s, e), ramp(0.10)))
    elif name == "scroll":
        x, y = _p(rng, (0.3, 0.6), (0.3, 0.4))
        d = rng.choice((-1, 1)) * rng.uniform(15, 30)
        dur = rng.uniform(0.35, 0.7)
        for dx in (0, 18):
            a.append(Actor("scroll", "finger", t + rng.uniform(0, 0.02), dur,
                           line((x + dx, y), (x + dx, y + d)), ramp(0.11)))
    elif name == "pinch":
        c = _p(rng, (0.4, 0.6), (0.4, 0.6))
        sign = rng.choice((-1, 1))
        for k in (-1, 1):
            a.append(Actor("pinch", "finger", t, 0.6, line((c[0] + k * (12 - sign * 6), c[1]),
                                                           (c[0] + k * (12 + sign * 6), c[1])), ramp(0.12)))
    elif name == "tap":
        a.append(Actor("tap", "finger", t, rng.uniform(0.06, 0.14), still(_p(rng), 0.1, rng), ramp(0.10, 0.3, 0.3)))
    elif name == "tap-2":
        p = _p(rng)
        for dx in (0, 17):
            a.append(Actor("tap-2", "finger", t, rng.uniform(0.07, 0.13), still((p[0] + dx, p[1]), 0.1, rng),
                           ramp(0.10, 0.3, 0.3)))
    elif name == "click-thumb":
        a.append(Actor("click-thumb", "thumb", t, 0.35, still((W * rng.uniform(0.35, 0.6), H * 0.88), 0.2, rng),
                       ramp(0.18, 0.2, 0.2, (0.35, 0.65, 0.45)), press=lambda u: 0.42 < u < 0.6))
    elif name == "click-drag":
        a.append(Actor("drag-thumb", "thumb", t, 1.2, still((W * 0.45, H * 0.9), 0.2, rng),
                       ramp(0.2, 0.05, 0.05, (0.1, 0.95, 0.3)), press=lambda u: 0.12 < u < 0.92))
        s = _p(rng, (0.3, 0.7), (0.2, 0.5))
        a.append(Actor("drag-finger", "finger", t + 0.2, 0.8, line(s, _p(rng, (0.3, 0.7), (0.2, 0.5))), ramp(0.12)))
    elif name in ("swipe-3", "swipe-4"):
        n = 3 if name == "swipe-3" else 4
        c = _p(rng, (0.3, 0.5), (0.3, 0.6))
        d = rng.choice(((30, 0), (-30, 0), (0, 20), (0, -20)))
        for i in range(n):
            s = (c[0] + i * 16, c[1] + (3 if i in (1, 2) else 0))
            a.append(Actor(name, "finger", t + i * 0.01, 0.45, line(s, (s[0] + d[0], s[1] + d[1])), ramp(0.11)))
    elif name == "thumb-rest-scroll":
        a.append(Actor("thumb-rest", "thumb", t, 1.3, still((W * rng.uniform(0.2, 0.6), H * 0.9), 0.3, rng),
                       ramp(0.16, 0.08, 0.05), conf=lambda u: True))
        x, y = W * 0.5, H * 0.35
        for dx in (0, 18):
            a.append(Actor("scroll", "finger", t + 0.3, 0.6, line((x + dx, y), (x + dx, y + 20)), ramp(0.11)))
    elif name == "thumb-rest-point":
        a.append(Actor("thumb-rest", "thumb", t, 1.2, still((W * rng.uniform(0.25, 0.65), H * 0.9), 0.3, rng),
                       ramp(0.15, 0.08, 0.05)))
        a.append(Actor("pointer", "finger", t + 0.25, 0.7, line(_p(rng), _p(rng)), ramp(0.12)))
    elif name == "palm-typing":
        side = rng.choice((0.08, 0.92))
        flag_at = rng.uniform(0.03, 0.2) if rng.random() < 0.7 else 2.0    # firmware misses some palms
        a.append(Actor("palm-typing", "palm", t, rng.uniform(1.0, 2.0),
                       still((W * side, H * rng.uniform(0.5, 0.9)), 0.5, rng),
                       ramp(0.32, 0.15, 0.05), conf=lambda u, f=flag_at: u < f))
        keys = [t + 0.1 + i * rng.uniform(0.09, 0.2) for i in range(10)]
    elif name == "thumb-drop":
        s = _p(rng, (0.3, 0.6), (0.2, 0.5))
        a.append(Actor("drag-finger", "finger", t, 1.0, line(s, _p(rng, (0.3, 0.6), (0.2, 0.5))),
                       ramp(0.14, 0.05, 0.05, (0.0, 0.9, 0.25)), press=lambda u: u < 0.9))
        a.append(Actor("thumb-drop", "thumb", t + rng.uniform(0.3, 0.6), 0.4,
                       still((W * rng.uniform(0.3, 0.7), H * 0.88), 0.3, rng), ramp(0.2, 0.1, 0.1)))
    elif name == "graze-typing":
        keys = [t + i * rng.uniform(0.08, 0.18) for i in range(8)]
        a.append(Actor("graze", rng.choice(("thumb", "palm")), t + rng.uniform(0.2, 0.6), rng.uniform(0.03, 0.09),
                       still(_p(rng, (0.1, 0.9), (0.6, 0.95)), 0.6, rng), ramp(0.035, 0.4, 0.4)))
    elif name == "palm-edge-reach":
        side = rng.choice((0.03, 0.97))
        a.append(Actor("palm-edge", "palm", t, rng.uniform(0.3, 0.8),
                       line((W * side, H * 0.3), (W * side, H * 0.55)), ramp(0.22, 0.2, 0.1),
                       conf=lambda u: u < 0.15))
        keys = [t + 0.15 + i * 0.12 for i in range(4)]
    elif name == "hand-rest":
        keys = [t + i * rng.uniform(0.09, 0.15) for i in range(6)]
        base = keys[-1] + 0.25
        a.append(Actor("hand-rest", "palm", base, rng.uniform(0.8, 1.6),
                       still((W * rng.uniform(0.2, 0.8), H * rng.uniform(0.55, 0.85)), 0.4, rng),
                       ramp(0.28, 0.1, 0.1), conf=lambda u: u < 0.1 or rng.random() < 0.0))
        keys += [base + 2.0 + i * 0.12 for i in range(5)]
    else:
        raise KeyError(name)
    t1 = max([x.t1 for x in a] + keys + [t]) + 0.2
    intent = "accidental" if any(CLASSES.get(x.label) == "accidental" for x in a) else "intended"
    return Trial(name, intent, t, t1, a, keys)


SCENARIOS = ["point", "precision", "scroll", "pinch", "tap", "tap-2", "click-thumb", "click-drag",
             "swipe-3", "swipe-4", "thumb-rest-scroll", "thumb-rest-point", "palm-typing", "thumb-drop",
             "graze-typing", "palm-edge-reach", "hand-rest"]


# ── Rendering ───────────────────────────────────────────────────────────────

def to_logical(layout: hid.TouchpadLayout, x_mm: float, y_mm: float) -> tuple[int, int]:
    xs = (layout.x_range[1] - layout.x_range[0]) / (layout.x_mm or W)
    ys = (layout.y_range[1] - layout.y_range[0]) / (layout.y_mm or H)
    x = max(layout.x_range[0], min(layout.x_range[1], round(x_mm * xs)))
    y = max(layout.y_range[0], min(layout.y_range[1], round(y_mm * ys)))
    return x, y


def render_reports(trials: list[Trial], layout: hid.TouchpadLayout, rng: random.Random):
    """Yield (time_s, report_bytes, frame) for every scan with a contact on the pad."""
    actors = sorted((x for tr in trials for x in tr.actors), key=lambda x: x.t0)
    if not actors:
        return
    end = max(x.t1 for x in actors) + FRAME_S * 2
    t = actors[0].t0
    active: list[Actor] = []
    pending = list(actors)
    lifted: list[tuple[Actor, tuple[int, int]]] = []
    last_pos: dict[int, tuple[int, int]] = {}
    scan = 0
    unit = layout.scan_time_unit()
    while t <= end:
        while pending and pending[0].t0 <= t:
            x = pending.pop(0)
            used = {y.ident for y in active}
            x.ident = next(i for i in range(16) if i not in used)
            active.append(x)
        contacts, frame = [], []
        buttons = 0
        for x in list(active):
            u = (t - x.t0) / x.dur
            if u > 1:
                active.remove(x)
                lifted.append((x, last_pos.get(id(x), (0, 0))))
                continue
            px, py = x.path(u)
            lx, ly = to_logical(layout, px + rng.gauss(0, 0.03), py + rng.gauss(0, 0.03))
            last_pos[id(x)] = (lx, ly)
            force = x.force(u)
            pressure = round(force * layout.pressure_range[1])
            conf = bool(x.conf(u))
            contacts.append(hid.Contact(x.ident, True, conf, lx, ly, pressure))
            frame.append((x, lx, ly, pressure, conf))
            if x.press(u):
                buttons = 1
        for x, (lx, ly) in lifted:
            contacts.append(hid.Contact(x.ident, False, True, lx, ly, 0))
            frame.append((x, lx, ly, 0, None))
        lifted = []
        if contacts or buttons:
            for i in range(0, len(contacts), len(layout.slots)):
                part = contacts[i:i + len(layout.slots)]
                report = layout.encode(scan, part, buttons)
                if i:                                # hybrid mode: later reports carry count 0
                    report = bytearray(report)
                    report = bytes(report)
                yield t, report, frame
        scan = (scan + round(FRAME_S / unit)) & 0xFFFF
        if not active and pending:
            gap = pending[0].t0 - t
            if gap > FRAME_S:
                scan = (scan + round(gap / unit)) & 0xFFFF
                t = pending[0].t0
                continue
        t += FRAME_S


class EvdevModel:
    """hid-multitouch's evdev output for a stream of decoded reports (Win8 PTP, confidence quirk)."""

    def __init__(self, slots: int = 5) -> None:
        self.slot_of: dict[int, int] = {}
        self.state: dict[int, dict] = {}
        self.next_tracking = 0
        self.current_slot = 0
        self.tool_bits = 0
        self.touch = 0
        self.button = 0
        self.nslots = slots
        self.ptr = (None, None, None)

    def frame(self, report: hid.TouchReport, scan_us: int) -> list[list[int]]:
        ev: list[list[int]] = []
        live = []
        for c in report.contacts:
            slot = self.slot_of.get(c.contact_id)
            if slot is None:
                if not c.tip:
                    continue
                used = set(self.slot_of.values())
                free = [s for s in range(self.nslots) if s not in used]
                if not free:
                    continue
                slot = free[0]
                self.slot_of[c.contact_id] = slot
                self.state[slot] = {}
            st = self.state[slot]
            out = []
            if not c.tip:
                out.append([3, ABS_MT_TRACKING_ID, -1])
                del self.slot_of[c.contact_id]
                del self.state[slot]
            else:
                # hid-multitouch keeps a Confidence=0 contact and marks it a palm.
                tool = MT_TOOL_PALM if c.confidence is False else MT_TOOL_FINGER
                if "tid" not in st:
                    st["tid"] = self.next_tracking
                    self.next_tracking = (self.next_tracking + 1) & 0xFFFF
                    out.append([3, ABS_MT_TRACKING_ID, st["tid"]])
                    st["tool"] = MT_TOOL_FINGER
                if st.get("tool") != tool:
                    out.append([3, ABS_MT_TOOL_TYPE, tool])
                    st["tool"] = tool
                for code, val in ((ABS_MT_POSITION_X, c.x), (ABS_MT_POSITION_Y, c.y),
                                  (ABS_MT_PRESSURE, c.pressure or 0)):
                    if st.get(code) != val:
                        out.append([3, code, val])
                        st[code] = val
                live.append((slot, c))
            if out:
                if slot != self.current_slot:
                    ev.append([3, ABS_MT_SLOT, slot])
                    self.current_slot = slot
                ev.extend(out)
        count = len(self.slot_of)
        touch = 1 if count else 0
        if touch != self.touch:
            ev.append([1, BTN_TOUCH, touch])
            self.touch = touch
        tools = {1: BTN_TOOL_FINGER, 2: BTN_TOOL_DOUBLETAP, 3: BTN_TOOL_TRIPLETAP, 4: BTN_TOOL_QUADTAP,
                 5: BTN_TOOL_QUINTTAP}
        want = tools.get(min(count, 5))
        for code in tools.values():
            on = 1 if code == want else 0
            bit = 1 << (code - BTN_TOOL_FINGER)
            if bool(self.tool_bits & bit) != bool(on):
                ev.append([1, code, on])
                self.tool_bits ^= bit
        if live:
            slot, c = min(live)
            for code, val, i in ((ABS_X, c.x, 0), (ABS_Y, c.y, 1), (ABS_PRESSURE, c.pressure or 0, 2)):
                if self.ptr[i] != val:
                    ev.append([3, code, val])
                    p = list(self.ptr)
                    p[i] = val
                    self.ptr = tuple(p)
        if report.buttons != self.button:
            ev.append([1, BTN_LEFT, report.buttons & 1])
            self.button = report.buttons & 1
        ev.append([4, MSC_TIMESTAMP, scan_us])
        return ev


def libinput_lines(trial: Trial, tp: str, rng: random.Random, t_of) -> list[tuple[float, str]]:
    """Plausible libinput debug-events output for a trial: what it would print, including its misses."""
    out: list[tuple[float, str]] = []

    def ev(t, name, text):
        out.append((t, f" {tp:<8}{name:<29}{t_of(t):+.3f}s\t{text}"))

    movers = [x for x in trial.actors if CLASSES.get(x.label) == "intended"]
    seq = [0]

    def dbg(t, text):
        seq[0] += 1
        out.append((t, f"{seq[0]:4d}: {tp:<7} - {text}"))
    for x in trial.actors:
        slot = x.ident
        accidental = CLASSES.get(x.label) == "accidental"
        flagged = next((k / 40 for k in range(41) if not x.conf(k / 40)), None)
        edge = x.path(0)[0] < 8 or x.path(0)[0] > W - 8
        typing = any(x.t0 - 0.5 <= k <= x.t0 for k in trial.keys)
        if flagged is not None:
            dbg(x.t0 + flagged * x.dur + 0.002, f"palm: touch {slot} (TOUCH_UPDATE), palm detected (tool-palm)")
        elif accidental and edge and rng.random() < 0.8:
            dbg(x.t0 + 0.01, f"palm: touch {slot} (TOUCH_BEGIN), palm detected (edge)")
        elif accidental and typing and rng.random() < 0.9:
            dbg(x.t0 + 0.002, f"palm: touch {slot} (TOUCH_BEGIN), palm detected (typing)")
        elif x.role == "thumb" and (accidental or rng.random() < 0.2):
            to = "THUMB_STATE_SUPPRESSED" if rng.random() < 0.6 else "THUMB_STATE_JAILED"
            dbg(x.t0 + 0.12, f"thumb: touch {slot}, THUMB_STATE_FINGER \u2192 {to}")
        elif accidental:
            steps = int(x.dur / FRAME_S)                   # libinput missed it: the pointer moves
            for k in range(1, steps, 2):
                u = k / steps
                (a0, a1), (b0, b1) = x.path(u - 1 / steps), x.path(u)
                ev(x.t0 + u * x.dur, "POINTER_MOTION", f"{(b0 - a0) * 3:6.2f}/{(b1 - a1) * 3:6.2f} "
                   f"({(b0 - a0) * 2:+6.2f}/{(b1 - a1) * 2:+6.2f})")
    for x in movers:
        steps = max(2, int(x.dur / FRAME_S))
        if x.label in ("pointer", "precision", "drag-finger"):
            for k in range(1, steps):
                u = k / steps
                (a0, a1), (b0, b1) = x.path((k - 1) / steps), x.path(u)
                ev(x.t0 + u * x.dur, "POINTER_MOTION", f"{(b0 - a0) * 3:6.2f}/{(b1 - a1) * 3:6.2f} "
                   f"({(b0 - a0) * 2:+6.2f}/{(b1 - a1) * 2:+6.2f})")
        elif x.label in ("tap", "tap-2") and x is movers[0]:
            btn = "BTN_LEFT (272)" if x.label == "tap" else "BTN_RIGHT (273)"
            ev(x.t1 + 0.18, "POINTER_BUTTON", f"{btn} pressed, seat count: 1")
            ev(x.t1 + 0.19, "POINTER_BUTTON", f"{btn} released, seat count: 0")
        elif x.label == "scroll" and x is movers[0]:
            for k in range(1, steps):
                u = k / steps
                dy = (x.path(u)[1] - x.path((k - 1) / steps)[1]) * 3
                ev(x.t0 + u * x.dur, "POINTER_SCROLL_FINGER", f"vert {dy:.2f}/0.0 horiz 0.00/0.0* (finger)")
        elif x.label in ("swipe-3", "swipe-4") and x is movers[0]:
            n = 3 if x.label == "swipe-3" else 4
            ev(x.t0 + 0.05, "GESTURE_SWIPE_BEGIN", f"{n}")
            for k in range(3, steps, 2):
                ev(x.t0 + k / steps * x.dur, "GESTURE_SWIPE_UPDATE", f"{n}  4.00/ 0.00 ( 4.00/ 0.00 unaccelerated)")
            ev(x.t1, "GESTURE_SWIPE_END", f"{n}")
        elif x.label == "pinch" and x is movers[0]:
            ev(x.t0 + 0.05, "GESTURE_PINCH_BEGIN", "2")
            ev(x.t1, "GESTURE_PINCH_END", "2")
    for x in trial.actors:
        if x.label in ("click-thumb", "drag-thumb", "drag-finger") or any(x.press(k / 20) for k in range(21)):
            ks = [k / 50 for k in range(51) if x.press(k / 50)]
            if ks:
                btn = "BTN_LEFT (272)"
                ev(x.t0 + ks[0] * x.dur, "POINTER_BUTTON", f"{btn} pressed, seat count: 1")
                ev(x.t0 + ks[-1] * x.dur, "POINTER_BUTTON", f"{btn} released, seat count: 0")
    return out


def build_session(out_dir: str, *, seed: int = 7, trials_per: int = 4, free_minutes: float = 1.0,
                  boot: str = "synthetic-0000-0000", t_base_ns: int = 1_000_000_000_000) -> str:
    """Write a complete synthetic session (stream + labels) under out_dir; return its session dir."""
    rng = random.Random(seed)
    desc = load_descriptor()
    layout = hid.TouchpadLayout(hid.parse_descriptor(desc))
    order = [s for s in SCENARIOS for _ in range(trials_per)]
    rng.shuffle(order)
    t = 1.0
    trials: list[Trial] = []
    for name in order:
        trial = scenario_actors(name, t, rng)
        trials.append(trial)
        t = trial.t1 + rng.uniform(0.4, 1.0)
    # "Just use it": a stretch of mixed natural use with two marked mistakes.
    natural_start = t
    while t < natural_start + free_minutes * 60:
        name = rng.choice(SCENARIOS)
        trial = scenario_actors(name, t, rng)
        trial.scenario = "free:" + name
        trials.append(trial)
        t = trial.t1 + rng.uniform(0.2, 2.0)
    ns = lambda s: t_base_ns + int(s * 1e9)
    sid = "20261118-120000-synthetic"
    sdir = os.path.join(out_dir, "sessions", sid)
    os.makedirs(sdir, exist_ok=True)
    tp = "event6"
    meta = {
        "format": "luma-touch-lab/1", "recorder_version": 1, "synthetic": True, "session": sid,
        "boot_id": boot, "started": "2026-11-18T12:00:00+0000",
        "clock": {"realtime_minus_monotonic_ns": 1_763_467_200_000_000_000 - t_base_ns,
                  "boottime_minus_monotonic_ns": 0},
        "host": {"product": "synthetic (XPS 14 DA14260 descriptor)", "kernel": "", "os": []},
        "libinput": {"tool": "synthetic", "flags": ["--enable-tap", "--set-click-method=clickfinger"],
                     "settings": {}, "devices": ["/dev/input/event6"]},
        "devices": {"touchpad": {"evdev": "/dev/input/event6", "sysname": tp, "hidraw": "/dev/hidraw1",
                                 "name": "VEN_2C2F:00 2C2F:0034 Touchpad", "report_descriptor": desc.hex(),
                                 "feature_reports": {"8": "0815", "10": "0a00", "11": "0b64"},
                                 "evdev_description": synthetic_evdev_description(layout)},
                    "keyboards": [{"index": 0, "internal": True, "name": "AT Translated Set 2 keyboard"}]},
        "key_quantum_ns": 10_000_000,
    }
    records: list[tuple[int, list]] = [(ns(0), [ns(0), "H", meta])]
    model = EvdevModel(len(layout.slots))
    for t_s, report, _frame in render_reports(trials, layout, rng):
        records.append((ns(t_s), [ns(t_s), "h", report.hex()]))
        dec = layout.decode(report)
        scan_us = int(dec.scan_time * layout.scan_time_unit() * 1e6)
        frame = model.frame(dec, scan_us)
        kernel_us = (ns(t_s) - 400_000) // 1000
        records.append((ns(t_s) + 50_000, [ns(t_s) + 50_000, "e", kernel_us, frame]))
    t0_lib = trials[0].t0 if trials else 0.0
    for tr in trials:
        for k in tr.keys:
            q = (ns(k) // 10_000_000) * 10_000_000
            records.append((q, [q, "k", 0]))
        for t_s, text in libinput_lines(tr, tp, rng, lambda s: s - t0_lib):
            records.append((ns(t_s) + 900_000, [ns(t_s) + 900_000, "l", text]))
    records.sort(key=lambda r: r[0])
    with gzip.open(os.path.join(sdir, "stream-000001.jsonl.gz"), "wt", encoding="utf-8") as fh:
        for _t, rec in records:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    with open(os.path.join(sdir, "session.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)
    write_synthetic_labels(out_dir, trials, ns, boot, natural_start, rng)
    return sdir


def write_synthetic_labels(out_dir, trials, ns, boot, natural_start, rng) -> None:
    os.makedirs(os.path.join(out_dir, "labels"), exist_ok=True)
    lines = []
    run = "synthetic-run"

    def rec(t, kind, **f):
        lines.append({"v": 1, "t": t, "boot_id": boot, "kind": kind, **f})
    guided = [tr for tr in trials if not tr.scenario.startswith("free:")]
    rec(ns(guided[0].t0 - 0.5), "run", phase="begin", run=run, mode="guided")
    counter: dict[str, int] = {}
    for tr in guided:
        counter[tr.scenario] = counter.get(tr.scenario, 0) + 1
        rec(ns(tr.t0 - 0.2), "scenario", phase="begin", run=run, scenario=tr.scenario,
            trial=counter[tr.scenario], intent=tr.intent)
        for x in tr.actors:
            rec(ns(tr.t1), "contact", run=run, scenario=tr.scenario, trial=counter[tr.scenario],
                down=ns(x.t0), contact_id=x.ident, label=x.label, role=x.role,
                **{"class": CLASSES[x.label]}, how="script")
        rec(ns(tr.t1), "scenario", phase="end", run=run, scenario=tr.scenario, trial=counter[tr.scenario])
    rec(ns(guided[-1].t1 + 0.1), "run", phase="end", run=run, mode="guided")
    rec(ns(natural_start - 0.1), "run", phase="begin", run="synthetic-free", mode="free")
    free = [tr for tr in trials if tr.scenario.startswith("free:") and tr.intent == "accidental"]
    for tr, which in zip(free[:3], ("wrong", "almost", "wrong")):
        rec(ns(tr.t1 + 0.6), "mark", mark=which, window_ns=MARK_WINDOW_NS, source="shortcut")
    rec(ns(trials[-1].t1 + 0.1), "run", phase="end", run="synthetic-free", mode="free")
    with open(os.path.join(out_dir, "labels", "20261118.jsonl"), "w", encoding="utf-8") as fh:
        for r in lines:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")


def synthetic_evdev_description(layout: hid.TouchpadLayout) -> dict:
    xr = round((layout.x_range[1] - layout.x_range[0]) / (layout.x_mm or W))
    yr = round((layout.y_range[1] - layout.y_range[0]) / (layout.y_mm or H))
    absinfo = {}
    for code, (lo, hi), res in ((ABS_X, layout.x_range, xr), (ABS_Y, layout.y_range, yr),
                                (ABS_MT_POSITION_X, layout.x_range, xr), (ABS_MT_POSITION_Y, layout.y_range, yr),
                                (ABS_PRESSURE, layout.pressure_range, 0), (ABS_MT_PRESSURE, layout.pressure_range, 0),
                                (ABS_MT_SLOT, (0, 4), 0), (ABS_MT_TOOL_TYPE, (0, 2), 0),
                                (ABS_MT_TRACKING_ID, (0, 65535), 0)):
        absinfo[str(code)] = {"value": 0, "minimum": lo, "maximum": hi, "fuzz": 0, "flat": 0, "resolution": res}
    return {"name": "VEN_2C2F:00 2C2F:0034 Touchpad", "id": {"bustype": 0x18, "vendor": 0x2C2F,
                                                            "product": 0x34, "version": 0x100},
            "properties": [0, 2, 7], "codes": {"1": [BTN_LEFT, BTN_TOOL_FINGER, BTN_TOOL_QUINTTAP, BTN_TOUCH,
                                                     BTN_TOOL_DOUBLETAP, BTN_TOOL_TRIPLETAP, BTN_TOOL_QUADTAP],
                                               "3": sorted(int(c) for c in absinfo), "4": [MSC_TIMESTAMP]},
            "absinfo": absinfo, "synthetic": True}
