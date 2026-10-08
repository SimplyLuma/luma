#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "config/shared/design-tokens.json"
# Family fragments (the kit builders' own token files): each is shaped like the
# main file ({"lumaui": {...}}) and is deep-merged into it in name order. A
# fragment may add keys; it may not change a value the main file or an earlier
# fragment set.
FRAGMENTS = ROOT / "config/shared/design-tokens.d"
OUTPUT = ROOT / "src/luma-platform/ui/luma-tokens.css"
ICON_OUTPUT = ROOT / "src/luma-platform/ui/luma-icon-tokens.h"
FRAME_OUTPUT = ROOT / "src/luma-platform/compat/luma-frame-tokens.h"
DARK_OUTPUT = ROOT / "src/luma-platform/ui/luma-tokens-dark.css"
APPKIT_OUTPUT = ROOT / "src/luma-platform/appkit/luma-appkit-tokens.css"
APPKIT_DARK_OUTPUT = ROOT / "src/luma-platform/appkit/luma-appkit-dark-tokens.css"
APPKIT_FROST_OUTPUT = ROOT / "src/luma-platform/appkit/luma-appkit-frost-tokens.css"
APPKIT_GLASS_OUTPUT = ROOT / "src/luma-platform/appkit/luma-appkit-glass-tokens.css"
PYTHON_OUTPUT = ROOT / "src/luma-platform/appkit/luma_appkit/lumaui_tokens.py"
PALETTE_OUTPUT = ROOT / "src/luma-platform/appkit/lumaui-palette.css"
TOOLKIT_SHEET = ROOT / "src/luma-platform/appkit/lumaui-toolkit.css"

# The provenance both LumaUI window sheets carry (ADR-052). They restate what
# Luma's patches add to libadwaita's stylesheet, in libadwaita's names.
LUMAUI_DERIVED_HEADER = """/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Provenance: derived from libadwaita 1.9.3's stylesheet (src/stylesheet,
 * LGPL-2.1-or-later) as changed by Luma's patches/libadwaita; see ADR-052.
 * Licence under review; nothing else in the kit is relicensed by this file. */
"""


def _merge(into: dict, add: dict, where: str) -> None:
    for key, value in add.items():
        if key in into and isinstance(into[key], dict) and isinstance(value, dict):
            _merge(into[key], value, f"{where}.{key}")
        elif key in into and into[key] != value and not key.startswith("_"):
            raise SystemExit(f"design-tokens.d: {where}.{key} is already set; a fragment only adds tokens")
        else:
            into[key] = value


def fragment_groups() -> dict[str, str]:
    """The top-level lumaui groups the fragments add, and the fragment that adds each."""
    groups: dict[str, str] = {}
    if FRAGMENTS.is_dir():
        base = json.loads(SOURCE.read_text(encoding="utf-8")).get("lumaui", {})
        for path in sorted(FRAGMENTS.glob("*.json")):
            for key in json.loads(path.read_text(encoding="utf-8")).get("lumaui", {}):
                if key not in base and not key.startswith("_") and key not in ("colors", "type_scale"):
                    groups.setdefault(key, path.name)
    return groups


def load_document() -> dict:
    """design-tokens.json with every design-tokens.d/*.json fragment merged in."""
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    if FRAGMENTS.is_dir():
        for path in sorted(FRAGMENTS.glob("*.json")):
            _merge(document, json.loads(path.read_text(encoding="utf-8")), path.name)
    return document


def render_window_theme(toolkit: int, appearance: str) -> str:
    """Native named-theme entry point; inner AppKit islands are untouched."""
    profiles = load_document()["window_elevation"]
    states = ("fullscreen", "tiled", "tiled-top", "tiled-left",
              "tiled-right", "tiled-bottom")

    def value(text: str) -> str:
        return re.sub(r"rgba\((\d+),(\d+),(\d+),\.(\d+)\)",
                      r"rgba(\1, \2, \3, 0.\4)", text).replace("),", "), ")

    if toolkit == 4:
        resource = f"Default/Default-{appearance}.css"
    else:
        resource = "Adwaita/gtk-contained" + ("-dark" if appearance == "dark" else "") + ".css"
    lines = ["/* Generated from config/shared/design-tokens.json. */",
             f'@import url("resource:///org/gtk/libgtk/theme/{resource}");']
    if toolkit == 3:
        lines.append('@import url("luma-common.css");')
    tail = " decoration" if toolkit == 3 else ""
    floating = "".join(f":not(.{state})" for state in states)
    variants = [(False, appearance)]
    if toolkit == 4:
        variants.extend((True, mode) for mode in profiles)
    for qualified, mode in variants:
        # The unqualified pair follows the selected native theme. Explicit
        # treatment classes are for existing/future native appearance adapters.
        lines.append("/* Explicit profile */" if qualified else "/* Default profile */")
        selector = "window.csd" + (f".luma-treatment-{mode}{floating}" if qualified else "")
        recipe = value(profiles[mode]["stroke"]) + ", " + value(profiles[mode]["shadow"])
        for backdrop in ("", ":backdrop"):
            lines.extend(["/* Focused and unfocused outer elevation is deliberately identical. */",
                          f"{selector}{backdrop}{tail} {{",
                          f"  box-shadow: {recipe};", "  transition: none;", "}"])
        if toolkit == 4:
            lines.append(f"{selector} {{ border-radius: 15px; }}")
        off = selector + ".luma-no-frame-shadow" + (floating if not qualified else "")
        lines.extend([f"{off}{tail}, {off}:backdrop{tail} {{",
                      f"  box-shadow: {value(profiles[mode]['stroke'])};", "}"])
    # Preserve stroke when the shadow is disabled, but never draw an outer
    # frame on a screen edge. Both actual focus states are explicit.
    selectors = [f"window.csd.{state}{backdrop}{tail}"
                 for state in states for backdrop in ("", ":backdrop")]
    lines.extend([",\n".join(selectors) + " {", "  box-shadow: none;",
                  "  border-radius: 0;", "  transition: none;", "}"])
    radius = load_document()["radius"]["window"]
    lines.extend([
        "/* Maximized desktop work areas are inset; fullscreen stays square. */",
        f"window.csd.maximized:not(.fullscreen){tail} {{ border-radius: {radius}px; }}",
        f"window.csd.maximized:not(.fullscreen) headerbar {{ border-radius: {radius}px {radius}px 0 0; }}",
    ])
    return "\n".join(lines) + "\n"


