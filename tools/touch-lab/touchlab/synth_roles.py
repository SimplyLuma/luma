# SPDX-License-Identifier: Apache-2.0
"""Synthetic active sessions on every surface class, with gold roles.

Hands are scripted per drill (pointing fingers, second fingers, resting and
clicking thumbs, palms, brushes), each with a path, a true force curve, a
contact area and a firmware story, then sampled the way a device of the
class would report them: its rate, its pad size, and only the signals its
mask says it has (capacitive "pressure" is area, not force). Devices and
users vary (size, force and speed scale, posture), and a share of every
role is drawn from the hard end (thumbs landing high, small palms, still
pointing fingers) so the task isn't trivially separable.

This is synthetic data. Accuracy measured on it says the pipeline works and
which signals matter; it says nothing about real hands until recordings
from active sessions replace it.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable

from .rfeatures import NAN, Contact, Scene

CLASS_DEVICES = {
    # pad (w, h) mm ranges, report Hz, probability of each signal
    "legacy-clickpad": {"w": (70, 105), "h": (40, 60), "hz": (80, 80), "pressure": 0.85, "force": 0.0,
                        "size": 0.15, "minor": 0.0, "confidence": 0.15, "button": 1.0, "keyboard": 1.0,
                        "pos_noise": 0.08},
    "ptp-clickpad": {"w": (95, 135), "h": (55, 80), "hz": (125, 125), "pressure": 0.4, "force": 0.0,
                     "size": 0.5, "minor": 0.6, "confidence": 0.95, "button": 1.0, "keyboard": 1.0,
                     "pos_noise": 0.04},
    "force-pad": {"w": (110, 150), "h": (65, 90), "hz": (120, 125), "pressure": 1.0, "force": 1.0,
                  "size": 0.2, "minor": 0.5, "confidence": 0.9, "button": 1.0, "keyboard": 1.0,
                  "pos_noise": 0.03},
    "touchscreen": {"w": (64, 78), "h": (140, 165), "hz": (120, 240), "pressure": 0.4, "force": 0.0,
                    "size": 0.9, "minor": 0.7, "confidence": 0.2, "button": 0.0, "keyboard": 0.0,
                    "pos_noise": 0.05},
}
SURFACE_CLASSES = tuple(CLASS_DEVICES)


@dataclass
class Device:
    cls: str
    pad: tuple
    hz: float
    mask: dict
    pos_noise: float
    xps: bool = False
    force_scale: float = 1.0                     # full-scale force differs between pads


def sample_device(cls: str, rng: random.Random) -> Device:
    d = CLASS_DEVICES[cls]
    mask = {k: int(rng.random() < d[k]) for k in ("pressure", "size", "minor", "confidence", "button", "keyboard")}
    mask["force"] = int(mask["pressure"] and rng.random() < d["force"])
    mask["minor"] = int(mask["minor"] and mask["size"])
    mask["direct"] = int(cls == "touchscreen")
    mask["mt"] = 1
    return Device(cls, (rng.uniform(*d["w"]), rng.uniform(*d["h"])), rng.uniform(*d["hz"]), mask, d["pos_noise"],
                  force_scale=rng.uniform(0.6, 1.5))


def xps14_device() -> Device:
    """The XPS 14's Sensel pad: force, firmware confidence, haptic click, no size."""
    mask = {"pressure": 1, "force": 1, "size": 0, "minor": 0, "confidence": 1, "button": 1, "keyboard": 1,
            "direct": 0, "mt": 1, "haptic": 1}
    return Device("force-pad", (149.6, 73.8), 120.0, mask, 0.03, xps=True)


@dataclass
class User:
    force: float = 1.0
    size: float = 1.0
    speed: float = 1.0
    thumb_y: float = 0.86          # where this user's thumb usually rests (fraction of height)
    thumb_x: float = 0.6
    right_handed: bool = True


def sample_user(rng: random.Random) -> User:
    right = rng.random() < 0.88
    return User(force=rng.uniform(0.65, 1.45), size=rng.uniform(0.85, 1.2), speed=rng.uniform(0.6, 1.6),
                thumb_y=rng.uniform(0.74, 0.94), thumb_x=rng.uniform(0.45, 0.75) if right else rng.uniform(0.25, 0.55),
                right_handed=right)


