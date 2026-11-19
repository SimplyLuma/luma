# SPDX-License-Identifier: Apache-2.0
"""Generate the libinput Lua plugin (and its quirks) from a trained role model.

The plugin is plugins/luma-touch-roles.lua.in with the model tables, the
tree evaluator and the list of force pads filled in. Force pads need a
quirk next to it: libinput 1.31 disables ABS_MT_PRESSURE on pressure pads
before any plugin sees a frame (tp_init_pressurepad), so the quirk takes
INPUT_PROP_PRESSUREPAD off those pads and turns libinput's own pressure use
off instead (AttrPressureRange=0:0, palm and thumb pressure thresholds 0).
The force then reaches the plugin and libinput still ignores it.
"""

from __future__ import annotations

import os

from . import gbdt, surfaces
from .rfeatures import NAMES

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(os.path.dirname(HERE), "plugins", "luma-touch-roles.lua.in")
BUS_IDS = {"i2c": 0x18, "usb": 0x03, "bluetooth": 0x05, "ps2": 0x11, "rmi": 0x1D, "spi": 0x1C}


def _template() -> str:
    with open(TEMPLATE, encoding="utf-8") as fh:
        return fh.read()


def names_lua() -> str:
    return "local NAMES = {" + ", ".join(f'"{n}"' for n in NAMES) + "}\n"


def force_pads(quirks: list) -> list[tuple[int, int, int, str]]:
    """(bus, vendor, product, section) for every quirk that makes a device a pressure pad."""
    out = []
    for q in quirks:
        if "PRESSUREPAD" not in q.attrs.get("AttrInputProp", "") or "+" not in q.attrs.get("AttrInputProp", "+"):
            continue
        bus = BUS_IDS.get(q.match.get("MatchBus", ""), 0)
        ven = q.match.get("MatchVendor")
        if not ven:
            continue
        for prod in (q.match.get("MatchProduct") or "").split(";"):
            if prod:
                out.append((bus, int(ven, 16), int(prod, 16), q.section))
    return sorted(set(out))


def force_pads_lua(pads) -> str:
    rows = [f'  ["{b:04x}:{v:04x}:{p:04x}"] = true,  -- {s}' for b, v, p, s in pads]
    return "local FORCE_PADS = {\n" + "\n".join(rows) + "\n}\n"


def quirks_file(pads) -> str:
    out = ["# Luma touch roles: let the role plugin read force on pressure pads.",
           "# libinput keeps ignoring pressure on these pads; only the plugin uses it.", ""]
    for b, v, p, s in pads:
        bus = {v_: k for k, v_ in BUS_IDS.items()}.get(b, "i2c")
        out += [f"[Luma touch roles: {s}]", f"MatchBus={bus}", f"MatchVendor=0x{v:04X}", f"MatchProduct=0x{p:04X}",
                "AttrInputProp=-INPUT_PROP_PRESSUREPAD", "AttrPressureRange=0:0", "AttrPalmPressureThreshold=0",
                "AttrThumbPressureThreshold=0", ""]
    return "\n".join(out)


def features_chunk() -> str:
    """The plugin's feature code alone (for the parity test)."""
    t = _template()
    a, b = t.index("-- @FEATURES_BEGIN@"), t.index("-- @FEATURES_END@")
    head = t[t.index("local ROLES"):t.index("-- @FORCE_PADS@")]
    helpers = "local function ok(v) return v == v end\nlocal function hypot(a, b) return math.sqrt(a * a + b * b) end\n"
    return head + names_lua() + helpers + t[a:b]


def build(models: dict, quirk_dirs: list[str] | None = None) -> tuple[str, str]:
    """(plugin source, quirks file)."""
    quirks = surfaces.load_quirks([d for d in (quirk_dirs or []) if os.path.exists(d)])
    pads = force_pads(quirks)
    t = _template()
    model = names_lua() + gbdt.to_lua(models["stage1"], "STAGE1") + gbdt.to_lua(models["stage2"], "STAGE2")
    t = t.replace("-- @FORCE_PADS@", force_pads_lua(pads))
    t = t.replace("-- @MODEL@", model)
    t = t.replace("-- @EVAL@", gbdt.LUA_EVAL.strip())
    synthetic = models["stage1"].meta.get("synthetic")
    note = ("-- MODEL: trained on synthetic data only; not for real use.\n" if synthetic else
            f"-- MODEL: {models['stage1'].meta.get('rows', 0)} rows.\n")
    t = t.replace("libinput:register({1})", note + "libinput:register({1})", 1)
    return t, quirks_file(pads)


def write(models: dict, out_dir: str, quirk_dirs: list[str] | None = None) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    src, quirks = build(models, quirk_dirs)
    paths = [os.path.join(out_dir, "50-luma-touch-roles.lua"), os.path.join(out_dir, "50-luma-touch-roles.quirks")]
    for path, text in zip(paths, (src, quirks)):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return paths
