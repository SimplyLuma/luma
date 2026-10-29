# SPDX-License-Identifier: Apache-2.0
"""Pick the few tools a turn needs (brief §5.3), so a small model isn't handed them all."""
from __future__ import annotations

import re

from .mcp import Tool

KEYWORDS = {
    "desktop_applications": "app application inside weather city cities note notes calendar task message browser",
    "inspect_application": "app application inside weather city cities note notes calendar task message browser read",
    "act_in_application": "app application inside weather city cities add create edit save write click press select note notes calendar task message browser",
    "app_surfaces": "app application inside screen read list task tasks note notes calendar event events message email browser",
    "request_app_access": "app application access permission inside note notes calendar task tasks message email",
    "app_action": "app application action inside create add write send edit mark complete task tasks note notes calendar event message email browser",
    "installed_apps": "installed install uninstall remove software app application",
    "search_software": "install get download find search software app application depot",
    "review_software": "install get download software app application depot",
    "install_software": "install get download software app application depot",
    "restore_system_application": "restore reinstall install removed system app application depot",
    "inspect_package": "install package software downloaded download app application rpm flatpak appimage file folder",
    "install_package": "install package software app application rpm flatpak appimage",
    "remove_application": "uninstall remove software app application",
    "list_project": "project build create develop code app application files folder customize edit fix test",
    "read_project_file": "project build create develop code app application source files customize edit fix test",
    "write_project_file": "project build create develop code app application source files customize edit fix implement",
    "create_project": "build create develop code program app application project scaffold",
    "run_project": "build create develop code app application project run test check compile package",
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
    "wikipedia": "encyclopedia wikipedia background history explain what who",
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


def development_request(text):
    return bool(re.search(r"\b(?:build|develop|code|program|implement|scaffold|debug|fix|create|make)\b", text, re.I)
                and re.search(r"\b(?:app|application|project|program|software|code|website|script)\b", text, re.I))


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
    if development_request(text):
        bundle = {"create_project", "list_project", "read_project_file", "write_project_file", "run_project"}
        preferred = [tool for tool in tools if tool.name in bundle]
        chosen = (preferred + [tool for tool in chosen if tool not in preferred])[:limit]
    elif re.search(r"\b(?:notes?|calendar|tasks?|messages?|contacts?|browser|email|weather)\b", text, re.I) or (
            re.search(r"\b(?:add|write|edit|create|save|delete|remove|change|set)\b", text, re.I)
            and re.search(r"\b(?:app|application)\b", text, re.I)):
        bundle = {"find_app", "open_app", "request_app_access", "app_surfaces", "app_action",
                  "desktop_applications", "inspect_application", "act_in_application"}
        preferred = [tool for tool in tools if tool.name in bundle]
        chosen = (preferred + [tool for tool in chosen if tool not in preferred])[:limit]
    elif re.search(r"\b(?:install|get|download)\b", text, re.I):
        bundle = {"search_software", "review_software", "install_software", "inspect_package", "install_package"}
        preferred = [tool for tool in tools if tool.name in bundle]
        chosen = (preferred + [tool for tool in chosen if tool not in preferred])[:limit]
    # A question with no obvious tool still gets search, the time and maths:
    # those are how Ari avoids answering from stale memory.
    for fallback in ("web_search", "wikipedia", "now", "calculate"):
        if len(chosen) >= limit:
            break
        tool = next((t for t in tools if t.name == fallback), None)
        if tool and tool not in chosen:
            chosen.append(tool)
    return chosen
