#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bind a gate fixture correction to an unchanged candidate's source."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


FIXTURE_PATHS = frozenset({
    "scripts/os/gate.sh",
    "scripts/os/verify-media.sh",
    "scripts/os/lib/gate_checks_revision.py",
    "tests/os/gate/admin-prompt.sh",
    "tests/os/gate/app-remove-restore.py",
    "tests/os/test_app_remove_restore_phase.py",
    "tests/os/gate/browser-opens.js",
    "tests/os/gate/browser-opens.sh",
    "tests/os/gate/browser-fixture-keyring.py",
    "tests/unit/test_browser_fixture_keyring.py",
    "tests/smoke/desktop.sh",
    "tests/unit/test_gate_checks_revision.py",
})


def admit(repo: Path, source: str, checks: str) -> dict:
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args])

    for revision in (source, checks):
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("Gate source and checks require full commit IDs")
        if git("rev-parse", revision + "^{commit}").decode().strip() != revision:
            raise ValueError("Revision is not the exact commit")
    if git("rev-parse", "HEAD").decode().strip() != checks:
        raise ValueError("Run the gate from the exact checks revision")
    if git("status", "--porcelain", "--untracked-files=no").strip():
        raise ValueError("Gate checks checkout has tracked changes")
    if subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", source, checks]).returncode:
        raise ValueError("Checks revision must descend from candidate source")
    paths = git("diff", "--no-renames", "--name-only", "-z", source, checks).decode().split("\0")
    paths = sorted(path for path in paths if path)
    unexpected = set(paths) - FIXTURE_PATHS
    if unexpected:
        raise ValueError("Changes outside gate fixtures: " + ", ".join(sorted(unexpected)))
    patch = git("diff", "--no-renames", "--binary", source, checks)
    return {"result": "PASS", "source_revision": source, "checks_revision": checks,
            "changed_paths": paths, "patch_sha256": hashlib.sha256(patch).hexdigest(),
            "candidate_payload_changed": False, "phase_reuse": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--checks", required=True)
    args = parser.parse_args()
    print(json.dumps(admit(args.repo, args.source, args.checks), indent=2))


if __name__ == "__main__":
    main()
