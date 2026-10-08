# SPDX-License-Identifier: Apache-2.0
"""The policy engine (brief §7): what may run, decided in code, not by the model.

Every tool has a tier registered here. A tool that isn't registered, or whose
tier isn't enabled in this phase, never runs, whatever the model asked for.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

ANSWER, PERSONAL_SETTINGS, READ_PERSONAL, ACT_ON_BEHALF, CHANGE_FILES, CHANGE_SYSTEM = range(6)
TIER_NAMES = {0: "Answer", 1: "Personal settings", 2: "Read personal data", 3: "Act on your behalf",
              4: "Change your files and design", 5: "Change the system"}

TOOL_TIERS = {
    "now": ANSWER, "calculate": ANSWER, "convert": ANSWER, "weather": ANSWER, "web_search": ANSWER,
    "fetch_page": ANSWER, "define": ANSWER, "find_app": ANSWER, "list_display_modes": ANSWER,
    "set_dock_position": PERSONAL_SETTINGS, "set_theme": PERSONAL_SETTINGS, "set_accent": PERSONAL_SETTINGS,
    "set_interface_scale": PERSONAL_SETTINGS, "set_night_light": PERSONAL_SETTINGS, "set_brightness": PERSONAL_SETTINGS,
    "set_wallpaper": PERSONAL_SETTINGS, "set_refresh_rate": PERSONAL_SETTINGS, "open_app": PERSONAL_SETTINGS,
    "media_status": ANSWER, "find_setting": ANSWER,
    "set_timezone": PERSONAL_SETTINGS, "set_volume": PERSONAL_SETTINGS, "set_do_not_disturb": PERSONAL_SETTINGS,
    "set_wifi": PERSONAL_SETTINGS, "set_bluetooth": PERSONAL_SETTINGS, "set_tiling": PERSONAL_SETTINGS,
    "media_control": PERSONAL_SETTINGS, "change_setting": PERSONAL_SETTINGS,
}
# Changes that reach past this person's own desktop -- the whole machine's
# clock, its connections -- are consequential (ADR-025): Ari asks first unless
# the person chose "Don't ask".
CONSEQUENTIAL = {"set_timezone", "set_wifi", "set_bluetooth"}
APPROVAL_MODES = ("system", "all", "never")
# Phase 1 runs Tiers 0 and 1. The rest exist so the refusal is honest.
ENABLED_TIERS = {ANSWER, PERSONAL_SETTINGS}
MAX_CALLS_PER_TURN = 6
MAX_CALLS_PER_MINUTE = 30


@dataclass(frozen=True)
class Decision:
    allowed: bool
    tier: int
    reason: str = ""


class Policy:
    def __init__(self, *, ask_before_settings: bool = False) -> None:
        self.ask_before_settings = ask_before_settings
        self.enabled = set(ENABLED_TIERS)  # the person can turn tiers off in Ari's settings
        self.approval_mode = "system"
        self._recent: deque[float] = deque()

    def needs_approval(self, tool: str) -> bool:
        if self.tier(tool) == ANSWER or self.approval_mode == "never":
            return False
        return self.approval_mode == "all" or tool in CONSEQUENTIAL

    def tier(self, tool: str) -> int:
        return TOOL_TIERS.get(tool, CHANGE_SYSTEM)

    def check(self, tool: str, *, calls_this_turn: int, after_untrusted: bool) -> Decision:
        tier = self.tier(tool)
        now = time.monotonic()
        while self._recent and now - self._recent[0] > 60:
            self._recent.popleft()
        if tool not in TOOL_TIERS:
            return Decision(False, tier, "That isn't something I can do on this machine yet.")
        if tier not in ENABLED_TIERS:
            return Decision(False, tier, f"{TIER_NAMES[tier]} isn't available yet.")
        if tier not in self.enabled:
            return Decision(False, tier, f"{TIER_NAMES[tier]} is turned off in Ari's settings. "
                                         "Tell the person they can turn it back on there.")
        if calls_this_turn >= MAX_CALLS_PER_TURN:
            return Decision(False, tier, "I stopped: that was too many steps for one request.")
        if len(self._recent) >= MAX_CALLS_PER_MINUTE:
            return Decision(False, tier, "I'm pausing: too many actions in the last minute.")
        # Web pages and other fetched text can inform an answer. They can
        # never be the reason a change happens (§7.2 rule 3). Tier 1 changes
        # after reading untrusted content are allowed only because the person
        # asked in their own words this turn; the agent checks that request
        # names the change before calling this.
        self._recent.append(now)
        return Decision(True, tier)