@dataclass
class Actor:
    role: str
    t0: float                                     # ms
    dur: float                                    # ms
    path: Callable[[float], tuple]                # u in 0..1 -> (x, y) mm
    force: Callable[[float], float]               # true force, 0..1 of a 600 gf full scale
    area: Callable[[float], float]                # contact area, mm^2
    aspect: float = 1.15
    press: Callable[[float], bool] = lambda u: False
    flag_at: float = 2.0                          # u at which firmware flags it a palm (>1: never)


# ── Building blocks ─────────────────────────────────────────────────────────

def _line(a, b, ease=True):
    def f(u):
        if ease:
            u = 0.5 - 0.5 * math.cos(math.pi * u)
        return a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u
    return f


def _still(p, wobble, rng, drift=(0.0, 0.0)):
    ph1, ph2 = rng.random() * 6.28, rng.random() * 6.28
    fx, fy = rng.uniform(4, 11), rng.uniform(4, 11)
    return lambda u: (p[0] + drift[0] * u + wobble * math.sin(ph1 + u * fx),
                      p[1] + drift[1] * u + wobble * math.cos(ph2 + u * fy))


def _plateau(level, rise=0.1, fall=0.1, wobble=0.0, rng=None, bump=None):
    ph = (rng or random).random() * 6.28

    def f(u):
        v = level * min(1.0, u / rise if rise > 0 else 1.0, (1 - u) / fall if fall > 0 else 1.0)
        v *= 1 + wobble * math.sin(ph + u * 13)
        if bump and bump[0] <= u <= bump[1]:
            mid, half = (bump[0] + bump[1]) / 2, (bump[1] - bump[0]) / 2
            v += bump[2] * max(0.0, 1 - abs(u - mid) / half) ** 0.5
        return max(0.0, v)
    return f


def _travel(a, b, frac, rng, wait=0.0):
    """Hold still for `wait` of the contact, travel a -> b until `frac`, then hold at b."""
    line, hold = _line(a, b), _still(b, 0.12, rng)

    def f(u):
        if u < wait:
            return a
        v = (u - wait) / max(1e-6, frac - wait)
        return line(v) if v < 1 else hold(u)
    return f


def _const(v):
    return lambda u: v


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


