# SPDX-License-Identifier: Apache-2.0
"""Fast paths for the most common requests (brief §5.4).

A deterministic match answers "dock to the top" or "dark mode" instantly on any
model. It produces the same tool call the model would, so the result still
passes through the policy engine and becomes the same step card.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Intent:
    tool: str
    arguments: dict
    done: str        # reply after success; {summary} is the tool's own words


_EDGE = r"(top|bottom|left|right)"
_PATTERNS: list[tuple[re.Pattern, callable]] = []


def _rule(pattern: str):
    def register(build):
        _PATTERNS.append((re.compile(pattern, re.I), build))
        return build
    return register


@_rule(rf"^(?:please\s+)?(?:move|put|place|dock|pin)\s+(?:my\s+|the\s+)?(?:dock|dash|shelf)(?:\s+(?:to|on|at))?\s+(?:the\s+)?{_EDGE}\b")
def _dock(match):
    edge = match.group(1).lower()
    return Intent("set_dock_position", {"edge": edge}, f"Done, the dock's along the {edge} now.")


@_rule(r"^(?:please\s+)?(?:make\s+it|switch\s+to|turn\s+on|use|go|set)?\s*(?:the\s+)?(dark|light)(?:\s+(?:mode|theme))?\s*$")
def _mode(match):
    theme = match.group(1).lower()
    return Intent("set_theme", {"theme": theme}, f"Done, Luma is {theme} now.")


@_rule(r"^(?:please\s+)?(?:switch\s+to|use|set|make\s+it)\s+(?:the\s+)?(frost|glass)(?:\s+(?:treatment|theme|look))?\s*$")
def _treatment(match):
    theme = match.group(1).lower()
    return Intent("set_theme", {"theme": theme}, f"Done, you're on {theme} now.")


@_rule(r"^(?:please\s+)?(?:change|set|switch|swap|give\s+me|use)\s+(?:my\s+|the\s+|a\s+)?(?:wallpaper|background)(?:\s+(?:to|for))?\s*(.*)$|^(random|new|another)\s+wallpaper$|^surprise me with a wallpaper$")
def _wallpaper(match):
    return Intent("set_wallpaper", {"wallpaper": (match.group(1) or "random").strip() or "random"}, "Done. {summary}.")


@_rule(r"^(?:please\s+)?(?:open|launch|start|run)\s+(?!the\s+(?:dock|dash))([\w .+-]{2,40})$")
def _open(match):
    name = match.group(1).strip().rstrip(".")
    if name.lower() in ("it", "that", "this"):
        return None
    return Intent("open_app", {"name": name}, "{summary}.")


@_rule(r"^(?:please\s+)?(?:turn|switch)\s+(on|off)\s+night\s*light$|^night\s*light\s+(on|off)$")
def _night(match):
    state = (match.group(1) or match.group(2)).lower()
    return Intent("set_night_light", {"state": state}, f"Night light's {state}.")


@_rule(r"^(?:please\s+)?(?:(?:make|turn)\s+(?:my\s+|the\s+)?(?:screen|display|brightness)\s+(brighter|dimmer|darker|up|down)|(?:set\s+)?(?:my\s+|the\s+)?(?:screen\s+)?brightness\s+(?:to\s+)?(\d{1,3})\s*%?|(dim|brighten)\s+(?:my\s+|the\s+)?(?:screen|display))$")
def _brightness(match):
    word, number, verb = (g.lower() if g else "" for g in match.groups())
    level = number or ("dimmer" if (word in ("dimmer", "darker", "down") or verb == "dim") else "brighter")
    return Intent("set_brightness", {"level": level}, "{summary}.")


@_rule(r"^(?:please\s+)?(?:make\s+)?(?:my\s+|the\s+)?accent(?:\s+colou?r)?\s+(?:to\s+)?(blue|teal|green|yellow|orange|red|pink|purple|slate|grey|gray)$")
def _accent(match):
    colour = match.group(1).lower().replace("grey", "slate").replace("gray", "slate")
    return Intent("set_accent", {"color": colour}, f"Done, the accent's {colour} now.")


@_rule(r"^(?:please\s+)?(?:(lower|reduce|drop|raise|increase|max(?:imi[sz]e)?)\s+(?:my\s+|the\s+)?refresh\s*rate(?:\s+(?:on|of)\s+(?:my\s+|the\s+)?([\w -]{2,30}))?|(?:set|change|put)\s+(?:my\s+|the\s+)?refresh\s*rate\s+to\s+(\d{2,3})(?:\s*hz)?|refresh\s*rate\s+(lower|higher|up|down))$")
def _refresh(match):
    verb, display, hz, short = (g.lower() if g else "" for g in match.groups())
    word = verb or short
    direction = hz or ("lower" if word in ("lower", "reduce", "drop", "down") else
                       "max" if word.startswith("max") else "higher")
    return Intent("set_refresh_rate", {"hz": direction, "display": display},
                  "{summary}. It goes back in 15 seconds unless you keep it.")


@_rule(r"^(?:what(?:'s| is)\s+|calculate\s+|how much is\s+)?((?:\d[\d,]*(?:\.\d+)?\s*%\s*of\s*\d[\d,]*(?:\.\d+)?)|(?:[\d(][\d,.\s()]*(?:\s*[-+*/×÷^]\s*[\d(][\d,.\s()]*)+))\s*\??$")
def _calculate(match):
    return Intent("calculate", {"expression": match.group(1).strip()}, "{summary}.")


@_rule(r"^(?:which|what)\s+(?:ai\s+|language\s+)?(?:model|llm|ai)\s+(?:are you|is this|do you (?:use|run)|are you (?:using|running))\??$|^who\s+(?:are|made)\s+you\??$|^what\s+are\s+you\??$")
def _about(_match):
    return Intent("_about", {}, "")


@_rule(r"^(?:please\s+)?(pause|resume|play|stop)(\s+(?:the\s+|my\s+)?(?:music|song|track|audio|video|podcast|playback))?(?:\s+(?:in|on)\s+([\w .-]{2,30}))?$|^(?:please\s+)?press\s+(play|pause)(?:\s+(?:in|inside|on)\s+([\w .-]{2,30}))?$")
def _media(match):
    verb = (match.group(1) or match.group(4) or "").lower()
    player = (match.group(3) or match.group(5) or "").strip()
    if verb in ("stop", "play") and not (match.group(2) or player):
        return None  # a bare "stop" is about Ari's answer; a bare "play" names nothing
    action = {"resume": "play", "play": "play", "pause": "pause", "stop": "pause"}[verb]
    return Intent("media_control", {"action": action, "player": player}, "{summary}.")


@_rule(r"^(?:please\s+)?(?:skip(?:\s+(?:this|the))?(?:\s+(?:song|track))?|next\s+(?:song|track)|play\s+the\s+next\s+(?:song|track))$")
def _next(_match):
    return Intent("media_control", {"action": "next", "player": ""}, "{summary}.")


@_rule(r"^(?:please\s+)?(?:previous\s+(?:song|track)|go\s+back\s+a\s+(?:song|track)|play\s+the\s+(?:previous|last)\s+(?:song|track))$")
def _previous(_match):
    return Intent("media_control", {"action": "previous", "player": ""}, "{summary}.")


@_rule(r"^(?:please\s+)?(?:turn|switch)\s+(on|off)\s+(tiling|wi-?fi|bluetooth|do not disturb|dnd)$|^(?:please\s+)?(?:turn|switch)\s+(tiling|wi-?fi|bluetooth|do not disturb|dnd)\s+(on|off)$|^(?:please\s+)?(enable|disable)\s+(tiling|wi-?fi|bluetooth|do not disturb)$")
def _switches(match):
    groups = [g.lower() if g else "" for g in match.groups()]
    state = next(g for g in (groups[0], groups[3], groups[4]) if g)
    thing = next(g for g in (groups[1], groups[2], groups[5]) if g)
    state = {"enable": "on", "disable": "off"}.get(state, state)
    tool = {"tiling": "set_tiling", "bluetooth": "set_bluetooth", "dnd": "set_do_not_disturb",
            "do not disturb": "set_do_not_disturb"}.get(thing, "set_wifi")
    return Intent(tool, {"state": state}, "{summary}.")


@_rule(r"^(?:please\s+)?(?:set|change|switch|put)\s+(?:my\s+|the\s+)?(?:system\s+)?(?:time\s*zone|timezone|time)\s+(?:to|into)\s+(.{2,40})$")
def _timezone(match):
    return Intent("set_timezone", {"zone": match.group(1).strip()}, "{summary}.")


@_rule(r"^(?:please\s+)?(?:(mute|unmute)(?:\s+(?:the\s+|my\s+)?(?:sound|audio|volume|computer))?|(?:turn|make)\s+(?:the\s+|my\s+)?(?:volume|sound|it)\s+(up|down|louder|quieter)|(?:set\s+)?(?:the\s+|my\s+)?volume\s+(?:to\s+)?(\d{1,3})\s*%?)$")
def _volume(match):
    word, direction, number = (g.lower() if g else "" for g in match.groups())
    return Intent("set_volume", {"level": number or word or {"louder": "up", "quieter": "down"}.get(direction, direction)},
                  "{summary}.")


@_rule(r"^what(?:'s| is)\s+the\s+(?:time|date)(?:\s+(?:now|today))?\??$|^what\s+(?:time|day)\s+is\s+it\??$")
def _time(_match):
    return Intent("now", {}, "It's {summary}.")


def match(text: str) -> Intent | None:
    cleaned = " ".join(text.strip().split()).rstrip(".!")
    for pattern, build in _PATTERNS:
        found = pattern.search(cleaned)
        if found:
            intent = build(found)
            if intent:
                return intent
    return None
