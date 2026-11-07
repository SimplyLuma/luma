# SPDX-License-Identifier: Apache-2.0
"""Languages, keyboards and time zones, from the system.

Languages are the locales the system can run (GnomeDesktop's list of
installed locales), one per language and country, each named in its own
language. The common ones come first, in the order a person most likely
looks for them; the rest follow by name. A language's keyboards are XKB
layouts: the locale's own first, then the others for its language and
country, then English (US). Time zones are the tz database's, each named by
its main city.

Without GnomeDesktop (a build or test machine) the common list stands in.
"""
from __future__ import annotations

import os
import zoneinfo
from dataclasses import dataclass
from functools import lru_cache

__all__ = ("Language", "Keyboard", "Zone", "languages", "language", "keyboards", "zones", "zone",
           "greeting", "search_zones", "COMMON_ZONES", "DEFAULT_LOCALE", "DEFAULT_LAYOUT")

DEFAULT_LOCALE = "en_US.UTF-8"
DEFAULT_LAYOUT = "us"


@dataclass(frozen=True)
class Language:
    locale: str      # en_US.UTF-8
    name: str        # English (US), in its own language


@dataclass(frozen=True)
class Keyboard:
    layout: str      # us, us+dvorak
    name: str


@dataclass(frozen=True)
class Zone:
    id: str          # America/Chicago
    name: str        # Central Time
    city: str        # Chicago


# The common languages first, named as people look for them.
_COMMON = (
    ("en_US.UTF-8", "English (US)"), ("en_GB.UTF-8", "English (UK)"), ("es_ES.UTF-8", "Español"),
    ("fr_FR.UTF-8", "Français"), ("de_DE.UTF-8", "Deutsch"), ("pt_BR.UTF-8", "Português (Brasil)"),
    ("it_IT.UTF-8", "Italiano"), ("ja_JP.UTF-8", "日本語"), ("zh_CN.UTF-8", "中文 (简体)"),
)

# The greeting each language says Hello with; the title of the first page.
_GREETINGS = {
    "en": "Hello.", "es": "Hola.", "fr": "Bonjour.", "de": "Hallo.", "pt": "Olá.", "it": "Ciao.",
    "ja": "こんにちは。", "zh": "你好。", "nl": "Hallo.", "sv": "Hej.", "nb": "Hei.", "da": "Hej.",
    "fi": "Hei.", "pl": "Cześć.", "cs": "Ahoj.", "sk": "Ahoj.", "tr": "Merhaba.", "ru": "Привет.",
    "uk": "Привіт.", "el": "Γεια σας.", "ko": "안녕하세요.", "ar": "مرحبًا.", "he": "שלום.",
    "hi": "नमस्ते।", "id": "Halo.", "vi": "Xin chào.", "th": "สวัสดี", "hu": "Szia.", "ro": "Salut.",
    "ca": "Hola.", "eu": "Kaixo.", "gl": "Ola.", "ga": "Dia duit.", "cy": "Helo.", "is": "Halló.",
    "et": "Tere.", "lv": "Sveiki.", "lt": "Labas.", "sl": "Živjo.", "hr": "Bok.", "sr": "Здраво.",
    "bg": "Здравейте.", "fa": "سلام.", "ms": "Helo.", "fil": "Kumusta.", "sw": "Habari.",
}

# Keyboards for the common languages, as the design names them; XKB is asked for the rest.
_COMMON_KEYBOARDS = {
    "en_US": (("us", "English (US)"), ("us+intl", "US International"), ("us+dvorak", "Dvorak")),
    "en_GB": (("gb", "English (UK)"), ("us", "English (US)")),
    "es_ES": (("es", "Español"), ("latam", "Latinoamericano"), ("us", "English (US)")),
    "fr_FR": (("fr", "Français (AZERTY)"), ("ca", "Français (Canada)"), ("us", "English (US)")),
    "de_DE": (("de", "Deutsch (QWERTZ)"), ("ch", "Deutsch (Schweiz)"), ("us", "English (US)")),
    "pt_BR": (("br", "Português (ABNT2)"), ("us", "English (US)")),
    "it_IT": (("it", "Italiano"), ("us", "English (US)")),
    "ja_JP": (("jp", "日本語 (かな)"), ("us", "English (US)")),
    "zh_CN": (("cn", "拼音"), ("us", "English (US)")),
}


def _desktop():
    if os.environ.get("LUMA_SETUP_FIXTURE"):
        return None
    try:
        import gi

        gi.require_version("GnomeDesktop", "4.0")
        from gi.repository import GnomeDesktop
        return GnomeDesktop
    except (ImportError, ValueError):
        return None


def _base(locale: str) -> str:
    """en_US.UTF-8 → en_US."""
    return locale.split(".", 1)[0].split("@", 1)[0]


