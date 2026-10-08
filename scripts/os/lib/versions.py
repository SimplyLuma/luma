#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Luma OS version ordering (Semantic Versioning 2.0.0 precedence).

  versions.py greater A B     exit 0 when A > B
  versions.py sort V...       print the versions in ascending order

1.0.0-nightly.20261001.1 < 1.0.0-nightly.20261002.1, 1.0.0-beta.1 < 1.0.0-beta.2
< 1.0.0 < 1.0.1. Comparing versions of different channels is allowed but not
meaningful for promotion, which compares within one channel only.
"""

import functools
import re
import sys

PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


def parse(version):
    match = PATTERN.match(version)
    if not match:
        raise ValueError(f"not a Luma OS version: {version}")
    core = tuple(int(part) for part in match.group(1, 2, 3))
    pre = match.group(4)
    return core, (pre.split(".") if pre else None)


def compare(a, b):
    (core_a, pre_a), (core_b, pre_b) = parse(a), parse(b)
    if core_a != core_b:
        return -1 if core_a < core_b else 1
    if pre_a is None or pre_b is None:
        return 0 if pre_a == pre_b else (1 if pre_a is None else -1)
    for x, y in zip(pre_a, pre_b):
        if x == y:
            continue
        if x.isdigit() and y.isdigit():
            return -1 if int(x) < int(y) else 1
        if x.isdigit() != y.isdigit():
            return -1 if x.isdigit() else 1
        return -1 if x < y else 1
    return (len(pre_a) > len(pre_b)) - (len(pre_a) < len(pre_b))


def main(argv):
    if len(argv) == 4 and argv[1] == "greater":
        return 0 if compare(argv[2], argv[3]) > 0 else 1
    if len(argv) >= 2 and argv[1] == "sort":
        print("\n".join(sorted(argv[2:], key=functools.cmp_to_key(compare))))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
