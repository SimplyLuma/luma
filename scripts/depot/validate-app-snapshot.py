#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Validate exact app export and optional declared Debug companion before signing."""
import argparse
import configparser
import json
import re
import subprocess

IDENTITY = re.compile(r"[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z][A-Za-z0-9_-]*){2,}\Z")
COMMIT = re.compile(r"[0-9a-f]{64}\Z")

def metadata(data):
    if len(data) > 1024 * 1024:
        raise ValueError("oversized export metadata")
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read_string(data)
    if parser.defaults():
        raise ValueError("unsupported metadata defaults")
    return parser

def validate(app_id, arch, refs, read):
    if not IDENTITY.fullmatch(app_id) or arch not in ("x86_64", "aarch64"):
        raise ValueError("invalid application identity or architecture")
    if len(refs) > 256 or len(set(refs)) != len(refs):
        raise ValueError("ambiguous or excessive export refs")
    apps = [r for r in refs if r.startswith(f"app/{app_id}/{arch}/")]
    if len(apps) != 1:
        raise ValueError("snapshot must contain exactly one application ref")
    app_ref = apps[0]
    branch = app_ref.rsplit("/", 1)[-1]
    if app_ref != f"app/{app_id}/{arch}/{branch}":
        raise ValueError("malformed application ref shape")
    if branch not in ("beta", "nightly", "stable"):
        raise ValueError("unmaintained application branch")
    debug_ref = f"runtime/{app_id}.Debug/{arch}/{branch}"
    allowed = {app_ref, debug_ref, f"appstream/{arch}", f"appstream2/{arch}"}
    if any(r not in allowed for r in refs):
        raise ValueError("unrelated ref in application snapshot")
    commit, app_data = read(app_ref)
    app = metadata(app_data)
    if not COMMIT.fullmatch(commit) or app.get("Application", "name", fallback=None) != app_id:
        raise ValueError("application metadata does not match exact export")
    selected = [{"ref": app_ref, "source_commit": commit}]
    if debug_ref in refs:
        declaration = f"Extension {app_id}.Debug"
        expected = {"directory": "lib/debug", "autodelete": "true", "no-autodownload": "true"}
        if not app.has_section(declaration) or dict(app[declaration]) != expected:
            raise ValueError("Debug companion is not declared by exact application")
        built = app.get("Build", "built-extensions", fallback="").split(";")
        if f"{app_id}.Debug" not in built or built.count(f"{app_id}.Debug") != 1:
            raise ValueError("Debug companion is not a unique built extension")
        debug_commit, debug_data = read(debug_ref)
        debug = metadata(debug_data)
        if (not COMMIT.fullmatch(debug_commit) or set(debug.sections()) != {"Runtime", "ExtensionOf"}
            or dict(debug["Runtime"]) != {"name": f"{app_id}.Debug"}
            or dict(debug["ExtensionOf"]) != {"ref": app_ref}):
            raise ValueError("Debug companion identity or ExtensionOf differs")
        selected.append({"ref": debug_ref, "source_commit": debug_commit})
    return selected

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--arch", required=True)
    args = parser.parse_args()
    def ostree(*command):
        return subprocess.check_output(["ostree", *command, f"--repo={args.repo}"], text=True, timeout=30)
    refs = ostree("refs").splitlines()
    def read(ref):
        commit = ostree("rev-parse", ref).strip()
        return commit, ostree("cat", commit, "/metadata")
    print(json.dumps(validate(args.app_id, args.arch, refs, read), sort_keys=True))

if __name__ == "__main__":
    main()
