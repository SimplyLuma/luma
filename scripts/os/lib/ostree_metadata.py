#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""OSTree metadata helpers for the Luma OS pipeline.

  pkglist INVENTORY            print rpmostree.rpmdb.pkglist as GVariant text
  root-dirtree REPO COMMIT     print the commit's root dirtree and dirmeta
  same-tree REPO A B           exit 0 when two commits carry the same tree
  copy-args REPO COMMIT KEY... print --add-metadata arguments copying KEYs
  version REPO COMMIT          print the commit's version metadata
  parents REPO COMMIT N        print up to N ancestors (nearest first)
  timestamp REPO COMMIT        print the commit's timestamp (UTC, ISO 8601)
"""

import subprocess
import sys


def ostree(repo, *args):
    return subprocess.check_output(["ostree", f"--repo={repo}", *args], text=True).rstrip("\n")


def gvariant_string(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def pkglist(inventory):
    rows = []
    with open(inventory, encoding="utf-8") as stream:
        for line in stream:
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 5 or fields[0] == "gpg-pubkey":
                continue
            name, epoch, version, release, arch = fields[:5]
            rows.append((name, int(epoch or 0), version, release, arch))
    # rpm-ostree reads rpmostree.rpmdb.pkglist only as a(sssss), the epoch a
    # decimal string, sorted by name (rpmostree_variant_pkgs_from_sack). Any
    # other type is ignored: rpm-ostree then looks for the rpmdb in the tree,
    # which a commit fetched metadata-only does not have, and rpmostreed crashes
    # computing the update diff (`rpm-ostree upgrade --check`).
    rows.sort(key=lambda row: (row[0].encode(), row[4].encode(), row[1], row[2], row[3]))
    if not rows:
        raise SystemExit("error: empty package inventory")
    items = ", ".join(
        f"({gvariant_string(n)}, {gvariant_string(str(e))}, {gvariant_string(v)}, {gvariant_string(r)}, {gvariant_string(a)})"
        for n, e, v, r, a in rows
    )
    return f"@a(sssss) [{items}]"


def main(argv):
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    command = argv[1]
    if command == "pkglist" and len(argv) == 3:
        print(pkglist(argv[2]))
    elif command == "root-dirtree" and len(argv) == 4:
        line = ostree(argv[2], "ls", "--checksum", "--dironly", argv[3], "/")
        print(line.split()[4])
    elif command == "same-tree" and len(argv) == 5:
        a = ostree(argv[2], "ls", "--checksum", "--dironly", argv[3], "/")
        b = ostree(argv[2], "ls", "--checksum", "--dironly", argv[4], "/")
        if a != b:
            print(f"error: trees differ:\n  {a}\n  {b}", file=sys.stderr)
            return 1
    elif command == "copy-args" and len(argv) >= 5:
        present = set(ostree(argv[2], "show", "--list-metadata-keys", argv[3]).splitlines())
        for key in argv[4:]:
            if key in present:
                value = ostree(argv[2], "show", f"--print-metadata-key={key}", argv[3])
                print(f"--add-metadata={key}={value}")
    elif command == "version" and len(argv) == 4:
        keys = set(ostree(argv[2], "show", "--list-metadata-keys", argv[3]).splitlines())
        if "version" in keys:
            value = ostree(argv[2], "show", "--print-metadata-key=version", argv[3])
            print(value.strip("'"))
    elif command == "timestamp" and len(argv) == 4:
        import datetime
        import gi
        gi.require_version("OSTree", "1.0")
        from gi.repository import Gio, OSTree
        repo = OSTree.Repo.new(Gio.File.new_for_path(argv[2]))
        repo.open(None)
        _, variant = repo.load_variant(OSTree.ObjectType.COMMIT, argv[3])
        seconds = OSTree.commit_get_timestamp(variant)
        print(datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    elif command == "parents" and len(argv) == 5:
        commit, remaining = argv[3], int(argv[4])
        while remaining > 0:
            try:
                parent = ostree(argv[2], "rev-parse", f"{commit}^")
            except subprocess.CalledProcessError:
                break
            print(parent)
            commit = parent
            remaining -= 1
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