class Script:
    def __init__(self, dev: Device, user: User, rng: random.Random) -> None:
        self.dev, self.user, self.rng = dev, user, rng
        self.W, self.H = dev.pad
        self.actors: list[Actor] = []
        self.keys: list[float] = []
        self.ts = dev.cls == "touchscreen"

    def u(self, lo, hi):
        return self.rng.uniform(lo, hi)

    def pt(self, xr=(0.15, 0.85), yr=(0.12, 0.75)):
        return (self.W * self.u(*xr), self.H * self.u(*yr))

    # Contacts by role ---------------------------------------------------
    def finger(self, t, dur, path, role="point", level=None, area=None, press=None, thumb=False):
        r, us = self.rng, self.user
        lvl = (level if level is not None else self.u(0.05, 0.2)) * us.force
        a = (area if area is not None else (self.u(130, 230) if thumb else self.u(55, 115))) * us.size
        flag = self.u(0.05, 0.5) if r.random() < 0.01 else 2.0
        # Fingers load anywhere from 10 to 150 ms: rise time alone must not give the role away.
        rise = min(0.45, self.u(10, 150) / max(dur, 60))
        self.actors.append(Actor(role, t, dur, path, _plateau(lvl, rise, min(0.3, self.u(10, 80) / max(dur, 60)),
                                                                 self.u(0.0, 0.12), r),
                                 _plateau(a, 0.08, 0.06, 0.04, r), self.u(1.3, 1.8) if thumb else self.u(1.05, 1.3),
                                 press or (lambda u: False), flag))

    def stroke(self, t, role="point", slow=False, start=None, thumb=False):
        speed = (self.u(20, 60) if slow else self.u(40, 180)) * self.user.speed
        a = start or self.pt()
        ang = self.u(0, 6.283)
        dist = self.u(10, 45) if not self.ts else self.u(15, 70)
        b = (_clamp(a[0] + dist * math.cos(ang), 2, self.W - 2), _clamp(a[1] + dist * math.sin(ang), 2, self.H - 2))
        dur = max(120.0, math.hypot(b[0] - a[0], b[1] - a[1]) / speed * 1000)
        self.finger(t, dur, _line(a, b), role, thumb=thumb)
        return t + dur

    def tap(self, t, role="point", at=None, thumb=False):
        dur = self.u(50, 170)
        self.finger(t, dur, _still(at or self.pt(), 0.12, self.rng), role, level=self.u(0.06, 0.25), thumb=thumb)
        return t + dur

    def hold(self, t, dur, at=None, role="point"):
        self.finger(t, dur, _still(at or self.pt(), 0.2, self.rng, (self.u(-1, 1), self.u(-1, 1))), role)
        return t + dur

    def rest_thumb(self, t, dur, role="thumb-rest"):
        r, us = self.rng, self.user
        if self.ts:
            # A holding finger or thumb on the screen's edge, partly off the glass.
            side = 0 if r.random() < 0.5 else 1
            x = self.u(0.3, 3.5) if side == 0 else self.W - self.u(0.3, 3.5)
            p = (x, self.H * self.u(0.35, 0.85))
            area = self.u(70, 160) * us.size
        else:
            hard = r.random() < 0.15
            y = self.H * (self.u(0.52, 0.72) if hard else _clamp(us.thumb_y + self.u(-0.06, 0.06), 0.6, 0.985))
            p = (self.W * _clamp(us.thumb_x + self.u(-0.12, 0.12), 0.08, 0.92), y)
            area = self.u(120, 240) * us.size
        roll = (self.u(-1.5, 1.5), self.u(-1.0, 1.0))
        lvl = self.u(0.05, 0.22) * us.force
        flag = self.u(0.1, 0.6) if r.random() < 0.12 else 2.0
        self.actors.append(Actor(role, t, dur, _still(p, self.u(0.1, 0.45), r, roll),
                                 _plateau(lvl, self.u(0.02, 0.12), 0.03, self.u(0.03, 0.15), r),
                                 _plateau(area, 0.05, 0.03, 0.06, r), self.u(1.35, 1.9), lambda u: False, flag))
        return p

    def click_thumb(self, t, dur, hold=(0.35, 0.6), at=None):
        r, us = self.rng, self.user
        p = at or (self.W * _clamp(us.thumb_x + self.u(-0.15, 0.15), 0.1, 0.9),
                   self.H * _clamp(us.thumb_y + self.u(-0.08, 0.06), 0.55, 0.985))
        rest = self.u(0.05, 0.16) * us.force
        peak = self.u(0.28, 0.6) * us.force
        lo, hi = hold
        flag = self.u(0.1, 0.5) if r.random() < 0.08 else 2.0
        self.actors.append(Actor("thumb-click", t, dur, _still(p, self.u(0.1, 0.35), r),
                                 _plateau(rest, 0.08, 0.06, 0.05, r, (lo - 0.08, hi + 0.04, peak)),
                                 _plateau(self.u(130, 230) * us.size, 0.08, 0.05, 0.05, r), self.u(1.35, 1.9),
                                 lambda u, lo=lo, hi=hi: lo < u < hi, flag))

    def palm(self, t, dur, at=None, moving=False, role="palm", small=None):
        r, us = self.rng, self.user
        small = r.random() < 0.12 if small is None else small
        if at is None:
            if self.ts:
                at = (self.u(0, 12) if r.random() < 0.5 else self.W - self.u(0, 12), self.H * self.u(0.75, 1.0))
            else:
                side = r.random()
                at = ((self.W * self.u(0.0, 0.2), self.H * self.u(0.35, 1.0)) if side < 0.4 else
                      (self.W * self.u(0.8, 1.0), self.H * self.u(0.35, 1.0)) if side < 0.8 else
                      (self.W * self.u(0.2, 0.8), self.H * self.u(0.75, 1.0)))
        area = (self.u(150, 300) if small else self.u(300, 900)) * us.size
        if moving:
            b = (_clamp(at[0] + self.u(-60, 60), 0, self.W), _clamp(at[1] + self.u(-20, 10), 0, self.H))
            path = _line(at, b)
        else:
            path = _still(at, self.u(0.3, 1.2), r, (self.u(-3, 3), self.u(-2, 2)))
        flag = self.u(0.02, 0.25) if r.random() < 0.75 else 2.0
        self.actors.append(Actor(role, t, dur, path,
                                 _plateau(self.u(0.12, 0.5) * us.force, self.u(0.03, 0.35), 0.1, self.u(0.02, 0.2), r),
                                 _plateau(area, self.u(0.1, 0.3), 0.1, 0.1, r), self.u(1.4, 2.6),
                                 lambda u: False, flag))

    def brush(self, t, at=None, knuckle=None):
        r, us = self.rng, self.user
        knuckle = r.random() < 0.4 if knuckle is None else knuckle
        dur = self.u(15, 110) if r.random() < 0.85 else self.u(110, 200)
        at = at or (self.pt((0.02, 0.98), (0.3, 1.0)) if not self.ts else
                    (self.u(0, 6) if r.random() < 0.5 else self.W - self.u(0, 6), self.H * self.u(0.1, 0.95)))
        if knuckle:
            path = _still(at, 0.3, r)
            force = _plateau(self.u(0.15, 0.55) * us.force, 0.3, 0.4, 0.0, r)
            area, aspect = self.u(40, 120), self.u(1.1, 1.5)
        else:
            ang = self.u(0, 6.283)
            d = self.u(2, 14)
            path = _line(at, (at[0] + d * math.cos(ang), at[1] + d * math.sin(ang)), ease=False)
            force = _plateau(self.u(0.015, 0.12) * us.force, 0.35, 0.35, 0.0, r)
            area, aspect = self.u(50, 200), self.u(1.6, 3.2)
        flag = self.u(0.2, 0.8) if r.random() < 0.25 else 2.0
        self.actors.append(Actor("brush", t, dur, path, force, _plateau(area * us.size, 0.3, 0.3, 0.0, r),
                                 aspect, lambda u: False, flag))
        return t + dur

    def type_burst(self, t, n):
        for _ in range(n):
            t += self.u(90, 260) / max(0.5, self.user.speed ** 0.3)
            self.keys.append(t)
        return t


