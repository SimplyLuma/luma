# SPDX-License-Identifier: Apache-2.0
"""Per-contact role features over windows that grow from touch-down.

Surface-neutral: a contact is a stream of samples (time ms, x/y mm, and
pressure, size and firmware confidence where the surface has them), the
same thing the Lua plugin keeps per evdev slot. A signal the surface lacks
is NaN, and the mask bits (m_*) say which signals are real, so one model
family covers every surface class.

Every feature at window H uses only what had arrived H ms after touch-down.
contact_features() is written loop by loop to match plugins/luma-touch-roles.lua
line for line; tests/test_roles.py runs both on the same contacts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .roles import ROLES

NAN = float("nan")
WINDOWS_MS = (25, 50, 100, 200)
KEEP_PREVIOUS_MS = 5000

FEATURES: dict[str, str] = {
    "window_ms": "the window this row describes (25, 50, 100 or 200 ms after touch-down)",
    "m_pressure": "mask: the surface reports per-contact pressure",
    "m_force": "mask: that pressure is real force (pressure pad), not contact area",
    "m_size": "mask: the surface reports contact size (touch major)",
    "m_confidence": "mask: the firmware flags palms (MT_TOOL_PALM / HID Confidence)",
    "m_button": "mask: a click button under the surface",
    "m_direct": "mask: a touchscreen",
    "m_keyboard": "mask: a built-in keyboard next to the surface",
    "x_rel": "touch-down x, fraction of the width",
    "y_rel": "touch-down y, fraction of the height (1 = bottom)",
    "edge_x_mm": "touch-down distance to the nearer side edge",
    "edge_top_mm": "touch-down distance to the top edge",
    "edge_bottom_mm": "touch-down distance to the bottom edge",
    "edge_min_mm": "touch-down distance to the nearest edge",
    "n": "samples in the window",
    "elapsed_ms": "time covered (the window, or less if lifted)",
    "lifted": "1 if the contact lifted inside the window",
    "path_mm": "path length",
    "net_mm": "straight-line displacement",
    "max_disp_mm": "largest distance from the touch-down point",
    "straightness": "net / path (0 when still)",
    "speed_max": "highest speed between samples, mm/s",
    "speed_mean": "mean speed, mm/s",
    "speed_last": "speed over the last 20 ms, mm/s",
    "still_frac": "fraction of steps under 10 mm/s",
    "jitter_mm": "RMS distance from the straight path",
    "p_first": "pressure at touch-down (0..1 of range)",
    "p_max": "highest pressure",
    "p_mean": "mean pressure",
    "p_last": "pressure at the end of the window",
    "p_std": "pressure standard deviation",
    "p_rise": "pressure slope over the first 30 ms, per second",
    "p_t_max_ms": "time of the highest pressure",
    "p_drop": "p_max - p_last",
    "p_settle_cv": "coefficient of variation over the window's second half (stability)",
    "p_area": "pressure integrated over the window (fraction of range x ms): the force-time shape",
    "p_rise_ms": "time to reach 80% of the peak",
    "maj_first": "contact major axis at touch-down, mm",
    "maj_max": "largest major axis, mm",
    "maj_mean": "mean major axis, mm",
    "maj_growth": "major axis change over the window, mm",
    "min_max": "largest minor axis, mm",
    "aspect": "maj_max / min_max",
    "conf_first": "firmware confidence at touch-down (1 confident, 0 palm)",
    "conf_frac": "fraction of samples flagged not confident",
    "conf_false_ms": "time to the first not-confident sample (-1 if none)",
    "button": "1 if the button went down in the window",
    "button_at_ms": "time of the first button-down (-1 if none)",
    "p_at_button": "pressure when the button went down",
    "others_down": "other contacts on the surface at touch-down",
    "others_now": "other contacts on the surface at the end of the window",
    "others_landed": "other contacts that landed after this one, in the window",
    "first_on_pad": "1 if nothing else was down at touch-down",
    "nearest_mm": "distance to the nearest other contact at the end of the window",
    "nearest_down_mm": "distance to the nearest other contact at touch-down",
    "nearest_dy_mm": "this contact's y minus the nearest other's (positive = lower)",
    "nearest_speed": "speed of the nearest other contact over the window, mm/s",
    "others_moving": "other contacts moving faster than 15 mm/s in the window",
    "others_still": "other contacts present and slower than that",
    "rel_y_mm": "this contact's y minus the mean y of the others",
    "since_key_ms": "time since the last key press (capped 10000)",
    "keys_1s": "key presses in the second before touch-down",
    "keys_300ms": "key presses in the 300 ms before touch-down",
    "keys_window": "key presses inside the window",
    "o_point": "highest pointing-finger probability among the others (stage 2)",
    "o_second": "highest second-finger probability among the others (stage 2)",
    "o_thumb": "highest thumb probability (resting or clicking) among the others (stage 2)",
    "o_palm": "highest palm-or-brush probability among the others (stage 2)",
    "nearest_role": "role index of the nearest other contact (stage 2)",
}
NAMES = list(FEATURES)
# Computed (and kept in step with the plugin) but not model inputs: on drill data they mostly
# encode the drill's rhythm, which doesn't carry over to free use.
EXTRA = ("since_prev_down_ms", "since_prev_up_ms")
STAGE2 = ("o_point", "o_second", "o_thumb", "o_palm", "nearest_role")
STAGE1 = [n for n in NAMES if n not in STAGE2]
MASKED = {
    "pressure": ("p_first", "p_max", "p_mean", "p_last", "p_std", "p_rise", "p_t_max_ms", "p_drop",
                 "p_settle_cv", "p_area", "p_rise_ms", "p_at_button"),
    "size": ("maj_first", "maj_max", "maj_mean", "maj_growth", "min_max", "aspect"),
    "confidence": ("conf_first", "conf_frac", "conf_false_ms"),
    "button": ("button", "button_at_ms", "p_at_button"),
    "keyboard": ("since_key_ms", "keys_1s", "keys_300ms", "keys_window"),
}


@dataclass
class Contact:
    """One contact as the plugin sees it: parallel arrays, times in ms, NaN where a signal is absent."""
    key: str
    t_down: float
    t_up: float | None
    t: list = field(default_factory=list)
    x: list = field(default_factory=list)
    y: list = field(default_factory=list)
    p: list = field(default_factory=list)
    maj: list = field(default_factory=list)
    mn: list = field(default_factory=list)
    conf: list = field(default_factory=list)
    btn: list = field(default_factory=list)
    role: str | None = None          # gold role, when known
    drill: str | None = None

    def add(self, t, x, y, p=NAN, maj=NAN, mn=NAN, conf=NAN, btn=0) -> None:
        self.t.append(t)
        self.x.append(x)
        self.y.append(y)
        self.p.append(p)
        self.maj.append(maj)
        self.mn.append(mn)
        self.conf.append(conf)
        self.btn.append(btn)


@dataclass
class Scene:
    """Contacts on one surface, key-press times (ms) and the surface's mask."""
    contacts: list
    keys: list
    pad_mm: tuple
    mask: dict
    surface_class: str = ""
    source: str = ""


