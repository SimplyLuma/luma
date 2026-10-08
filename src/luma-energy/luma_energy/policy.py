# SPDX-License-Identifier: MPL-2.0
"""What each attention state costs an application, and what may never be touched.

The ladder is deliberately shallow and its rungs are deliberately mild. A
laptop that saves a watt and drops a frame in the window the person is reading
has made a bad trade, and they will turn the whole thing off -- at which point
it saves nothing at all. So the foreground is never touched by anything here,
and the strongest measure short of freezing is a scheduler *hint* rather than a
quota.
"""
from __future__ import annotations

from dataclasses import dataclass

FOCUSED = "focused"
VISIBLE = "visible"
OCCLUDED = "occluded"
HIDDEN = "hidden"
WORKING = "working"

#: Most awake first.
ORDER = (FOCUSED, VISIBLE, OCCLUDED, HIDDEN)


@dataclass(frozen=True)
class Rung:
    """What a state costs.

    ``None`` means "do not set this at all", which is not the same as setting
    it to its default: uresourced raises the focused application's weight, and
    writing a default over the top of that would be Luma fighting the desktop
    for the same knob.
    """

    cpu_weight: int | None = None
    io_weight: int | None = None
    #: Percentage of a CPU's capacity this cgroup is told it needs. A hint to
    #: the governor, not a quota: the work still runs, at a cheaper operating
    #: point. This is Windows' EcoQoS lesson, and it cannot make an application
    #: miss a deadline it never told us about.
    uclamp_max: int | None = None
    #: Seconds in the state before the rung applies. Nothing is done to an
    #: application the instant a window moves over it; the person may be
    #: dragging it back.
    after_seconds: int = 0


#: On battery. The foreground is absent from this table on purpose.
ON_BATTERY = {
    VISIBLE: Rung(),
    OCCLUDED: Rung(cpu_weight=40, io_weight=60, uclamp_max=60, after_seconds=10),
    HIDDEN: Rung(cpu_weight=20, io_weight=30, uclamp_max=40, after_seconds=30),
}

#: On mains. Weights still order the queue when something is competing, which
#: costs an idle machine nothing, but nothing is hinted down and nothing is
#: ever frozen: there is no battery to save.
ON_MAINS = {
    VISIBLE: Rung(),
    OCCLUDED: Rung(cpu_weight=60, io_weight=80, after_seconds=10),
    HIDDEN: Rung(cpu_weight=40, io_weight=60, after_seconds=30),
}

#: Slices whose contents are never touched, whatever state their windows are
#: in. The session's own services, the Luma agents that exist precisely so that
#: nothing is missed, and anything outside the person's own manager.
PROTECTED_SLICES = (
    "/session.slice",
    "/luma-background-essential.slice",
    "/init.scope",
    "/system.slice",
)

#: Names that are never limited even inside an ordinary application slice.
#: These either draw the desktop, carry its sound, or are how the person types.
PROTECTED_NAMES = (
    "gnome-shell",
    "gnome-session",
    "xdg-desktop-portal",
    "pipewire",
    "wireplumber",
    "pulseaudio",
    "ibus-",
    "fcitx",
    "gsd-",
    "gdm",
    "luma-",
)


def rung_for(state: str, on_battery: bool) -> Rung | None:
    """What to apply, or None to leave the application entirely alone."""
    if state in (FOCUSED, WORKING):
        return None
    return (ON_BATTERY if on_battery else ON_MAINS).get(state)


def protected(cgroup: str, unit: str = "") -> bool:
    """Is this cgroup one Luma must not touch?"""
    if not cgroup or not cgroup.startswith("/"):
        return True
    for slice_name in PROTECTED_SLICES:
        if slice_name in cgroup:
            return True
    haystack = f"{cgroup} {unit}"
    return any(name in haystack for name in PROTECTED_NAMES)


def most_awake(states: list[str]) -> str:
    """An application is as awake as its most awake window."""
    for state in ORDER:
        if state in states:
            return state
    return HIDDEN
