#!/usr/bin/python3
"""Every Luma window opens inside the shared rule, on a desk and on a laptop.

This reads the applications as source — every `AppWindow` subclass in the
tree, the size and minimum it declares, and the widths at which its own
breakpoints fold its layout away — and runs the kit's opening-size rule over
all of them on two reference monitors. No display, no packages: a window that
would open folded into its narrow layout, past the edge of the work area, or
below what it says it needs fails here, at the source, which is where the
Phone and Messages "no way to close it" bug came from (Phone asked for 860px
with a 959px narrow breakpoint).

It also checks the other half of ADR-042 statically: nothing outside the kit's
reach may hide a title row, because the kit is what guarantees a desktop window
keeps its close control.
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/luma-platform/appkit"))

from luma_appkit import window_policy  # noqa: E402

#: A 1x ultrawide on a desk, and a 1920x1200 laptop panel at 1.25 — which GTK
#: sizes windows on as 1536x960 logical pixels, so that is what a screen is
#: here. Both with the Shelf on its default edge.
SCREENS = {
    "ultrawide 3440x1440 at 1x": window_policy.Screen(3440, 1440),
    "laptop 1920x1200 at 1.25": window_policy.Screen(1536, 960),
}

_MAX_WIDTH = re.compile(r"max-width:\s*(\d+)px")
_PLACEHOLDER = re.compile(r"max-width:\s*\{(\w+)\}px")


@dataclass(frozen=True)
class Declared:
    """What one window class says about itself."""

    where: str
    default: tuple[int, int]
    minimum: tuple[int, int]
    size_class: str
    narrow: int


def _ints(node: ast.AST | None, constants: dict[str, int] | None = None) -> list[int]:
    """Every integer a keyword value can be, including a conditional one."""
    constants = constants or {}
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return [node.value]
    if isinstance(node, ast.Name) and node.id in constants:
        return [constants[node.id]]
    if isinstance(node, ast.IfExp):
        return _ints(node.body, constants) + _ints(node.orelse, constants)
    return []


def _module_constants(tree: ast.Module) -> dict[str, int]:
    constants: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, int):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = node.value.value
    return constants


def _narrow_widths(source: str, constants: dict[str, int]) -> tuple[int, int]:
    """The widest breakpoint in a module, and how many could not be read."""
    widths = [int(value) for value in _MAX_WIDTH.findall(source)]
    unread = 0
    for name in _PLACEHOLDER.findall(source):
        if name in constants:
            widths.append(constants[name])
        else:
            unread += 1
    # A breakpoint applies at or below its width, so a window keeps its whole
    # layout at one pixel more.
    return (max(widths) + 1 if widths else 0), unread


def _declared(path: Path) -> tuple[list[Declared], int]:
    source = path.read_text(encoding="utf-8")
    if "AppWindow" not in source:
        return [], 0
    tree = ast.parse(source)
    constants = _module_constants(tree)
    narrow, unread = _narrow_widths(source, constants)
    found: list[Declared] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(isinstance(b, ast.Name) and b.id == "AppWindow" for b in node.bases):
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            function = call.func
            if not (isinstance(function, ast.Attribute) and function.attr == "__init__"):
                continue
            keywords = {k.arg: k.value for k in call.keywords if k.arg}
            if "default_width" not in keywords:
                continue
            widths = _ints(keywords.get("default_width"), constants) or [820]
            heights = _ints(keywords.get("default_height"), constants) or [560]
            size_class = "auto"
            declared_class = keywords.get("size_class")
            if isinstance(declared_class, ast.Constant):
                size_class = str(declared_class.value)
            declared_narrow = max(_ints(keywords.get("narrow_width"), constants) or [0])
            for width in widths:
                for height in heights:
                    found.append(
                        Declared(
                            where=f"{path.relative_to(ROOT)}:{node.name}",
                            default=(width, height),
                            minimum=(
                                min(_ints(keywords.get("minimum_width"), constants) or [460]),
                                min(_ints(keywords.get("minimum_height"), constants) or [380]),
                            ),
                            size_class=size_class,
                            narrow=max(narrow, declared_narrow),
                        )
                    )
    # A window that says where its full layout begins covers the breakpoints
    # this scan could not read out of an f-string.
    if found and all(window.narrow > narrow for window in found):
        unread = 0
    return found, unread


def _windows() -> tuple[list[Declared], int]:
    windows: list[Declared] = []
    unread = 0
    for path in sorted((ROOT / "src").rglob("*.py")):
        if "/tests/" in str(path) or "/.codex-tmp/" in str(path):
            continue
        found, module_unread = _declared(path)
        windows.extend(found)
        unread += module_unread if found else 0
    return windows, unread


class EveryWindowOpensInsideTheRule(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.windows, cls.unread = _windows()

    def test_the_tree_still_has_its_windows(self) -> None:
        self.assertGreaterEqual(
            len({window.where for window in self.windows}),
            25,
            "the scan stopped finding Luma windows; it is no longer evidence",
        )

    def test_nothing_opens_folded_into_its_own_narrow_layout(self) -> None:
        for name, screen in SCREENS.items():
            work_width, _ = screen.work_area
            for window in self.windows:
                width, _height = window_policy.opening_size(
                    screen=screen,
                    default=window.default,
                    minimum=window.minimum,
                    narrow=window.narrow,
                    declared=window.size_class,
                )
                if not window.narrow:
                    continue
                if window.narrow > round(work_width * window_policy.EDGE_FRACTION):
                    # The narrow layout is wider than this screen: the window is
                    # narrow because the monitor is, and the kit's close
                    # affordance guarantee is what keeps it usable.
                    continue
                self.assertGreaterEqual(
                    width,
                    window.narrow,
                    f"{window.where} opens at {width}px on {name}, inside its own "
                    f"narrow layout below {window.narrow}px",
                )

    def test_nothing_opens_past_the_work_area_or_below_its_minimum(self) -> None:
        for name, screen in SCREENS.items():
            work_width, work_height = screen.work_area
            for window in self.windows:
                size = window_policy.opening_size(
                    screen=screen,
                    default=window.default,
                    minimum=window.minimum,
                    narrow=window.narrow,
                    declared=window.size_class,
                )
                self.assertLessEqual(
                    size[0], max(window.minimum[0], work_width), f"{window.where} on {name}"
                )
                self.assertLessEqual(
                    size[1], max(window.minimum[1], work_height), f"{window.where} on {name}"
                )
                self.assertGreaterEqual(size[0], window.minimum[0], f"{window.where} on {name}")
                self.assertGreaterEqual(size[1], window.minimum[1], f"{window.where} on {name}")

    def test_windows_open_at_one_height_and_a_few_widths(self) -> None:
        """A desk of Luma windows looks like a set, not like fifteen apps."""
        for name, screen in SCREENS.items():
            heights: dict[int, list[str]] = {}
            widths: set[int] = set()
            for window in self.windows:
                if window_policy.shape(*window.default, window.size_class) == "utility":
                    continue
                width, height = window_policy.opening_size(
                    screen=screen,
                    default=window.default,
                    minimum=window.minimum,
                    narrow=window.narrow,
                    declared=window.size_class,
                )
                heights.setdefault(height, []).append(window.where)
                if not window.narrow:
                    widths.add(width)
            self.assertEqual(
                len(heights), 1, f"Luma windows open at {len(heights)} heights on {name}: {heights}"
            )
            # Every window that is not pushed wider by its own full layout opens
            # at one of the shape widths.
            self.assertLessEqual(
                len(widths), len(window_policy.SHAPES), f"{name}: {sorted(widths)}"
            )

    def test_no_window_is_giant(self) -> None:
        for name, screen in SCREENS.items():
            work_width, work_height = screen.work_area
            for window in self.windows:
                width, height = window_policy.opening_size(
                    screen=screen,
                    default=window.default,
                    minimum=window.minimum,
                    narrow=window.narrow,
                    declared=window.size_class,
                )
                if window.narrow and width == window.narrow + window_policy.FOLD_CLEARANCE:
                    continue
                self.assertLessEqual(
                    width / work_width,
                    window_policy.EDGE_FRACTION,
                    f"{window.where} fills {width}/{work_width} of {name}",
                )
                self.assertLessEqual(height / work_height, window_policy.EDGE_FRACTION)

    def test_every_breakpoint_width_was_read(self) -> None:
        self.assertEqual(
            self.unread,
            0,
            "a breakpoint width could not be read from source, so the matrix is "
            "not covering it; give the window an explicit narrow_width=",
        )


class OnlyTheKitMayHideATitleRow(unittest.TestCase):
    """The close affordance is guaranteed in one place, so it must be reachable.

    Every window that hides its title row has to be an `AppWindow`, because
    `AppWindow` is what puts the row back on a desktop. A plain GTK or
    libadwaita window that hides a header bar of its own would escape the
    guarantee.
    """

    def test_title_rows_are_only_hidden_inside_appkit_windows(self) -> None:
        offenders: list[str] = []
        for path in sorted((ROOT / "src").rglob("*.py")):
            if "/tests/" in str(path) or "/.codex-tmp/" in str(path):
                continue
            source = path.read_text(encoding="utf-8")
            hides = "title_bar" in source and (
                "title_bar.set_visible(False)" in source
                or re.search(r'title_bar,\s*"visible",\s*False', source)
            )
            if not hides:
                continue
            if "AppWindow" not in source:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(
            offenders, [], "a title row is hidden outside an appkit window"
        )


if __name__ == "__main__":
    unittest.main()