def from_track(tr, t0_ns: int = 0) -> Contact:
    """A contacts.Track (HID or evdev) as a Contact."""
    ms = lambda ns: (ns - t0_ns) / 1e6
    c = Contact(tr.key, ms(tr.t_down), None if tr.t_up is None else ms(tr.t_up))
    for s in tr.samples:
        conf = NAN if s.confident is None else (1.0 if s.confident else 0.0)
        c.add(ms(s.t), s.x, s.y, s.force if s.force is not None else NAN,
              NAN if getattr(s, "major", None) is None else s.major,
              NAN if getattr(s, "minor", None) is None else s.minor, conf, 1 if s.button else 0)
    c.role = getattr(tr, "role", None)
    c.drill = getattr(tr, "scenario", None)
    return c


def _ok(v) -> bool:
    return v == v


def contact_features(c: Contact, others: list, keys: list, pad_mm, mask: dict, window: float,
                     roles: dict | None = None, as_dict: bool = False):
    """One row (NAMES order). others: other contacts (active or lifted in the last few seconds).
    roles: {other key: [p per role]} for the stage-2 context, or None."""
    w, h = pad_mm
    t_down = c.t_down
    end = t_down + window
    n = 0
    for i in range(len(c.t)):
        if c.t[i] <= end:
            n = i + 1
        else:
            break
    if n < 1:
        n = 1
    tq = c.t[n - 1]
    lifted = c.t_up is not None and c.t_up <= end
    x0, y0 = c.x[0], c.y[0]
    f = {"window_ms": window}
    for bit in ("pressure", "force", "size", "confidence", "button", "direct", "keyboard"):
        f["m_" + bit] = 1 if mask.get(bit) else 0
    f["x_rel"] = x0 / w
    f["y_rel"] = y0 / h
    f["edge_x_mm"] = min(x0, w - x0)
    f["edge_top_mm"] = y0
    f["edge_bottom_mm"] = h - y0
    f["edge_min_mm"] = min(f["edge_x_mm"], y0, h - y0)
    f["n"] = n
    f["elapsed_ms"] = (c.t_up if lifted else min(end, tq)) - t_down
    f["lifted"] = 1 if lifted else 0
    # Motion.
    path, smax, ssum, still = 0.0, 0.0, 0.0, 0
    for i in range(1, n):
        d = math.hypot(c.x[i] - c.x[i - 1], c.y[i] - c.y[i - 1])
        dt = max(1e-4, c.t[i] - c.t[i - 1])
        sp = d / dt * 1000.0
        path += d
        ssum += sp
        if sp > smax:
            smax = sp
        if sp < 10.0:
            still += 1
    net = math.hypot(c.x[n - 1] - x0, c.y[n - 1] - y0)
    mdisp = 0.0
    for i in range(n):
        d = math.hypot(c.x[i] - x0, c.y[i] - y0)
        if d > mdisp:
            mdisp = d
    f["path_mm"] = path
    f["net_mm"] = net
    f["max_disp_mm"] = mdisp
    f["straightness"] = net / path if path > 1e-6 else 0.0
    f["speed_max"] = smax
    f["speed_mean"] = ssum / (n - 1) if n > 1 else 0.0
    j = n - 1
    while j > 0 and c.t[j - 1] >= tq - 20.0:
        j -= 1
    f["speed_last"] = (math.hypot(c.x[n - 1] - c.x[j], c.y[n - 1] - c.y[j]) / max(1e-3, tq - c.t[j]) * 1000.0
                       if j < n - 1 else 0.0)
    f["still_frac"] = still / (n - 1) if n > 1 else 1.0
    acc = 0.0
    if n > 2 and net > 1e-6:
        ux, uy = (c.x[n - 1] - x0) / net, (c.y[n - 1] - y0) / net
        for i in range(n):
            e = (c.x[i] - x0) * uy - (c.y[i] - y0) * ux
            acc += e * e
    else:
        for i in range(n):
            acc += (c.x[i] - x0) ** 2 + (c.y[i] - y0) ** 2
    f["jitter_mm"] = math.sqrt(acc / n)
    # Pressure: on force pads the force-time shape stands in for contact size.
    for k in MASKED["pressure"]:
        f[k] = NAN
    if mask.get("pressure") and _ok(c.p[0]):
        pmax, psum, imax = c.p[0], 0.0, 0
        for i in range(n):
            psum += c.p[i]
            if c.p[i] > pmax:
                pmax, imax = c.p[i], i
        pmean = psum / n
        var = 0.0
        for i in range(n):
            var += (c.p[i] - pmean) ** 2
        f["p_first"], f["p_max"], f["p_mean"], f["p_last"] = c.p[0], pmax, pmean, c.p[n - 1]
        f["p_std"] = math.sqrt(var / n)
        e = 0
        for i in range(n):
            if c.t[i] <= t_down + 30.0:
                e = i
        f["p_rise"] = (c.p[e] - c.p[0]) / max(1e-3, c.t[e] - c.t[0]) * 1000.0 if e > 0 else 0.0
        f["p_t_max_ms"] = c.t[imax] - t_down
        f["p_drop"] = pmax - c.p[n - 1]
        a = n // 2
        hs, hv = 0.0, 0.0
        for i in range(a, n):
            hs += c.p[i]
        hm = hs / (n - a)
        for i in range(a, n):
            hv += (c.p[i] - hm) ** 2
        f["p_settle_cv"] = math.sqrt(hv / (n - a)) / hm if hm > 1e-6 else 0.0
        area = 0.0
        for i in range(1, n):
            area += (c.p[i] + c.p[i - 1]) * 0.5 * (c.t[i] - c.t[i - 1])
        f["p_area"] = area
        r = 0
        while r < n - 1 and c.p[r] < 0.8 * pmax:
            r += 1
        f["p_rise_ms"] = c.t[r] - t_down
    # Size.
    for k in MASKED["size"]:
        f[k] = NAN
    if mask.get("size") and _ok(c.maj[0]):
        mx, ms_ = c.maj[0], 0.0
        for i in range(n):
            ms_ += c.maj[i]
            if c.maj[i] > mx:
                mx = c.maj[i]
        f["maj_first"], f["maj_max"], f["maj_mean"] = c.maj[0], mx, ms_ / n
        f["maj_growth"] = c.maj[n - 1] - c.maj[0]
        if mask.get("minor") and _ok(c.mn[0]):
            mn = c.mn[0]
            for i in range(n):
                if c.mn[i] > mn:
                    mn = c.mn[i]
            f["min_max"] = mn
            f["aspect"] = mx / max(0.1, mn)
    # Firmware confidence.
    for k in MASKED["confidence"]:
        f[k] = NAN
    if mask.get("confidence") and _ok(c.conf[0]):
        bad, first_bad = 0, -1.0
        for i in range(n):
            if c.conf[i] < 0.5:
                bad += 1
                if first_bad < 0:
                    first_bad = c.t[i] - t_down
        f["conf_first"], f["conf_frac"], f["conf_false_ms"] = c.conf[0], bad / n, first_bad
    # Button.
    for k in ("button", "button_at_ms"):
        f[k] = NAN
    if mask.get("button"):
        b = -1
        for i in range(n):
            if c.btn[i] > 0 and b < 0:
                b = i
        f["button"] = 1 if b >= 0 else 0
        f["button_at_ms"] = c.t[b] - t_down if b >= 0 else -1.0
        if b >= 0 and mask.get("pressure") and _ok(c.p[b]):
            f["p_at_button"] = c.p[b]
    # Other contacts.
    down, now, landed, prev_down, prev_up = 0, 0, 0, -1e18, -1e18
    nearest, nearest_dy, nearest_speed, nearest_down = NAN, NAN, NAN, NAN
    nearest_key, moving, still_o, ysum, ycount = None, 0, 0, 0.0, 0
    best = {"point": NAN, "second": NAN, "thumb": NAN, "palm": NAN}
    for o in others:
        if o.t_down < t_down and o.t_down > prev_down:
            prev_down = o.t_down
        if o.t_up is not None and o.t_up <= t_down and o.t_up > prev_up:
            prev_up = o.t_up
        if o.t_down > end or (o.t_up is not None and o.t_up < t_down):
            continue
        if o.t_down <= t_down and (o.t_up is None or o.t_up > t_down):
            down += 1
            k = -1
            for i in range(len(o.t)):
                if o.t[i] <= t_down:
                    k = i
            if k >= 0:
                d = math.hypot(o.x[k] - x0, o.y[k] - y0)
                if not _ok(nearest_down) or d < nearest_down:
                    nearest_down = d
        if o.t_down > t_down:
            landed += 1
        if o.t_down <= tq and (o.t_up is None or o.t_up > tq):
            now += 1
        # Where it was at the end of the window, and how fast it moved inside it.
        k, k0 = -1, -1
        for i in range(len(o.t)):
            if o.t[i] <= tq:
                k = i
                if k0 < 0 and o.t[i] >= t_down:
                    k0 = i
        if k < 0:
            continue
        sp = 0.0
        if k0 >= 0 and k > k0:
            dist = 0.0
            for i in range(k0 + 1, k + 1):
                dist += math.hypot(o.x[i] - o.x[i - 1], o.y[i] - o.y[i - 1])
            sp = dist / max(1e-3, o.t[k] - o.t[k0]) * 1000.0
        if sp > 15.0:
            moving += 1
        else:
            still_o += 1
        ysum += o.y[k]
        ycount += 1
        d = math.hypot(o.x[k] - c.x[n - 1], o.y[k] - c.y[n - 1])
        if not _ok(nearest) or d < nearest:
            nearest, nearest_dy, nearest_speed, nearest_key = d, c.y[n - 1] - o.y[k], sp, o.key
        if roles is not None and o.key in roles:
            pr = roles[o.key]
            for name, v in (("point", pr[0]), ("second", pr[1]), ("thumb", pr[2] + pr[3]),
                            ("palm", pr[4] + pr[5])):
                if not _ok(best[name]) or v > best[name]:
                    best[name] = v
    f["others_down"], f["others_now"], f["others_landed"] = down, now, landed
    f["first_on_pad"] = 1 if down == 0 else 0
    f["since_prev_down_ms"] = min(5000.0, t_down - prev_down)
    f["since_prev_up_ms"] = min(5000.0, t_down - prev_up)
    f["nearest_mm"], f["nearest_down_mm"] = nearest, nearest_down
    f["nearest_dy_mm"], f["nearest_speed"] = nearest_dy, nearest_speed
    f["others_moving"], f["others_still"] = moving, still_o
    f["rel_y_mm"] = c.y[n - 1] - ysum / ycount if ycount > 0 else NAN
    # Keyboard: timing only.
    for k in MASKED["keyboard"]:
        f[k] = NAN
    if mask.get("keyboard"):
        last, k1, k3, kw = -1e18, 0, 0, 0
        for kt in keys:
            if kt <= t_down:
                if kt > last:
                    last = kt
                if kt > t_down - 1000.0:
                    k1 += 1
                if kt > t_down - 300.0:
                    k3 += 1
            elif kt <= end:
                kw += 1
        f["since_key_ms"] = min(10000.0, t_down - last)
        f["keys_1s"], f["keys_300ms"], f["keys_window"] = k1, k3, kw
    # Stage 2: what the model thinks the others are.
    f["o_point"], f["o_second"], f["o_thumb"], f["o_palm"] = best["point"], best["second"], best["thumb"], best["palm"]
    f["nearest_role"] = NAN
    if roles is not None and nearest_key is not None and nearest_key in roles:
        pr = roles[nearest_key]
        top = 0
        for i in range(1, len(ROLES)):
            if pr[i] > pr[top]:
                top = i
        f["nearest_role"] = top
    if as_dict:
        return f
    return [float(f[k]) for k in NAMES]


def scene_others(scene: Scene, idx: int, window: float) -> list:
    c = scene.contacts[idx]
    end = c.t_down + window
    out = []
    for k, o in enumerate(scene.contacts):
        if k == idx:
            continue
        if o.t_down > end:
            continue
        if o.t_up is not None and o.t_up < c.t_down - KEEP_PREVIOUS_MS:
            continue
        out.append(o)
    return out


def scene_rows(scene: Scene, windows=WINDOWS_MS, roles_at=None):
    """(contact index, window, row) for every contact and window. roles_at(idx, window) -> {key: probs}."""
    for i, c in enumerate(scene.contacts):
        if not c.t:
            continue
        for wnd in windows:
            others = scene_others(scene, i, wnd)
            roles = roles_at(i, wnd) if roles_at else None
            yield i, wnd, contact_features(c, others, scene.keys, scene.pad_mm, scene.mask, wnd, roles)
