# SPDX-License-Identifier: Apache-2.0
"""Training, evaluation and export of the contact-role model.

Two stages, both gradient-boosted trees over rfeatures rows from every
window (25/50/100/200 ms, the window is an input):
  stage 1 sees the contact, the surface mask, the other contacts' geometry
          and motion, and keyboard timing;
  stage 2 adds what stage 1 thinks the other contacts are (their role
          probabilities), trained on out-of-fold stage-1 predictions so it
          learns from the same quality of context it gets at run time.
A contact's role at any moment is the vote of every window evaluated so far
(Schwarz et al. CHI'14: windows grow from birth and votes accumulate).

The libinput-style baseline below is what Luma has today (thresholds,
edge zones, disable-while-typing, a thumb zone) scored on the same rows.
"""

from __future__ import annotations

import json
import math
import os
import random
import time

from . import gbdt
from .rfeatures import NAMES, STAGE1, WINDOWS_MS, Scene, contact_features, scene_others
from .roles import INDEX, ROLES, SUPPRESS

VOTE_WEIGHT = {25: 0.6, 50: 0.8, 100: 1.0, 200: 1.2}
ROLE_SUPPRESS = [1 if r in SUPPRESS else 0 for r in ROLES]
CLASS_WEIGHT_CAP = 4.0


def _user(scene: Scene) -> str:
    parts = scene.source.split(":")
    return parts[1] if len(parts) > 1 else scene.source


def _class(scene: Scene) -> str:
    return scene.surface_class + (":xps14" if scene.source.endswith(":xps14") else "")


# ── Rows ────────────────────────────────────────────────────────────────────

class Rows:
    def __init__(self) -> None:
        self.X: list[list[float]] = []
        self.y: list[int] = []
        self.meta: list[tuple] = []          # (scene index, contact index, window)

    def add(self, x, y, meta) -> None:
        self.X.append(x)
        self.y.append(y)
        self.meta.append(meta)


def select(row: list[float], names: list[str]) -> list[float]:
    idx = [NAMES.index(n) for n in names]
    return [row[i] for i in idx]


def stage1_rows(scenes: list[Scene], windows=WINDOWS_MS) -> Rows:
    out = Rows()
    for si, sc in enumerate(scenes):
        for ci, c in enumerate(sc.contacts):
            if not c.t or c.role not in INDEX:
                continue
            for w in windows:
                row = contact_features(c, scene_others(sc, ci, w), sc.keys, sc.pad_mm, sc.mask, w)
                out.add(row, INDEX[c.role], (si, ci, w))
    return out


def context_probs(scenes, rows1: Rows, probs1: list[list[float]]):
    """For each scene contact: {window: stage-1 probs}, to build the others' role context."""
    table: dict[tuple, dict[int, list[float]]] = {}
    for (si, ci, w), p in zip(rows1.meta, probs1):
        table.setdefault((si, ci), {})[w] = p
    return table


def roles_for(scene_idx: int, scene: Scene, ci: int, window: float, table) -> dict:
    """The others' latest stage-1 opinion that existed at the end of this contact's window."""
    c = scene.contacts[ci]
    n = 0
    end = c.t_down + window
    for i, t in enumerate(c.t):
        if t <= end:
            n = i + 1
    tq = c.t[max(0, n - 1)]
    out = {}
    for oi, o in enumerate(scene.contacts):
        if oi == ci:
            continue
        have = table.get((scene_idx, oi))
        if not have:
            continue
        best = None
        for w, p in have.items():
            ready = o.t_down + w if (o.t_up is None or o.t_up > o.t_down + w) else o.t_up
            if ready <= tq and (best is None or w > best[0]):
                best = (w, p)
        if best:
            out[o.key] = best[1]
    return out


def stage2_rows(scenes: list[Scene], rows1: Rows, table) -> Rows:
    out = Rows()
    for (si, ci, w), y in zip(rows1.meta, rows1.y):
        sc = scenes[si]
        c = sc.contacts[ci]
        roles = roles_for(si, sc, ci, w, table)
        row = contact_features(c, scene_others(sc, ci, w), sc.keys, sc.pad_mm, sc.mask, w, roles)
        out.add(row, y, (si, ci, w))
    return out


