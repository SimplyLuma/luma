# SPDX-License-Identifier: Apache-2.0
"""Which libraries an application needs and its package never declared.

Third-party packages bundle an engine and declare little of what it links
against. Checking every ELF file a package ships with the loader finds those
libraries, and finds too much: the package's own bundled libraries (found at
run time through the program's own search path), and optional integrations
with toolkits the program does not use (Chrome's Qt 5 and Qt 6 theme shims
pulled both toolkits into its tree).

The check here follows the program the launcher starts:

1. The launcher's command is resolved to its program. A wrapper script is
   followed to the programs it names in its own package.
2. The program's libraries are followed transitively. A library the package
   ships is found by name, as the program's own search path would find it.
   What resolves nowhere is missing.
3. A shipped library outside that set (a plugin) counts only if it plugs into
   something the program loads: one of its direct dependencies, other than
   the C and C++ runtime, is in the program's set. A platform plugin of a
   bundled toolkit counts; a shim for a toolkit the program never loads does
   not.

Runs on a tree on disk; reads files only.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

from .device_graphics import elf_dynamic

MACHINE_FLAGS = {62: "x86-64", 183: "AArch64"}
# Present for every program; says nothing about which toolkit a plugin belongs to.
RUNTIME = re.compile(r"^(ld-linux.*\.so\.\d+|ld\.so\.1|linux-vdso\.so\.1|"
                     r"lib(c|m|dl|rt|pthread|util|resolv|gcc_s|stdc\+\+|atomic|c\+\+|c\+\+abi)\.so(\.\d+)*)$")
PATH_FOLDERS = ("usr/local/bin", "usr/bin", "bin", "usr/games", "usr/local/sbin", "usr/sbin", "sbin")
SCRIPT_BYTES = 256 * 1024
PLUGIN_LIMIT = 6000


def resolve_in(root: Path, path: str) -> Path | None:
    """A path inside the tree with its links followed inside the tree."""
    pending = [part for part in Path(path).parts if part != "/"]
    current: list[str] = []
    hops = 0
    while pending:
        part = pending.pop(0)
        if part == ".":
            continue
        if part == "..":
            current = current[:-1]
            continue
        step = root.joinpath(*current, part)
        if step.is_symlink():
            hops += 1
            if hops > 40:
                return None
            target = os.readlink(step)
            if target.startswith("/"):
                current = []
            pending = [piece for piece in Path(target).parts if piece != "/"] + pending
            continue
        current.append(part)
    real = root.joinpath(*current)
    return real if real.exists() else None


def _inside(root: Path, path: Path) -> str:
    return "/" + str(path.relative_to(root))


def _is_program(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            head = stream.read(20)
    except OSError:
        return False
    return len(head) >= 18 and head[:4] == b"\x7fELF" and head[16] in (2, 3) and ".so" not in path.name


def launcher_programs(root: Path, command: str, shipped: list[str], depth: int = 0) -> list[str]:
    """The ELF programs the launcher's command starts, as paths in the tree."""
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    while words and (words[0] == "env" or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0])):
        words.pop(0)
    if not words:
        return []
    candidates = [words[0]] if words[0].startswith("/") else [f"/{folder}/{words[0]}" for folder in PATH_FOLDERS]
    target = next((found for found in (resolve_in(root, candidate) for candidate in candidates)
                   if found is not None and found.is_file()), None)
    if target is None:
        return []
    if _is_program(target):
        return [_inside(root, target)]
    try:
        with target.open("rb") as stream:
            text = stream.read(SCRIPT_BYTES).decode("utf-8", "replace")
    except OSError:
        return []
    if not text.startswith("#!") or depth >= 2:
        return []
    # The programs of its own package the script names; failing that, the ones beside it.
    folder = target.parent
    own = [line.strip() for line in shipped if line.strip().startswith("/")]
    named, beside = [], []
    for line in own:
        path = root / line.lstrip("/")
        name = Path(line).name
        if path.is_symlink() or not path.is_file() or not os.access(path, os.X_OK):
            continue
        mentioned = re.search(r"(?<![A-Za-z0-9_.-])" + re.escape(name) + r"(?![A-Za-z0-9_.-])", text) is not None
        if mentioned and (path.parent == folder or line in text or f"/{name}" in text):
            if _is_program(path):
                named.append(line)
            elif depth < 1 and path != target:
                named += launcher_programs(root, line, shipped, depth + 1)
        elif path.parent == folder and _is_program(path):
            beside.append(line)
    return list(dict.fromkeys(named or beside))[:8]


