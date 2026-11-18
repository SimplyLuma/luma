# SPDX-License-Identifier: Apache-2.0
"""Per-contact feature vectors over the first N ms of a touch.

Every feature at horizon N uses only what had arrived N ms after touch-down
(the contact's own samples, the other contacts' samples and key presses up
to that moment), so a model trained on horizon N can run at time N inside
libinput. Columns prefixed "li_" and "label" are outcomes, never inputs.
The full column list with meanings is FEATURES below and docs/dataset.md.
"""

from __future__ import annotations

import csv
import json
import math
import os

from .dataset import Joined

HORIZONS_MS = (25, 50, 100, 200)
SEQ_SAMPLES = 32

FEATURES: dict[str, str] = {
    "x0_mm": "touch-down x, mm from the left edge",
    "y0_mm": "touch-down y, mm from the top edge",
    "x0_rel": "touch-down x as a fraction of the pad width",
    "y0_rel": "touch-down y as a fraction of the pad height",
    "edge_left_mm": "distance from the left edge at touch-down",
    "edge_right_mm": "distance from the right edge at touch-down",
    "edge_top_mm": "distance from the top edge at touch-down",
    "edge_bottom_mm": "distance from the bottom edge at touch-down",
    "edge_min_mm": "distance from the nearest edge at touch-down",
    "n_samples": "reports seen for this contact so far",
    "elapsed_ms": "time covered (the horizon, or less if lifted)",
    "lifted": "1 if the contact lifted before the horizon",
    "dt_mean_ms": "mean time between this contact's reports",
    "f_first": "force in the first report (0..1 of range)",
    "f_max": "highest force so far",
    "f_mean": "mean force so far",
    "f_last": "force at the horizon",
    "f_std": "force standard deviation",
    "f_rise_per_s": "force slope over the first 30 ms",
    "f_t_max_ms": "time of the highest force after touch-down",
    "f_drop": "f_max minus f_last",
    "path_mm": "path length",
    "net_mm": "straight-line displacement from touch-down",
    "max_disp_mm": "largest distance from the touch-down point",
    "straightness": "net_mm / path_mm (1 for a straight line, 0 if still)",
    "speed_max_mm_s": "highest speed between reports",
    "speed_mean_mm_s": "mean speed",
    "speed_last_mm_s": "speed over the last 20 ms",
    "dir_deg": "direction of the net displacement (0 = right, 90 = down)",
    "still_frac": "fraction of steps under 10 mm/s",
    "jitter_mm": "RMS distance from the straight path",
    "conf_first": "firmware confidence in the first report (1, 0, or -1 if absent)",
    "conf_all": "1 if every report so far was confident",
    "conf_frac": "fraction of reports flagged not confident",
    "conf_first_false_ms": "time until the first not-confident report (-1 if none)",
    "button": "1 if the pad's button was down at any point",
    "button_at_ms": "time of the first button-down after touch-down (-1 if none)",
    "others_at_down": "other contacts on the pad at touch-down",
    "others_max": "most other contacts at once so far",
    "others_landed_since": "other contacts that landed after this one",
    "first_on_pad": "1 if no other contact was on the pad at touch-down",
    "since_prev_down_ms": "time since the previous touch-down of any contact (capped 5000)",
    "since_prev_up_ms": "time since the previous lift of any contact (capped 5000)",
    "nearest_other_mm": "distance to the nearest other contact (999 if none)",
    "others_speed_mean_mm_s": "mean speed of the other contacts over the window",
    "rel_y_to_others_mm": "this contact's y minus the others' mean y (positive = lower; 0 if none)",
    "moving_while_others_still": "1 if this moves while all others are still",
    "still_while_others_move": "1 if this is still while another moves",
    "since_key_ms": "time since the last key press on any keyboard (capped 10000)",
    "since_key_internal_ms": "time since the last key press on the built-in keyboard (capped 10000)",
    "keys_1s": "key presses in the second before touch-down",
    "keys_300ms": "key presses in the 300 ms before touch-down",
    "keys_window": "key presses between touch-down and the horizon",
}
OUTCOMES = {
    "li_verdict": "libinput's verdict over the whole contact: palm, thumb, thumb-suspect or finger",
    "li_first_ms": "when libinput first called it a palm or a suppressed thumb (-1 if never)",
    "kernel_palm": "1 if the kernel marked the slot MT_TOOL_PALM",
    "duration_ms": "whole-contact duration",
    "label": "gold label from a guided scenario (labels.py vocabulary)",
    "label_class": "intended or accidental (gold)",
    "label_source": "guided, mark-wrong, mark-almost, free-unmarked or none",
    "scenario": "guided scenario the contact happened in",
}
ID_COLUMNS = ("session", "contact", "cid", "t_down")


def _cap(v: float | None, cap: float) -> float:
    return cap if v is None else min(cap, v)


