# SPDX-License-Identifier: Apache-2.0
"""RPM version ordering (rpmvercmp), so the updater can tell whether a package
this computer added is newer than the copy a new release ships in its base.

Pure Python and the same algorithm as rpm's rpmvercmp (rpmio/rpmvercmp.c),
including ``~`` (sorts before anything, even the end) and ``^`` (sorts after
the end but before any further segment). NEVRA parsing accepts the forms
rpm-ostree and libsolv print: name-[epoch:]version-release.arch."""

from __future__ import annotations

import re

_SEGMENT = re.compile(r"(\d+|[A-Za-z]+)")


def rpmvercmp(a: str, b: str) -> int:
    """-1, 0 or 1 as rpm orders the version (or release) strings ``a`` and ``b``."""
    if a == b:
        return 0
    i = j = 0
    while i < len(a) or j < len(b):
        # Skip separators (anything that is not alnum, ~ or ^).
        while i < len(a) and not (a[i].isalnum() or a[i] in "~^"):
            i += 1
        while j < len(b) and not (b[j].isalnum() or b[j] in "~^"):
            j += 1
        # Tilde sorts before everything, including the end of the string.
        if (i < len(a) and a[i] == "~") or (j < len(b) and b[j] == "~"):
            if not (i < len(a) and a[i] == "~"):
                return 1
            if not (j < len(b) and b[j] == "~"):
                return -1
            i, j = i + 1, j + 1
            continue
        # Caret sorts after the end but before any other segment.
        if (i < len(a) and a[i] == "^") or (j < len(b) and b[j] == "^"):
            if i >= len(a):
                return -1
            if j >= len(b):
                return 1
            if a[i] != "^":
                return 1
            if b[j] != "^":
                return -1
            i, j = i + 1, j + 1
            continue
        if i >= len(a) or j >= len(b):
            break
        ma, mb = _SEGMENT.match(a, i), _SEGMENT.match(b, j)
        sa, sb = ma.group(0), mb.group(0)
        numeric = sa[0].isdigit()
        if numeric != sb[0].isdigit():
            # A numeric segment is newer than an alphabetic one.
            return 1 if numeric else -1
        if numeric:
            sa, sb = sa.lstrip("0") or "0", sb.lstrip("0") or "0"
            if len(sa) != len(sb):
                return 1 if len(sa) > len(sb) else -1
        if sa != sb:
            return 1 if sa > sb else -1
        i, j = ma.end(), mb.end()
    if i >= len(a) and j >= len(b):
        return 0
    return -1 if i >= len(a) else 1


def split_nevra(nevra: str) -> tuple[str, int, str, str, str] | None:
    """(name, epoch, version, release, arch) of name-[epoch:]version-release.arch, or None."""
    parts = nevra.rsplit("-", 2)
    if len(parts) != 3:
        return None
    name, version, release_arch = parts
    epoch = 0
    if ":" in version:
        head, _, version = version.partition(":")
        if not head.isdigit():
            return None
        epoch = int(head)
    release, dot, arch = release_arch.rpartition(".")
    if not dot:
        release, arch = release_arch, ""
    if not name or not version or not release:
        return None
    return name, epoch, version, release, arch


def compare_nevra(a: str, b: str) -> int | None:
    """Order two NEVRAs of the same package by epoch, version, release; None if unreadable."""
    pa, pb = split_nevra(a), split_nevra(b)
    if pa is None or pb is None:
        return None
    if pa[1] != pb[1]:
        return 1 if pa[1] > pb[1] else -1
    return rpmvercmp(pa[2], pb[2]) or rpmvercmp(pa[3], pb[3])