# The two treatments name the same roles differently: light carries `card`,
# `chrome`, `chrome_secondary` and `hairline`, while dark expresses the same
# surfaces as `island`/`content`, `window` and `border`. The C UI asks for one
# vocabulary, so the mapping is stated here rather than left to whichever key
# happens to exist.
_UI_ROLES = {
    "light": {
        "surface": "card",
        "chrome": "chrome",
        "chrome_secondary": "chrome_secondary",
        "hairline": "hairline",
    },
    "dark": {
        "surface": "content",
        "chrome": "window",
        "chrome_secondary": "content",
        "hairline": "border",
    },
}


def render(appearance: str = "light") -> str:
    source = load_document()[appearance]
    roles = _UI_ROLES[appearance]
    values = {
        "accent": source["accent"],
        "surface": source[roles["surface"]],
        "chrome": source[roles["chrome"]],
        "chrome_secondary": source[roles["chrome_secondary"]],
        "ink": source["ink"],
        "secondary_ink": source["ink"],
        "muted_ink": source["muted"],
        "faint_ink": source["faint"],
        "hairline": source[roles["hairline"]],
    }
    lines = ["/* Generated by scripts/developer/generate-luma-platform-tokens.py. */"]
    lines.extend(f"@define-color luma_{name} {value};" for name, value in values.items())
    return "\n".join(lines) + "\n"


def _controls(document: dict, appearance: str) -> dict:
    """Filled-control roles (ADR-043) for one appearance mode."""
    return dict(document["controls"][appearance])


# Accent as text, and the ground it sits on, per paper appearance. These were
# kept by hand in the sheets for a while; they are stated here so that
# regenerating the sheets no longer drops them.
_ACCENT_BLOCKS = {
    "light": (
        "/* Accent as text, and the ground it sits on. Mixed toward black here because\n"
        " * the surface is light; see the dark treatment for why this is a token rather\n"
        " * than something each surface works out for itself. */\n"
        "@define-color luma_accent_ink oklch(from {accent} 0.48 0.12 h); /* v70 --acc-ink (light): 6.5:1 on white */\n"
        "@define-color luma_accent_soft alpha({accent}, 0.14);\n"
        "@define-color luma_accent_link @luma_accent_ink;"
    ),
    "dark": (
        "/* The reason this is a token. Mixing the accent toward black on a dark ground\n"
        " * produces something indistinguishable from the ground, so here it lifts\n"
        " * toward white: 42% for text on a tinted chip, 38% for a link on the surface\n"
        " * itself, which needs slightly more lift to separate from body text. */\n"
        "@define-color luma_accent_ink oklch(from {accent} 0.82 0.09 h); /* v70 --acc-ink (dark): 5.2:1 on the chip */\n"
        "@define-color luma_accent_link mix({accent}, #ffffff, 0.38);\n"
        "@define-color luma_accent_soft alpha({accent}, 0.20);"
    ),
}
_CONTROLS_COMMENT = ("/* Filled controls (ADR-043): one quiet state fill, a ringed knob, and the\n"
                     " * sheet text and graphic colours measured on this mode's surfaces. */")


def _lumaui(document: dict | None = None) -> dict:
    return (document or load_document())["lumaui"]


