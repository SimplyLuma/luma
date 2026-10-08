# SPDX-License-Identifier: Apache-2.0
"""Tier 0 tools: things Ari can find out without changing anything (brief §8.1).

Everything fetched from the network is returned as data for Ari to read, never
as instructions. Search uses Wikipedia by default, which needs no account and
says where each answer came from; a SearXNG instance can be configured instead.
"""
from __future__ import annotations

import ast
import html
import json
import math
import operator
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ari.mcp import serve  # noqa: E402

USER_AGENT = "Luma-Ari/0.1 (+https://projectluma.org/ari)"
MAX_PAGE_CHARS = 12_000


def _get(url: str, *, timeout: float = 12) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(2_000_000)


# ── Time and maths ───────────────────────────────────────────────────────

def now(_arguments: dict) -> dict:
    moment = datetime.now().astimezone()
    return {"ok": True, "summary": moment.strftime("%A %-d %B %Y, %-I:%M %p %Z"),
            "data": {"iso": moment.isoformat(), "timezone": str(moment.tzinfo)}}


_OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
              ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCTIONS = {name: getattr(math, name) for name in
              ("sqrt", "sin", "cos", "tan", "log", "log10", "log2", "exp", "floor", "ceil", "fabs")}
_FUNCTIONS.update({"abs": abs, "round": round})
_CONSTANTS = {"pi": math.pi, "e": math.e}


