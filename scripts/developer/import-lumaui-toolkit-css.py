#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Move the Luma design out of the patched libadwaita and into LumaUI (ADR-052).

    import-lumaui-toolkit-css.py STOCK.css PATCHED.css

STOCK.css and PATCHED.css are the compiled libadwaita stylesheets
(`/org/gnome/Adwaita/styles/gtk.css`) of the same libadwaita release, stock and
with `patches/libadwaita` applied. Read them from each library with

    python3 -c 'import gi; gi.require_version("Adw", "1"); from gi.repository \
      import Adw, Gio; Adw.init(); print(Gio.resources_lookup_data( \
      "/org/gnome/Adwaita/styles/gtk.css", 0).get_data().decode())'

The script keeps exactly what the patches add or change, rule by rule and
property by property, in the patched sheet's order, and writes
`lumaui-toolkit.css`: every changed rule (frame, title row, identity, window
controls, gutter, islands, sidebars, menus, command rows, dialogs, materials),
with each colour literal replaced by a name from the palette.

The palette those names come from, `lumaui-palette.css`, is generated from
config/shared/design-tokens.json (`lumaui.toolkit_palette`) by
generate-luma-platform-tokens.py; this script lists any colour name, variable
or shadow base the patches define that the tokens do not yet hold.

Kit token names (`@luma_*` defined by the appkit token sheets) are never
redefined: the kit's treatment sheets already own them.

This is a migration tool. Once the visual patches are retired (ADR-052), the
toolkit sheet is LumaUI's own source and is edited by hand; until then, re-run
it whenever a visual libadwaita patch changes so the two stay in step.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPKIT = ROOT / "src/luma-platform/appkit"
TOKEN_SHEETS = sorted(APPKIT.glob("luma-appkit*-tokens.css"))
TOKENS = ROOT / "config/shared/design-tokens.json"

HEADER = """/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Provenance: derived from libadwaita {version}'s stylesheet (src/stylesheet,
 * LGPL-2.1-or-later) as changed by Luma's patches/libadwaita; see ADR-052.
 * Licence under review; nothing else in the kit is relicensed by this file. */
/* {title}
 *
 * Imported by scripts/developer/import-lumaui-toolkit-css.py from what
 * patches/libadwaita adds to libadwaita {version}'s stylesheet (ADR-052). LumaUI
 * loads it above the system libadwaita, so a LumaUI app looks the same on stock
 * and on patched libraries. Re-import while the visual patches exist; edit by
 * hand once they are retired. */
"""

# Shadow and tint bases that recur in the patches, by the role they play.
BASE_NAMES = {
    (25, 27, 31): "lumaui_shade",
    (9, 11, 14): "lumaui_shade_deep",
    (8, 11, 16): "lumaui_shade_night",
    (0, 0, 0): "lumaui_black",
    (0, 0, 6): "lumaui_black_blue",
    (255, 255, 255): "lumaui_white",
}

COLOUR = re.compile(
    r"(?<![\w-])#(?P<hex>[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b"
    r"|\b(?P<fn>rgba?)\((?P<args>[^()]*)\)",
    re.I,
)


def strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def blocks(text: str, media: str = "", out=None, defs=None):
    """Flatten a sheet into (media, selectors, body) and @define-color statements."""
    out = [] if out is None else out
    defs = [] if defs is None else defs
    index, length = 0, len(text)
    while index < length:
        brace = text.find("{", index)
        segment = text[index: brace if brace >= 0 else length]
        while (statement := re.search(r"@(define-color|import)\b([^;{]*);", segment)):
            if statement.group(1) == "define-color":
                name, value = statement.group(2).strip().split(None, 1)
                defs.append((media, name, " ".join(value.split())))
            segment = segment[:statement.start()] + segment[statement.end():]
        if brace < 0:
            break
        head = segment.strip()
        depth, end = 1, brace + 1
        while depth and end < length:
            depth += {"{": 1, "}": -1}.get(text[end], 0)
            end += 1
        body = text[brace + 1:end - 1]
        if head.startswith(("@media", "@supports")):
            blocks(body, (media + " " + head).strip(), out, defs)
        elif not head.startswith("@"):
            out.append((media, head, body))
        index = end
    return out, defs


def declarations(body: str) -> list[tuple[str, str]]:
    found = []
    for part in re.split(r";(?![^(]*\))", body):
        if ":" in part:
            prop, value = part.split(":", 1)
            found.append((prop.strip(), " ".join(value.split())))
    return found


def selectors(head: str) -> list[str]:
    return [" ".join(s.split()) for s in re.split(r",(?![^(]*\))", head) if s.strip()]


def final_values(rule_blocks):
    table = collections.defaultdict(dict)
    for media, head, body in rule_blocks:
        for selector in selectors(head):
            for prop, value in declarations(body):
                table[(media, selector)][prop] = value
    return table


def rgba(match: re.Match) -> tuple[int, int, int, float]:
    if match.group("hex"):
        digits = match.group("hex")
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        alpha = int(digits[6:8], 16) / 255 if len(digits) == 8 else 1.0
        return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16), alpha
    parts = [p for p in re.split(r"[\s,/]+", match.group("args").strip()) if p]
    red, green, blue = (int(float(p)) for p in parts[:3])
    alpha = 1.0
    if len(parts) > 3:
        alpha = float(parts[3][:-1]) / 100 if parts[3].endswith("%") else float(parts[3])
    return red, green, blue, alpha


