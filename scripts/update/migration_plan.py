#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Plan the move of an existing machine onto a Luma release channel.

Input: `rpm-ostree status --json` of the machine and the target commit's
package list (rpmostree.rpmdb.pkglist, as `ostree show --print-metadata-key`
prints it, or a NAME<TAB>EPOCH<TAB>VERSION<TAB>RELEASE<TAB>ARCH inventory).

Output (JSON): what the migration will do with every local change the booted
deployment carries on top of its base:

  * overrides (replaced or removed base packages) are reset: the release is a
    different base. A replacement whose version is newer than the release's
    package is a downgrade and needs --allow-downgrade. A removal of a package
    the release still contains is re-applied.
  * layered packages (from repositories or local RPM files) that the release
    already contains are uninstalled (the release's version wins; a newer
    local build is a downgrade); the others are kept.

Nothing here touches the system; migrate-to-channel.sh executes the plan.
"""

import argparse
import json
import re
import subprocess
import sys

NEVRA = re.compile(r"^(?P<name>.+)-(?:(?P<epoch>\d+):)?(?P<version>[^-]+)-(?P<release>[^-]+)\.(?P<arch>[^.]+)$")


def rpmvercmp(a, b):
    """A faithful port of rpm's rpmvercmp() (rpmio/rpmvercmp.c)."""
    if a == b:
        return 0
    i = j = 0
    la, lb = len(a), len(b)
    while i < la or j < lb:
        while i < la and not a[i].isalnum() and a[i] not in "~^":
            i += 1
        while j < lb and not b[j].isalnum() and b[j] not in "~^":
            j += 1
        if (i < la and a[i] == "~") or (j < lb and b[j] == "~"):
            if not (i < la and a[i] == "~"):
                return 1
            if not (j < lb and b[j] == "~"):
                return -1
            i += 1
            j += 1
            continue
        if (i < la and a[i] == "^") or (j < lb and b[j] == "^"):
            if i >= la:
                return -1
            if j >= lb:
                return 1
            if a[i] != "^":
                return 1
            if b[j] != "^":
                return -1
            i += 1
            j += 1
            continue
        if not (i < la and j < lb):
            break
        si, sj = i, j
        if a[i].isdigit():
            while i < la and a[i].isdigit():
                i += 1
            while j < lb and b[j].isdigit():
                j += 1
            numeric = True
        else:
            while i < la and a[i].isalpha():
                i += 1
            while j < lb and b[j].isalpha():
                j += 1
            numeric = False
        x, y = a[si:i], b[sj:j]
        if not y:
            return 1 if numeric else -1
        if numeric:
            x, y = x.lstrip("0"), y.lstrip("0")
            if len(x) != len(y):
                return 1 if len(x) > len(y) else -1
        if x != y:
            return 1 if x > y else -1
    if i >= la and j >= lb:
        return 0
    return -1 if i >= la else 1


def evr_compare(a, b):
    """Compare (epoch, version, release) tuples."""
    if int(a[0] or 0) != int(b[0] or 0):
        return 1 if int(a[0] or 0) > int(b[0] or 0) else -1
    return rpmvercmp(a[1], b[1]) or rpmvercmp(a[2], b[2])


def parse_nevra(text):
    match = NEVRA.match(text)
    if not match:
        return None
    return match.group("name"), (match.group("epoch") or "0", match.group("version"), match.group("release")), match.group("arch")


def parse_pkglist(text):
    """Parse rpmostree.rpmdb.pkglist GVariant text (a(sssss), as rpm-ostree
    writes it, or the a(stsss) the first Luma nightlies carried) or a
    tab-separated inventory into {name: (e, v, r)}.

    a(stsss) epochs are ignored as printed: `ostree show` byte-swaps uint64
    values on little-endian hosts, so epoch 1 prints as 72057594037927936; the
    epoch is recovered by swapping back."""
    packages = {}
    text = text.strip()
    if text.startswith("@a(") or text.startswith("[("):
        for name, epoch, version, release, _arch in re.findall(
                r"\('((?:[^'\\]|\\.)*)', (?:uint64 )?'?(\d+)'?, '((?:[^'\\]|\\.)*)', '((?:[^'\\]|\\.)*)', '((?:[^'\\]|\\.)*)'\)", text):
            value = int(epoch)
            if value >= 1 << 32:
                value = int.from_bytes(value.to_bytes(8, "little"), "big")
            packages[name] = (str(value), version, release)
    else:
        for line in text.splitlines():
            fields = line.split("\t")
            if len(fields) >= 5:
                packages[fields[0]] = (fields[1], fields[2], fields[3])
    if not packages:
        raise ValueError("the target package list is empty or unreadable")
    return packages


def booted(status):
    for deployment in status.get("deployments", []):
        if deployment.get("booted"):
            return deployment
    raise ValueError("rpm-ostree reports no booted deployment")


def plan(status, target):
    deployment = booted(status)
    result = {
        "origin": deployment.get("origin"),
        "booted_checksum": deployment.get("checksum"),
        "reset_overrides": False,
        "downgrades": [],
        "reapply_removals": [],
        "dropped_removals": [],
        "uninstall": [],
        "keep_layers": [],
        "notes": [],
    }

    replacements = deployment.get("requested-base-local-replacements", []) + \
        deployment.get("requested-base-replacements", [])
    removals = deployment.get("requested-base-removals", [])
    if replacements or removals:
        result["reset_overrides"] = True
    for item in replacements:
        parsed = parse_nevra(item if isinstance(item, str) else item[0])
        if not parsed:
            result["notes"].append(f"unparsed override {item!r}")
            continue
        name, evr, _ = parsed
        if name in target:
            if evr_compare(evr, target[name]) > 0:
                result["downgrades"].append({"package": name, "local": "-".join(evr[1:]), "release": "-".join(target[name][1:])})
        else:
            result["notes"].append(f"override {name} has no counterpart in the release; it is dropped")
    for name in removals:
        (result["reapply_removals"] if name in target else result["dropped_removals"]).append(name)

    for item in deployment.get("requested-packages", []):
        name = item
        (result["uninstall"] if name in target else result["keep_layers"]).append(name)
    for item in deployment.get("requested-local-packages", []):
        parsed = parse_nevra(item)
        if not parsed:
            result["notes"].append(f"unparsed local package {item!r}")
            continue
        name, evr, _ = parsed
        if name in target:
            result["uninstall"].append(item)
            if evr_compare(evr, target[name]) > 0:
                result["downgrades"].append({"package": name, "local": "-".join(evr[1:]), "release": "-".join(target[name][1:])})
        else:
            result["keep_layers"].append(item)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", help="rpm-ostree status --json output (default: run it)")
    parser.add_argument("--target-pkglist", required=True, help="file with the target commit's package list")
    args = parser.parse_args(argv)
    if args.status:
        status = json.load(open(args.status, encoding="utf-8"))
    else:
        status = json.loads(subprocess.check_output(["rpm-ostree", "status", "--json"], text=True))
    target = parse_pkglist(open(args.target_pkglist, encoding="utf-8").read())
    json.dump(plan(status, target), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
