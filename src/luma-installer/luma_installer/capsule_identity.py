"""One SELinux category pair per installed application, allocated and kept.

A capsule's processes and its private files carry an MCS level of two
categories; the kernel keeps apart anything whose categories differ. The pair
used to be derived from a hash of the package, so two applications could land
on the same pair and read each other's private files. A pair is now allocated
once, checked against every other installed application, and written to the
application's record (`mcs_level`).

A record written before this keeps the pair its hash gave it when no other
application holds it: its private files are already labelled with it, and
nothing needs relabelling. When two older records share a pair, the first one
opened keeps it and the other is given a free pair; Podman relabels that
application's private files the next time it starts (the `Z` volume option).
"""
from __future__ import annotations

import fcntl
import hashlib
import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from .desktop import iter_records, read_record, records_root, safe_id, write_record
from .errors import InstallerError

CATEGORIES = 1024
PAIRS = CATEGORIES * (CATEGORIES - 1) // 2
_LEVEL = re.compile(r"s0:c(\d{1,4}),c(\d{1,4})")


def parse_level(value: object) -> tuple[int, int] | None:
    """The two categories of a level this module wrote, or None."""
    match = _LEVEL.fullmatch(value) if isinstance(value, str) else None
    if not match:
        return None
    low, high = int(match.group(1)), int(match.group(2))
    return (low, high) if low < high < CATEGORIES else None


def format_level(pair: tuple[int, int]) -> str:
    return f"s0:c{pair[0]},c{pair[1]}"


def legacy_pair(identity: str) -> tuple[int, int]:
    """The pair releases before allocation derived from the package's hash."""
    digest = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big")
    first, second = digest % 1024, (digest >> 10) % 1023
    second += second >= first
    low, high = sorted((first, second))
    return low, high


def pair_index(pair: tuple[int, int]) -> int:
    """Position of a pair in the fixed order (0,1), (0,2), ... (1022,1023)."""
    low, high = pair
    return low * (2 * CATEGORIES - low - 1) // 2 + (high - low - 1)


def index_pair(index: int) -> tuple[int, int]:
    low = 0
    while index >= CATEGORIES - low - 1:
        index -= CATEGORIES - low - 1
        low += 1
    return low, low + 1 + index


def choose(preferred: tuple[int, int], taken: Iterable[tuple[int, int]]) -> tuple[int, int]:
    """`preferred` when it is free, else the next free pair after it."""
    held = set(taken)
    if len(held) >= PAIRS:
        raise InstallerError("Every application category pair on this device is in use.")
    index = pair_index(preferred)
    while index_pair(index) in held:
        index = (index + 1) % PAIRS
    return index_pair(index)


@contextmanager
def _allocation_lock() -> Iterator[None]:
    root = records_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(root / ".allocation.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def ensure_level(record: dict[str, object]) -> dict[str, object]:
    """The record with its allocated level, allocating and saving it if needed."""
    if parse_level(record.get("mcs_level")):
        return record
    application_id = safe_id(str(record.get("application_id", "")))
    with _allocation_lock():
        try:
            # Another launch of the same application may have allocated since.
            current: dict[str, object] | None = read_record(application_id)
        except InstallerError:
            current = None
        if current and parse_level(current.get("mcs_level")):
            return {**record, "mcs_level": current["mcs_level"]}
        taken = [pair for other in iter_records() if other.get("application_id") != application_id
                 for pair in [parse_level(other.get("mcs_level"))] if pair]
        chosen = format_level(choose(legacy_pair(str(record.get("sha256") or application_id)), taken))
        if current is not None:
            write_record(application_id, {**current, "mcs_level": chosen})
    return {**record, "mcs_level": chosen}


def level(record: dict[str, object]) -> str:
    """The level to run this application at; allocated on first use."""
    return str(ensure_level(record)["mcs_level"])

