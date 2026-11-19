# SPDX-License-Identifier: Apache-2.0
"""A small multi-class gradient-boosted tree model, written to be exported.

Softmax boosting with histogram splits, L2-regularised leaves and a learned
default direction for missing values (NaN), which is how one model serves
every surface: a signal a surface lacks is NaN and takes the branch that
training found best for "absent". Pure Python (numpy is used for the
histograms when it is installed), so it trains on any Luma machine; the
model is plain arrays that the Lua plugin walks with a dozen lines.

fit(..., init=model) keeps boosting from an existing model: the app's
personal retrain adds a few trees fitted to one person's corrections on top
of the shipped model.
"""

from __future__ import annotations

import bisect
import json
import math
import random
import time

try:                                    # optional: only speeds up histograms
    import numpy as _np
except ImportError:                     # pragma: no cover - depends on the machine
    _np = None


def _isnan(v: float) -> bool:
    return v != v


class Tree:
    """Flat arrays. Node i: feat[i] >= 0 splits on x[feat] <= thr[i] (left) else right; NaN goes to
    left if miss_left[i]. feat[i] == -1 is a leaf with value[i]."""

    __slots__ = ("cls", "feat", "thr", "left", "right", "miss_left", "value")

    def __init__(self, cls: int) -> None:
        self.cls = cls
        self.feat: list[int] = []
        self.thr: list[float] = []
        self.left: list[int] = []
        self.right: list[int] = []
        self.miss_left: list[int] = []
        self.value: list[float] = []

    def node(self) -> int:
        for a in (self.feat, self.left, self.right, self.miss_left):
            a.append(-1 if a is not self.miss_left else 1)
        self.thr.append(0.0)
        self.value.append(0.0)
        return len(self.feat) - 1

    def predict(self, x) -> float:
        i = 0
        feat, thr = self.feat, self.thr
        while feat[i] >= 0:
            v = x[feat[i]]
            if v != v:
                i = self.left[i] if self.miss_left[i] else self.right[i]
            elif v <= thr[i]:
                i = self.left[i]
            else:
                i = self.right[i]
        return self.value[i]

    def to_json(self) -> dict:
        return {"c": self.cls, "f": self.feat, "t": self.thr, "l": self.left, "r": self.right,
                "m": self.miss_left, "v": self.value}

    @classmethod
    def from_json(cls, d: dict) -> "Tree":
        t = cls(d["c"])
        t.feat, t.thr, t.left, t.right, t.miss_left, t.value = d["f"], d["t"], d["l"], d["r"], d["m"], d["v"]
        return t


