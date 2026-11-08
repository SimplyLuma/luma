# SPDX-License-Identifier: Apache-2.0
"""LumaUI content: the changelog, one format for Luma and every app.

Most developers send plain text, and that reads as one simple bulleted list
(Depot 11.6). A line may lead with a short title in bold that ends with a full
stop. When lines carry a type they group under plain small headings in a fixed
order: New, Better, Fixed, Security (from Feature, Improvement, Fix, Security).
No coloured pills; lines wrap at about 68 characters. Used by Depot's What's
new and Luma's update card, Settings › About and release notes everywhere.

    Changelog(["Faster search", "Fixes a crash when the library is empty"])
    Changelog([ChangeLine("Set how fast mice and touchpads scroll.", title="Adjustable scroll speed", kind="Feature"),
               ChangeLine("The dock no longer flickers on wake", kind="Fix")])
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

__all__ = ["CHANGE_GROUPS", "ChangeLine", "Changelog", "group_changelog"]

#: (heading, the kinds that map to it), in the one fixed order.
CHANGE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("New", ("feature", "new")),
    ("Better", ("improvement", "better")),
    ("Fixed", ("fix", "fixed")),
    ("Security", ("security",)),
)
_HEADING = {kind: heading for heading, kinds in CHANGE_GROUPS for kind in kinds}


@dataclass(frozen=True)
class ChangeLine:
    """One line: its text, an optional short title, an optional kind (Feature/Improvement/Fix/Security)."""

    text: str
    title: str = ""
    kind: str = ""

    @classmethod
    def of(cls, line: object) -> "ChangeLine":
        """A ChangeLine from a string, a mapping ({type, title, text}) or a (type, icon, title, text) row."""
        if isinstance(line, ChangeLine):
            return line
        if isinstance(line, str):
            return cls(line)
        if isinstance(line, dict):
            return cls(str(line.get("text", "")), str(line.get("title", "") or ""),
                       str(line.get("type", line.get("kind", "")) or ""))
        if isinstance(line, (tuple, list)):
            if len(line) == 4:  # Luma's system notes: [type, icon, title, text]
                kind, _icon, title, text = line
                return cls(str(text), str(title or ""), str(kind or ""))
            if len(line) == 2:
                return cls(str(line[1]), "", str(line[0]))
        raise TypeError(f"a changelog line is text, a mapping or a row, not {line!r}")

    @property
    def heading(self) -> str:
        return _HEADING.get(self.kind.strip().lower(), "")


def group_changelog(lines: Iterable[object]) -> list[tuple[str, list[ChangeLine]]]:
    """The groups to draw: [("", lines)] for an untyped log; typed lines under New/Better/Fixed/Security.

    In a typed log, lines without a type lead under no heading. Empty lines are dropped; empty groups
    are never returned, so an empty log draws nothing.
    """
    parsed = [ChangeLine.of(line) for line in lines]
    parsed = [line for line in parsed if line.text.strip() or line.title.strip()]
    if not parsed:
        return []
    if not any(line.heading for line in parsed):
        return [("", parsed)]
    groups: list[tuple[str, list[ChangeLine]]] = []
    loose = [line for line in parsed if not line.heading]
    if loose:
        groups.append(("", loose))
    for heading, _kinds in CHANGE_GROUPS:
        members = [line for line in parsed if line.heading == heading]
        if members:
            groups.append((heading, members))
    return groups


def Changelog(lines: Sequence[object]):  # noqa: N802 - a widget constructor by name
    """The widget: small headings and bulleted lines, a bold lead title where a line has one."""
    from ._changelog_view import ChangelogView
    return ChangelogView(group_changelog(lines))
