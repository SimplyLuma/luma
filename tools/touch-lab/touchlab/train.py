# SPDX-License-Identifier: Apache-2.0
"""`touchlab train`: build the data, train both stages, measure, export.

Data, in order of preference: active-session recordings with role labels
(--root; private, never in the repository), the phase 1 HID replay of the
XPS 14 descriptor (real decoders, scripted hands), and synthetic sessions
on every surface class. Until recordings exist the model is synthetic and
the report says so in its first line.
"""

from __future__ import annotations

import json
import os
import random
import time

from . import luart, plugin, rfeatures, rolemodel, surfaces, synth, synth_roles
from .rfeatures import Scene, from_track
from .roles import ROLES


def surface_from_meta(meta: dict, descriptor: bytes = b"", quirk_dirs=("/usr/share/libinput", "/etc/libinput")):
    """(mask, class, pad mm) for a recorded or live touchpad, from the recorder header."""
    tp = meta.get("devices", {}).get("touchpad", {})
    caps = surfaces.from_description(tp.get("evdev_description") or {}, "recording")
    surfaces.apply_quirks(caps, surfaces.load_quirks([d for d in quirk_dirs if os.path.exists(d)]))
    s = surfaces.classify(caps)
    mask = dict(s.mask)
    if descriptor:
        # Recordings read HID directly: force, confidence and size come from the reports themselves.
        lo = synth.hid.TouchpadLayout(synth.hid.parse_descriptor(descriptor))
        if lo.ok and lo.slots:
            first = lo.slots[0]
            mask["pressure"] = int("pressure" in first)
            mask["confidence"] = int("confidence" in first)
            mask["size"] = int("width" in first)
            mask["minor"] = int("height" in first)
            # A pad with a haptics feature report and tip pressure is a force pad.
            if mask["pressure"] and s.cls != "touchscreen" and "11" in tp.get("feature_reports", {}):
                mask["force"] = 1
                if s.cls != "force-pad":
                    s.cls = "force-pad"
                    s.reasons.append("HID tip pressure with a haptics feature report")
    return mask, s.cls, s.pad_mm


def _session_mask(session) -> tuple[dict, str, tuple]:
    return surface_from_meta(session.meta, session.descriptor)


def scenes_from_root(root: str, source: str = "recording") -> list[Scene]:
    """Every recorded session under root with role labels, one scene per drill window."""
    from . import dataset
    from .session import Session, find_sessions
    out = []
    for path in find_sessions(root):
        sess = Session(path, root)
        j = dataset.join(sess)
        mask, cls, _pad = _session_mask(sess)
        tracks = [t for t in j.tracks if t.samples]
        if not tracks:
            continue
        t0 = tracks[0].t_down
        contacts = [from_track(t, t0) for t in tracks]
        keys = [(k - t0) / 1e6 for k in j.keys_internal]
        # One scene per session: every contact is context for every other, as on the machine.
        if any(c.role for c in contacts):
            out.append(Scene(contacts, keys, j.pad_mm, mask, cls, f"{source}:{sess.id}:{cls}"))
    return out


def replay_scenes(seed: int = 11) -> list[Scene]:
    """Phase 1's synthetic HID session (real XPS 14 descriptor) through the real decoders."""
    import tempfile
    import shutil
    root = tempfile.mkdtemp(prefix="touchlab-replay-")
    try:
        synth.build_session(root, seed=seed, trials_per=3, free_minutes=0.0)
        scenes = scenes_from_root(root, "replay")
    finally:
        shutil.rmtree(root)
    for sc in scenes:
        sc.source = sc.source.replace("replay:", "replay:xps-hid:", 1)
    return scenes


def lua_latency(models: dict, scenes: list[Scene], n: int = 200) -> dict | None:
    """Per evaluation (features + both stages) in real Lua 5.4, where liblua exists."""
    if not luart.available():
        return None
    from . import gbdt
    cases = []
    for sc in scenes:
        for ci, c in enumerate(sc.contacts):
            if len(cases) >= n:
                break
            cases.append((c, rfeatures.scene_others(sc, ci, 100), sc))
    def ctab(c):
        return ("{key=%s,t_down=%r,t_up=%s,t=%s,x=%s,y=%s,p=%s,maj=%s,mn=%s,conf=%s,btn=%s,s1={}}" % (
            luart.table(c.key), c.t_down, luart.table(c.t_up), luart.table(c.t), luart.table(c.x), luart.table(c.y),
            luart.table(c.p), luart.table(c.maj), luart.table(c.mn), luart.table(c.conf), luart.table(c.btn)))
    data = "local CASES = {" + ",".join(
        "{c=%s,o={%s},k=%s,w=%r,h=%r,m={%s}}" % (ctab(c), ",".join(ctab(o) for o in others), luart.table(sc.keys),
                                                 sc.pad_mm[0], sc.pad_mm[1],
                                                 ",".join(f"{k}={int(bool(v))}" for k, v in sc.mask.items()))
        for c, others, sc in cases) + "}\n"
    idx = ("local function idx(names) local pos = {} for i, n in ipairs(NAMES) do pos[n] = i end "
           "local out = {} for j, n in ipairs(names) do out[j] = pos[n] end return out end\n"
           "local I1, I2 = idx(STAGE1.features), idx(STAGE2.features)\n"
           "local function pick(ix, row) local x = {} for j = 1, #ix do x[j] = row[ix[j]] end return x end\n")
    src = (plugin.features_chunk() + gbdt.to_lua(models["stage1"], "STAGE1") + gbdt.to_lua(models["stage2"], "STAGE2")
           + gbdt.LUA_EVAL + data + idx +
           "local t0 = os.clock()\nlocal reps = 0\n"
           "for r = 1, 5 do for _, e in ipairs(CASES) do\n"
           "  for _, w in ipairs({25, 50, 100, 200}) do\n"
           "    local row = contact_features(e.c, e.o, e.k, e.w, e.h, e.m, w, {})\n"
           "    softmax(model_raw(STAGE1, pick(I1, row)))\n"
           "    softmax(model_raw(STAGE2, pick(I2, row)))\n"
           "    reps = reps + 1\n  end\nend end\n"
           "return string.format('%.3f %d', (os.clock() - t0) * 1e6 / reps, reps)\n")
    us, reps = luart.run(src).split()
    return {"mean_us_per_evaluation": float(us), "evaluations": int(reps),
            "per_contact_us": round(float(us) * 4, 1)}


