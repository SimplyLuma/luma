#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""The Shell wears the glass veil applications wear, and nothing else.

In Frost and Glass an application window paints its title band, context menus
and popovers with one veil per treatment, from config/shared/design-tokens.json
(`surface_treatments.<frost|glass>.surface_veil`). The Shell's floating and
standing surfaces -- the Shelf islands (dock, clock, live chips), Quick
Options, the calendar, Shell popup menus, and the dock's labels, previews and
folders -- are the same material, so Quick Options and an application's
context menu match exactly. The Shell theme states that veil once, in one rule
per treatment in luma-shelf.css.

This fails when:

* the Shell's veil differs from the platform token (either side moved);
* the one rule is missing, split, or no longer names every surface;
* any other rule paints one of those surfaces in Frost or Glass (a second
  definition is how the two drifted apart in the first place);
* a Frost or Glass rule gives text a shadow;
* nothing was read (a check that parsed no rules passes for the wrong reason).

Modes:
  check-shell-veil.py CSS...          packaged sheets (luma-shelf.css and
                                      gnome-shell-light.css, from the theme
                                      GResource); run by the Shell build
  check-shell-veil.py --patches DIR   the veil as the patch series adds it,
                                      against the tokens; run by tests/unit
  check-shell-veil.py --self-test     prove each failure above still fires
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOKENS = ROOT / "config/shared/design-tokens.json"
TREATMENTS = ("frost", "glass")
MARKER = "The glass veil: the one definition."

# Every surface the one rule must paint, per treatment. {t} is the treatment.
SURFACES = (
    ".luma-shelf-material.luma-surface-{t}",
    ".luma-shelf-root.luma-shelf-separate > .luma-shelf-group > "
    ".luma-shelf-actions-island > "
    ".luma-shelf-material.luma-status-cluster.luma-surface-{t}",
    ".popup-menu.luma-surface-{t} .popup-menu-content",
    ".popup-menu.luma-surface-{t} .popup-menu-content.luma-quick-options",
    ".dash-label.luma-surface-{t}",
    ".luma-dock-preview.luma-surface-{t}",
    ".luma-dockf-surface.luma-surface-{t}",
)

COLOUR = re.compile(
    r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)")


def colour(text: str):
    m = COLOUR.fullmatch(text.strip())
    if not m:
        return None
    r, g, b, a = m.groups()
    return (int(r), int(g), int(b), round(float(a if a is not None else 1), 3))