@lru_cache(maxsize=1)
def languages() -> tuple[Language, ...]:
    common = [Language(locale, name) for locale, name in _COMMON]
    desktop = _desktop()
    if desktop is None:
        return tuple(common)
    installed = {}
    for locale in desktop.get_all_locales():
        base = _base(locale)
        if "_" not in base or base in installed:
            continue
        installed[base] = locale if "." in locale else f"{base}.UTF-8"
    shown = [lang for lang in common if _base(lang.locale) in installed]
    seen = {_base(lang.locale) for lang in shown}
    rest = []
    for base, locale in installed.items():
        if base in seen:
            continue
        name = desktop.get_language_from_locale(locale, locale) or base
        rest.append(Language(locale, name))
    rest.sort(key=lambda lang: lang.name.casefold())
    return tuple(shown + rest) or tuple(common)


def language(locale: str) -> Language:
    for lang in languages():
        if lang.locale == locale or _base(lang.locale) == _base(locale):
            return lang
    return Language(locale, _base(locale))


def greeting(locale: str) -> str:
    code = _base(locale).split("_", 1)[0]
    return _GREETINGS.get(code, "Hello.")


def _xkb():
    desktop = _desktop()
    return desktop.XkbInfo.new() if desktop is not None else None


def _layout_name(xkb, layout: str) -> str:
    found, name, *_rest = xkb.get_layout_info(layout)
    return name if found and name else layout


def keyboards(locale: str) -> tuple[Keyboard, ...]:
    """The keyboards offered with a language; the first is what picking the language selects."""
    base = _base(locale)
    if base in _COMMON_KEYBOARDS:
        return tuple(Keyboard(layout, name) for layout, name in _COMMON_KEYBOARDS[base])
    xkb, desktop = _xkb(), _desktop()
    if xkb is None:
        return (Keyboard(DEFAULT_LAYOUT, "English (US)"),)
    layouts: list[str] = []
    found, kind, own = desktop.get_input_source_from_locale(locale)
    if found and kind == "xkb":
        layouts.append(own)
    language_code, _, country = base.partition("_")
    for layout in list(xkb.get_layouts_for_language(language_code)) + list(xkb.get_layouts_for_country(country)):
        if layout not in layouts:
            layouts.append(layout)
        if len(layouts) >= 3:
            break
    if DEFAULT_LAYOUT not in layouts:
        layouts.append(DEFAULT_LAYOUT)
    return tuple(Keyboard(layout, _layout_name(xkb, layout)) for layout in layouts)


def _weather():
    if os.environ.get("LUMA_SETUP_FIXTURE"):
        return None
    try:
        import gi

        gi.require_version("GWeather", "4.0")
        from gi.repository import GWeather
        return GWeather
    except (ImportError, ValueError):
        return None


def _city(zone_id: str) -> str:
    return zone_id.rsplit("/", 1)[-1].replace("_", " ")


# Time zones named as a person says them, where GWeather has no name of its own.
_ZONE_NAMES = {
    "America/Chicago": "Central Time", "America/New_York": "Eastern Time", "America/Denver": "Mountain Time",
    "America/Los_Angeles": "Pacific Time", "America/Phoenix": "Mountain Time", "America/Anchorage": "Alaska Time",
    "Pacific/Honolulu": "Hawaii Time", "Europe/London": "British Time", "Europe/Dublin": "Irish Time",
    "Europe/Paris": "Central European Time", "Europe/Berlin": "Central European Time",
    "Europe/Madrid": "Central European Time", "Europe/Rome": "Central European Time",
    "Asia/Tokyo": "Japan Time", "Australia/Sydney": "Australian Eastern Time", "UTC": "Coordinated Universal Time",
}


@lru_cache(maxsize=1)
def zones() -> tuple[Zone, ...]:
    """Every time zone a person can live in, by city."""
    weather = _weather()
    named: dict[str, str] = {}
    if weather is not None:
        try:
            for tz in weather.Location.get_world().get_timezones():
                named[tz.get_identifier()] = tz.get_name() or ""
        except (AttributeError, TypeError):
            named = {}
    found = []
    for zone_id in sorted(zoneinfo.available_timezones()):
        if "/" not in zone_id or zone_id.startswith(("Etc/", "SystemV/", "US/", "posix/", "right/")):
            continue
        name = _ZONE_NAMES.get(zone_id) or named.get(zone_id) or zone_id.split("/", 1)[0]
        found.append(Zone(zone_id, name, _city(zone_id)))
    found.sort(key=lambda z: z.city.casefold())
    return tuple(found)


def zone(zone_id: str) -> Zone:
    for z in zones():
        if z.id == zone_id:
            return z
    return Zone(zone_id, _ZONE_NAMES.get(zone_id, zone_id), _city(zone_id))


#: The list a search starts from.
COMMON_ZONES = ("America/Chicago", "America/New_York", "America/Denver", "America/Los_Angeles",
                "Europe/London", "Europe/Paris", "Europe/Berlin", "Asia/Tokyo", "Australia/Sydney")


def search_zones(query: str, limit: int = 60) -> list[Zone]:
    """Cities matching every word typed; the common ones when nothing is typed."""
    words = query.casefold().split()
    if not words:
        return [zone(z) for z in COMMON_ZONES]
    hits = [z for z in zones() if all(w in f"{z.city} {z.name} {z.id}".casefold() for w in words)]
    hits.sort(key=lambda z: (not z.city.casefold().startswith(words[0]), z.city.casefold()))
    return hits[:limit]
