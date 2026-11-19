# SPDX-License-Identifier: Apache-2.0
"""Live contact roles for the app's test round: the plugin's logic over the live track builder.

Each contact is evaluated when each window ends (or at lift), stage 1 then
stage 2 with the others' stage-1 opinions, and the votes accumulate, exactly
as plugins/luma-touch-roles.lua does inside libinput.
"""

from __future__ import annotations

from .rfeatures import STAGE1, WINDOWS_MS, contact_features, from_track
from .rolemodel import VOTE_WEIGHT, select
from .roles import ROLES

KEEP_NS = 5_000_000_000


class LiveRoles:
    def __init__(self, models: dict, pad_mm, mask: dict) -> None:
        self.models, self.pad_mm, self.mask = models, pad_mm, mask
        self.state: dict[str, dict] = {}         # track key -> {"s1": {w: p}, "votes": [..], "done": set()}
        self.t0: int | None = None

    def update(self, tracks: list, keys_ns: list, now_ns: int) -> None:
        live = [t for t in tracks if t.samples and (t.t_up is None or now_ns - t.t_up < KEEP_NS)]
        if not live:
            return
        if self.t0 is None:
            self.t0 = min(t.t_down for t in live)
        cs = {t.key: from_track(t, self.t0) for t in live}
        keys = [(k - self.t0) / 1e6 for k in keys_ns]
        now = (now_ns - self.t0) / 1e6
        m1, m2 = self.models["stage1"], self.models["stage2"]
        for key, c in cs.items():
            st = self.state.setdefault(key, {"s1": {}, "votes": [0.0] * len(ROLES), "done": set(), "role": None,
                                             "prob": 0.0, "t_down": c.t_down})
            for w in WINDOWS_MS:
                if w in st["done"] or not (c.t_down + w <= now or c.t_up is not None):
                    continue
                st["done"].add(w)
                others = [o for k, o in cs.items() if k != key and o.t_down <= c.t_down + w]
                roles = self._roles_for(c, others, w)
                row = contact_features(c, others, keys, self.pad_mm, self.mask, w, roles)
                st["s1"][w] = m1.proba(select(row, STAGE1))
                p = m2.proba(select(row, m2.features))
                for k in range(len(ROLES)):
                    st["votes"][k] += VOTE_WEIGHT.get(w, 1.0) * p[k]
                z = sum(st["votes"])
                top = max(range(len(ROLES)), key=lambda k: st["votes"][k])
                st["role"], st["prob"] = ROLES[top], st["votes"][top] / z
        for key in [k for k, v in self.state.items() if k not in cs and v["done"] and len(self.state) > 64]:
            del self.state[key]

    def _roles_for(self, c, others, window) -> dict:
        end = c.t_down + window
        tq = max([t for t in c.t if t <= end] or c.t[:1])
        out = {}
        for o in others:
            st = self.state.get(o.key)
            if not st:
                continue
            best = None
            for w, p in st["s1"].items():
                ready = o.t_down + w if (o.t_up is None or o.t_up > o.t_down + w) else o.t_up
                if ready <= tq and (best is None or w > best[0]):
                    best = (w, p)
            if best:
                out[o.key] = best[1]
        return out

    def role(self, key: str) -> tuple[str | None, float]:
        st = self.state.get(key)
        return (st["role"], st["prob"]) if st else (None, 0.0)
