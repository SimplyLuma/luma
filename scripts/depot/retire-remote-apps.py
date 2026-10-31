#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Mark withdrawn apps EOL without deleting refs, history or application data.

Invoked inside the guarded signer by update-remote.sh before regenerating
AppStream and the summary. Policy belongs to the sealed source, never a feed.
"""
import argparse
import json
import re
import subprocess

RETIRED = {"org.projectluma.Displays": "Display controls are in Settings."}


def retire(repo, fingerprint, homedir):
    def run(*args):
        return subprocess.check_output(args, text=True, timeout=120).strip()

    def ostree(*args):
        return run("ostree", f"--repo={repo}", *args)

    # Inventory all architectures and branches, including older supported
    # versions. An unknown/malformed retired ref must stop publication.
    refs = ostree("refs").splitlines()
    selected = []
    for ref in refs:
        parts = ref.split("/")
        if len(parts) < 2 or parts[:1] != ["app"] or parts[1] not in RETIRED:
            continue
        if len(parts) != 4 or any(not re.fullmatch(r"[A-Za-z0-9._-]+", p) for p in parts[2:]):
            raise ValueError("malformed retired application ref")
        selected.append((ref, RETIRED[parts[1]], ostree("rev-parse", ref)))
    before = {ref: ostree("rev-parse", ref) for ref in refs}
    records = []
    for ref, reason, previous in sorted(selected):
        keys = ostree("show", "--list-metadata-keys", previous).splitlines()
        if "ostree.endoflife" in keys:
            current = ostree("show", "--print-metadata-key=ostree.endoflife", previous)
            if current == "'" + reason + "'":
                records.append({"ref": ref, "previous": previous, "commit": previous, "changed": False})
                continue
        # The previous immutable commit is the only source. No app rebuild,
        # uninstall, rebase, ref deletion, object pruning or summary update.
        run("flatpak", "build-commit-from", "--force", "--no-update-summary",
            f"--gpg-sign={fingerprint}", f"--gpg-homedir={homedir}",
            f"--src-ref={previous}", f"--end-of-life={reason}", str(repo), ref)
        commit = ostree("rev-parse", ref)
        if (ostree("rev-parse", commit + "^") != previous or
                ostree("diff", previous, commit) or
                ostree("show", "--print-metadata-key=ostree.endoflife", commit) != "'" + reason + "'"):
            raise ValueError("retirement changed payload, lost history or lacks EOL metadata")
        records.append({"ref": ref, "previous": previous, "commit": commit, "changed": True})
    after = ostree("refs").splitlines()
    changed = {row["ref"] for row in records}
    if set(refs) != set(after) or any(ostree("rev-parse", ref) != value
                                    for ref, value in before.items() if ref not in changed):
        raise ValueError("retirement changed unrelated refs")
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--gpg-sign", required=True)
    parser.add_argument("--gpg-homedir", required=True)
    args = parser.parse_args()
    print(json.dumps({"retired": retire(args.repo, args.gpg_sign, args.gpg_homedir)}, sort_keys=True))


if __name__ == "__main__":
    main()