def _css_number(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def render_lumaui_colors(family: str, document: dict | None = None) -> str:
    """LumaUI's part colours for one family: light (also frost and glass) or dark."""
    colours = _lumaui(document)["colors"][family]
    lines = ["", "/* LumaUI part colours (v70): categories, count badge, danger, raised chip,",
             " * floating surfaces, toast, scrims, lit-background cards and media. */"]
    lines.extend(f"@define-color luma_{name} {value};" for name, value in colours.items()
                 if not name.startswith("_"))
    return "\n".join(lines)


def render_lumaui_high_contrast(document: dict | None = None) -> str:
    lumaui = _lumaui(document)
    rules = lumaui["colors"]["high_contrast"]
    keep_dark = set(rules.get("keep_dark", ()))
    lines = ["", "/* LumaUI part colours: high contrast does not tint. Parts keep their shape",
             " * and take the window's ink, ground and accent; media and lit-background",
             " * cards keep their dark values because they sit over pictures. */"]
    for name, value in lumaui["colors"]["dark"].items():
        if name.startswith("_"):
            continue
        if name in keep_dark:
            mapped = value
        elif name in rules:
            mapped = rules[name]
        elif name.startswith("cat_") and name.endswith("_bg"):
            mapped = rules["cat_*_bg"]
        elif name.startswith("cat_") and name.endswith("_ink"):
            mapped = rules["cat_*_ink"]
        else:
            mapped = "@window_fg_color"
        lines.append(f"@define-color luma_{name} {mapped};")
    return "\n".join(lines)


def render_lumaui_metrics(document: dict | None = None) -> str:
    """LumaUI metrics and motion as CSS custom properties (GTK 4.16+), one block for every sheet."""
    t = _lumaui(document)
    m = t["motion"]
    bezier = lambda points: "cubic-bezier(" + ", ".join(_css_number(p) for p in points) + ")"
    values: list[tuple[str, str]] = [
        ("ease", bezier(m["ease"])), ("spring", bezier(m["spring"])),
    ]
    for key, value in m.items():
        if key.endswith("_ms"):
            values.append(("duration-" + key[:-3].replace("_", "-"), f"{value}ms"))
        elif key.endswith("_px"):
            values.append(("distance-" + key[:-3].replace("_", "-"), f"{value}px"))
    for role, spec in t["type_scale"].items():
        if not isinstance(spec, dict):
            continue
        name = "t-" + role.replace("_", "-")
        values += [(f"{name}-size", f"{_css_number(spec['size'])}px"),
                   (f"{name}-weight", str(spec["weight"])),
                   # In px, as CSS means it (the font size times the factor), so a role's line box is
                   # the design's on every font: Pango's factor multiplies the font's own line, and GTK
                   # takes line-height as a number or a length (an em value did nothing on the ThinkPad;
                   # Settings, 28 Sep: row-title came out 19 for v70's 17.6).
                   (f"{name}-line-height", f"{_css_number(round(spec['size'] * spec['line_height'], 2))}px"),
                   (f"{name}-tracking", f"{_css_number(spec['tracking_em'] * spec['size'])}px"),
                   (f"{name}-unit-size", f"{_css_number(spec['size'] * t['type_scale']['unit_scale'])}px"),
                   (f"{name}-unit-gap", f"{_css_number(spec['size'] * 0.12)}px")]
        if spec.get("phone_size"):
            values.append((f"{name}-phone-size", f"{_css_number(spec['phone_size'])}px"))
    # GTK sizes a box's content without its padding; the design states the
    # outside. These are the content sizes that make the design's outsides.
    count, dialog = t["count_badge"], t["dialog"]
    values += [("count-content-min-width", f"{_css_number(count['min_width'] - 2 * count['padding_x'])}px"),
               ("dialog-content-width", f"{_css_number(dialog['width'] - 2 * dialog['padding_x'])}px")]
    # The sidebar's rows sit `inset` from the window edge; the window body's
    # gutter gives `gutter` of it, the sidebar's own padding the rest. A row's
    # content height is its outside less its padding.
    # A label that ellipsizes in a narrow column is measured at its font's own
    # line, not the text line height; these are the 1.45 line boxes (px) such
    # labels hold as a minimum, per text size v70 uses.
    ratio = t["text"]["line_height"]
    # The same 1.45 as CSS means it (a multiple of the font size, in em), which GTK honours the same
    # on every platform; the Pango factor above depends on the font's own line (Figtree on the
    # ThinkPad made rows a pixel taller than on a desk without it).
    values += [("text-line-height-em", f"{_css_number(ratio)}em")]  # rows set it per size below
    values += [(f"text-line-{name}", f"{round(size * ratio)}px")
               for name, size in (("caption", t["type_scale"]["caption"]["size"]), ("small", 12),
                                  ("body", t["type_scale"]["body"]["size"]))]
    side = t["sidebar"]
    values += [("sidebar-edge", f"{_css_number(side['inset'] - side['gutter'])}px"),
               ("sidebar-row-content-height", f"{_css_number(side['row_height'] - 2 * side['row_padding_y'])}px")]
    groups = (("count", t["count_badge"]), ("pill", t["category"]["pill"]),
              ("stack", t["stacked_button"]), ("stack-sm", t["stacked_button"]["small"]),
              ("toast", t["toast"]), ("dialog", t["dialog"]), ("drawer", t["drawer"]),
              ("sky-card", t["sky_card"]), ("sidebar", t["sidebar"]))
    # Structure parts (F2): every group under "structure" is --lumaui-<group>-<key>.
    groups += tuple((name.replace("_", "-"), group) for name, group in t.get("structure", {}).items()
                    if isinstance(group, dict))
    # F3: action and content parts.
    groups += (("ac", t["action_center"]), ("bubble", t["selection_bubble"]), ("file", t["file_card"]),
               ("mini", t["mini_card"]), ("account", t["account_card"]), ("card", t["card"]),
               ("place", t["place_search"]), ("field", t["field"]), ("msg", t["message_bubble"]),
               ("open-in", t["open_in"]), ("status", t["status_pill"]), ("hero", t["hero_field"]),
               ("lit", t["lit_header"]), ("text", t["text"]), ("window", t["window"]), ("fact", t["fact"]))
    # Family fragments (design-tokens.d): each new group is --lumaui-<group>-<key>;
    # a group of groups is --lumaui-<group>-<sub>-<key>.
    def flat(prefix: str, group: dict):
        yield prefix, {k: v for k, v in group.items() if not isinstance(v, dict)}
        for sub, value in group.items():
            if isinstance(value, dict) and not sub.startswith("_"):
                yield from flat(f"{prefix}-{sub.replace('_', '-')}", value)
    for name in fragment_groups():
        if isinstance(t.get(name), dict):
            groups += tuple(flat(name.replace("_", "-"), t[name]))
    for prefix, group in groups:
        for key, value in group.items():
            if isinstance(value, (dict, list, str)) or key.startswith("_"):
                continue
            if key.endswith("line_height"):
                rendered = _gtk_line_height(value, t)
            elif key.endswith("_ms"):
                rendered = f"{_css_number(value)}ms"
            elif key.endswith(("_pct", "_scale", "_alpha", "_opacity", "darkening", "weight", "_cap", "_count", "_ratio")):
                rendered = _css_number(value)
            else:
                rendered = f"{_css_number(value)}px"
            values.append((f"{prefix}-{key.removesuffix('_px').replace('_', '-')}", rendered))
    lines = ["", "/* LumaUI metrics and motion (v70). Read with var(--lumaui-*); change them in",
             " * config/shared/design-tokens.json. */", ":root {"]
    lines.extend(f"  --lumaui-{name}: {value};" for name, value in values)
    lines.append("}")
    lines += render_fragment_type_roles(t)
    return "\n".join(lines)


_GENERIC_FAMILIES = {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "emoji", "math"}


def _font_family(value: str, role: str) -> str:
    """A CSS family list: every name quoted, and a generic family at the end."""
    names = [n.strip().strip("'\"") for n in value.split(",") if n.strip()]
    out = [n if n in _GENERIC_FAMILIES else f'"{n}"' for n in names]
    if not any(n in _GENERIC_FAMILIES for n in names):
        out.append("monospace" if "mono" in role or "mono" in value.lower() else "sans-serif")
    return ", ".join(out)


def render_fragment_type_roles(t: dict) -> list[str]:
    """.lumaui-t-<role> for the type roles a family fragment adds (the main file's
    roles are written by hand in luma-appkit-base.css)."""
    core = json.loads(SOURCE.read_text(encoding="utf-8"))["lumaui"]["type_scale"]
    tones = {"secondary": "@luma_ink_secondary", "muted": "@luma_muted", "faint": "@luma_faint"}
    out = []
    for role, spec in t["type_scale"].items():
        if role in core or not isinstance(spec, dict):
            continue
        name = "t-" + role.replace("_", "-")
        rule = [f"font-size: var(--lumaui-{name}-size)", f"font-weight: var(--lumaui-{name}-weight)",
                # The line height as a literal length: Settings' C build ignored it through var() (28 Sep).
                # Rounded to the nearest pixel: GTK rounds a fractional line box up, which put every
                # two-line Settings row 1.3 px tall (16.2 drew 17).
                f"line-height: {round(spec['size'] * spec['line_height'])}px",
                f"letter-spacing: {_css_number(round(spec['tracking_em'] * spec['size'], 3))}px"]  # literal, as the line height
        if spec.get("family"):
            rule.append(f"font-family: {_font_family(spec['family'], role)}")
        if spec.get("tabular"):
            rule.append('font-feature-settings: "tnum"')
        if spec.get("tone") in tones:
            rule.append(f"color: {tones[spec['tone']]}")
        # Named inside a Luma window too, so the role's line height wins over the window's body default.
        out.append(f".lumaui-{name}, window.luma-app-window label.lumaui-{name} {{ " + "; ".join(rule) + "; }")
        if spec.get("phone_size"):
            out.append(f".lumaui-{name}.lumaui-t-phone {{ font-size: var(--lumaui-{name}-phone-size); }}")
    # Treatment sheets import generated roles in a later GTK provider than
    # base.css. Explicit apply_type weights must live in that provider too;
    # selector specificity alone cannot override an earlier provider.
    for weight in (300, 400, 450, 500, 550, 600, 650, 700, 750):
        cls = f".lumaui-w-{weight}.lumaui-w-{weight}"
        out.append(f"{cls}, {cls} label, window.luma-app-window label{cls}, "
                   f"window.luma-app-window {cls} label {{ font-weight: {weight}; }}")
    return (["", "/* LumaUI type roles from family fragments (design-tokens.d). */"] + out) if out else []


def render_lumaui_appearance_metrics(family: str, document: dict | None = None) -> str:
    metrics = _lumaui(document)["appearance_metrics"][family]
    lines = [":root {"]
    lines.extend(f"  --lumaui-{key.replace('_', '-')}: {_css_number(value)};" for key, value in metrics.items())
    lines.append("}")
    return "\n".join(lines)


def _plain(group: dict) -> dict:
    return {k: v for k, v in group.items() if not k.startswith("_")}


def _plain_deep(group: dict) -> dict:
    return {k: (_plain_deep(v) if isinstance(v, dict) else v) for k, v in group.items() if not k.startswith("_")}


def _gtk_line_height(css_value: float, t: dict) -> str:
    """A CSS line height (a multiple of the font size) as GTK's (a multiple of the font's own line)."""
    return _css_number(round(css_value / t["text"]["font_line_ratio"], 4))


def render_lumaui_python(document: dict | None = None) -> str:
    """The same tokens for Python: durations, timeouts, caps and names."""
    t = _lumaui(document)
    m = t["motion"]
    motion = {k: v for k, v in m.items() if not k.startswith("_")}
    type_scale = {k: v for k, v in t["type_scale"].items() if isinstance(v, dict)}
    expression_ink = {family: t["colors"][family]["calc_expression_operator_ink"]
                      for family in ("light", "dark", "high_contrast")}
    lines = [
        "# SPDX-License-Identifier: Apache-2.0",
        "# Generated by scripts/developer/generate-luma-platform-tokens.py from",
        "# config/shared/design-tokens.json. Do not edit.",
        '"""LumaUI tokens for Python: motion, timeouts, caps and category names."""',
        "",
        f"PHONE_MAX_WIDTH = {t['layout']['phone_max_width']}",
        f"MOTION = {motion!r}",
        f"TYPE_SCALE = {type_scale!r}",
        f"TYPE_UNIT_SCALE = {t['type_scale']['unit_scale']!r}",
        f"CALC_EXPRESSION_OPERATOR_INK = {expression_ink!r}",
        f"COUNT_ATTENTION_CAP = {t['count_badge']['attention_cap']!r}",
        f"COUNT_SEPARATOR = {t['count_badge']['separator']!r}",
        f"CATEGORY_ORDER = {tuple(t['category']['order'])!r}",
        f"CATEGORY_LABELS = {t['category']['labels']!r}",
        f"TOAST = {t['toast']!r}",
        f"DIALOG = {t['dialog']!r}",
        f"DRAWER = {t['drawer']!r}",
        f"SIDEBAR = { {k: v for k, v in t['sidebar'].items() if not k.startswith('_')}!r}",
        f"STRUCTURE = {dict((k, v) for k, v in t.get('structure', {}).items() if isinstance(v, dict))!r}",
        f"ACTION_CENTER = {_plain(t['action_center'])!r}",
        f"SELECTION_BUBBLE = {_plain(t['selection_bubble'])!r}",
        f"FILE_CARD = {_plain(t['file_card'])!r}",
        f"PLACE_SEARCH = {_plain(t['place_search'])!r}",
        f"MINI_CARD = {_plain(t['mini_card'])!r}",
        f"ACCOUNT_CARD = {_plain(t['account_card'])!r}",
        f"LIT_HEADER = {_plain(t['lit_header'])!r}",
        f"WINDOW = {_plain(t['window'])!r}",
        f"SCROLL = {_plain(t['scroll'])!r}",
        f"STACK = { {k: v for k, v in t['stacked_button'].items() if not isinstance(v, dict)}!r}",
        f"HUE = {_plain(t['hue'])!r}",
        f"CARD = {_plain(t['card'])!r}",
        f"MESSAGE_BUBBLE = {_plain(t['message_bubble'])!r}",
    ]
    # Family fragments: each new group as its own constant, e.g. ROWS, BAR, CREATIVE.
    lines += [f"{name.upper()} = {_plain_deep(t[name])!r}" for name in fragment_groups()
              if isinstance(t.get(name), dict)]
    lines.append("")
    return "\n".join(lines)


def _token_reference(document: dict, value: str) -> str:
    """A palette value: a literal, or "{group.key}" naming another token."""
    match = re.fullmatch(r"\{([\w.]+)\}", value)
    if not match:
        return value
    node = document
    for key in match.group(1).split("."):
        node = node[key]
    return _token_reference(document, node)


def render_lumaui_palette(document: dict | None = None) -> str:
    """lumaui-palette.css: libadwaita's names in the Luma design (ADR-052).

    The window sheet (lumaui-toolkit.css, imported from the patches' rules)
    names shadow bases as lumaui_<role> or lumaui_ink_<rrggbb>; the latter
    carry their value in their name, so they are read off the sheet."""
    document = document or load_document()
    palette = _lumaui(document)["toolkit_palette"]
    value = lambda text: _token_reference(document, text)  # noqa: E731
    lines = [LUMAUI_DERIVED_HEADER.rstrip(),
             "/* LumaUI palette, generated by scripts/developer/generate-luma-platform-tokens.py",
             " * from config/shared/design-tokens.json (lumaui.toolkit_palette). Loaded above",
             " * libadwaita, so a LumaUI app has the same colours on stock and patched",
             " * libraries. */", "",
             "/* libadwaita's default accent in Luma's shade (window_frame names it as the",
             " * accent only while the accent is the default). */",
             f"@define-color lumaui_accent_blue {value(palette['accent_blue'])};", "",
             "/* Bases for the toolkit sheet's shadows and tints. */"]
    bases = {f"lumaui_{name}": value(v) for name, v in palette["bases"].items()}
    toolkit = TOOLKIT_SHEET.read_text(encoding="utf-8") if TOOLKIT_SHEET.exists() else ""
    for hexa in sorted(set(re.findall(r"@lumaui_ink_([0-9a-f]{6})\b", toolkit))):
        bases[f"lumaui_ink_{hexa}"] = f"#{hexa}"
    lines += [f"@define-color {name} {v};" for name, v in sorted(bases.items())]
    named = palette["named"]
    lines += [""] + [f"@define-color {k} {value(v)};" for k, v in named["light"].items()]
    for media, key in (("(prefers-color-scheme: dark)", "dark"), ("(prefers-color-scheme: light)", "light_only")):
        lines += ["", f"@media {media} {{"]
        lines += [f"  @define-color {k} {value(v)};" for k, v in named[key].items()]
        lines.append("}")
    variables = palette["variables"]
    lines += ["", ":root {"] + [f"  {k}: {value(v)};" for k, v in variables["all"].items()] + ["}"]
    for media, key in (("(prefers-color-scheme: dark)", "dark"), ("(prefers-contrast: more)", "high_contrast")):
        lines += ["", f"@media {media} {{", "  :root {"]
        lines += [f"    {k}: {value(v)};" for k, v in variables[key].items()]
        lines += ["  }", "}"]
    lines += ["", "/* Frost and Glass materials: window_frame names these as the kit's colours",
              " * while a window is in that treatment. */"]
    for treatment, roles in palette["materials"].items():
        lines += [f"@define-color lumaui_{treatment}_{role} {value(v)};" for role, v in roles.items()]
    return "\n".join(lines) + "\n"


def render_appkit(appearance: str) -> str:
    document = load_document()
    values = dict(document[appearance])
    if appearance == "light":
        values["ink"] = values["application_ink"]
    lines = ["/* Generated by scripts/developer/generate-luma-platform-tokens.py. */"]
    lines.extend(f"@define-color luma_{name} {value};" for name, value in values.items())
    lines += ["", _ACCENT_BLOCKS[appearance].format(accent=values["accent"]), "", _CONTROLS_COMMENT]
    lines.extend(f"@define-color luma_{name} {value};"
                 for name, value in _controls(document, appearance).items())
    lines.append(render_lumaui_colors(appearance, document))
    lines.append(render_lumaui_metrics(document))
    lines.append(render_lumaui_appearance_metrics(appearance, document))
    return "\n".join(lines) + "\n"


def render_translucent_appkit(appearance: str) -> str:
    """Project a typed compositor recipe onto AppKit's semantic color roles."""
    document = load_document()
    recipe = document["surface_treatments"][appearance]
    # Frost and glass are one light material, the same as the Shell's: light,
    # see-through panels over the blurred wallpaper with dark ink (owner,
    # 2026-09-18: they look like their Settings thumbnails). Every role they
    # do not name comes from the light family.
    #
    # The surface roles are layers, not finished colours: the window paints
    # `frame`, the title band (`chrome`) and the panes (`pane`) paint over the
    # frame, and cards, rows and a pane's own header strip paint over a pane.
    # Each alpha is sized so the stack composites to the recipe: frost 62%
    # frame, 76% title band, 84% panes, about 89% rows; glass 30% frame, 62%
    # title band, 70% panes, about 77% rows. Stacking finished colours instead
    # is what turned every window into a near-opaque slab.
    values = dict(document["light"])
    values.update({
        "card": recipe["surface"],
        "window": recipe["frame"],
        "content": recipe["pane"],
        "surface": recipe["surface"],
        # The title band is painted over the frame and brings the title row
        # to a denser veil than the frame, so its small text holds 4.5:1
        # whatever the wallpaper behind it.
        "chrome": recipe.get("chrome", recipe["pane"]),
        "chrome_secondary": recipe.get("chrome_secondary", recipe["pane"]),
        "application_ink": recipe["ink"],
        "ink": recipe["ink"],
        "muted": recipe["muted"],
        "faint": recipe["muted"],
        # Amber as a glyph on a see-through panel needs one step darker than
        # paper's to hold 3:1 over a dark wallpaper.
        "amber": recipe["amber"],
        "border": recipe["line"],
        "border_faint": recipe["line"],
        "hairline": recipe["line"],
        "line": recipe["line"],
        "line_faint": recipe["line"],
        "island": recipe["island"],
        "menu": recipe.get("menu", recipe["surface"]),
        "menu_raised": recipe["island"],
        "menu_border": recipe["line"],
        "passive": recipe["fill"],
        "fill": recipe["fill"],
        "hover": recipe["hover"],
        "pressed": recipe["selected"],
        "selected": recipe["selected"],
    })
    values.update(_controls(document, appearance))
    lines = ["/* Generated by scripts/developer/generate-luma-platform-tokens.py. */"]
    lines.extend(f"@define-color luma_{name} {value};" for name, value in values.items())
    # Accent as text, and the ground it sits on: mixed toward black because
    # the panels are light. Glass lets more wallpaper through, so its accent
    # ink sits one step deeper to hold 4.5:1 on a selected row over black.
    mix = {"frost": "0.42", "glass": "0.50"}[appearance]
    accent = values["accent"]
    lines += [
        "",
        "/* Accent as text, and the ground it sits on: mixed toward black because the",
        " * panels are light. 4.5:1 or better on every frost and glass surface. */",
        f"@define-color luma_accent_ink mix({accent}, #000000, {mix});",
        f"@define-color luma_accent_soft alpha({accent}, 0.14);",
        "@define-color luma_accent_link @luma_accent_ink;",
    ]
    lines.append(render_lumaui_colors("light", document))
    # v70 Frost: the frame's own roles (smoked glass, off-white ink) over the light family.
    if appearance in _lumaui(document)["colors"]:
        lines.append(render_lumaui_colors(appearance, document))
    lines.append(render_lumaui_metrics(document))
    lines.append(render_lumaui_appearance_metrics("light", document))
    return "\n".join(lines) + "\n"


def render_icon_tokens() -> str:
    values = load_document()["icon"]
    lines = ["/* Generated by scripts/developer/generate-luma-platform-tokens.py. */", "#pragma once"]
    for name in ("application_corner_ratio", "application_tile_top_alpha", "application_tile_bottom_alpha", "application_outline_alpha"):
        lines.append(f"#define LUMA_ICON_{name.upper()} {values[name]}f")
    return "\n".join(lines) + "\n"


def render_frame_tokens() -> str:
    values = load_document()
    window = values["lumaui"]["window"]
    lines = ["/* Generated from config/shared/design-tokens.json. */", "#pragma once"]
    metrics = {
        "gap": window["gutter"], "window_radius": window["radius"],
        "island_radius": window["island_radius"], "header_height": window["title_height"],
        "title_size": window["identity_name_size"], "title_weight": window["identity_name_weight"],
        "control_size": window["control_size"], "control_padding": 0,
        "control_gap": window["control_gap"], "control_radius": window["control_radius"],
        "control_icon_size": window["control_icon"], "control_inset": window["title_padding_end"],
        "identity_inset": window["title_padding_start"], "identity_icon_size": window["identity_icon"],
        "identity_gap": window["identity_gap"], "back_gap": window["identity_gap"],
        "resize_band": values["window_frame"]["resize_band"],
        "resize_corner": values["window_frame"]["resize_corner"],
    }
    for name, value in metrics.items():
        name = name.upper()
        lines.append(f"#define LUMA_FRAME_{name} {value}")
    for appearance, shadow in values["elevation"].items():
        for name, value in shadow.items():
            if name == "color": value = "0x" + value.removeprefix("#") + "u"
            lines.append(f"#define LUMA_FRAME_{appearance.upper()}_SHADOW_{name.upper()} {value}")
    for appearance in ("light", "dark"):
        for name in ("window", "content", "ink", "muted"):
            colour = values[appearance][name].removeprefix("#")
            lines.append(f"#define LUMA_FRAME_{appearance.upper()}_{name.upper()} 0xff{colour}u")
    # Exactly the colour cascade used by render_appkit/render_translucent_appkit.
    def argb(colour):
        if colour.startswith("#"):
            return 0xff000000 | int(colour[1:], 16)
        match = re.fullmatch(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", colour)
        if not match:
            raise ValueError(f"Unsupported compatibility frame colour: {colour}")
        red, green, blue = (int(match[i]) for i in (1, 2, 3))
        alpha = int(float(match[4]) * 255 + 0.5)
        return (alpha << 24) | (red << 16) | (green << 8) | blue
    for appearance in ("light", "dark", "frost", "glass"):
        colours = dict(values["lumaui"]["colors"]["dark" if appearance == "dark" else "light"])
        colours.update(values["lumaui"]["colors"].get(appearance, {}))
        for role in ("window_top", "window_bottom", "frame_ink", "frame_muted",
                     "window_control_hover", "window_close_hover", "window_close_hover_ink",
                     "island_edge", "island_out", "island_lip", "window_ring"):
            lines.append(f"#define LUMA_FRAME_{appearance.upper()}_{role.upper()} 0x{argb(colours[role]):08x}u")
    return "\n".join(lines) + "\n"


def render_empty_states() -> str:
    t = load_document()["empty_state"]
    c = t["compact"]
    return f"""/* Generated from config/shared/design-tokens.json. */
.luma-empty-state {{ padding: {t['padding']}px; }}
.luma-empty-actions > flowboxchild {{ padding: 0; background: transparent; }}
.luma-empty-disc {{ min-width: {t['disc']}px; min-height: {t['disc']}px; border-radius: 999px; background: @luma_fill; }}
.luma-empty-icon {{ color: @luma_muted; opacity: .7; }}
.luma-empty-title {{ color: @luma_ink; font-size: {t['title']}px; font-weight: {t['title_weight']}; letter-spacing: -.015em; }}
.luma-empty-description {{ color: @luma_muted; font-size: {t['description']}px; font-weight: {t['description_weight']}; }}
.luma-list-empty {{ padding: {t['padding']}px; color: @luma_muted; font-size: 12px; font-weight: 600; }}
.luma-empty-state.compact .luma-empty-disc {{ min-width: {c['disc']}px; min-height: {c['disc']}px; }}
.luma-empty-state.compact .luma-empty-title {{ font-size: {c['title']}px; }}
.luma-empty-state.compact .luma-empty-description {{ font-size: {c['description']}px; }}
/* Native AdwStatusPage adapter. Opt-in only; its state/actions remain owned by
 * the application. The icon's padding plus glyph makes the same disc as Bin. */
statuspage.luma-native-empty > scrolledwindow > viewport > box {{ margin: 0; padding: {t['padding']}px; border-spacing: 16px; }}
statuspage.luma-native-empty > scrolledwindow > viewport > box > clamp > box {{ border-spacing: 8px; }}
statuspage.luma-native-empty .icon {{ min-width: 0; min-height: 0; -gtk-icon-size: {t['glyph']}px; padding: {(t['disc']-t['glyph'])//2}px; margin: 0 0 4px; border-radius: 999px; background: @luma_fill; color: @luma_muted; }}
statuspage.luma-native-empty .title {{ font-size: {t['title']}px; font-weight: {t['title_weight']}; color: @luma_ink; margin: 0; }}
statuspage.luma-native-empty .description {{ font-size: {t['description']}px; font-weight: {t['description_weight']}; color: @luma_muted; margin: 0; }}
statuspage.luma-native-empty.compact .icon {{ -gtk-icon-size: {c['glyph']}px; padding: {(c['disc']-c['glyph'])//2}px; }}
statuspage.luma-native-empty.compact .title {{ font-size: {c['title']}px; }}
statuspage.luma-native-empty.compact .description {{ font-size: {c['description']}px; }}
"""


def render_high_contrast() -> str:
    """Keep named semantic roles while deferring contrast to the actual GTK theme."""
    document = load_document()
    values = document["light"]
    roles = set(values) | {"surface", "chrome_secondary", "muted_ink", "faint_ink"}
    backgrounds = {"card", "window", "content", "surface", "chrome", "chrome_secondary",
                   "menu", "menu_raised", "island", "sheet"}
    lines = ["/* Generated; high contrast is owned by GTK/libadwaita. */"]
    for role in sorted(roles):
        if role in backgrounds:
            value = "@window_bg_color"
        elif role == "primary_ink":
            value = "@accent_fg_color"
        elif role in {"state_ink"}:
            value = "@accent_fg_color"
        elif role in {"knob"}:
            value = "@window_bg_color"
        elif role in {"destructive_text", "destructive_text_dim"}:
            # Measured: 8.5:1 on the light high-contrast sheet, 7.2:1 on the
            # dark one, and above 5:1 under the pressed wash in both.
            value = "mix(#ff3b30, @window_fg_color, 0.55)"
        elif role in {"ember_dot"}:
            value = "@warning_color"
        elif role in {"primary", "primary_hover", "selected", "pressed",
                      "state", "knob_ring"}:
            value = "@accent_bg_color"
        elif role in {"passive", "fill", "hover"}:
            value = "alpha(@window_fg_color, 0.12)"
        elif role in {"island_shadow", "menu_shadow"}:
            value = "transparent"
        else:
            value = "@window_fg_color"
        lines.append(f"@define-color luma_{role} {value};")
    lines += ["",
              "/* High contrast does not tint: accent text is the accent, and its ground is",
              " * the window, so nothing is carried by a wash that may not survive. */",
              "@define-color luma_accent_ink @luma_accent;",
              "@define-color luma_accent_soft @window_bg_color;",
              "@define-color luma_accent_link @luma_accent_ink;",
              "", _CONTROLS_COMMENT]
    controls = {
        "destructive_text": "mix(#ff3b30, @window_fg_color, 0.55)",
        "destructive_text_dim": "mix(#ff3b30, @window_fg_color, 0.55)",
        "ember_dot": "@warning_color", "sheet": "@window_bg_color",
        "menu_heading": "@window_fg_color", "menu_shortcut": "@window_fg_color",
        "menu_disabled": "@window_fg_color", "section_label": "@window_fg_color",
        "knob": "@window_bg_color", "knob_ring": "@accent_bg_color",
        "state": "@accent_bg_color", "state_ink": "@accent_fg_color",
    }
    # Measured: 8.5:1 on the light high-contrast sheet, 7.2:1 on the dark one,
    # and above 5:1 under the pressed wash in both, for destructive_text.
    missing = set(_controls(document, "light")) - set(controls)
    assert not missing, f"high contrast has no mapping for {sorted(missing)}"
    lines.extend(f"@define-color luma_{name} {value};" for name, value in controls.items())
    lines.append(render_lumaui_high_contrast(document))
    lines.append(render_lumaui_metrics(document))
    lines.append(render_lumaui_appearance_metrics("high_contrast", document))
    return "\n".join(lines) + "\n"


def render_surface_recipes() -> str:
    """One typed recipe source for every native renderer; no GTK blur fiction."""
    treatments = load_document()["surface_treatments"]
    lines = ["/* Generated from config/shared/design-tokens.json. */", "#pragma once",
             "static const char *luma_surface_recipes[] = {"]
    for mode in ("light", "dark", "frost", "glass"):
        parts = []
        for key, value in treatments[mode].items():
            encoded = "<" + (str(float(value)) if isinstance(value, (int, float))
                              else "'" + value + "'") + ">"
            parts.append("'" + key + "': " + encoded)
        lines.append("  " + json.dumps("{" + ", ".join(parts) + "}") + ",")
    return "\n".join(lines + ["};", ""])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    hc_output = ROOT / "src/luma-platform/appkit/luma-appkit-high-contrast-tokens.css"
    expected_hc = render_high_contrast()
    recipe_output = ROOT / "src/luma-platform/appearance/luma-surface-recipes.h"
    expected_recipes = render_surface_recipes()
    window_themes = {
        ROOT / "src/luma-shell-state" / filename: render_window_theme(toolkit, mode)
        for filename, toolkit, mode in (("gtk.css", 3, "light"),
                                       ("gtk-dark.css", 3, "dark"),
                                       ("gtk4.css", 4, "light"),
                                       ("gtk4-dark.css", 4, "dark"))
    }
    empty_output = ROOT / "src/luma-platform/appkit/luma-empty-state-tokens.css"
    expected_empty = render_empty_states()
    empty_header = ROOT / "src/luma-platform/ui/luma-empty-state-tokens.h"
    t = load_document()["empty_state"]
    expected_empty_header = ("/* Generated from config/shared/design-tokens.json. */\n#pragma once\n"
        f"#define LUMA_EMPTY_MAX_WIDTH {t['max_width']}\n"
        f"#define LUMA_EMPTY_COMPACT_MAX_WIDTH {t['compact']['max_width']}\n")
    expected_frame = render_frame_tokens()
    expected_icons = render_icon_tokens()
    expected = render("light")
    expected_dark = render("dark")
    expected_appkit = render_appkit("light")
    expected_appkit_dark = render_appkit("dark")
    expected_appkit_frost = render_translucent_appkit("frost")
    expected_appkit_glass = render_translucent_appkit("glass")
    expected_python = render_lumaui_python()
    expected_palette = render_lumaui_palette()
    if args.check:
        stale = [path for path, value in (*window_themes.items(),
                    (recipe_output, expected_recipes),
                    (hc_output, expected_hc),
                    (empty_output, expected_empty),
                    (empty_header, expected_empty_header),
                    (FRAME_OUTPUT, expected_frame),
                    (ICON_OUTPUT, expected_icons),
                    (OUTPUT, expected),
                    (DARK_OUTPUT, expected_dark),
                    (APPKIT_OUTPUT, expected_appkit),
                    (APPKIT_DARK_OUTPUT, expected_appkit_dark),
                    (APPKIT_FROST_OUTPUT, expected_appkit_frost),
                    (APPKIT_GLASS_OUTPUT, expected_appkit_glass),
                    (PYTHON_OUTPUT, expected_python),
                    (PALETTE_OUTPUT, expected_palette),
                 )
                 if not path.is_file() or path.read_text(encoding="utf-8") != value]
        if stale:
            print("out of date: " + ", ".join(map(str, stale)))
            return 1
        print(f"current: {OUTPUT}, {DARK_OUTPUT}, {APPKIT_OUTPUT}, {APPKIT_DARK_OUTPUT}")
        return 0
    PYTHON_OUTPUT.write_text(expected_python, encoding="utf-8")
    PALETTE_OUTPUT.write_text(expected_palette, encoding="utf-8")
    hc_output.write_text(expected_hc, encoding="utf-8")
    recipe_output.write_text(expected_recipes, encoding="utf-8")
    empty_output.write_text(expected_empty, encoding="utf-8")
    for path, value in window_themes.items():
        path.write_text(value, encoding="utf-8")
    empty_header.write_text(expected_empty_header, encoding="utf-8")
    FRAME_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    FRAME_OUTPUT.write_text(expected_frame, encoding="utf-8")
    ICON_OUTPUT.write_text(expected_icons, encoding="utf-8")
    OUTPUT.write_text(expected, encoding="utf-8")
    DARK_OUTPUT.write_text(expected_dark, encoding="utf-8")
    APPKIT_OUTPUT.write_text(expected_appkit, encoding="utf-8")
    APPKIT_DARK_OUTPUT.write_text(expected_appkit_dark, encoding="utf-8")
    APPKIT_FROST_OUTPUT.write_text(expected_appkit_frost, encoding="utf-8")
    APPKIT_GLASS_OUTPUT.write_text(expected_appkit_glass, encoding="utf-8")
    print(f"wrote: {OUTPUT}, {DARK_OUTPUT}, {APPKIT_OUTPUT}, {APPKIT_DARK_OUTPUT}")
    # LumaUI-1's C token header follows the same tokens; regenerate it too, so C never goes stale.
    c_tokens = ROOT / "src/luma-platform/ui/gen-lumaui-tokens.py"
    if c_tokens.is_file():
        import subprocess
        import sys
        subprocess.run([sys.executable, str(c_tokens)], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
