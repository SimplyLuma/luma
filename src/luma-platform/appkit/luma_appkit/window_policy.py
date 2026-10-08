# SPDX-License-Identifier: Apache-2.0
"""How a Luma window opens, and what it may never give up.

Two separate promises live here, and both are the kit's because every Luma
window is an ``AppWindow``.

**A window can always be closed.** A narrow layout is for a narrow surface: it
folds a sidebar away, it swaps a toolbar for a bottom bar. On a phone the shell
owns the surface chrome, so an application's own title row would be a second
one and is correctly hidden. On a desktop the title row *is* the close control,
and a window that hides it because it happens to be narrow leaves a person with
no way to close, move or resize it. That state is forbidden, and the forbidding
happens once, here, rather than in each application's breakpoints.

**Every window opens at a familiar size.** Applications used to name their own
opening pixels: 408x372 for one, 1440x900 for another, so a desk filled with
windows of unrelated sizes and half of them needed resizing on sight. The rule
below gives every application the same opening height — a fraction of the
monitor's work area — and a width from its shape, so windows look like a set.
Nothing opens larger than a fraction of the work area, nothing opens below what
the application says it needs, and on a desktop nothing opens narrow enough to
trip its own narrow layout.

The functions are pure and take plain numbers: the policy can be checked for
every application on any monitor without a display. ``widgets.AppWindow``
applies them, and ADR-042 records which component owns which part.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

#: Width divided by height for each shape an application can have. One opening
#: height and three widths is what makes a desk of windows look related: a
#: player is wider than a dialler, both are the same height.
SHAPES: dict[str, float] = {
    "compact": 0.82,
    "regular": 1.42,
    "wide": 1.62,
}

#: A window opens this much of the work area's height, whatever its shape.
HEIGHT_FRACTION = 0.70

#: and never wider than this much of the work area, so a wide application on an
#: ultrawide monitor does not open across the whole desk.
WIDTH_FRACTION = 0.62

#: Neither dimension reaches the edge of the work area. A window that opens
#: flush with the work area reads as maximized without being maximized.
EDGE_FRACTION = 0.94

#: A window opens this much clear of its own narrow layout, never on the fold.
#: At exactly the fold width, anything that takes a few pixels from the content
#: (a fractional scale rounding down, a frame drawn by a compositor-less X11
#: session) folds it into its phone layout the moment it appears.
FOLD_CLEARANCE = 16

#: An application that asks for less than this is a utility surface — a note, an
#: assistant, a single column of controls — and keeps the size it asked for. It
#: is a size, not a category: growing such a window to a document's proportions
#: would be worse than leaving it alone.
UTILITY_SIZE = (640, 480)

#: Logical pixels the shell keeps for itself on the edge that owns the Shelf.
#: Apps cannot ask a Wayland compositor for the work area, and Mutter's
#: work-area constraint (patch 0014) corrects anything this estimate gets
#: wrong, so an honest reserve is enough here.
SHELL_RESERVE = 96

_CONDITION = re.compile(r"max-width:\s*(\d+)(px)?", re.IGNORECASE)


@dataclass(frozen=True)
class Screen:
    """A monitor as GTK sees it: logical pixels, with the Shelf's edge named.

    ``width`` and ``height`` are logical, which is how GTK sizes windows, so a
    1920x1200 panel at 1.25 is a 1536x960 screen here and needs no scale of its
    own.
    """

    width: int
    height: int
    shelf_edge: str = "bottom"

    @property
    def work_area(self) -> tuple[int, int]:
        """What is left for windows once the Shelf has its edge."""
        width, height = self.width, self.height
        if self.shelf_edge in {"left", "right"}:
            width = max(1, width - SHELL_RESERVE)
        else:
            height = max(1, height - SHELL_RESERVE)
        return width, height


def narrow_width(conditions: object) -> int:
    """The width at which an application stops being wide enough for itself.

    Takes anything iterable of breakpoint condition strings — libadwaita's
    ``AdwBreakpointCondition.to_string()`` reads ``max-width: 959px`` — and
    gives back the first width at which no breakpoint applies, so a window
    opened at it keeps its full layout. Zero when nothing narrow is declared.
    """
    widest = 0
    for condition in conditions or ():
        for match in _CONDITION.finditer(str(condition)):
            widest = max(widest, int(match.group(1)) + 1)
    return widest


def shape(default_width: int, default_height: int, declared: str = "auto") -> str:
    """Which shape an application has.

    ``declared`` wins when an application says. Otherwise the shape comes from
    the proportions the application already asked for, so a player stays wide
    and a clock stays square without every application being edited.
    """
    if declared in SHAPES or declared == "utility":
        return declared
    if declared not in {"", "auto"}:
        raise ValueError(f"unknown window shape: {declared!r}")
    if default_width <= 0 or default_height <= 0:
        return "regular"
    if default_width < UTILITY_SIZE[0] or default_height < UTILITY_SIZE[1]:
        return "utility"
    proportion = default_width / default_height
    if proportion >= 1.50:
        return "wide"
    if proportion <= 1.05:
        return "compact"
    return "regular"


def opening_size(
    *,
    screen: Screen,
    default: tuple[int, int],
    minimum: tuple[int, int],
    narrow: int = 0,
    declared: str = "auto",
    desktop: bool = True,
) -> tuple[int, int]:
    """The size a window opens at when nothing is remembered for it.

    ``default`` is what the application asked for, which decides its shape and
    is kept as-is for a utility surface. ``minimum`` is what it says it needs.
    ``narrow`` is the width below which its own narrow layout applies, from
    :func:`narrow_width`; on a desktop a window opens at least that wide, so it
    never opens folded on a desk. The result is never past
    :data:`EDGE_FRACTION` of the work area and never below ``minimum``.
    """
    work_width, work_height = screen.work_area
    kind = shape(default[0], default[1], declared)

    if kind == "utility":
        width, height = max(1, default[0]), max(1, default[1])
    else:
        # One height for every window on this monitor, whatever its shape, is
        # what makes a desk of Luma windows look like a set. Where the width
        # cap bites — a wide application on a narrow panel — the window becomes
        # squarer rather than shorter, because a row of windows of differing
        # heights is the thing being fixed.
        height = round(work_height * HEIGHT_FRACTION)
        width = min(round(height * SHAPES[kind]), round(work_width * WIDTH_FRACTION))

    # A window opens at least as wide as its own full layout needs, even where
    # that is wider than the cap: a folded window on a desk is the bug. The
    # height does not follow, so every window on a monitor still opens at the
    # same height and only the widths differ.
    width = max(width, minimum[0], narrow + FOLD_CLEARANCE if desktop and narrow else 0)
    height = max(height, minimum[1])

    # The work area wins over everything except the application's own minimum:
    # a window that cannot fit is better narrow than off screen, and the title
    # row it keeps is what makes a narrow window closable.
    width = min(width, max(minimum[0], round(work_width * EDGE_FRACTION)))
    height = min(height, max(minimum[1], round(work_height * EDGE_FRACTION)))
    return width, height


def close_affordance_required(*, desktop: bool, decorated: bool) -> bool:
    """Whether this window must keep its title row on screen.

    A desktop window that draws its own frame has no other close control: the
    title row is it. A handheld surface is framed by the shell, and a window
    that has deliberately given up its frame (a sticky note carrying its own
    controls) answers for its own close control.
    """
    return bool(desktop and decorated)