def drill_script(drill: str, dev: Device, user: User, rng: random.Random, seconds: float) -> Script:
    s = Script(dev, user, rng)
    u, ts = s.u, s.ts
    t = u(100, 400)
    end = seconds * 1000
    while t < end:
        if drill == "point-slow":
            t = s.stroke(t, slow=True, start=s.pt((0.1, 0.9), (0.08, 0.9) if rng.random() < 0.15 else (0.1, 0.75)),
                         thumb=ts and rng.random() < 0.4) + u(150, 700)
        elif drill == "point-precise":
            a, b = s.pt(), s.pt()
            travel = max(80.0, math.hypot(b[0] - a[0], b[1] - a[1]) / (u(40, 150) * user.speed) * 1000)
            dur = travel + u(250, 900)                     # travel to the dot, then hold still on it
            s.finger(t, dur, _travel(a, b, travel / dur, rng))
            t += dur + u(200, 600)
        elif drill in ("thumb-rest-point", "thumb-rest-tap", "scroll-thumb-down") or (
                ts and drill in ("thumb-click", "typing")):
            dur = u(2000, 6000)
            thumb_first = rng.random() < 0.75
            t_rest = t if thumb_first else t + u(150, 600)
            s.rest_thumb(t_rest, dur)
            if ts and rng.random() < 0.4:
                s.rest_thumb(t_rest + u(0, 300), dur * u(0.6, 1.0))       # a second holding finger
            k = t + (u(150, 700) if thumb_first else 0)
            while k < t_rest + dur - 400:
                if drill == "thumb-rest-tap" or (ts and drill == "typing"):
                    k = s.tap(k, thumb=ts) + (u(150, 400) if ts and drill == "typing" else u(500, 1100))
                elif drill == "scroll-thumb-down" and not ts:
                    k = scroll_pair(s, k) + u(200, 600)
                else:
                    k = s.stroke(k, thumb=ts and drill == "thumb-click") + u(150, 600)
            t = t_rest + dur + u(300, 900)
        elif drill == "thumb-click" and not ts:
            dur = u(3000, 6000)
            idx = s.pt((0.3, 0.7), (0.15, 0.55))
            s.hold(t, dur, idx)                           # the resting index finger points
            k = t + u(300, 700)
            while k < t + dur - 700:
                d = u(220, 600)
                s.click_thumb(k, d)
                k += d + u(250, 700)
            t += dur + u(300, 800)
        elif drill == "thumb-click-drag" and not ts:
            d = u(900, 2200)
            s.click_thumb(t, d, hold=(0.15, 0.88))
            k = t + d * u(0.15, 0.3)
            s.stroke(k, start=s.pt((0.2, 0.8), (0.1, 0.55)))
            s.actors[-1].dur = min(s.actors[-1].dur, t + d * 0.85 - k)
            t += d + u(300, 900)
        elif drill == "thumb-click-drag":
            a, b = s.pt(), s.pt()
            dur = u(700, 1600)
            s.finger(t, dur, _travel(a, b, 1.0, rng, wait=u(0.3, 0.5)))    # touch and hold, then drag
            t += dur + u(200, 600)
        elif drill == "scroll":
            t = scroll_pair(s, t) + u(250, 800)
        elif drill == "typing":
            burst_end = s.type_burst(t, rng.randint(6, 16))
            for side in (0, 1):
                if rng.random() < 0.8:
                    at = (s.W * (u(0.0, 0.22) if side == 0 else u(0.78, 1.0)), s.H * u(0.4, 1.0))
                    s.palm(t + u(-300, 300), max(200.0, burst_end - t + u(-200, 600)), at=at)
            for _ in range(rng.randint(0, 3)):
                s.brush(u(t, burst_end), knuckle=False)
            t = burst_end + u(300, 1200)
        elif drill == "palm-heel":
            s.palm(t, u(300, 1600), at=s.pt((0.1, 0.9), (0.4, 1.0)) if not ts else None)
            t += s.actors[-1].dur + u(300, 900)
        elif drill == "knuckle-brush":
            t = s.brush(t) + u(200, 700)
        elif drill == "reach-grip":
            if rng.random() < 0.5 and not ts:
                s.palm(t, u(250, 700), at=s.pt((0.05, 0.95), (0.3, 0.9)), moving=True)
                s.type_burst(t + u(0, 200), rng.randint(1, 4))
                t += s.actors[-1].dur + u(300, 800)
            else:
                n = rng.randint(1, 3)
                dur = u(150, 1400)
                for i in range(n):
                    at = ((s.W * u(0.1, 0.9), s.H - u(0.2, 3.0)) if not ts else
                          (u(0, 4) if rng.random() < 0.5 else s.W - u(0, 4), s.H * u(0.1, 0.95)))
                    if dur >= 220:
                        s.palm(t + u(0, 80), dur * u(0.7, 1.0), at=at, small=True)
                    else:
                        s.brush(t + u(0, 60), at=at)
                t += dur + u(300, 900)
        else:
            raise KeyError(drill)
    return s


