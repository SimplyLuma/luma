# SPDX-License-Identifier: MPL-2.0
"""What each category of agent is for, what it may use, and what people read.

The table is ADR-033's. A category is chosen by the app, but everything that
follows from it -- the limits, whether Power Saver pauses it, the sentence in
the dock and Settings -- is decided here, so an app cannot describe itself
one way and behave another.
"""

from __future__ import annotations

import gettext
from dataclasses import dataclass

_ = gettext.translation("luma-background", fallback=True).gettext

MIB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class Category:
    id: str
    memory_high: int
    memory_max: int
    pauses_in_power_saver: bool
    #: Schedules for these categories fire to the second; for the rest systemd
    #: may batch them within a minute, which is what lets wake-ups coincide.
    schedule_accuracy_seconds: int
    _explanation: str
    _consequence: str

    @property
    def essential_slice(self) -> bool:
        return not self.pauses_in_power_saver

    @property
    def slice(self) -> str:
        return (
            "luma-background-essential.slice"
            if self.essential_slice
            else "luma-background-deferrable.slice"
        )

    def explanation(self, app_name: str) -> str:
        return self._explanation.format(app=app_name)

    def consequence(self, app_name: str) -> str:
        return self._consequence.format(app=app_name)


CATEGORIES: dict[str, Category] = {
    category.id: category
    for category in (
        Category(
            "communication", 64 * MIB, 128 * MIB, False, 1,
            _("Get messages and calls when {app} is closed"),
            _("You won’t get messages or calls while {app} is closed"),
        ),
        Category(
            "calendar", 64 * MIB, 128 * MIB, False, 1,
            _("Get event reminders when {app} is closed"),
            _("You won’t get event reminders while {app} is closed"),
        ),
        Category(
            "alarms", 64 * MIB, 128 * MIB, False, 1,
            _("Alarms and timers go off when {app} is closed"),
            _("Alarms and timers won’t go off while {app} is closed"),
        ),
        Category(
            "mail", 128 * MIB, 256 * MIB, True, 60,
            _("Get new mail when {app} is closed"),
            _("You won’t get new mail while {app} is closed"),
        ),
        Category(
            "sync", 64 * MIB, 128 * MIB, True, 60,
            _("Keep {app} up to date when it is closed"),
            _("{app} won’t stay up to date while it is closed"),
        ),
        Category(
            "widget-data", 64 * MIB, 128 * MIB, True, 60,
            _("Keep {app}’s live information current when it is closed"),
            _("{app}’s live information won’t update while it is closed"),
        ),
        Category(
            "other", 64 * MIB, 128 * MIB, True, 60,
            _("Let {app} work when it is closed"),
            _("{app} won’t work while it is closed"),
        ),
    )
}

WAKE_EVENTS = ("login", "network", "resume", "schedule")
#: Wake reasons delivered to agents. "request" is not declarable: it is what
#: an app asking for background activity, or a person pressing a switch, is.
WAKE_REASONS = WAKE_EVENTS + ("request",)


def category(name: str) -> Category:
    return CATEGORIES[name]