def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("That exponent is too large.")
        return _OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPERATORS:
        return _OPERATORS[type(node.op)](_evaluate(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS:
        return _FUNCTIONS[node.func.id](*(_evaluate(a) for a in node.args))
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    raise ValueError("I can only work out arithmetic.")


def calculate(arguments: dict) -> dict:
    expression = str(arguments.get("expression", ""))[:200]
    cleaned = expression.replace("×", "*").replace("÷", "/").replace("^", "**").replace(",", "")
    cleaned = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*of\s*", r"(\1/100)*", cleaned)
    value = _evaluate(ast.parse(cleaned, mode="eval"))
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    text = f"{value:,.10g}" if isinstance(value, float) else f"{value:,}"
    return {"ok": True, "summary": f"{expression.strip()} is {text}", "data": {"value": value}}


_UNITS = {
    # length (metres)
    "m": ("length", 1), "km": ("length", 1000), "cm": ("length", 0.01), "mm": ("length", 0.001),
    "mi": ("length", 1609.344), "yd": ("length", 0.9144), "ft": ("length", 0.3048), "in": ("length", 0.0254),
    # mass (grams)
    "g": ("mass", 1), "kg": ("mass", 1000), "mg": ("mass", 0.001), "lb": ("mass", 453.59237),
    "oz": ("mass", 28.349523125), "st": ("mass", 6350.29318),
    # volume (litres)
    "l": ("volume", 1), "ml": ("volume", 0.001), "gal": ("volume", 3.785411784), "qt": ("volume", 0.946352946),
    "cup": ("volume", 0.2365882365), "floz": ("volume", 0.0295735296),
    # speed (m/s)
    "kmh": ("speed", 1 / 3.6), "mph": ("speed", 0.44704), "ms": ("speed", 1), "kn": ("speed", 0.514444),
    # data (bytes)
    "b": ("data", 1), "kb": ("data", 1000), "mb": ("data", 1000 ** 2), "gb": ("data", 1000 ** 3),
    "tb": ("data", 1000 ** 4), "kib": ("data", 1024), "mib": ("data", 1024 ** 2), "gib": ("data", 1024 ** 3),
    # time (seconds)
    "s": ("time", 1), "min": ("time", 60), "h": ("time", 3600), "day": ("time", 86400), "week": ("time", 604800),
}
_ALIASES = {"meter": "m", "metre": "m", "meters": "m", "metres": "m", "kilometer": "km", "kilometers": "km",
            "kilometres": "km", "mile": "mi", "miles": "mi", "foot": "ft", "feet": "ft", "inch": "in",
            "inches": "in", "pound": "lb", "pounds": "lb", "lbs": "lb", "kilogram": "kg", "kilograms": "kg",
            "kilos": "kg", "gram": "g", "grams": "g", "ounce": "oz", "ounces": "oz", "liter": "l",
            "litre": "l", "liters": "l", "litres": "l", "gallon": "gal", "gallons": "gal", "cups": "cup",
            "km/h": "kmh", "kph": "kmh", "m/s": "ms", "knots": "kn", "hours": "h", "hour": "h",
            "minutes": "min", "minute": "min", "seconds": "s", "second": "s", "days": "day", "weeks": "week",
            "celsius": "c", "°c": "c", "fahrenheit": "f", "°f": "f", "kelvin": "k"}


def convert(arguments: dict) -> dict:
    value = float(arguments["value"])
    source = _ALIASES.get(str(arguments["from"]).strip().lower(), str(arguments["from"]).strip().lower())
    target = _ALIASES.get(str(arguments["to"]).strip().lower(), str(arguments["to"]).strip().lower())
    temperatures = {"c", "f", "k"}
    if source in temperatures and target in temperatures:
        kelvin = {"c": value + 273.15, "f": (value - 32) * 5 / 9 + 273.15, "k": value}[source]
        result = {"c": kelvin - 273.15, "f": (kelvin - 273.15) * 9 / 5 + 32, "k": kelvin}[target]
    elif source in _UNITS and target in _UNITS and _UNITS[source][0] == _UNITS[target][0]:
        result = value * _UNITS[source][1] / _UNITS[target][1]
    else:
        return {"ok": False, "summary": f"I can't convert {arguments['from']} to {arguments['to']}."}
    return {"ok": True, "summary": f"{value:g} {arguments['from']} is {result:,.4g} {arguments['to']}",
            "data": {"value": result}}


# ── Weather: the Weather app's own source ────────────────────────────────

def _uses_fahrenheit() -> bool:
    """The person's measurement system: GNOME's weather unit if set, else the locale's."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        source = Gio.SettingsSchemaSource.get_default()
        if source and source.lookup("org.gnome.GWeather4", True):
            unit = Gio.Settings.new("org.gnome.GWeather4").get_string("temperature-unit")
            if unit in ("fahrenheit", "centigrade", "kelvin"):
                return unit == "fahrenheit"
    except (ImportError, ValueError):
        pass
    locale = os.environ.get("LC_ALL") or os.environ.get("LC_MEASUREMENT") or os.environ.get("LANG") or ""
    country = locale.split(".")[0].split("@")[0].partition("_")[2].upper()
    return country in ("US", "LR", "MM")


def weather(arguments: dict) -> dict:
    try:
        from prairie_apps import weather_backend as backend
        from prairie_apps.weather_model import condition_for
    except ImportError:
        return {"ok": False, "summary": "The Weather app isn't installed, so I can't check the weather here."}
    wanted = str(arguments.get("place") or "").strip()
    if wanted:
        search = backend.LocationSearch()
        try:
            search.load()
            matches = search.search(wanted, limit=1)
        except Exception:
            matches = ()
        if not matches:
            return {"ok": False, "summary": f"I couldn't find a place called {wanted}."}
        place = backend.new_place(matches[0]) if not matches[0].uid else matches[0]
    else:
        saved = backend.PlaceStore().list()
        if not saved:
            return {"ok": False, "summary": "No place is saved in Weather yet. Tell me a city."}
        place = saved[0]
    try:
        forecast = backend.fetch_forecast(place)
    except Exception:
        forecast = backend.cached_forecast(place)
        if forecast is None:
            return {"ok": False, "summary": "I couldn't reach the forecast service."}
    current = forecast.current
    condition = condition_for(current.symbol).text
    high = current.high_c if current.high_c is not None else (forecast.days[0].high_c if forecast.days else None)
    low = current.low_c if current.low_c is not None else (forecast.days[0].low_c if forecast.days else None)
    fahrenheit = _uses_fahrenheit()
    unit = "°F" if fahrenheit else "°C"

    def degrees(celsius: float) -> str:
        return f"{celsius * 9 / 5 + 32:.0f}" if fahrenheit else f"{celsius:.0f}"
    parts = [f"{place.name}: {degrees(current.temperature_c)}{unit}, {condition.lower()}"]
    if high is not None and low is not None:
        parts.append(f"high {degrees(high)}{unit}, low {degrees(low)}{unit}")
    return {"ok": True, "summary": ", ".join(parts),
            "data": {"place": place.name, "temperature_c": current.temperature_c, "condition": condition,
                     "high_c": high, "low_c": low, "fetched_at": forecast.fetched_at},
            "source": {"name": "MET Norway", "url": "https://www.met.no/"}}


# ── Search and pages ─────────────────────────────────────────────────────

def _config() -> dict:
    path = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "luma" / "ari" / "config.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _strip(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", markup)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", markup))
    return re.sub(r"\s+", " ", text).strip()


def web_search(arguments: dict) -> dict:
    query = str(arguments.get("query", "")).strip()[:300]
    if not query:
        return {"ok": False, "summary": "Search needs a query."}
    backend = (_config().get("search") or {})
    try:
        if backend.get("backend") == "searxng" and str(backend.get("url", "")).startswith(("http://", "https://")):
            url = backend["url"].rstrip("/") + "/search?" + urllib.parse.urlencode({"q": query, "format": "json"})
            payload = json.loads(_get(url))
            results = [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
                       for r in payload.get("results", [])[:5]]
            source = "SearXNG"
        else:
            topic = _topic(query)
            url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
                {"action": "query", "list": "search", "srsearch": topic, "srlimit": 4, "format": "json"})
            hits = json.loads(_get(url)).get("query", {}).get("search", [])
            # An article whose title is the topic answers it better than
            # whatever full-text search ranks first.
            try:
                exact = json.loads(_get("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
                    {"action": "opensearch", "search": topic, "limit": 2, "namespace": 0, "format": "json"})))[1]
            except Exception:
                exact = []
            wanted = topic.lower()
            exact = [t for t in exact if t.lower().startswith(wanted) or wanted.startswith(t.lower())]
            titles = [hit["title"] for hit in hits]
            for title in reversed(exact):
                if title in titles:
                    hits.insert(0, hits.pop(titles.index(title)))
                    titles.insert(0, titles.pop(titles.index(title)))
                else:
                    hits.insert(0, {"title": title, "snippet": ""})
                    titles.insert(0, title)
            hits = hits[:4]
            results = []
            titles = [hit["title"] for hit in hits]
            extracts = {}
            if titles:
                intro_url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
                    {"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
                     "titles": "|".join(titles[:4]), "format": "json", "redirects": 1})
                try:
                    answered = json.loads(_get(intro_url)).get("query", {})
                    extracts = {page.get("title"): page.get("extract", "")
                                for page in answered.get("pages", {}).values()}
                    # A redirect's extract is filed under the article it leads to.
                    for hop in answered.get("normalized", []) + answered.get("redirects", []):
                        if hop.get("to") in extracts:
                            extracts.setdefault(hop.get("from"), extracts[hop["to"]])
                    for hop in answered.get("normalized", []):
                        for redirect in answered.get("redirects", []):
                            if redirect.get("from") == hop.get("to") and redirect.get("to") in extracts:
                                extracts.setdefault(hop.get("from"), extracts[redirect["to"]])
                except Exception:
                    extracts = {}
            for index, hit in enumerate(hits):
                title = hit["title"]
                # The whole introduction, not the one-line summary: it is where
                # an article names who holds an office now.
                limit = 3000 if index < 2 else 700
                extract = (extracts.get(title) or _strip(hit.get("snippet", "")))[:limit]
                results.append({"title": title, "snippet": extract,
                                "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))})
            source = "Wikipedia"
    except Exception:
        return {"ok": False, "summary": "I couldn't reach search right now."}
    if not results:
        return {"ok": True, "summary": f"{source} found nothing for {query}.", "data": {"results": []}}
    checked = datetime.now().astimezone().strftime("%-d %B %Y")
    return {"ok": True, "summary": f"{len(results)} results from {source}, checked {checked}",
            "data": {"results": results, "source": source, "checked": checked,
                     "key_sentences": key_sentences(query, results)},
            "untrusted": True, "source": {"name": source, "url": results[0].get("url", "")}}


def _topic(query: str) -> str:
    """"who is the president of france?" -> "president of france"."""
    topic = re.sub(r"^\s*(?:who|what|when|where|which|how)(?:'s| is| are| was| were| does| do| did)?\s+(?:the\s+)?",
                   "", query.strip().rstrip("?.!"), flags=re.I)
    topic = re.sub(r"^(?:current|currently|new|sitting)\s+", "", topic, flags=re.I)
    return topic or query


_STOP = {"the", "a", "an", "of", "is", "are", "was", "who", "what", "when", "where", "which", "how", "in",
         "on", "for", "to", "and", "does", "do", "current", "now", "today"}


def key_sentences(query: str, results: list[dict], limit: int = 5) -> list[str]:
    """The sentences most likely to answer the question, first: small models
    read the top of a result and trust their memory for the rest."""
    words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in _STOP}
    scored = []
    for order, result in enumerate(results):
        for sentence in re.split(r"(?<=[.!?])\s+", result.get("snippet", "")):
            lowered = sentence.lower()
            score = sum(1 for w in words if w in lowered)
            if re.search(r"\b(current|currently|incumbent|serving|latest)\b", lowered):
                score += 4
            if re.search(r"\b(since|as of|now)\b", lowered):
                score += 1
            this_year = datetime.now().year
            if any(abs(int(year) - this_year) <= 2 for year in re.findall(r"\b(?:19|20)\d\d\b", sentence)):
                score += 2
            if score:
                scored.append((score, -order, sentence.strip()))
    scored.sort(reverse=True)
    seen, chosen = set(), []
    for _score, _order, sentence in scored:
        if sentence not in seen and 20 < len(sentence) < 400:
            seen.add(sentence)
            chosen.append(sentence)
        if len(chosen) >= limit:
            break
    return chosen


def fetch_page(arguments: dict) -> dict:
    url = str(arguments.get("url", ""))
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
        return {"ok": False, "summary": "I can only read ordinary web addresses."}
    if parts.hostname in ("localhost",) or re.fullmatch(r"(127|10|192\.168|169\.254)\..*", parts.hostname):
        return {"ok": False, "summary": "I don't read pages on this machine or its local network."}
    try:
        text = _strip(_get(url).decode("utf-8", "replace"))
    except Exception:
        return {"ok": False, "summary": "I couldn't open that page."}
    return {"ok": True, "summary": f"Read {parts.hostname}", "untrusted": True,
            "data": {"url": url, "text": text[:MAX_PAGE_CHARS]}, "source": {"name": parts.hostname, "url": url}}


def define(arguments: dict) -> dict:
    word = re.sub(r"[^A-Za-z' -]", "", str(arguments.get("word", "")))[:60].strip()
    if not word:
        return {"ok": False, "summary": "Which word?"}
    try:
        payload = json.loads(_get("https://en.wiktionary.org/api/rest_v1/page/definition/"
                                  + urllib.parse.quote(word)))
    except Exception:
        return {"ok": False, "summary": f"I couldn't look up {word}."}
    senses = []
    for entry in payload.get("en", [])[:2]:
        for sense in entry.get("definitions", [])[:2]:
            senses.append(f"({entry.get('partOfSpeech', '').lower()}) {_strip(sense.get('definition', ''))}")
    if not senses:
        return {"ok": False, "summary": f"Wiktionary has no definition for {word}."}
    return {"ok": True, "summary": "; ".join(senses), "source": {"name": "Wiktionary", "url": f"https://en.wiktionary.org/wiki/{word}"},
            "untrusted": True}


TOOLS = {
    "now": ("The current date, time and time zone on this machine.",
            {"type": "object", "properties": {}, "additionalProperties": False}, now),
    "calculate": ("Work out an arithmetic expression exactly.",
                  {"type": "object", "properties": {"expression": {"type": "string"}},
                   "required": ["expression"], "additionalProperties": False}, calculate),
    "convert": ("Convert a value between units of length, mass, volume, speed, data, time or temperature.",
                {"type": "object", "properties": {"value": {"type": "number"}, "from": {"type": "string"},
                                                  "to": {"type": "string"}},
                 "required": ["value", "from", "to"], "additionalProperties": False}, convert),
    "weather": ("Current weather and today's high and low, from the Weather app's source. "
                "Leave place empty for the person's saved place.",
                {"type": "object", "properties": {"place": {"type": "string"}}, "additionalProperties": False},
                weather),
    "web_search": ("Search the web for anything current or factual: people in office, prices, news, releases, "
                   "scores, or facts you are unsure of.",
                   {"type": "object", "properties": {"query": {"type": "string"}},
                    "required": ["query"], "additionalProperties": False}, web_search),
    "fetch_page": ("Read the text of a public web page.",
                   {"type": "object", "properties": {"url": {"type": "string"}},
                    "required": ["url"], "additionalProperties": False}, fetch_page),
    "define": ("Dictionary definition of a word.",
               {"type": "object", "properties": {"word": {"type": "string"}},
                "required": ["word"], "additionalProperties": False}, define),
}

if __name__ == "__main__":
    serve("ari-answers", TOOLS)
