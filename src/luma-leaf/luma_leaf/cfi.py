# SPDX-License-Identifier: Apache-2.0
"""EPUB CFI, read on the library's side.

A book's position is an EPUB Canonical Fragment Identifier. The page writes
it (with foliate-js's epubcfi.js, which follows the specification); the
library only has to read one back far enough to know which section it points
into and how many characters of that section come before it. That is enough
to derive chapter, percent and minutes left without laying the book out, so
none of those figures is ever stored and none can disagree with the page.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .epub import Epub, Node, document_element

_STEP = re.compile(r"/(\d+)(?:\[([^\]]*)\])?")
_OFFSET = re.compile(r":(\d+)")


@dataclass(frozen=True)
class Point:
    spine: int
    steps: tuple[int, ...]
    offset: int | None


def _unwrap(value: str) -> str:
    value = value.strip()
    if value.startswith("epubcfi(") and value.endswith(")"):
        value = value[8:-1]
    return value


def _steps(path: str) -> tuple[tuple[int, ...], int | None]:
    steps = tuple(int(match.group(1)) for match in _STEP.finditer(path))
    tail = path[path.rfind("/"):] if "/" in path else path
    offset = _OFFSET.search(tail)
    return steps, int(offset.group(1)) if offset else None


def parse(value: str) -> tuple[Point, Point | None]:
    """A point, or the start and end of a range."""
    body = _unwrap(value)
    parts = _split_range(body)
    if len(parts) == 3:
        parent, start, end = parts
        return _point(parent + start), _point(parent + end)
    return _point(body), None


def _split_range(body: str) -> list[str]:
    out, depth, current = [], 0, ""
    for character in body:
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
        if character == "," and depth == 0:
            out.append(current)
            current = ""
        else:
            current += character
    out.append(current)
    return out


def _point(body: str) -> Point:
    if "!" not in body:
        raise ValueError(f"Not a content CFI: {body}")
    package, document = body.split("!", 1)
    package_steps, _ = _steps(package)
    if len(package_steps) < 2:
        raise ValueError(f"No spine step in CFI: {body}")
    spine = package_steps[-1] // 2 - 1
    steps, offset = _steps(document)
    return Point(spine, steps, offset)


def make(spine: int, document_path: str) -> str:
    return f"epubcfi(/6/{(spine + 1) * 2}!{document_path})"


def character_offset(book: Epub, point: Point) -> int:
    """Characters of the section's text that come before this point."""
    if not 0 <= point.spine < len(book.sections):
        return 0
    text = book.text(point.spine)
    node: Node = document_element(book.tree(point.spine))
    for step in point.steps:
        if step % 2 == 0:
            elements = node.elements()
            index = step // 2 - 1
            if not 0 <= index < len(elements):
                break
            node = elements[index]
        else:
            # An odd step names the character data between two elements.
            slot = (step - 1) // 2
            seen = 0
            chosen = None
            for child in node.children:
                if child.tag is None:
                    if seen == slot:
                        chosen = child
                        break
                else:
                    seen += 1
            if chosen is None:
                break
            node = chosen
            break
    if node.tag is None:
        base = text.offsets.get(id(node), 0)
        return base + min(point.offset or 0, len(node.text))
    return _first_offset(node, text)


def _first_offset(node: Node, text) -> int:
    stack = [node]
    while stack:
        current = stack.pop(0)
        if current.tag is None and id(current) in text.offsets:
            return text.offsets[id(current)]
        if current.tag is not None:
            stack[0:0] = current.children
    return len(text.text)