class Namer:
    def __init__(self) -> None:
        self.names: dict[tuple[int, int, int], str] = {}

    def __call__(self, match: re.Match) -> str:
        red, green, blue, alpha = rgba(match)
        name = self.names.setdefault(
            (red, green, blue),
            BASE_NAMES.get((red, green, blue), f"lumaui_ink_{red:02x}{green:02x}{blue:02x}"),
        )
        if alpha >= 1:
            return f"@{name}"
        return f"alpha(@{name}, {round(alpha, 4):g})"


def kit_token_names() -> set[str]:
    names = set()
    for sheet in TOKEN_SHEETS:
        names.update(re.findall(r"@define-color\s+([\w-]+)", sheet.read_text()))
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stock", type=Path)
    parser.add_argument("patched", type=Path)
    parser.add_argument("--version", default="1.9.3")
    parser.add_argument("--output", type=Path, default=APPKIT)
    options = parser.parse_args()

    stock_blocks, stock_defs = blocks(strip_comments(options.stock.read_text()))
    patched_blocks, patched_defs = blocks(strip_comments(options.patched.read_text()))
    stock = final_values(stock_blocks)
    patched = final_values(patched_blocks)
    delta = {
        (media, selector, prop)
        for (media, selector), props in patched.items()
        for prop, value in props.items()
        if stock.get((media, selector), {}).get(prop) != value
    }

    # A changed declaration outside any @media now sits above libadwaita, so it
    # would also beat libadwaita's own dark or high-contrast variant of the
    # same selector and property. Carry those variants along, unchanged.
    delta |= {
        (media, selector, prop)
        for (media, selector), props in patched.items() if media
        for prop in props
        if ("", selector, prop) in delta
    }

    # Emit each changed declaration once, where its final value is declared,
    # so the layer keeps the patched sheet's order for equal specificity.
    last = {}
    for number, (media, head, body) in enumerate(patched_blocks):
        for selector in selectors(head):
            for prop, value in declarations(body):
                if (media, selector, prop) in delta and patched[(media, selector)][prop] == value:
                    last[(media, selector, prop)] = number
    by_block = collections.defaultdict(lambda: collections.defaultdict(list))
    for (media, selector, prop), number in last.items():
        by_block[number][selector].append(prop)

    root_vars = collections.OrderedDict()
    namer = Namer()
    rules: list[tuple[str, str]] = []
    for number in sorted(by_block):
        media, head, body = patched_blocks[number]
        values = dict(declarations(body))
        grouped = collections.OrderedDict()
        for selector in selectors(head):
            props = by_block[number].get(selector)
            if props:
                ordered = tuple(p for p, _v in declarations(body) if p in props)
                grouped.setdefault(ordered, []).append(selector)
        for props, group in grouped.items():
            if group == [":root"]:
                for prop in props:
                    root_vars[(media, prop)] = values[prop]
                continue
            lines = [",\n".join(group) + " {"]
            for prop in props:
                lines.append(f"  {prop}: {COLOUR.sub(namer, values[prop])};")
            lines.append("}")
            rules.append((media, "\n".join(lines)))

    kit_names = kit_token_names()
    stock_def = {(m, n): v for m, n, v in stock_defs}
    palette = collections.OrderedDict()
    for media, name, value in patched_defs:
        if name in kit_names or stock_def.get((media, name)) == value:
            continue
        palette[(media, name)] = value

    def wrap(media: str, text: str) -> str:
        if not media:
            return text
        inner = "\n".join("  " + line if line else line for line in text.splitlines())
        return f"{media} {{\n{inner}\n}}"

    # The palette is generated from config/shared/design-tokens.json
    # (lumaui.toolkit_palette) by generate-luma-platform-tokens.py. Say what the
    # patches define that the tokens do not, so it can be added there.
    tokens = json.loads(TOKENS.read_text(encoding="utf-8"))["lumaui"]["toolkit_palette"]
    known_names = {"": set(tokens["named"]["light"]), "@media (prefers-color-scheme: dark)": set(tokens["named"]["dark"]),
                   "@media (prefers-color-scheme: light)": set(tokens["named"]["light_only"])}
    known_vars = {"": set(tokens["variables"]["all"]), "@media (prefers-color-scheme: dark)": set(tokens["variables"]["dark"]),
                  "@media (prefers-contrast: more)": set(tokens["variables"]["high_contrast"])}
    missing = [f"{media or 'all'}: @{name}" for (media, name) in palette if name not in known_names.get(media, set())]
    missing += [f"{media or 'all'}: {prop}" for (media, prop) in root_vars if prop not in known_vars.get(media, set())]
    missing += [f"base: {name}" for name in namer.names.values()
                if not name.startswith("lumaui_ink_") and name[len("lumaui_"):] not in tokens["bases"]]
    if missing:
        print("add to lumaui.toolkit_palette in design-tokens.json:", *missing, sep="\n  ")

    out = [HEADER.format(title="LumaUI toolkit: the window frame and the parts every Luma app shares.",
                         version=options.version)]
    current, pending = None, []
    for media, text in rules + [(None, "")]:
        if media != current and pending:
            out.append(wrap(current, "\n".join(pending)))
            pending = []
        current = media
        if text:
            pending.append(text)
    (options.output / "lumaui-toolkit.css").write_text("\n".join(out) + "\n")
    print(f"{len(delta)} changed declarations, {len(rules)} rules, "
          f"{len(palette)} palette names, {len(root_vars)} variables, {len(namer.names)} bases; "
          "now run generate-luma-platform-tokens.py for lumaui-palette.css")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
