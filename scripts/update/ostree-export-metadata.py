#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Export an accepted deployment as a coherent base, not a builder client layer."""

import argparse
import subprocess
import sys
from pathlib import Path

# rpm-ostree's client-layer reader interprets the parent as the base when either
# clientlayer=true or the legacy spec is present. These describe the builder's
# local transaction, not the accepted filesystem that Luma publishes as a base.
CLIENT_KEYS = frozenset({
    "rpmostree.clientlayer", "rpmostree.clientlayer_version", "rpmostree.spec",
    "rpmostree.packages", "rpmostree.modules", "rpmostree.removed-base-packages",
    "rpmostree.replaced-base-packages", "rpmostree.replaced-base-remote-packages",
    "rpmostree.state-sha512",
})
REPLACED_KEYS = frozenset({
    "ostree.ref-binding", "ostree.collection-binding",
    "org.projectluma.source-revision", "org.projectluma.accepted-deployment",
    "org.projectluma.image-sha256", "org.projectluma.build-report-sha256",
    "org.projectluma.export-policy",
})


def ostree(repo, *args):
    return subprocess.check_output(
        ["ostree", f"--repo={repo}", *args], text=True, encoding="utf-8"
    ).rstrip("\n")


def keys(repo, commit):
    return set(ostree(repo, "show", "--list-metadata-keys", commit).splitlines())


def preserved_keys(repo, commit):
    source_keys = keys(repo, commit)
    unknown = {key for key in source_keys
               if key.startswith("rpmostree.clientlayer") and key not in CLIENT_KEYS}
    if unknown:
        raise ValueError(f"unreviewed client-layer metadata: {sorted(unknown)}")
    if source_keys & {"rpmostree.clientlayer", "rpmostree.spec"}:
        if "rpmostree.rpmdb.pkglist" not in source_keys:
            raise ValueError("client-layer export requires its complete rpmdb package inventory")
    return {key for key in source_keys - CLIENT_KEYS - REPLACED_KEYS
            if not key.startswith("ostree.composefs.")}


def verify(repo, source, exported):
    keep = preserved_keys(repo, source)
    exported_keys = keys(repo, exported)
    if exported_keys & CLIENT_KEYS:
        raise ValueError("export still contains builder client-layer metadata")
    if {key for key in exported_keys if key.startswith("rpmostree.clientlayer")}:
        raise ValueError("export contains unreviewed client-layer metadata")
    # The sole allowed filesystem change discards the builder's ancestral RPMDB.
    # The helper compares every other file and directory metadata object.
    subprocess.run([sys.executable, str(Path(__file__).with_name("ostree-export-tree.py")),
                    "verify", repo, source, exported], check=True)
    if any(key.startswith("ostree.composefs.") for key in exported_keys):
        raise ValueError("export retains composefs metadata for the pre-normalized tree")
    for key in sorted(keep):
        args = ("show", "--no-byteswap", f"--print-metadata-key={key}")
        if key not in exported_keys or ostree(repo, *args, source) != ostree(
            repo, *args, exported
        ):
            raise ValueError(f"export changed preserved metadata: {key}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("keep", "verify"))
    parser.add_argument("repo")
    parser.add_argument("source")
    parser.add_argument("exported", nargs="?")
    args = parser.parse_args()
    try:
        if args.action == "keep":
            print("\n".join(sorted(preserved_keys(args.repo, args.source))))
        elif args.exported:
            verify(args.repo, args.source, args.exported)
        else:
            parser.error("verify requires the exported commit")
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f"error: OSTree export metadata: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