class Model:
    def __init__(self, classes: list[str], features: list[str]) -> None:
        self.classes = list(classes)
        self.features = list(features)
        self.base = [0.0] * len(classes)
        self.trees: list[Tree] = []
        self.meta: dict = {}

    def raw(self, x) -> list[float]:
        s = list(self.base)
        for t in self.trees:
            s[t.cls] += t.predict(x)
        return s

    def proba(self, x) -> list[float]:
        return softmax(self.raw(x))

    def to_json(self) -> dict:
        return {"format": "luma-touch-gbdt/1", "classes": self.classes, "features": self.features,
                "base": self.base, "trees": [t.to_json() for t in self.trees], "meta": self.meta}

    @classmethod
    def from_json(cls, d: dict) -> "Model":
        m = cls(d["classes"], d["features"])
        m.base = list(d["base"])
        m.trees = [Tree.from_json(t) for t in d["trees"]]
        m.meta = d.get("meta", {})
        return m

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_json(), fh, separators=(",", ":"))

    @classmethod
    def load(cls, path: str) -> "Model":
        with open(path, encoding="utf-8") as fh:
            return cls.from_json(json.load(fh))

    def nodes(self) -> int:
        return sum(len(t.feat) for t in self.trees)

    def importance(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.trees:
            for f in t.feat:
                if f >= 0:
                    out[self.features[f]] = out.get(self.features[f], 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def softmax(s: list[float]) -> list[float]:
    m = max(s)
    e = [math.exp(v - m) for v in s]
    z = sum(e)
    return [v / z for v in e]


# ── Training ────────────────────────────────────────────────────────────────

def _edges(col: list[float], max_bins: int) -> list[float]:
    vals = sorted(v for v in col if not _isnan(v))
    if not vals:
        return []
    uniq = sorted(set(vals))
    if len(uniq) <= max_bins - 1:
        # Midpoints between distinct values, so thresholds sit between observed values.
        return [(a + b) / 2 for a, b in zip(uniq, uniq[1:])]
    out = []
    for k in range(1, max_bins - 1):
        q = vals[min(len(vals) - 1, int(k * len(vals) / (max_bins - 1)))]
        if not out or q > out[-1]:
            out.append(q)
    return out


def fit(X: list[list[float]], y: list[int], classes: list[str], features: list[str], *, w: list[float] | None = None,
        rounds: int = 60, depth: int = 4, lr: float = 0.2, lam: float = 1.0, min_hess: float = 1.0,
        max_bins: int = 32, colsample: float = 0.7, subsample: float = 0.85, seed: int = 0,
        init: Model | None = None, valid: tuple | None = None, patience: int = 8, log=None) -> Model:
    """Boost `rounds` rounds of one tree per class. valid=(Xv, yv) enables early stopping."""
    rng = random.Random(seed)
    K, N, F = len(classes), len(X), len(features)
    w = w or [1.0] * N
    model = Model(classes, features)
    if init is not None:
        model.base = list(init.base)
        model.trees = list(init.trees)
        model.meta = dict(init.meta)
    else:
        tot = sum(w)
        prior = [sum(wi for wi, yi in zip(w, y) if yi == k) / tot for k in range(K)]
        model.base = [math.log(max(p, 1e-4)) for p in prior]
    first_new = len(model.trees)
    edges = [_edges([row[f] for row in X], max_bins) for f in range(F)]
    nb = [len(e) + 2 for e in edges]                    # bins 0..len(e), then missing
    bins = []
    for f in range(F):
        e, miss = edges[f], nb[f] - 1
        bins.append([miss if _isnan(row[f]) else bisect.bisect_left(e, row[f]) for row in X])
    raw = [model.raw(x) for x in X]
    vraw = [model.raw(x) for x in valid[0]] if valid else None
    best, best_round, stall = float("inf"), 0, 0
    np_bins = [_np.asarray(b) for b in bins] if _np is not None else None
    t0 = time.time()
    for rnd in range(rounds):
        probs = [softmax(r) for r in raw]
        rows = [i for i in range(N) if rng.random() < subsample]
        feats = [f for f in range(F) if rng.random() < colsample and nb[f] > 2] or list(range(F))
        for k in range(K):
            g = [(probs[i][k] - (1.0 if y[i] == k else 0.0)) * w[i] for i in range(N)]
            h = [max(probs[i][k] * (1 - probs[i][k]), 1e-6) * w[i] for i in range(N)]
            tree = _grow(rows, g, h, bins, np_bins, nb, edges, feats, depth, lr, lam, min_hess, k)
            model.trees.append(tree)
            for i in range(N):
                raw[i][k] += tree.predict(X[i])
            if vraw is not None:
                for i, x in enumerate(valid[0]):
                    vraw[i][k] += tree.predict(x)
        if valid:
            loss = logloss(vraw, valid[1])
            if log:
                log(f"round {rnd + 1}: valid logloss {loss:.4f} ({time.time() - t0:.1f}s)")
            if loss < best - 1e-5:
                best, best_round, stall = loss, rnd + 1, 0
            else:
                stall += 1
                if stall >= patience:
                    break
        else:
            best_round = rnd + 1
    model.trees = model.trees[:first_new + best_round * K]
    model.meta.update({"rounds": len(model.trees) // K, "depth": depth, "lr": lr, "lambda": lam,
                       "trained_s": round(time.time() - t0, 2)})
    return model


def logloss(raw: list[list[float]], y: list[int]) -> float:
    tot = 0.0
    for r, yi in zip(raw, y):
        p = softmax(r)[yi]
        tot -= math.log(max(p, 1e-12))
    return tot / max(1, len(y))


def _hist(rows, g, h, col, np_col, nbins, np_rows=None, np_g=None, np_h=None):
    if np_col is not None and np_rows is not None:
        b = np_col[np_rows]
        G = _np.bincount(b, weights=np_g, minlength=nbins).tolist()
        H = _np.bincount(b, weights=np_h, minlength=nbins).tolist()
        return G, H
    G, H = [0.0] * nbins, [0.0] * nbins
    for r in rows:
        b = col[r]
        G[b] += g[r]
        H[b] += h[r]
    return G, H


def _grow(rows, g, h, bins, np_bins, nb, edges, feats, depth, lr, lam, min_hess, k) -> Tree:
    tree = Tree(k)
    root = tree.node()
    g_arr = _np.asarray(g) if np_bins is not None else None
    h_arr = _np.asarray(h) if np_bins is not None else None
    stack = [(root, rows, 0)]
    while stack:
        node, idx, d = stack.pop()
        G = sum(g[r] for r in idx)
        H = sum(h[r] for r in idx)
        best = None
        if d < depth and H >= 2 * min_hess and len(idx) >= 4:
            parent = G * G / (H + lam)
            np_rows = np_g = np_h = None
            if np_bins is not None and len(idx) > 64:
                np_rows = _np.asarray(idx)
                np_g, np_h = g_arr[np_rows], h_arr[np_rows]
            for f in feats:
                hg, hh = _hist(idx, g, h, bins[f], np_bins[f] if np_bins else None, nb[f], np_rows, np_g, np_h)
                miss_g, miss_h = hg[-1], hh[-1]
                gl = hl = 0.0
                for b in range(nb[f] - 2):              # split after bin b: x <= edges[b]
                    gl += hg[b]
                    hl += hh[b]
                    for ml in (True, False):
                        GL, HL = (gl + miss_g, hl + miss_h) if ml else (gl, hl)
                        GR, HR = G - GL, H - HL
                        if HL < min_hess or HR < min_hess:
                            continue
                        gain = GL * GL / (HL + lam) + GR * GR / (HR + lam) - parent
                        if best is None or gain > best[0]:
                            best = (gain, f, b, ml)
        if best is None or best[0] <= 1e-9:
            tree.value[node] = -G / (H + lam) * lr
            continue
        _gain, f, b, ml = best
        tree.feat[node] = f
        tree.thr[node] = edges[f][b]
        tree.miss_left[node] = 1 if ml else 0
        col, miss = bins[f], nb[f] - 1
        left = [r for r in idx if (col[r] == miss and ml) or (col[r] != miss and col[r] <= b)]
        lset = set(left)
        right = [r for r in idx if r not in lset]
        li, ri = tree.node(), tree.node()
        tree.left[node], tree.right[node] = li, ri
        stack.append((li, left, d + 1))
        stack.append((ri, right, d + 1))
    return tree


# ── Export ──────────────────────────────────────────────────────────────────

def _num(v: float) -> str:
    r = repr(float(v))
    return r if r not in ("nan", "inf", "-inf") else {"nan": "0/0", "inf": "math.huge", "-inf": "-math.huge"}[r]


def to_lua(model: Model, name: str = "MODEL") -> str:
    """The model as a Lua table: 1-based node and feature indices, 0 marks a leaf."""
    out = [f"local {name} = {{", f"  classes = {{{', '.join(repr(c).replace(chr(39), chr(34)) for c in model.classes)}}},",
           f"  features = {{{', '.join(chr(34) + f + chr(34) for f in model.features)}}},",
           f"  base = {{{', '.join(_num(b) for b in model.base)}}},", "  trees = {"]
    for t in model.trees:
        f = ",".join(str(v + 1) for v in t.feat)
        th = ",".join(_num(v) if t.feat[i] >= 0 else "0" for i, v in enumerate(t.thr))
        lo = ",".join(str(v + 1) for v in t.left)
        hi = ",".join(str(v + 1) for v in t.right)
        m = ",".join(str(v) for v in t.miss_left)
        val = ",".join(_num(v) if t.feat[i] < 0 else "0" for i, v in enumerate(t.value))
        out.append(f"    {{c={t.cls + 1},f={{{f}}},t={{{th}}},l={{{lo}}},r={{{hi}}},m={{{m}}},v={{{val}}}}},")
    out.append("  },")
    out.append("}")
    return "\n".join(out) + "\n"


LUA_EVAL = """
local function model_raw(model, x)
  local s = {}
  for k = 1, #model.base do s[k] = model.base[k] end
  local trees = model.trees
  for i = 1, #trees do
    local tr = trees[i]
    local f, t, l, r, m = tr.f, tr.t, tr.l, tr.r, tr.m
    local n = 1
    while f[n] > 0 do
      local v = x[f[n]]
      if v ~= v then
        if m[n] == 1 then n = l[n] else n = r[n] end
      elseif v <= t[n] then
        n = l[n]
      else
        n = r[n]
      end
    end
    s[tr.c] = s[tr.c] + tr.v[n]
  end
  return s
end

local function softmax(s)
  local mx = s[1]
  for k = 2, #s do if s[k] > mx then mx = s[k] end end
  local z, p = 0.0, {}
  for k = 1, #s do p[k] = math.exp(s[k] - mx); z = z + p[k] end
  for k = 1, #s do p[k] = p[k] / z end
  return p
end
"""
