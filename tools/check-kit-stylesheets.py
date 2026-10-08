#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""An application's style sheet belongs to the kit, not to the application.

`luma_appkit.add_style_sheet()` (and `luma_ui_add_style_resource()` in C) keep
a sheet in step with the surface treatment: when the desktop moves to Glass,
every sheet the kit owns is reloaded against the new palette. A sheet loaded
through a `Gtk.CssProvider` of the application's own is loaded once and never
again, so the application goes on painting whatever the palette was when it
started, beside windows that followed. Eleven applications did this, which is
one of the reasons no two windows on one Glass desktop were the same colour.

The check is deliberately blunt: any construction of a provider that is then
added to a display, outside the kit itself, is reported. Somewhere that really
must own its own provider says so in tools/private-css-providers.txt with a
reason; an entry with no reason is refused, because silence is how something
ends up excluded by accident.

Run with --self-test to prove the detector still detects.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
ALLOW_FILE = ROOT / "tools/private-css-providers.txt"

# The kit is where a provider is supposed to be made.
KIT_OWNED = (
    "src/luma-platform/appkit/luma_appkit/",
    "src/luma-platform/ui/",
)

PYTHON_PATTERN = re.compile(r"\bGtk\.CssProvider\s*\(\s*\)")
C_PATTERN = re.compile(r"\bgtk_css_provider_new\s*\(\s*\)")
DISPLAY_PATTERN = re.compile(
    r"add_provider_for_display|gtk_style_context_add_provider_for_display"
)


def _read_allow_list() -> dict[str, str]:
    """Paths allowed to own a provider, each with the reason it must."""
    allowed: dict[str, str] = {}
    if not ALLOW_FILE.is_file():
        return allowed
    for number, line in enumerate(ALLOW_FILE.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        path, _, reason = stripped.partition(":")
        if not reason.strip():
            raise SystemExit(
                f"{ALLOW_FILE}:{number}: every entry needs a reason after the path"
            )
        allowed[path.strip()] = reason.strip()
    return allowed


def _candidate_files(root: pathlib.Path) -> list[pathlib.Path]:
    files = sorted(
        path
        for pattern in ("*.py", "*.c")
        for path in root.rglob(pattern)
        if path.is_file()
    )
    return files


def scan(root: pathlib.Path, allowed: dict[str, str]) -> tuple[list[str], int]:
    """Return (findings, files_read). A scan that read nothing is a failure."""
    findings: list[str] = []
    read = 0
    for path in _candidate_files(root):
        try:
            relative = str(path.relative_to(ROOT))
        except ValueError:
            relative = str(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        read += 1
        if any(relative.startswith(owned) for owned in KIT_OWNED):
            continue
        if relative in allowed:
            continue
        if not DISPLAY_PATTERN.search(text):
            continue
        pattern = PYTHON_PATTERN if path.suffix == ".py" else C_PATTERN
        for number, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                findings.append(
                    f"{relative}:{number}: style sheet loaded through a private "
                    f"CssProvider; use add_style_sheet() so it follows the "
                    f"surface treatment"
                )
    return findings, read


def self_test() -> int:
    """Fail if the detector stops detecting the case it was written for."""
    with tempfile.TemporaryDirectory() as directory:
        sandbox = pathlib.Path(directory)
        bad = sandbox / "app.py"
        bad.write_text(
            "provider = Gtk.CssProvider()\n"
            "Gtk.StyleContext.add_provider_for_display(display, provider, 601)\n",
            encoding="utf-8",
        )
        good = sandbox / "kind.py"
        good.write_text("add_style_sheet('/usr/share/app/app.css')\n", encoding="utf-8")
        findings, read = scan(sandbox, {})
        if read != 2:
            print(f"self-test: scanned {read} files, expected 2", file=sys.stderr)
            return 1
        if len(findings) != 1 or "app.py:1" not in findings[0]:
            print(f"self-test: expected one finding in app.py, got {findings}",
                  file=sys.stderr)
            return 1
        allowed_findings, _ = scan(sandbox, {str(bad): "deliberate, in the test"})
        if allowed_findings:
            print(f"self-test: allow list did not exclude {bad}: {allowed_findings}",
                  file=sys.stderr)
            return 1
    print("self-test: the private-provider detector still detects")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true",
                        help="prove the detector still catches its motivating case")
    parser.add_argument("--root", default=str(SOURCE_ROOT),
                        help="directory to scan (default: src/)")
    arguments = parser.parse_args()

    if arguments.self_test:
        return self_test()

    allowed = _read_allow_list()
    root = pathlib.Path(arguments.root)
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    findings, read = scan(root, allowed)
    if read == 0:
        print(f"error: read no source files under {root}; a check that reads "
              f"nothing passes for the wrong reason", file=sys.stderr)
        return 2
    for finding in findings:
        print(finding)
    print(f"read {read} source files, {len(findings)} private style providers",
          file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
