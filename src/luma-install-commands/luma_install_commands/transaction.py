# SPDX-License-Identifier: MPL-2.0
"""dnf5's own transaction preview, read back.

Luma asks dnf5 to resolve every install or removal first (``--assumeno``),
shows the person dnf5's table exactly as Fedora shows it, and reads the
sections to decide how the change can be made on an image-based system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

#: Section headings dnf5 prints, mapped to a stable key.
SECTIONS = {
    "Installing:": "install",
    "Installing group/module packages:": "install-group",
    "Installing dependencies:": "install-dep",
    "Installing weak dependencies:": "install-weak",
    "Upgrading:": "upgrade",
    "Downgrading:": "downgrade",
    "Reinstalling:": "reinstall",
    "Replacing:": "replaced",
    "Removing:": "remove",
    "Removing dependent packages:": "remove-dep",
    "Removing unused dependencies:": "remove-unused",
    "Changing reason:": "reason",
    "Installing groups:": "groups",
    "Installing environmental groups:": "groups",
    "Removing groups:": "groups",
    "Upgrading groups:": "groups",
}

_ROW = re.compile(r"^ (\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+([\d.]+\s+\S+)\s*$")
_ALREADY = re.compile(r'^Package "(.+)" is already installed\.$')
NOISE = ("Operation aborted by the user.", "Operation aborted.")


@dataclass
class Row:
    name: str
    arch: str
    evr: str
    repo: str

    @property
    def release(self) -> str:
        return self.evr.rsplit("-", 1)[-1] if "-" in self.evr else ""


@dataclass
class Preview:
    text: str = ""                      # what to show the person (dnf5's words, minus the abort line)
    ok: bool = True                     # dnf5 resolved the request
    nothing_to_do: bool = False
    sections: dict[str, list[Row]] = field(default_factory=dict)
    already_installed: list[str] = field(default_factory=list)   # NEVRAs

    def names(self, *keys: str) -> list[str]:
        out: list[str] = []
        for key in keys:
            for row in self.sections.get(key, []):
                if row.name not in out:
                    out.append(row.name)
        return out

    def rows(self, *keys: str) -> list[Row]:
        return [row for key in keys for row in self.sections.get(key, [])]

    @property
    def adds(self) -> list[str]:
        """Packages asked for by the person (not their dependencies)."""
        return self.names("install", "install-group")

    @property
    def changes_installed(self) -> bool:
        """The request changes or removes something already installed."""
        return bool(self.rows("upgrade", "downgrade", "replaced", "remove", "remove-dep", "remove-unused", "reinstall"))


def parse(output: str, returncode: int) -> Preview:
    preview = Preview()
    shown: list[str] = []
    current = ""
    failed = False
    for line in output.splitlines():
        stripped = line.strip()
        if stripped in NOISE:
            continue
        shown.append(line)
        if stripped.startswith("Failed to resolve the transaction") or stripped.startswith("No match for argument") \
                or stripped.startswith("Problem") or stripped.startswith("Error:") \
                or stripped.startswith("Failed to "):
            failed = True
        if stripped == "Nothing to do.":
            preview.nothing_to_do = True
        match = _ALREADY.match(stripped)
        if match:
            preview.already_installed.append(match.group(1))
            continue
        if line in SECTIONS or stripped in SECTIONS:
            current = SECTIONS.get(line) or SECTIONS[stripped]
            preview.sections.setdefault(current, [])
            continue
        if stripped.startswith("Transaction Summary"):
            current = ""
            continue
        if current and current != "groups":
            row = _ROW.match(line)
            if row:
                name, arch, evr, repo, _size = row.groups()
                if evr.startswith("0:"):
                    evr = evr[2:]
                preview.sections[current].append(Row(name, arch, evr, repo))
            elif line and not line.startswith("  "):
                current = ""
    preview.text = "\n".join(shown).rstrip("\n")
    # dnf5 answers --assumeno with exit 1 whether it resolved or not.
    preview.ok = not failed
    if preview.ok and not any(preview.sections.get(k) for k in SECTIONS.values() if k not in ("reason", "groups")):
        preview.nothing_to_do = True
    return preview
