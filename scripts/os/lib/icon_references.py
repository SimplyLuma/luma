#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail when first-party code names an icon that nothing in the image provides.

    icon_references.py --root IMAGE_ROOT [--theme Prairie]
        [--source DIR ...] [--patches DIR ...] [--installed]
        [--allowlist FILE] [--json REPORT] [--overlay DIR ...]

An icon name is provided when the image has it in the icon theme chain the
desktop uses (the theme, what it Inherits, and hicolor), in a GResource that a
binary or a .gresource file carries under an icons/ path (GtkApplication adds
its resource base path's icons/ to the theme, and GTK and libadwaita add their
own), or in an icons/ directory an application ships in its own data
directory for Gtk.IconTheme.add_search_path. Private resource and data icons
count for every app: the check proves a name exists in the image, not which
app may see it, so a reference to another app's private icon is missed.

Names are read from .ui (icon properties and menu icon attributes), .py, .js,
.c and .h (literals ending in -symbolic, and any literal passed where an icon
name is expected), .css (-gtk-icontheme()), and the added lines of .patch
files that change those kinds of file. Names built at run time are not seen.
Tests and fixtures are skipped. An exact name is required: GTK's fallback from
a missing name draws the missing-image placeholder or another glyph, and a
reference that only works through that fallback is the bug this check stops.

--overlay adds the payload of candidate packages (an extracted RPM tree) to
what the image provides, to prove a fix before it is pinned.

