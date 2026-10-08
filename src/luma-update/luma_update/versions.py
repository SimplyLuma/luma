# SPDX-License-Identifier: Apache-2.0
"""Luma OS version strings (ADR-030 section 3) and their order.

Versions are Semantic Versioning 2.0 without build metadata: ``1.0.0``,
``1.0.0-beta.3``, ``1.0.1-nightly.20261010.2``. Precedence is SemVer's, so a
release outranks its own pre-releases and pre-release identifiers compare
numerically when numeric and lexically otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import total_ordering
import re

__all__ = ("Version", "InvalidVersion", "parse")

_PATTERN = re.compile(
    r"(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})"
    r"(?:-((?:0|[1-9]\d{0,8}|\d*[A-Za-z-][0-9A-Za-z-]{0,31})"
    r"(?:\.(?:0|[1-9]\d{0,8}|\d*[A-Za-z-][0-9A-Za-z-]{0,31})){0,7}))?\Z")


class InvalidVersion(ValueError):
    pass


@total_ordering
@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple[int | str, ...] = ()
    text: str = ""

    def _key(self):
        # A version without pre-release sorts after every pre-release of it.
        pre = tuple((0, part, "") if isinstance(part, int) else (1, 0, part) for part in self.prerelease)
        return (self.major, self.minor, self.patch, 0 if self.prerelease else 1, pre)

    def __lt__(self, other: "Version") -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()

    def __eq__(self, other) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def __str__(self) -> str:
        return self.text


def parse(text: str) -> Version:
    if not isinstance(text, str):
        raise InvalidVersion("a version is a string")
    match = _PATTERN.match(text)
    if not match:
        raise InvalidVersion(f"not a Luma version: {text[:64]!r}")
    pre: tuple[int | str, ...] = ()
    if match.group(4):
        pre = tuple(int(part) if part.isdigit() else part for part in match.group(4).split("."))
    return Version(int(match.group(1)), int(match.group(2)), int(match.group(3)), pre, text)