def parse_cache(text: str) -> dict[tuple[str, str], str]:
    """`ldconfig -p` as {(soname, architecture flag): path}."""
    found: dict[tuple[str, str], str] = {}
    for line in text.splitlines():
        match = re.match(r"\s*(\S+) \(([^)]*)\) => (\S+)", line)
        if not match:
            continue
        flags = [flag.strip() for flag in match.group(2).split(",")]
        architecture = next((flag for flag in flags if flag in MACHINE_FLAGS.values()), "")
        found.setdefault((match.group(1), architecture), match.group(3))
    return found


def check(root: Path, programs: list[str], shipped: list[str], cache_text: str) -> dict[str, object]:
    """{"programs", "missing" (program's own), "plugin_missing", "loaded"} for a tree."""
    cache = parse_cache(cache_text)
    bundled: dict[str, list[str]] = {}
    for line in shipped:
        line = line.strip()
        name = Path(line).name
        if line.startswith("/") and ".so" in name:
            bundled.setdefault(name, []).append(line)
    facts: dict[str, dict[str, object] | None] = {}

    def dynamic(path: str) -> dict[str, object] | None:
        if path not in facts:
            real = resolve_in(root, path)
            facts[path] = elf_dynamic(real) if real is not None and real.is_file() else None
        return facts[path]

    def locate(soname: str, machine: int) -> str:
        for candidate in bundled.get(soname, []):
            described = dynamic(candidate)
            if described is not None and described["machine"] == machine:
                return candidate
        return cache.get((soname, MACHINE_FLAGS.get(machine, "")), "")

    loaded: set[str] = set()
    missing: set[str] = set()
    queue = [program for program in programs if dynamic(program) is not None]
    seen = set(queue)
    while queue:
        described = dynamic(queue.pop())
        if described is None:
            continue
        for soname in described["needed"]:  # type: ignore[union-attr]
            loaded.add(soname)
            path = locate(soname, int(described["machine"]))  # type: ignore[arg-type]
            if not path:
                if not RUNTIME.match(soname):
                    missing.add(soname)
            elif path not in seen:
                seen.add(path)
                queue.append(path)
    plugin_missing: set[str] = set()
    toolkit = {soname for soname in loaded if not RUNTIME.match(soname)}
    if programs:
        for paths in list(bundled.values())[:PLUGIN_LIMIT]:
            for path in paths:
                if path in seen:
                    continue
                described = dynamic(path)
                if described is None or not toolkit.intersection(described["needed"]):  # type: ignore[arg-type]
                    continue
                for soname in described["needed"]:  # type: ignore[union-attr]
                    if not RUNTIME.match(soname) and not locate(soname, int(described["machine"])):  # type: ignore[arg-type]
                        plugin_missing.add(soname)
    return {"programs": programs, "missing": sorted(missing), "plugin_missing": sorted(plugin_missing - missing),
            "loaded": len(loaded)}


def main(argv: list[str] | None = None) -> int:
    """tree_check ROOT COMMAND FILES-LIST CACHE-TEXT [EXTRA-PROGRAM ...]: prints the check as JSON."""
    values = list(sys.argv[1:] if argv is None else argv)
    root, command = Path(values[0]), values[1]
    shipped = Path(values[2]).read_text(errors="replace").splitlines()
    programs = launcher_programs(root, command, shipped)
    for extra in values[4:]:
        programs += [program for program in launcher_programs(root, extra, shipped) if program not in programs]
    print(json.dumps(check(root, programs, shipped, Path(values[3]).read_text(errors="replace"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