def contact_features(j: Joined, idx: int, horizon_ms: float) -> dict:
    tr = j.tracks[idx]
    w, h = j.pad_mm
    end = tr.t_down + int(horizon_ms * 1e6)
    ss = [s for s in tr.samples if s.t <= end] or tr.samples[:1]
    f: dict[str, float] = {}
    x0, y0 = ss[0].x, ss[0].y
    f.update(x0_mm=x0, y0_mm=y0, x0_rel=x0 / w, y0_rel=y0 / h, edge_left_mm=x0, edge_right_mm=w - x0,
             edge_top_mm=y0, edge_bottom_mm=h - y0, edge_min_mm=min(x0, w - x0, y0, h - y0))
    lifted = tr.t_up is not None and tr.t_up <= end
    f["n_samples"] = len(ss)
    f["elapsed_ms"] = ((tr.t_up if lifted else min(end, ss[-1].t)) - tr.t_down) / 1e6
    f["lifted"] = int(lifted)
    dts = [(b.t - a.t) / 1e6 for a, b in zip(ss, ss[1:])]
    f["dt_mean_ms"] = sum(dts) / len(dts) if dts else 0.0
    forces = [s.force for s in ss]
    f["f_first"], f["f_max"], f["f_last"] = forces[0], max(forces), forces[-1]
    f["f_mean"] = sum(forces) / len(forces)
    f["f_std"] = math.sqrt(sum((v - f["f_mean"]) ** 2 for v in forces) / len(forces))
    early = [s for s in ss if s.t <= tr.t_down + 30_000_000]
    f["f_rise_per_s"] = ((early[-1].force - early[0].force) / max(1e-3, (early[-1].t - early[0].t) / 1e9)
                         if len(early) > 1 else 0.0)
    imax = max(range(len(ss)), key=lambda i: ss[i].force)
    f["f_t_max_ms"] = (ss[imax].t - tr.t_down) / 1e6
    f["f_drop"] = f["f_max"] - f["f_last"]
    steps = [math.hypot(b.x - a.x, b.y - a.y) for a, b in zip(ss, ss[1:])]
    speeds = [d / max(1e-4, (b.t - a.t) / 1e9) for d, a, b in zip(steps, ss, ss[1:])]
    f["path_mm"] = sum(steps)
    f["net_mm"] = math.hypot(ss[-1].x - x0, ss[-1].y - y0)
    f["max_disp_mm"] = max(math.hypot(s.x - x0, s.y - y0) for s in ss)
    f["straightness"] = f["net_mm"] / f["path_mm"] if f["path_mm"] > 1e-6 else 0.0
    f["speed_max_mm_s"] = max(speeds, default=0.0)
    f["speed_mean_mm_s"] = sum(speeds) / len(speeds) if speeds else 0.0
    tail = [s for s in ss if s.t >= ss[-1].t - 20_000_000]
    f["speed_last_mm_s"] = (math.hypot(tail[-1].x - tail[0].x, tail[-1].y - tail[0].y) /
                            max(1e-3, (tail[-1].t - tail[0].t) / 1e9)) if len(tail) > 1 else 0.0
    f["dir_deg"] = math.degrees(math.atan2(ss[-1].y - y0, ss[-1].x - x0)) if f["net_mm"] > 0.2 else 0.0
    f["still_frac"] = sum(1 for v in speeds if v < 10.0) / len(speeds) if speeds else 1.0
    if len(ss) > 2 and f["net_mm"] > 1e-6:
        ux, uy = (ss[-1].x - x0) / f["net_mm"], (ss[-1].y - y0) / f["net_mm"]
        f["jitter_mm"] = math.sqrt(sum(((s.x - x0) * uy - (s.y - y0) * ux) ** 2 for s in ss) / len(ss))
    else:
        f["jitter_mm"] = math.sqrt(sum((s.x - x0) ** 2 + (s.y - y0) ** 2 for s in ss) / len(ss))
    confs = [s.confident for s in ss]
    f["conf_first"] = -1 if confs[0] is None else int(confs[0])
    f["conf_all"] = int(all(c is not False for c in confs))
    f["conf_frac"] = sum(1 for c in confs if c is False) / len(confs)
    first_false = next((s.t for s in ss if s.confident is False), None)
    f["conf_first_false_ms"] = -1 if first_false is None else (first_false - tr.t_down) / 1e6
    btn = next((s.t for s in ss if s.button), None)
    f["button"], f["button_at_ms"] = int(btn is not None), -1 if btn is None else (btn - tr.t_down) / 1e6
    # Other contacts.
    others = [o for k, o in enumerate(j.tracks) if k != idx and o.t_down <= end and (o.t_up or end) >= tr.t_down]
    at_down = [o for o in others if o.t_down <= tr.t_down]
    f["others_at_down"] = len(at_down)
    f["first_on_pad"] = int(not at_down)
    f["others_landed_since"] = sum(1 for o in others if o.t_down > tr.t_down)
    f["others_max"] = max((sum(1 for o in others if o.t_down <= s.t <= (o.t_up or end)) for s in ss), default=0)
    prev_downs = [o.t_down for o in j.tracks[max(0, idx - 12):idx] if o.t_down < tr.t_down]
    prev_ups = [o.t_up for o in j.tracks[max(0, idx - 12):idx] if o.t_up and o.t_up <= tr.t_down]
    f["since_prev_down_ms"] = _cap((tr.t_down - max(prev_downs)) / 1e6 if prev_downs else None, 5000)
    f["since_prev_up_ms"] = _cap((tr.t_down - max(prev_ups)) / 1e6 if prev_ups else None, 5000)
    nearest, others_speed, rel = 999.0, [], []
    for o in others:
        os_ = [s for s in o.samples if tr.t_down <= s.t <= end]
        if not os_:
            continue
        here = min(ss, key=lambda s: abs(s.t - os_[-1].t))
        nearest = min(nearest, math.hypot(os_[-1].x - here.x, os_[-1].y - here.y))
        rel.append(os_[-1].y)
        if len(os_) > 1:
            dur = max(1e-3, (os_[-1].t - os_[0].t) / 1e9)
            others_speed.append(sum(math.hypot(b.x - a.x, b.y - a.y) for a, b in zip(os_, os_[1:])) / dur)
    f["nearest_other_mm"] = nearest
    f["others_speed_mean_mm_s"] = sum(others_speed) / len(others_speed) if others_speed else 0.0
    f["rel_y_to_others_mm"] = ss[-1].y - sum(rel) / len(rel) if rel else 0.0
    me_moving = f["speed_mean_mm_s"] > 15.0
    f["moving_while_others_still"] = int(me_moving and bool(others_speed) and max(others_speed) < 15.0)
    f["still_while_others_move"] = int(not me_moving and bool(others_speed) and max(others_speed) >= 15.0)
    last_key = j.keys_before(tr.t_down)
    last_int = j.keys_before(tr.t_down, internal_only=True)
    f["since_key_ms"] = _cap((tr.t_down - last_key) / 1e6 if last_key else None, 10000)
    f["since_key_internal_ms"] = _cap((tr.t_down - last_int) / 1e6 if last_int else None, 10000)
    f["keys_1s"] = j.keys_between(tr.t_down - 1_000_000_000, tr.t_down)
    f["keys_300ms"] = j.keys_between(tr.t_down - 300_000_000, tr.t_down)
    f["keys_window"] = j.keys_between(tr.t_down, end)
    return f


