#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Apply the shared, pinned keyboard series to a pristine Stevia checkout."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    source = args.source.resolve()
    pins = {}
    for line in (repo / "config/mobile/stevia-source.env").read_text().splitlines():
        if line.startswith("STEVIA_"):
            key, value = line.split("=", 1)
            pins[key] = value
    def git(*argv: str) -> str:
        return subprocess.check_output(["git", "-C", str(source), *argv], text=True).strip()
    if git("rev-parse", "HEAD") != pins["STEVIA_UPSTREAM_COMMIT"]:
        parser.error("source is not the pinned Stevia commit")
    if git("status", "--porcelain", "--untracked-files=all"):
        parser.error("source must be pristine; preserve existing edits in a separate checkout")
    series = repo / "patches/stevia"
    names = [line.strip() for line in (series / "series").read_text().splitlines()
             if line.strip() and not line.startswith("#")]
    if any(Path(name).name != name or not name.endswith(".patch") for name in names):
        parser.error("invalid patch series entry")
    patches = [series / name for name in names]
    # Later patches depend on earlier ones. Preflight the entire series in a
    # temporary index so a failed check leaves the caller's checkout untouched.
    with tempfile.TemporaryDirectory(prefix="luma-stevia-index-") as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        subprocess.run(["git", "-C", str(source), "read-tree", "HEAD"], env=env, check=True)
        for patch in patches:
            subprocess.run(["git", "-C", str(source), "apply", "--cached", str(patch)],
                           env=env, check=True)
    for patch in patches:
        subprocess.run(["git", "-C", str(source), "apply", str(patch)], check=True)
    fonts = source / "data/fonts"
    fonts.mkdir()
    for name in ("Figtree.ttf", "OFL.txt"):
        shutil.copyfile(repo / "assets/fonts/figtree" / name, fonts / name)
    print(json.dumps({"upstream": pins["STEVIA_UPSTREAM_COMMIT"],
                      "patches": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in patches},
                      "figtree": hashlib.sha256((fonts / "Figtree.ttf").read_bytes()).hexdigest()}, indent=2))


if __name__ == "__main__":
    main()
