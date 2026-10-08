# SPDX-License-Identifier: Apache-2.0
"""Pick the few tools a turn needs (brief §5.3), so a small model isn't handed them all."""
from __future__ import annotations

import re

from .mcp import Tool

KEYWORDS = {
    "set_dock_position": "dock dash shelf move edge top bottom left right",
    "set_theme": "dark light mode theme treatment frost glass look appearance",
    "set_accent": "accent colour color highlight",
    "set_interface_scale": "bigger smaller size text scale zoom larger tiny huge everything",
    "set_night_light": "night light warm blue eyes evening",
    "set_brightness": "brightness bright brighter dim dimmer screen darker lighter backlight",
    "set_wallpaper": "wallpaper background picture desktop image",
    "list_display_modes": "display monitor screen refresh rate hz hertz resolution",
    "set_refresh_rate": "refresh rate hz hertz display monitor screen lower higher smoother",
    "find_app": "installed app application have",
    "open_app": "open launch start run app application",
    "now": "time date today day clock timezone",
    "calculate": "calculate plus minus times divided percent sum math maths",
    "convert": "convert how many in to miles km pounds kg celsius fahrenheit",
    "weather": "weather temperature rain forecast sunny cold hot outside umbrella",
    "web_search": "who what when where latest news current president price score release search look up find",
    "fetch_page": "page website link url read http",
    "define": "define meaning definition word mean",
    "set_timezone": "timezone time zone eastern central pacific mountain utc gmt clock travel",
    "set_volume": "volume louder quieter mute unmute sound loud quiet speaker audio",
    "set_do_not_disturb": "disturb notifications banners quiet focus dnd",
    "set_wifi": "wifi wi fi wireless internet network",
    "set_bluetooth": "bluetooth headphones earbuds pairing",
    "set_tiling": "tiling tile tiled windows snap layout",
    "media_status": "playing song track music what now listening",
    "media_control": "play pause resume stop skip next previous song track music video tide spotify",
    "find_setting": "setting settings preference option clock seconds mouse touchpad keyboard speed sleep power "
                    "battery animations cursor font workspaces notifications hot corner show hide enable disable",
    "change_setting": "setting settings preference option clock seconds mouse touchpad keyboard speed sleep power "
                      "battery animations cursor font workspaces show hide enable disable",
}
ALWAYS = ("now",)


def select(text: str, tools: list[Tool], limit: int) -> list[Tool]:
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    scored = []
    for tool in tools:
        vocabulary = set(KEYWORDS.get(tool.name, "").split()) or set(re.findall(r"[a-z]+", tool.description.lower()))
        score = len(words & vocabulary) + (0.5 if tool.name in ALWAYS else 0)
        if "http" in text and tool.name == "fetch_page":
            score += 3
        scored.append((score, tool))
    scored.sort(key=lambda item: -item[0])
    chosen = [tool for score, tool in scored if score > 0][:limit]
    # A question with no obvious tool still gets search, the time and maths:
    # those are how Ari avoids answering from stale memory.
    for fallback in ("web_search", "now", "calculate"):
        if len(chosen) >= limit:
            break
        tool = next((t for t in tools if t.name == fallback), None)
        if tool and tool not in chosen:
            chosen.append(tool)
    return chosen