Exit 0 when every name resolves, 1 when any does not, 2 on bad input.
"""

from __future__ import annotations

import argparse
import configparser
import fnmatch
import json
import os
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

ICON_SUFFIXES = (".svg", ".png", ".xpm")
SOURCE_SUFFIXES = {".ui", ".py", ".js", ".c", ".h", ".css", ".desktop", ".in"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", "tests", "test", "fixtures", "testdata"}
NAME = r"[a-z0-9][a-z0-9._-]*[a-z0-9]"
# Where the context says "icon name", application ids such as org.projectluma.Contacts count too.
ANY_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]"

UI_PROPERTY = re.compile(
    r'<property\s+name="(?:icon-name|icon_name|start-icon-name|end-icon-name|'
    r'primary-icon-name|secondary-icon-name|default-icon-name)"[^>]*>\s*(' + ANY_NAME + r')\s*</property>')
UI_ATTRIBUTE = re.compile(r'<attribute\s+name="(?:icon|verb-icon)"[^>]*>\s*(' + NAME + r')\s*</attribute>')
SYMBOLIC_LITERAL = re.compile(r'''(?<![\w{])(["'])(''' + NAME + r'''-symbolic(?:-rtl|-ltr)?)\1''')
ICON_CONTEXT = (
    # icon_name="x", iconName: 'x', icon-name = "x" (keyword arguments, JS
    # object properties, assignments); not device.icon-name or fallback-icon-name.
    re.compile(r'''(?<![\w.-])(?:icon[_-]?name|iconName)\s*[=:]\s*(?P<q>["'])(''' + ANY_NAME + r''')(?P=q)'''),
    # set_icon_name("x"), gtk_image_new_from_icon_name ("x"),
    # gtk_button_set_icon_name (button, "x"), Gio.ThemedIcon.new("x").
    re.compile(r'''\b\w*(?:set_icon_name|set_from_icon_name|new_from_icon_name|from_icon_name|'''
               r'''ThemedIcon(?:\.new)?|g_themed_icon_new|set_icon_from_name)\s*\(\s*'''
               r'''(?:[\w.>-]+(?:\s*\([^()]*\))?\s*,\s*)?(?P<q>["'])(''' + ANY_NAME + r''')(?P=q)'''),
    # g_object_set (image, "icon-name", "x", NULL), set_property('icon-name', 'x').
    re.compile(r'''(?P<k>["'])icon[_-]name(?P=k)[ \t]*,[ \t]*(?P<q>["'])(''' + ANY_NAME + r''')(?P=q)'''),
)
CSS_ICONTHEME = re.compile(r'''-gtk-icontheme\(\s*(["'])(''' + NAME + r''')\1\s*\)''')
DESKTOP_ICON = re.compile(r'^Icon=(' + ANY_NAME + r')\s*$', re.MULTILINE)
UI_TEXT_SYMBOLIC = re.compile(r'>\s*(' + NAME + r'-symbolic(?:-rtl|-ltr)?)\s*<')


@dataclass
class Reference:
    name: str
    path: str
    line: int


@dataclass
class Providers:
    names: dict[str, str] = field(default_factory=dict)

    def add(self, name: str, where: str) -> None:
        self.names.setdefault(name, where)


def icon_name(filename: str) -> str | None:
    """The theme lookup name a file answers to, or None for a non-icon file."""
    base = os.path.basename(filename)
    if base.endswith(".symbolic.png"):
        return base[: -len(".symbolic.png")] + "-symbolic"
    for suffix in ICON_SUFFIXES:
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return None


# ---------------------------------------------------------------- providers

def theme_chain(icons: Path, theme: str) -> list[str]:
    chain, queue = [], [theme]
    while queue:
        name = queue.pop(0)
        if name in chain or not (icons / name).is_dir():
            continue
        chain.append(name)
        index = icons / name / "index.theme"
        if index.is_file():
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                parser.read(index, encoding="utf-8")
                inherits = parser.get("Icon Theme", "Inherits", fallback="")
            except configparser.Error:
                inherits = ""
            queue.extend(part.strip() for part in inherits.split(",") if part.strip())
    if "hicolor" not in chain and (icons / "hicolor").is_dir():
        chain.append("hicolor")
    return chain


def gvdb_keys(data: bytes) -> list[str]:
    """Every key of a GVDB file (the GResource container format)."""
    if len(data) < 24 or data[:8] not in (b"GVariant", b"raVGtnai"):
        return []
    endian = "<" if data[:8] == b"GVariant" else ">"
    start, end = struct.unpack_from(endian + "II", data, 16)
    if end > len(data) or end < start + 8:
        return []
    n_bloom, n_buckets = struct.unpack_from(endian + "II", data, start)
    n_bloom &= (1 << 27) - 1
    items_at = start + 8 + 4 * n_bloom + 4 * n_buckets
    count = (end - items_at) // 24
    raw = []
    for index in range(max(count, 0)):
        _hash, parent, key_start, key_size, _kind, _pad, _vs, _ve = struct.unpack_from(
            endian + "IIIHccII", data, items_at + 24 * index)
        raw.append((parent, data[key_start:key_start + key_size].decode("utf-8", "replace")))
    keys: list[str] = []
    for index in range(len(raw)):
        parts, seen, cursor = [], set(), index
        while cursor != 0xFFFFFFFF and cursor < len(raw) and cursor not in seen:
            seen.add(cursor)
            parent, key = raw[cursor]
            parts.append(key)
            cursor = parent
        keys.append("".join(reversed(parts)))
    return keys


def elf_resources(data: bytes) -> list[bytes]:
    """The .gresource.* sections of a 64- or 32-bit ELF file."""
    if data[:4] != b"\x7fELF":
        return []
    wide, endian = data[4] == 2, "<" if data[5] == 1 else ">"
    try:
        if wide:
            shoff = struct.unpack_from(endian + "Q", data, 0x28)[0]
            shentsize, shnum, shstrndx = struct.unpack_from(endian + "HHH", data, 0x3A)
            layout, name_at, off_at, size_at = endian + "IIQQQQIIQQ", 0, 4, 5
        else:
            shoff = struct.unpack_from(endian + "I", data, 0x20)[0]
            shentsize, shnum, shstrndx = struct.unpack_from(endian + "HHH", data, 0x2E)
            layout, name_at, off_at, size_at = endian + "IIIIIIIIII", 0, 4, 5
        headers = [struct.unpack_from(layout, data, shoff + i * shentsize) for i in range(shnum)]
        names = headers[shstrndx]
        strtab = data[names[off_at]:names[off_at] + names[size_at]]
    except (struct.error, IndexError):
        return []
    found = []
    for header in headers:
        start = header[name_at]
        label = strtab[start:strtab.find(b"\0", start)]
        if label.startswith(b".gresource."):
            found.append(data[header[off_at]:header[off_at] + header[size_at]])
    return found


def resource_icons(blob: bytes) -> list[str]:
    names = []
    for key in gvdb_keys(blob):
        if "/icons/" in key and not key.endswith("/"):
            name = icon_name(key)
            if name:
                names.append(name)
    return names


def collect_providers(root: Path, theme: str, chain: list[str] | None = None) -> Providers:
    providers = Providers()
    icons = root / "usr/share/icons"
    for member in chain if chain is not None else theme_chain(icons, theme):
        for directory, _dirs, files in os.walk(icons / member):
            for filename in files:
                name = icon_name(filename)
                if name:
                    providers.add(name, f"theme {member}")
    binaries: list[Path] = []
    for relative in ("usr/bin", "usr/libexec", "usr/lib64", "usr/lib"):
        base = root / relative
        if not base.is_dir():
            continue
        for directory, dirs, files in os.walk(base):
            depth = Path(directory).relative_to(base).parts
            if len(depth) >= 2 or "python3" in directory or "site-packages" in directory:
                dirs[:] = []
            for filename in files:
                path = Path(directory) / filename
                if relative in ("usr/lib64", "usr/lib") and ".so" not in filename \
                        and not filename.endswith(".gresource"):
                    continue
                binaries.append(path)
    for directory, _dirs, files in os.walk(root / "usr/share"):
        for filename in files:
            if filename.endswith(".gresource"):
                binaries.append(Path(directory) / filename)
    for path in binaries:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 512 * 1024 * 1024:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        blobs = [data] if data[:8] in (b"GVariant", b"raVGtnai") else elf_resources(data)
        for blob in blobs:
            for name in resource_icons(blob):
                providers.add(name, f"resource in /{path.relative_to(root)}")
    # Icons an application ships in its own data directory and adds with
    # Gtk.IconTheme.add_search_path.
    private_roots = [p for p in (root / "usr/share").glob("*") if p.name != "icons"]
    private_roots += list(root.glob("usr/lib/python3*/site-packages/*"))
    for base in private_roots:
        if not base.is_dir():
            continue
        for directory, _dirs, files in os.walk(base):
            if "/icons" not in directory[len(str(base)):] + "/":
                continue
            for filename in files:
                name = icon_name(filename)
                if name:
                    providers.add(name, f"app data /{Path(directory, filename).relative_to(root)}")
    return providers


# --------------------------------------------------------------- references

def references_in_text(text: str, suffix: str, path: str, first_line: int = 1,
                       line_numbers: list[int] | None = None) -> list[Reference]:
    found: list[Reference] = []

    def emit(match: re.Match, group: int) -> None:
        offset = text.count("\n", 0, match.start(group))
        line = line_numbers[offset] if line_numbers else first_line + offset
        found.append(Reference(match.group(group), path, line))

    if suffix == ".ui":
        for pattern in (UI_PROPERTY, UI_ATTRIBUTE, UI_TEXT_SYMBOLIC):
            for match in pattern.finditer(text):
                emit(match, 1)
    elif suffix == ".desktop":
        for match in DESKTOP_ICON.finditer(text):
            emit(match, 1)
    elif suffix == ".css":
        for match in CSS_ICONTHEME.finditer(text):
            emit(match, 2)
    else:
        for match in SYMBOLIC_LITERAL.finditer(text):
            emit(match, 2)
        for pattern in ICON_CONTEXT:
            for match in pattern.finditer(text):
                emit(match, match.lastindex)
    unique: dict[tuple[str, int], Reference] = {}
    for reference in found:
        unique.setdefault((reference.name, reference.line), reference)
    return list(unique.values())


def source_kind(path: Path) -> str | None:
    """.ui, .py, ... for a scanned file; foo.desktop.in counts as .desktop."""
    suffix = Path(path.name[:-3]).suffix if path.name.endswith(".in") else path.suffix
    return suffix if suffix in SOURCE_SUFFIXES and suffix != ".in" else None


def skipped(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts) or path.name.startswith("test_") \
        or path.name.startswith("test-")


def scan_sources(base: Path, label: str | None = None) -> list[Reference]:
    found: list[Reference] = []
    for directory, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for filename in sorted(files):
            path = Path(directory) / filename
            kind = source_kind(path)
            if kind is None or skipped(path.relative_to(base)):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            shown = str(Path(label) / path.relative_to(base)) if label else str(path)
            found.extend(references_in_text(text, kind, shown))
    return found


def installed_launchers(root: Path, overlays: list[Path]) -> set[str]:
    names: set[str] = set()
    for base in (root, *overlays):
        applications = base / "usr/share/applications"
        if applications.is_dir():
            names.update(entry.name for entry in applications.iterdir())
    return names


def package_namespace(base: Path) -> bool:
    """Whether BASE groups independently packaged application source roots.

    Imported applications can be nested under a namespace such as src/external.
    Their launchers must be evaluated separately; one installed sibling does
    not install every application in that namespace. Require multiple children
    with both packaging and launchers, so ordinary app layout directories and
    shared libraries keep their existing complete source coverage.
    """
    applications = 0
    for child in base.iterdir():
        if not child.is_dir() or child.name in SKIP_DIRS:
            continue
        has_spec = any(not skipped(path.relative_to(child)) for path in child.rglob("*.spec"))
        if not has_spec:
            continue
        has_launcher = any(path.name.endswith((".desktop", ".desktop.in"))
                           and not skipped(path.relative_to(child))
                           for path in child.rglob("*.desktop*"))
        if has_launcher:
            applications += 1
            if applications >= 2:
                return True
    return False


def scan_components(base: Path, launchers: set[str]) -> tuple[list[Reference], list[str]]:
    """Scan each top-level component of a source tree that the image contains.

    A component that ships launchers (.desktop files) is judged only when at
    least one of them is installed: an app withdrawn from the image keeps its
    source in the tree, and its own package still provides its icons.
    Components without launchers (libraries, the platform) are always read.
    """
    found: list[Reference] = []
    absent: list[str] = []
    if not base.is_dir():
        return found, absent
    for component in sorted(p for p in base.iterdir() if p.is_dir() and p.name not in SKIP_DIRS):
        if package_namespace(component):
            nested, missing = scan_components(component, launchers)
            found.extend(nested)
            absent.extend(f"{component.name}/{name}" for name in missing)
            continue
        desktop = {path.name.removesuffix(".in") for path in component.rglob("*.desktop*")
                   if path.name.endswith((".desktop", ".desktop.in")) and not skipped(path.relative_to(component))}
        if desktop and not desktop & launchers:
            absent.append(component.name)
            continue
        found.extend(scan_sources(component, str(base / component.name)))
    for path in sorted(p for p in base.iterdir() if p.is_file() and source_kind(p)):
        found.extend(references_in_text(path.read_text(encoding="utf-8", errors="replace"), source_kind(path), str(path)))
    return found, absent


def scan_patches(base: Path) -> list[Reference]:
    """Names on the added lines of patches, attributed to the patched file."""
    found: list[Reference] = []
    for patch in sorted(base.rglob("*.patch")):
        if skipped(patch.relative_to(base)):
            continue
        target, lines, numbers, new_line = None, [], [], 0

        def flush() -> None:
            if target and lines:
                found.extend(references_in_text("\n".join(lines), source_kind(Path(target)),
                                                 f"{patch.relative_to(base.parent)} ({target})",
                                                 line_numbers=numbers))

        for raw in patch.read_text(encoding="utf-8", errors="replace").splitlines():
            if raw.startswith("+++ "):
                flush()
                lines, numbers = [], []
                name = raw[4:].split("\t")[0].strip()
                name = name[2:] if name.startswith(("a/", "b/")) else name
                target = None if name == "/dev/null" or source_kind(Path(name)) is None \
                    or skipped(Path(name)) else name
            elif raw.startswith("@@"):
                match = re.search(r"\+(\d+)", raw)
                new_line = int(match.group(1)) if match else 0
            elif raw.startswith("+") and target:
                lines.append(raw[1:])
                numbers.append(new_line)
                new_line += 1
            elif raw.startswith(" "):
                new_line += 1
        flush()
    return found


INSTALLED_GLOBS = (
    "usr/share/applications/org.projectluma.*.desktop",
    "usr/share/applications/io.luma.*.desktop",
    "usr/lib/python3*/site-packages/luma_*",
    "usr/lib/python3*/site-packages/prairie_*",
    "usr/lib/python3*/site-packages/charlie_*",
    "usr/share/luma*",
    "usr/share/prairie-core",
    "usr/share/charlie",
)


def scan_installed(root: Path) -> list[Reference]:
    found: list[Reference] = []
    for pattern in INSTALLED_GLOBS:
        for base in sorted(root.glob(pattern)):
            if base.is_dir():
                found.extend(scan_sources(base, "/" + str(base.relative_to(root))))
            elif base.is_file() and source_kind(base):
                text = base.read_text(encoding="utf-8", errors="replace")
                found.extend(references_in_text(text, source_kind(base), "/" + str(base.relative_to(root))))
    return found


# ---------------------------------------------------------------- allowlist

def load_allowlist(path: Path | None) -> list[tuple[str, str]]:
    """Lines of `NAME [PATH-GLOB]  # reason`; a reason is required."""
    entries: list[tuple[str, str]] = []
    if not path:
        return entries
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        body, _, reason = raw.partition("#")
        fields = body.split()
        if not fields:
            continue
        if not reason.strip():
            raise SystemExit(f"{path}:{number}: an allowlist entry needs a # reason")
        entries.append((fields[0], fields[1] if len(fields) > 1 else "*"))
    return entries


def allowed(reference: Reference, entries: list[tuple[str, str]]) -> bool:
    return any(fnmatch.fnmatch(reference.name, name) and fnmatch.fnmatch(reference.path, glob)
               for name, glob in entries)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", required=True, type=Path, help="image root (/ inside the image)")
    parser.add_argument("--theme", default="Prairie")
    parser.add_argument("--source", action="append", default=[], type=Path)
    parser.add_argument("--patches", action="append", default=[], type=Path)
    parser.add_argument("--installed", action="store_true",
                        help="also scan first-party code installed in the image")
    parser.add_argument("--allowlist", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--overlay", action="append", default=[], type=Path,
                        help="extracted candidate packages whose icons count as provided")
    args = parser.parse_args(argv)
    if not (args.root / "usr/share/icons").is_dir() or not all(o.is_dir() for o in args.overlay):
        print(f"no icon themes under {args.root}/usr/share/icons", file=sys.stderr)
        return 2
    providers = collect_providers(args.root, args.theme)
    chain = theme_chain(args.root / "usr/share/icons", args.theme)
    for overlay in args.overlay:
        extra = collect_providers(overlay, args.theme, chain)
        for name, where in extra.names.items():
            providers.add(name, f"overlay {where}")
    references: list[Reference] = []
    skipped_components: list[str] = []
    for source in args.source:
        found, absent = scan_components(source, installed_launchers(args.root, args.overlay))
        references.extend(found)
        skipped_components.extend(absent)
    if skipped_components:
        print(f"note: not in the image, not checked: {', '.join(sorted(skipped_components))}")
    for patches in args.patches:
        references.extend(scan_patches(patches))
    if args.installed:
        references.extend(scan_installed(args.root))
    entries = load_allowlist(args.allowlist)
    missing = [r for r in references if r.name not in providers.names and not allowed(r, entries)]
    by_name: dict[str, list[Reference]] = {}
    for reference in missing:
        by_name.setdefault(reference.name, []).append(reference)
    for name in sorted(by_name):
        places = by_name[name]
        shown = ", ".join(f"{r.path}:{r.line}" for r in places[:4])
        more = f" and {len(places) - 4} more" if len(places) > 4 else ""
        print(f"FAIL icon {name}: no theme, resource or app data provides it; used at {shown}{more}")
    print(f"{'FAIL' if by_name else 'PASS'} icon references: {len(references)} references, "
          f"{len({r.name for r in references})} names, {len(providers.names)} provided names, "
          f"{len(by_name)} missing (theme chain {' > '.join(theme_chain(args.root / 'usr/share/icons', args.theme))})")
    if args.json:
        args.json.write_text(json.dumps({
            "result": "fail" if by_name else "pass",
            "references": len(references),
            "provided": len(providers.names),
            "missing": {name: [f"{r.path}:{r.line}" for r in refs] for name, refs in sorted(by_name.items())},
        }, indent=2) + "\n")
    return 1 if by_name else 0


if __name__ == "__main__":
    sys.exit(main())