def scroll_pair(s: Script, t: float) -> float:
    u, rng = s.u, s.rng
    a = s.pt((0.2, 0.7), (0.15, 0.6))
    gap = u(12, 26)
    ang = math.radians(u(-25, 25))
    dy = rng.choice((-1, 1)) * u(12, 35) * (2 if s.ts else 1)
    dx = u(-4, 4)
    dur = max(200.0, abs(dy) / (u(40, 150) * s.user.speed) * 1000)
    lag = u(0, 70) if rng.random() < 0.85 else u(70, 160)
    if s.ts and rng.random() < 0.5:                       # a pinch on screens
        c = a
        spread = rng.choice((-1, 1)) * u(10, 25)
        s.finger(t, dur, _line((c[0] - 10, c[1]), (c[0] - 10 - spread, c[1])), "point")
        s.finger(t + lag, dur - lag * 0.5, _line((c[0] + 10, c[1] + 4), (c[0] + 10 + spread, c[1] + 4)), "second")
        return t + dur
    b = (a[0] + gap * math.cos(ang), a[1] + gap * math.sin(ang))
    s.finger(t, dur, _line(a, (a[0] + dx, a[1] + dy)), "point")
    s.finger(t + lag, dur - lag * 0.6, _line(b, (b[0] + dx, b[1] + dy)), "second")
    return t + dur