def label_source(tr) -> str:
    if tr.label:
        return "guided"
    if "wrong" in tr.marks:
        return "mark-wrong"
    if "almost" in tr.marks:
        return "mark-almost"
    return "none"


def outcomes(j: Joined, tr) -> dict:
    src = label_source(tr)
    if src == "none" and any(r["mode"] == "free" and r["t0"] <= tr.t_down <= r["t1"] for r in j.runs):
        src = "free-unmarked"
    first = tr.libinput_first_ms()
    return {"li_verdict": tr.libinput_verdict(), "li_first_ms": -1 if first is None else round(first, 1),
            "kernel_palm": int(tr.kernel_palm_at is not None), "duration_ms": round(tr.duration_ms, 1),
            "label": tr.label or "", "label_class": tr.label_class or "", "label_source": src,
            "scenario": tr.scenario or ""}


def rows(j: Joined, horizon_ms: float):
    for i, tr in enumerate(j.tracks):
        if not tr.samples:
            continue
        f = contact_features(j, i, horizon_ms)
        yield {"session": j.session.id, "contact": tr.key, "cid": tr.cid, "t_down": tr.t_down,
               **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in f.items()}, **outcomes(j, tr)}


def sequence(j: Joined, tr) -> dict:
    """The first SEQ_SAMPLES reports as relative arrays, for a small sequence model."""
    ss = tr.samples[:SEQ_SAMPLES]
    x0, y0 = ss[0].x, ss[0].y
    return {"session": j.session.id, "contact": tr.key,
            "t_ms": [round((s.t - tr.t_down) / 1e6, 2) for s in ss],
            "dx_mm": [round(s.x - x0, 3) for s in ss], "dy_mm": [round(s.y - y0, 3) for s in ss],
            "force": [round(s.force, 4) for s in ss],
            "confident": [-1 if s.confident is None else int(s.confident) for s in ss],
            "x0_mm": round(x0, 2), "y0_mm": round(y0, 2), **outcomes(j, tr)}


def write(joined: list[Joined], out_dir: str, horizons=HORIZONS_MS) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for hz in horizons:
        path = os.path.join(out_dir, f"features-{int(hz)}ms.csv")
        cols = list(ID_COLUMNS) + list(FEATURES) + list(OUTCOMES)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for j in joined:
                for row in rows(j, hz):
                    w.writerow(row)
        written.append(path)
    seq = os.path.join(out_dir, "sequences.jsonl")
    with open(seq, "w", encoding="utf-8") as fh:
        for j in joined:
            for tr in j.tracks:
                if tr.samples:
                    fh.write(json.dumps(sequence(j, tr), separators=(",", ":")) + "\n")
    written.append(seq)
    schema = os.path.join(out_dir, "schema.json")
    with open(schema, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "horizons_ms": list(horizons), "id": list(ID_COLUMNS), "features": FEATURES,
                   "outcomes": OUTCOMES, "sequence_samples": SEQ_SAMPLES}, fh, indent=1)
    written.append(schema)
    return written