def weights(y: list[int]) -> list[float]:
    counts = [max(1, y.count(k)) for k in range(len(ROLES))]
    mean = sum(counts) / len(counts)
    cw = [min(CLASS_WEIGHT_CAP, mean / c) for c in counts]
    return [cw[v] for v in y]


# ── Training ────────────────────────────────────────────────────────────────

DEFAULTS = {"rounds": 60, "depth": 4, "lr": 0.2, "colsample": 0.6, "subsample": 0.8, "max_bins": 24}


def train(scenes: list[Scene], params: dict | None = None, valid_scenes: list[Scene] | None = None, log=None,
          seed: int = 0) -> dict:
    """Both stages. Returns {"stage1": Model, "stage2": Model}."""
    p = {**DEFAULTS, **(params or {})}
    rows1 = stage1_rows(scenes)
    X1 = [select(r, STAGE1) for r in rows1.X]
    v1 = None
    if valid_scenes:
        vr = stage1_rows(valid_scenes)
        v1 = ([select(r, STAGE1) for r in vr.X], vr.y)
    w1 = weights(rows1.y)
    m1 = gbdt.fit(X1, rows1.y, list(ROLES), STAGE1, w=w1, valid=v1, seed=seed, log=log, **p)
    # Out-of-fold stage-1 opinions (two folds by user) for stage 2's context.
    users = sorted({_user(s) for s in scenes})
    fold = {u: i % 2 for i, u in enumerate(users)}
    oof = [None] * len(rows1.y)
    if len(users) > 1:
        for k in (0, 1):
            tr = [i for i, (si, _ci, _w) in enumerate(rows1.meta) if fold[_user(scenes[si])] != k]
            te = [i for i, (si, _ci, _w) in enumerate(rows1.meta) if fold[_user(scenes[si])] == k]
            mk = gbdt.fit([X1[i] for i in tr], [rows1.y[i] for i in tr], list(ROLES), STAGE1,
                          w=[w1[i] for i in tr], seed=seed + 1 + k, **{**p, "rounds": max(10, len(m1.trees) // 6)})
            for i in te:
                oof[i] = mk.proba(X1[i])
    else:
        oof = [m1.proba(x) for x in X1]
    table = context_probs(scenes, rows1, oof)
    rows2 = stage2_rows(scenes, rows1, table)
    v2 = None
    if valid_scenes:
        vr1 = stage1_rows(valid_scenes)
        vt = context_probs(valid_scenes, vr1, [m1.proba(select(r, STAGE1)) for r in vr1.X])
        vr2 = stage2_rows(valid_scenes, vr1, vt)
        v2 = (vr2.X, vr2.y)
    m2 = gbdt.fit(rows2.X, rows2.y, list(ROLES), NAMES, w=weights(rows2.y), valid=v2, seed=seed + 7, log=log, **p)
    for m, n in ((m1, rows1), (m2, rows2)):
        m.meta.update({"rows": len(n.y), "synthetic": all(s.source.startswith("synthetic") for s in scenes)})
    return {"stage1": m1, "stage2": m2}


# ── Inference over a scene (what the plugin does, in Python) ────────────────

def predict_scene(models: dict, sc: Scene, windows=WINDOWS_MS) -> dict:
    """{contact index: {window: (stage-2 probs, accumulated vote probs)}} in plugin order."""
    m1, m2 = models["stage1"], models["stage2"]
    # A window is evaluated when it ends, or at lift if the contact lifts first (as in the plugin).
    events = sorted(((min(c.t_down + w, c.t_up if c.t_up is not None else c.t_down + w), ci, w)
                     for ci, c in enumerate(sc.contacts) if c.t for w in windows))
    s1: dict[tuple, dict[int, list[float]]] = {}
    out: dict[int, dict[int, tuple]] = {}
    votes: dict[int, list[float]] = {}
    for _t, ci, w in events:
        c = sc.contacts[ci]
        others = scene_others(sc, ci, w)
        # The role context only fills the stage-2 columns, so one row serves both stages.
        roles = roles_for(0, sc, ci, w, s1)
        row = contact_features(c, others, sc.keys, sc.pad_mm, sc.mask, w, roles)
        s1.setdefault((0, ci), {})[w] = m1.proba(select(row, STAGE1))
        p = m2.proba(row)
        v = votes.setdefault(ci, [0.0] * len(ROLES))
        for k in range(len(ROLES)):
            v[k] += VOTE_WEIGHT.get(w, 1.0) * p[k]
        z = sum(v)
        out.setdefault(ci, {})[w] = (p, [x / z for x in v])
    return out


# ── The libinput-style baseline ─────────────────────────────────────────────

def baseline_suppress(f: dict) -> int:
    """What libinput 1.31's heuristics would do with this contact by the end of the window (1 = ignore it).
    f: contact_features(..., as_dict=True)."""
    ok = lambda v: v == v
    if f["m_confidence"] and ok(f["conf_frac"]) and f["conf_frac"] > 0:
        return 1                                          # tool-palm from the firmware
    if f["m_pressure"] and not f["m_force"] and ok(f["p_max"]) and f["p_max"] > 130 / 255:
        return 1                                          # pressure palm (default threshold 130)
    if f["m_size"] and ok(f["maj_max"]) and f["maj_max"] > 25.0:
        return 1                                          # touch-size palm
    if not f["m_direct"]:
        if min(f["x_rel"], 1 - f["x_rel"]) < 0.08 and f["speed_max"] < 80:
            return 1                                      # edge palm: lands in the side 8% and stays
        if f["m_keyboard"] and ok(f["since_key_ms"]) and f["since_key_ms"] < 300:
            return 1                                      # disable-while-typing
        # Thumbs (evdev-mt-touchpad-thumb.c): jailed below the 92% line, or below 85% when pressure
        # or size says thumb; speed-based when another finger is moving and it isn't a scroll pair;
        # contextual when it lands late and lower than the finger already down.
        thumbish = ((f["m_pressure"] and not f["m_force"] and ok(f["p_max"]) and f["p_max"] > 100 / 255) or
                    (f["m_size"] and ok(f["maj_max"]) and f["maj_max"] > 15.0))
        if (f["y_rel"] > 0.92 or (f["y_rel"] > 0.85 and thumbish)) and f["speed_max"] < 60:
            return 1
        if f["others_down"] > 0 and f["others_moving"] > 0 and ok(f["nearest_down_mm"]) and f["nearest_down_mm"] > 35:
            return 1
        # Contextual: with two or more down, the lowest touch is a thumb when the next one is more than
        # 25 mm above it, unless both landed within 100 ms and it is above the 92% line.
        if f["others_now"] > 0 and ok(f["nearest_dy_mm"]) and f["nearest_dy_mm"] > 25:
            together = (f["others_down"] > 0 and f["since_prev_down_ms"] < 100) or \
                       (f["others_landed"] > 0 and f["others_down"] == 0 and f["elapsed_ms"] < 100)
            if not (together and f["y_rel"] < 0.92):
                return 1
    return 0


# ── Evaluation ──────────────────────────────────────────────────────────────

def evaluate(models: dict, scenes: list[Scene], windows=WINDOWS_MS) -> dict:
    """Per window: 6-way role accuracy (single window and accumulated votes), the track/ignore call
    against the baseline, and the failures that matter."""
    stats = {}

    def bucket(key):
        return stats.setdefault(key, {"n": 0, "role_ok": 0, "vote_ok": 0, "bin_ok": 0, "base_ok": 0, "late_ok": 0,
                                      "confusion": [[0] * len(ROLES) for _ in ROLES],
                                      "thumb_rest": [0, 0, 0, 0], "second": [0, 0, 0, 0], "point": [0, 0, 0, 0],
                                      "thumb_click": [0, 0, 0, 0]})
    for sc in scenes:
        pred = predict_scene(models, sc, windows)
        for ci, by_w in pred.items():
            c = sc.contacts[ci]
            if c.role not in INDEX:
                continue
            gold = INDEX[c.role]
            for w, (p, v) in by_w.items():
                row = contact_features(c, scene_others(sc, ci, w), sc.keys, sc.pad_mm, sc.mask, w, as_dict=True)
                base = baseline_suppress(row)
                # libinput re-decides when later touches land; give it the whole contact's life too.
                late = base or any(baseline_suppress(contact_features(c, scene_others(sc, ci, lw), sc.keys,
                                                                     sc.pad_mm, sc.mask, lw, as_dict=True))
                                   for lw in (400, 800, 1600, 3200) if lw > w and c.t_down + lw / 2 < (c.t_up or 0))
                top = max(range(len(ROLES)), key=lambda k: p[k])
                vtop = max(range(len(ROLES)), key=lambda k: v[k])
                for key in (("all", w), (_class(sc), w)):
                    b = bucket(key)
                    b["n"] += 1
                    b["role_ok"] += top == gold
                    b["vote_ok"] += vtop == gold
                    b["bin_ok"] += ROLE_SUPPRESS[vtop] == ROLE_SUPPRESS[gold]
                    b["base_ok"] += base == ROLE_SUPPRESS[gold]
                    b["late_ok"] += int(late) == ROLE_SUPPRESS[gold]
                    b["confusion"][gold][vtop] += 1
                    # [model wrong, baseline wrong, count] for the failures users feel.
                    crit = {"thumb-rest": "thumb_rest", "second": "second", "point": "point",
                            "thumb-click": "thumb_click"}.get(c.role)
                    if crit:
                        bad_model = ROLE_SUPPRESS[vtop] != ROLE_SUPPRESS[gold]
                        b[crit][0] += bad_model
                        b[crit][1] += base != ROLE_SUPPRESS[gold]
                        b[crit][2] += 1
                        b[crit][3] += int(late) != ROLE_SUPPRESS[gold]
    out = {}
    for (scope, w), b in sorted(stats.items(), key=lambda kv: (kv[0][0] != "all", kv[0])):
        n = max(1, b["n"])
        out.setdefault(scope, {})[str(w)] = {
            "contacts": b["n"], "role_acc": round(b["role_ok"] / n, 4), "vote_acc": round(b["vote_ok"] / n, 4),
            "track_ignore_acc": round(b["bin_ok"] / n, 4), "baseline_track_ignore_acc": round(b["base_ok"] / n, 4),
            "baseline_eventually_acc": round(b["late_ok"] / n, 4),
            "resting_thumb_tracked": _rate(b["thumb_rest"]), "second_finger_dropped": _rate(b["second"]),
            "pointing_finger_dropped": _rate(b["point"]), "clicking_thumb_counted": _rate(b["thumb_click"]),
            "confusion": b["confusion"],
        }
    return out


def _rate(v) -> dict:
    return {"model": round(v[0] / v[2], 4) if v[2] else None, "baseline": round(v[1] / v[2], 4) if v[2] else None,
            "baseline_eventually": round(v[3] / v[2], 4) if v[2] else None, "n": v[2]}


def latency(models: dict, scenes: list[Scene], limit: int = 300) -> dict:
    """Wall time per evaluation (features + both stages), Python, on this machine."""
    m1, m2 = models["stage1"], models["stage2"]
    times = {w: [] for w in WINDOWS_MS}
    count = 0
    for sc in scenes:
        for ci, c in enumerate(sc.contacts):
            if not c.t:
                continue
            for w in WINDOWS_MS:
                others = scene_others(sc, ci, w)
                t0 = time.perf_counter()
                row = contact_features(c, others, sc.keys, sc.pad_mm, sc.mask, w, {})
                m1.proba(select(row, STAGE1))
                m2.proba(row)
                times[w].append((time.perf_counter() - t0) * 1e6)
            count += 1
            if count >= limit:
                break
        if count >= limit:
            break
    return {str(w): {"median_us": round(sorted(v)[len(v) // 2], 1), "p95_us": round(sorted(v)[int(len(v) * 0.95)], 1)}
            for w, v in times.items() if v}


# ── Personal retrain ────────────────────────────────────────────────────────

def personalise(models: dict, scenes: list[Scene], rounds: int = 6, log=None) -> dict:
    """A few extra trees per stage fitted to one person's drill and correction data, on top of the
    shipped model. Seconds on a laptop, pure Python."""
    t0 = time.time()
    # Small steps and big leaves: a few minutes of one person's data nudges the shipped model, it
    # doesn't replace it.
    p = {"rounds": rounds, "depth": 3, "lr": 0.08, "colsample": 0.6, "subsample": 0.9, "max_bins": 16,
         "min_hess": 3.0}
    rows1 = stage1_rows(scenes)
    X1 = [select(r, STAGE1) for r in rows1.X]
    m1 = gbdt.fit(X1, rows1.y, list(ROLES), STAGE1, w=weights(rows1.y), init=models["stage1"], log=log, **p)
    table = context_probs(scenes, rows1, [m1.proba(x) for x in X1])
    rows2 = stage2_rows(scenes, rows1, table)
    m2 = gbdt.fit(rows2.X, rows2.y, list(ROLES), NAMES, w=weights(rows2.y), init=models["stage2"], log=log, **p)
    for m in (m1, m2):
        m.meta["personal_rows"] = len(rows1.y)
        m.meta["personal_s"] = round(time.time() - t0, 2)
    return {"stage1": m1, "stage2": m2}


# ── Files ───────────────────────────────────────────────────────────────────

def save(models: dict, path: str, extra: dict | None = None) -> None:
    doc = {"format": "luma-touch-roles/1", "roles": list(ROLES), "windows_ms": list(WINDOWS_MS),
           "vote_weight": {str(k): v for k, v in VOTE_WEIGHT.items()}, "features": NAMES,
           "stage1": models["stage1"].to_json(), "stage2": models["stage2"].to_json(), **(extra or {})}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    os.replace(tmp, path)


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    return {"stage1": gbdt.Model.from_json(doc["stage1"]), "stage2": gbdt.Model.from_json(doc["stage2"]),
            "doc": {k: v for k, v in doc.items() if k not in ("stage1", "stage2")}}


def split_users(scenes: list[Scene], test_share: float = 0.25, seed: int = 0):
    users = sorted({_user(s) for s in scenes})
    rng = random.Random(seed)
    rng.shuffle(users)
    k = max(1, int(round(len(users) * test_share)))
    test = set(users[:k])
    return [s for s in scenes if _user(s) not in test], [s for s in scenes if _user(s) in test]


def summary(report: dict) -> str:
    """A short table for the terminal and docs/model.md."""
    lines = ["| Window | Contacts | Role (one window) | Role (votes so far) | Track/ignore | Baseline at window | "
             "Baseline, whole contact |", "|---|---|---|---|---|---|---|"]
    for w, r in report["eval"]["all"].items():
        lines.append(f"| {w} ms | {r['contacts']} | {r['role_acc']:.1%} | {r['vote_acc']:.1%} | "
                     f"{r['track_ignore_acc']:.1%} | {r['baseline_track_ignore_acc']:.1%} | "
                     f"{r['baseline_eventually_acc']:.1%} |")
    lines += ["", "| Surface | 25 ms votes | 100 ms votes | 200 ms track/ignore | baseline |", "|---|---|---|---|---|"]
    for scope, by_w in report["eval"].items():
        if scope == "all":
            continue
        lines.append(f"| {scope} | {by_w['25']['vote_acc']:.1%} | {by_w['100']['vote_acc']:.1%} | "
                     f"{by_w['200']['track_ignore_acc']:.1%} | {by_w['200']['baseline_track_ignore_acc']:.1%} |")
    a = report["eval"]["all"]["100"]
    lines += ["", "| At 100 ms | Model | Baseline at window | Baseline, whole contact |", "|---|---|---|---|"]
    for k, name in (("resting_thumb_tracked", "Resting thumb tracked (moves the pointer / breaks a scroll)"),
                    ("second_finger_dropped", "Second finger dropped (scroll or gesture breaks)"),
                    ("pointing_finger_dropped", "Pointing finger dropped"),
                    ("clicking_thumb_counted", "Clicking thumb tracked (jitters the pointer, turns a click right)")):
        v = a[k]
        if v["n"]:
            lines.append(f"| {name} | {v['model']:.1%} | {v['baseline']:.1%} | {v['baseline_eventually']:.1%} |")
    return "\n".join(lines)