# ── Sampling into what the device reports ──────────────────────────────────

def render(s: Script, t_offset: float = 0.0) -> Scene:
    dev, rng = s.dev, s.rng
    m = dev.mask
    step = 1000.0 / dev.hz
    out = []
    for i, a in enumerate(sorted(s.actors, key=lambda a: a.t0)):
        if a.dur < step:
            a.dur = step * 1.2
        c = Contact(f"{t_offset + a.t0:.3f}:{i}", t_offset + a.t0, t_offset + a.t0 + a.dur, role=a.role)
        phase = rng.uniform(0, step)
        t = a.t0 + phase * 0.3
        flagged = False
        while t <= a.t0 + a.dur:
            uu = (t - a.t0) / a.dur
            x, y = a.path(uu)
            x = _clamp(x + rng.gauss(0, dev.pos_noise), 0, s.W)
            y = _clamp(y + rng.gauss(0, dev.pos_noise), 0, s.H)
            force = _clamp(a.force(uu) * dev.force_scale * (1 + rng.gauss(0, 0.03)), 0, 1)
            area = max(8.0, a.area(uu) * (1 + rng.gauss(0, 0.04)))
            if dev.cls == "touchscreen":
                # Partly off the glass at an edge: the panel reports what is on it.
                edge = min(x, s.W - x, y, s.H - y)
                area *= _clamp(0.45 + edge / 8, 0.45, 1.0)
            p = NAN
            if m.get("pressure"):
                p = force if m.get("force") else _clamp(area / 700 + rng.gauss(0, 0.01), 0, 1)
                p = round(p * 255) / 255 if not m.get("force") else round(p * 4095) / 4095
            maj = mn = NAN
            if m.get("size"):
                maj = 2 * math.sqrt(area * a.aspect / math.pi)
                maj = round(maj * 2) / 2
                if m.get("minor"):
                    mn = round(2 * math.sqrt(area / a.aspect / math.pi) * 2) / 2
            conf = NAN
            if m.get("confidence"):
                flagged = flagged or uu >= a.flag_at
                conf = 0.0 if flagged else 1.0
            btn = 1 if (m.get("button") and a.press(uu)) else 0
            c.add(t_offset + t, x, y, p, maj, mn, conf, btn)
            t += step * (1 + rng.gauss(0, 0.02))
        if c.t:
            c.t_up = max(c.t_up, c.t[-1] + 0.5)
            out.append(c)
    # A click is one button for the whole pad: every contact down while it is pressed sees it.
    if m.get("button"):
        presses = [(c.t[i], c.t[i + 1] if i + 1 < len(c.t) else c.t[i] + step) for c in out
                   for i in range(len(c.t)) if c.btn[i]]
        if presses:
            for c in out:
                for i, tt in enumerate(c.t):
                    if not c.btn[i] and any(a <= tt < b for a, b in presses):
                        c.btn[i] = 1
    keys = sorted(t_offset + round(k / 10) * 10 for k in s.keys) if m.get("keyboard") else []
    return Scene(out, keys, (s.W, s.H), dict(m), dev.cls, "synthetic")


def session(drills: list[str], dev: Device, user: User, rng: random.Random, seconds: float = 20.0) -> list[Scene]:
    scenes = []
    for d in drills:
        sc = render(drill_script(d, dev, user, rng, seconds))
        for c in sc.contacts:
            c.drill = d
        scenes.append(sc)
    return scenes


def corpus(n_users: int = 6, seconds: float = 15.0, seed: int = 1, classes=SURFACE_CLASSES,
           include_xps: bool = True, drills: list[str] | None = None) -> list[Scene]:
    """Scenes from n_users simulated people on every class (one sampled device each), plus the XPS."""
    from .drills import DRILLS
    rng = random.Random(seed)
    ids = drills or [d.id for d in DRILLS]
    scenes = []
    for k in range(n_users):
        user = sample_user(rng)
        devs = [sample_device(c, rng) for c in classes]
        if include_xps:
            devs.append(xps14_device())
        for dev in devs:
            for sc in session(ids, dev, user, rng, seconds):
                sc.source = f"synthetic:user{k}:{dev.cls}{':xps14' if dev.xps else ''}"
                scenes.append(sc)
    return scenes