def personalisation(models: dict, test: list[Scene], seed: int = 0) -> dict:
    """A new person on the XPS 14 with a heavy hand and a high-resting thumb: retrain on one active
    session (all drills), test on a second session of the same person."""
    from .drills import DRILLS
    rng = random.Random(seed + 101)
    user = synth_roles.User(force=1.4, size=1.1, speed=0.8, thumb_y=0.72, thumb_x=0.62)
    dev = synth_roles.xps14_device()
    ids = [d.id for d in DRILLS]
    fit = synth_roles.session(ids, dev, user, rng, 20.0)
    held = synth_roles.session(ids, dev, user, rng, 20.0)
    for sc in fit + held:
        sc.source = "synthetic:personal:force-pad:xps14"
    before = rolemodel.evaluate(models, held)["all"]
    t0 = time.time()
    tuned = rolemodel.personalise(models, fit)
    secs = time.time() - t0
    after = rolemodel.evaluate(tuned, held)["all"]
    rows = sum(1 for sc in fit for c in sc.contacts if c.role)
    return {"who": "synthetic person: heavy hand (force x1.4), thumb resting at 72% of the pad height",
            "fit_contacts": rows, "retrain_s": round(secs, 2),
            "vote_acc_before": {w: before[w]["vote_acc"] for w in before},
            "vote_acc_after": {w: after[w]["vote_acc"] for w in after},
            "track_ignore_before": {w: before[w]["track_ignore_acc"] for w in before},
            "track_ignore_after": {w: after[w]["track_ignore_acc"] for w in after}}


def run(out_dir: str, *, users: int = 10, seconds: float = 20.0, rounds: int = 60, depth: int = 4, seed: int = 1,
        root: str | None = None, log=print) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    real = scenes_from_root(root) if root else []
    scenes = synth_roles.corpus(n_users=users, seconds=seconds, seed=seed)
    train, test = rolemodel.split_users(scenes, 0.25, seed)
    rtrain, rtest = rolemodel.split_users(real, 0.25, seed) if real else ([], [])
    replay = replay_scenes()
    synthetic = not real
    log(f"{'SYNTHETIC: ' if synthetic else ''}{len(train) + len(rtrain)} train scenes, "
        f"{len(test) + len(rtest)} test scenes, {len(replay)} replay scenes")
    vtrain, valid = rolemodel.split_users(train, 0.15, seed + 1)
    t0 = time.time()
    models = rolemodel.train(vtrain + rtrain, {"rounds": rounds, "depth": depth}, valid_scenes=valid, seed=seed,
                             log=lambda m: None)
    train_s = time.time() - t0
    log(f"trained in {train_s:.0f}s: stage 1 {len(models['stage1'].trees) // len(ROLES)} rounds, "
        f"stage 2 {len(models['stage2'].trees) // len(ROLES)} rounds")
    ev = rolemodel.evaluate(models, test + rtest)
    ev_replay = rolemodel.evaluate(models, replay)
    report = {
        "data": "synthetic" if synthetic else "recorded+synthetic",
        "note": ("Trained and tested on synthetic sessions only: these numbers show the pipeline and the "
                 "signals work; they are not a measure of real-hand accuracy.") if synthetic else "",
        "train_scenes": len(train) + len(rtrain), "test_scenes": len(test) + len(rtest),
        "train_rows_stage1": models["stage1"].meta.get("rows"), "train_s": round(train_s, 1),
        "trees": {k: len(m.trees) for k, m in models.items() if k in ("stage1", "stage2")},
        "nodes": {k: m.nodes() for k, m in models.items() if k in ("stage1", "stage2")},
        "eval": ev, "replay_xps_hid": ev_replay.get("all"),
        "latency_python": rolemodel.latency(models, test),
        "latency_lua": lua_latency(models, test),
        "personalisation": personalisation(models, test, seed),
        "importance_stage2": dict(list(models["stage2"].importance().items())[:25]),
    }
    rolemodel.save(models, os.path.join(out_dir, "roles-model.json"), {"report_summary": {
        "data": report["data"], "note": report["note"]}})
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    quirk_dirs = ["/usr/share/libinput", "/etc/libinput"]
    plugin.write(models, os.path.join(out_dir, "plugin"), quirk_dirs)
    log(rolemodel.summary(report))
    return report