def rules(text: str, name: str):
    """(name, line, [selectors], {property: value}) for each plain rule."""
    blanked = re.sub(r"/\*.*?\*/",
                     lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", blanked):
        head = m.group(1)
        if head.lstrip().startswith("@"):
            continue
        selectors = [" ".join(s.split()) for s in head.split(",") if s.strip()]
        decls = {}
        for d in m.group(2).split(";"):
            if ":" in d:
                k, v = d.split(":", 1)
                decls[k.strip()] = v.strip()
        line = blanked.count("\n", 0, m.start(1) + len(head) - len(head.lstrip())) + 1
        out.append((name, line, selectors, decls))
    return out


def load_tokens(path: pathlib.Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return {t: data["surface_treatments"][t]["surface_veil"] for t in TREATMENTS}


def check(sheets, tokens, require_exclusive=True):
    """sheets: [(name, text)]. Returns a list of failures (empty is a pass)."""
    failures = []
    parsed = []
    for name, text in sheets:
        found = rules(text, name)
        if not found:
            failures.append(f"{name}: no CSS rules read")
        parsed.extend(found)
    if not parsed:
        return failures or ["no CSS rules read at all"]
    for t in TREATMENTS:
        wanted = colour(tokens[t])
        if wanted is None:
            failures.append(f"token {t}.surface_veil is not a colour: {tokens[t]}")
            continue
        required = {s.format(t=t) for s in SURFACES}
        defining = [r for r in parsed
                    if required <= set(r[2]) and "background-color" in r[3]]
        if len(defining) != 1:
            partial = [f"{r[0]}:{r[1]}" for r in parsed
                       if required & set(r[2]) and "background-color" in r[3]]
            failures.append(
                f"{t}: expected one rule painting all {len(required)} surfaces, "
                f"found {len(defining)} (partial: {', '.join(partial) or 'none'})")
        for name, line, selectors, decls in defining:
            got = colour(decls["background-color"])
            if got != wanted:
                failures.append(
                    f"{name}:{line}: {t} veil {decls['background-color']} "
                    f"drifted from the platform token {tokens[t]}")
            if decls.get("text-shadow") != "none":
                failures.append(f"{name}:{line}: {t} veil rule must set text-shadow: none")
        if require_exclusive:
            for rule in parsed:
                name, line, selectors, decls = rule
                if rule in defining:
                    continue
                if required & set(selectors) and (
                        "background-color" in decls or "background" in decls):
                    hit = sorted(required & set(selectors))[0]
                    failures.append(
                        f"{name}:{line}: a second rule paints {hit}; "
                        "the veil is stated once")
        for name, line, selectors, decls in parsed:
            shadow = decls.get("text-shadow")
            if shadow and shadow != "none" and any(
                    f"luma-surface-{t}" in s for s in selectors):
                failures.append(f"{name}:{line}: text-shadow on a {t} surface: {shadow}")
    return failures


def veil_from_patches(directory: pathlib.Path):
    """The veil block as the last patch that adds it leaves it."""
    patches = sorted(directory.glob("[0-9][0-9][0-9][0-9]-*.patch"))
    if not patches:
        raise SystemExit(f"error: no patches under {directory}")
    block = None
    for patch in patches:
        current = None
        added = []
        for raw in patch.read_text(encoding="utf-8", errors="replace").splitlines():
            if raw.startswith("+++ "):
                current = raw[4:].strip()
                continue
            if current and current.endswith("data/theme/luma-shelf.css") and \
                    raw.startswith("+"):
                added.append(raw[1:])
        text = "\n".join(added)
        if MARKER in text:
            # From the comment that carries the marker, so it stays a comment.
            start = text.rfind("/*", 0, text.index(MARKER))
            block = (patch.name, text[max(start, 0):])
    return len(patches), block


SELF_TEST_TOKENS = {"frost": "rgba(248,249,250,.90)", "glass": "rgba(250,250,251,.85)"}


def good_sheet(frost="rgba(248, 249, 250, 0.90)", glass="rgba(250, 250, 251, 0.85)",
               drop=None, shadow="none"):
    parts = []
    for t, v in (("frost", frost), ("glass", glass)):
        sels = [s.format(t=t) for s in SURFACES if s.format(t=t) != drop]
        parts.append(",\n".join(sels) +
                     f" {{\n  background-color: {v};\n  text-shadow: {shadow}; }}\n")
    return (".luma-shelf-material.luma-surface-light { background-color: #eef0f2; }\n"
            "/* " + MARKER + " .dash-label.luma-surface-glass "
            "{ background-color: red; } */\n" + "".join(parts))


def self_test() -> int:
    cases = [
        ("the shared rule at the token values", [("a.css", good_sheet())], True),
        ("a glass veil one step off the token",
         [("a.css", good_sheet(glass="rgba(250, 250, 251, 0.84)"))], False),
        ("a frost veil of another colour",
         [("a.css", good_sheet(frost="rgba(246, 247, 249, 0.90)"))], False),
        ("a second rule painting a dock label",
         [("a.css", good_sheet()),
          ("b.css", ".dash-label.luma-surface-glass {\n  background-color: "
                    "rgba(250, 250, 251, 0.80); }\n")], False),
        ("a second rule using the background shorthand",
         [("a.css", good_sheet() +
           ".popup-menu.luma-surface-frost .popup-menu-content { background: #fff; }\n")],
         False),
        ("the shared rule no longer naming Quick Options",
         [("a.css", good_sheet(
             drop=".popup-menu.luma-surface-glass .popup-menu-content.luma-quick-options"))],
         False),
        ("a text shadow on the veil", [("a.css", good_sheet(shadow="0 1px 2px black"))],
         False),
        ("a text shadow elsewhere on a glass surface",
         [("a.css", good_sheet() +
           ".luma-surface-glass .luma-status-time { text-shadow: 0 1px 1px #000; }\n")],
         False),
        ("an empty sheet", [("a.css", "")], False),
    ]
    bad = 0
    for label, sheets, should_pass in cases:
        failures = check(sheets, SELF_TEST_TOKENS)
        ok = (not failures) == should_pass
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {label}: "
              f"{'pass' if not failures else failures[0]}")
    # The patch-series reader must find the block, and must see it change.
    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp)
        body = "".join("+" + line + "\n" for line in good_sheet().splitlines())
        (d / "0001-a.patch").write_text(
            "--- a/data/theme/luma-shelf.css\n+++ b/data/theme/luma-shelf.css\n"
            "@@ -1,0 +1,20 @@\n" + body, encoding="utf-8")
        count, block = veil_from_patches(d)
        found = block is not None and not check([block], SELF_TEST_TOKENS,
                                                require_exclusive=False)
        print(f"{'ok  ' if found else 'FAIL'} the veil read out of a patch")
        bad += not found
        (d / "0002-b.patch").write_text(
            "--- a/data/theme/luma-shelf.css\n+++ b/data/theme/luma-shelf.css\n"
            "@@ -1,0 +1,20 @@\n" + body.replace("0.85)", "0.62)"), encoding="utf-8")
        count, block = veil_from_patches(d)
        caught = count == 2 and bool(check([block], SELF_TEST_TOKENS,
                                           require_exclusive=False))
        print(f"{'ok  ' if caught else 'FAIL'} a later patch that moves the veil")
        bad += not caught
    print(f"self-test: {bad} of {len(cases) + 2} cases wrong")
    return 1 if bad else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tokens", type=pathlib.Path, default=TOKENS)
    parser.add_argument("--patches", type=pathlib.Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("css", nargs="*", type=pathlib.Path)
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    tokens = load_tokens(args.tokens)
    if args.patches:
        count, block = veil_from_patches(args.patches)
        if block is None:
            print(f"FAIL: no patch of {count} adds the veil block ({MARKER!r})")
            return 1
        failures = check([block], tokens, require_exclusive=False)
        label = f"{count} patches, veil from {block[0]}"
    else:
        if not args.css:
            parser.error("give the Shell theme sheets, --patches DIR or --self-test")
        sheets = [(p.name, p.read_text(encoding="utf-8")) for p in args.css]
        failures = check(sheets, tokens)
        label = f"{len(sheets)} sheets, {sum(len(rules(t, n)) for n, t in sheets)} rules"
    for failure in failures:
        print(f"FAIL: {failure}")
    print(f"Shell veil: {'FAIL' if failures else 'PASS'} ({label}; "
          + ", ".join(f"{t} {tokens[t]}" for t in TREATMENTS) + ")")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
