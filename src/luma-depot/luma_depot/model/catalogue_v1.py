# SPDX-License-Identifier: Apache-2.0
"""The signed Depot catalogue, v1 (Depot platform plan, decision 6): the listings, words and pictures.

    https://dl.simplyluma.com/depot/catalogue/v1/catalogue.json  (+ .minisig, key E6B75A79572BD6E8)

The file is verified against the pinned key before a byte of it is parsed, cached beside its
signature, and re-verified from the cache when the network is away. When it is missing, unsigned or
altered Depot falls back to the AppStream data it has always read. Media is content-addressed
(https://media.simplyluma.com/depot/<app-id>/<sha256>.<ext>), so a cached file is kept for good.

This module lists and describes. Installing still resolves sources only from the installer's own
signed catalogue (luma_installer.depot_catalog), never from a ref in this document.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import threading
import urllib.request

from ..providers import App, Release, Screenshot
from .world import Look, Tweak

__all__ = ["CATALOGUE_URL", "PUBLIC_KEY", "Listing", "MediaCache", "curated", "load", "parse", "verify"]

CATALOGUE_URL = "https://dl.simplyluma.com/depot/catalogue/v1/catalogue.json"
SIGNATURE_URL = CATALOGUE_URL + ".minisig"
PUBLIC_KEY = ("untrusted comment: minisign public key E6B75A79572BD6E8\n"
              "RWTo1itXeVq35sW2kLxnao46f1WNno7vQPromWZaEibbFV5cAUxkScXw\n")
#: Luma's own hosts; media is named by its sha256, which is checked on arrival.
MEDIA_HOSTS = ("https://media.simplyluma.com/", "https://dl.simplyluma.com/media/", "https://hub.simplyluma.com/api/depot/media/")
_LIMIT = 16 * 1024 * 1024

#: The eleven words back to the publisher-glyph form the pages' mapping reads (8.11).
_WORD_GLYPH = {"Camera": "camera", "Microphone": "mic", "Location": "map-pin", "Files": "folder-open",
               "Internet": "globe", "Notifications": "bell", "Screen": "monitor", "Other apps": "layers",
               "Passwords": "key-round", "Devices": "cpu", "System": "terminal"}


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "luma-depot" / "catalogue-v1"


def verify(document: bytes, signature: bytes) -> str:
    """The trusted comment, or SignatureError: the key, the file and the comment must all hold."""
    from luma_installer.depot_signature import verify_file
    comment = verify_file(PUBLIC_KEY, document, signature)
    match = re.search(r"sha256=([0-9a-f]{64})", comment)
    if match and match.group(1) != hashlib.sha256(document).hexdigest():
        from luma_installer.depot_signature import SignatureError
        raise SignatureError("The catalogue's comment names other contents.")
    return comment


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Luma Depot"})
    with urllib.request.urlopen(request, timeout=20) as stream:
        data = stream.read(_LIMIT + 1)
    if len(data) > _LIMIT:
        raise ValueError("catalogue too large")
    return data


def load() -> dict | None:
    """The verified document (fresh, or the last verified copy), or None: the caller falls back."""
    cache = _cache_dir()
    try:
        document, signature = _fetch(CATALOGUE_URL), _fetch(SIGNATURE_URL)
        verify(document, signature)
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "catalogue.json.minisig").write_bytes(signature)
        (cache / "catalogue.json").write_bytes(document)
        return json.loads(document)
    except Exception:  # noqa: BLE001 - away, unsigned or altered: try the verified copy
        pass
    try:
        document = (cache / "catalogue.json").read_bytes()
        verify(document, (cache / "catalogue.json.minisig").read_bytes())
        return json.loads(document)
    except Exception:  # noqa: BLE001
        return None


class MediaCache:
    """Content-addressed media on disk; `path(url)` is the local file once fetched, else None."""

    def __init__(self, on_ready=None) -> None:
        self.root = _cache_dir() / "media"
        self.on_ready = on_ready
        self._pending: set[str] = set()

    def _local(self, url: str) -> Path:
        return self.root / hashlib.sha256(url.encode()).hexdigest()[:32] / url.rsplit("/", 1)[-1]

    def path(self, url: str) -> str | None:
        if not url:
            return None
        url = url.split("#", 1)[0]
        if not url.startswith(MEDIA_HOSTS):
            return url if Path(url).is_file() else None
        local = self._local(url)
        return str(local) if local.is_file() else None

    def prefetch(self, urls) -> None:
        urls = [u.split("#", 1)[0] for u in urls if u]
        wanted = [u for u in urls if u.startswith(MEDIA_HOSTS) and self.path(u) is None and u not in self._pending]
        if not wanted:
            return
        self._pending.update(wanted)

        def work() -> None:
            got = False
            for url in wanted:
                try:
                    data = _fetch(url)
                    name = url.rsplit("/", 1)[-1]
                    digest = name.split(".", 1)[0]
                    if re.fullmatch(r"[0-9a-f]{64}", digest) and hashlib.sha256(data).hexdigest() != digest:
                        continue  # content-addressed: a file that isn't what it's named is dropped
                    local = self._local(url)
                    local.parent.mkdir(parents=True, exist_ok=True)
                    local.write_bytes(data)
                    got = True
                except Exception:  # noqa: BLE001 - a missing picture stays missing
                    pass
                finally:
                    self._pending.discard(url)
            if got and self.on_ready is not None:
                from gi.repository import GLib
                GLib.idle_add(lambda: (self.on_ready(), False)[1])

        threading.Thread(target=work, daemon=True).start()


class Listing:
    """What one v1 entry adds to Depot: an App (with its words and pictures), a Look or a Tweak."""


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _get(entry: dict, *names, default=None):
    for name in names:
        if name in entry and entry[name] not in (None, ""):
            return entry[name]
    return default


def _media(value) -> str:
    if isinstance(value, dict):
        return _text(value.get("url") or value.get("src"))
    return _text(value)


def _date(value) -> str:
    """"2026-11-07" → "Nov 7, 2026" (rows say "Nov 7")."""
    from datetime import datetime
    text = _text(value)
    try:
        moment = datetime.strptime(text[:10], "%Y-%m-%d")
    except ValueError:
        return text
    return f"{moment.strftime('%b')} {moment.day}, {moment.year}"


def _size(value) -> str:
    if isinstance(value, (int, float)) and value > 0:
        return f"{value / 1e9:.1f} GB" if value >= 1e9 else f"{max(1, round(value / 1e6))} MB"
    return _text(value)


def _note(line) -> object:
    """"**Adjustable scroll speed.** Set how fast…" → {title, text}; typed ["New", "…"] → {type, …}."""
    if isinstance(line, (list, tuple)) and len(line) == 2:
        note = _note(line[1])
        return dict(note, type=_text(line[0])) if isinstance(note, dict) else {"type": _text(line[0]), "text": _text(note)}
    text = _text(line)
    match = re.match(r"\*\*(.+?)\.?\*\*\s*(.*)", text)
    return {"title": match.group(1), "text": match.group(2)} if match else text


def _shot(value) -> str:
    """A screenshot as its URL with its form and theme after "#" (frames read them)."""
    if isinstance(value, dict):
        url = _text(value.get("url"))
        return f"{url}#{_text(value.get('form') or 'desktop')}-{_text(value.get('theme') or 'dark')}" if url else ""
    return _text(value)


def _app(entry: dict) -> App:
    developer = entry.get("developer") or {}
    releases = [r for r in entry.get("releases") or [] if isinstance(r, dict)]
    latest = releases[0] if releases else {}
    news = entry.get("news") or {}
    rating = entry.get("rating") or {}
    shots = [_shot(s) for s in entry.get("shots") or entry.get("screenshots") or []]
    reach = tuple((_WORD_GLYPH.get(p.get("word", ""), "globe"), p.get("word", ""), _text(p.get("reason")))
                  for p in entry.get("permissions") or [] if isinstance(p, dict) and p.get("word") in _WORD_GLYPH)
    version = _text(latest.get("version")) or _text(entry.get("version")) or _text(news.get("v"))
    notes = [_note(n) for n in (news.get("typed") or news.get("items") or latest.get("notes") or [])]
    mark = entry.get("mark") or {}
    return App(
        app_id=_text(entry["id"]), name=_text(entry.get("name")), summary=_text(entry.get("summary")),
        description="\n\n".join(p for p in (entry.get("description") or entry.get("desc") or []) if isinstance(p, str))
        if isinstance(entry.get("description") or entry.get("desc"), list) else _text(entry.get("description")),
        developer=_text(developer.get("name") if isinstance(developer, dict) else developer) or _text(entry.get("dev")),
        developer_verified="organization" if ((developer.get("verified") if isinstance(developer, dict) and developer else None)
                                              or entry.get("verified") or entry.get("first_party")) else "",
        # The listing's own category, or none: an app nobody filed is left out of category shelves.
        categories=tuple(c for c in (_text(_get(entry, "category", "cat", default="")).casefold(),) if c),
        category_key=_text(_get(entry, "category", "cat", default="")).casefold(),
        icon_name="", icon_url=_media(entry.get("icon")), licence=_text(_get(entry, "license", "licence", default="")),
        rating=float(rating.get("avg", 0) or 0), rating_count=int(rating.get("n", 0) or 0),
        rating_distribution=tuple(rating.get("dist") or ()),
        screenshots=tuple(Screenshot(url=s) for s in shots if s), shot_keys=tuple(s for s in shots if s),
        releases=(Release(version=version, date=_date(entry.get("updated") or latest.get("date") or news.get("date")),
                          notes=tuple(n for n in notes if n)),) if version else (),
        homepage=_text(_get(developer if isinstance(developer, dict) else {}, "site", default="") or entry.get("site", "")),
        flatpak_id=_text(entry["id"]), slug=_text(entry.get("slug")) or _text(entry["id"]),
        featured=entry.get("featured") is True,
        tier="luma" if entry.get("first_party") or _text(developer.get("name") if isinstance(developer, dict) else entry.get("dev")) == "Luma" else "listed",
        runs=tuple(entry.get("runs") or ()), mark=_media(mark.get("file") if isinstance(mark, dict) else ""),
        mark_colour=_text(mark.get("bg") if isinstance(mark, dict) else ""),
        package_kind=_text(_get(entry, "format", "pk", default="flatpak")).casefold(), size_label=_size(entry.get("size")),
        reviews=tuple(tuple(r) for r in entry.get("reviews") or [] if isinstance(r, (list, tuple)) and len(r) == 5),
        reach=reach, alternatives=tuple(entry.get("alt") or ()),
        tv_input=_text(entry["tv"].get("input")) if isinstance(entry.get("tv"), dict) else "", installs_total=int(entry.get("downloads", 0) or 0),
    )


def _look(entry: dict) -> Look:
    developer = entry.get("developer") or {}
    return Look(id=_text(entry["id"]), name=_text(entry.get("name")),
                by=_text(developer.get("name") if isinstance(developer, dict) else entry.get("by")),
                seal="luma" if (developer.get("name") if isinstance(developer, dict) else "") == "Luma"
                else "verified" if (developer.get("verified") if isinstance(developer, dict) else False) else "community",
                kind=_text(entry.get("look_kind") or entry.get("kind_detail") or entry.get("type") or "Theme"),
                line=_text(entry.get("summary") or entry.get("line")), about=_text(entry.get("about") or entry.get("description")),
                users=int(entry.get("downloads", 0) or entry.get("n", 0) or 0), runs=tuple(entry.get("runs") or ()),
                art=_media(entry.get("art")), theme=_text(entry.get("theme")), swatch=tuple(entry.get("swatch") or ()),
                ink=bool(entry.get("ink")))


def _tweak(entry: dict) -> Tweak:
    developer = entry.get("developer") or {}
    return Tweak(id=_text(entry["id"]), name=_text(entry.get("name")),
                 by=_text(developer.get("name") if isinstance(developer, dict) else entry.get("by")),
                 seal="luma" if (developer.get("name") if isinstance(developer, dict) else "") == "Luma"
                 else "verified" if (developer.get("verified") if isinstance(developer, dict) else False) else "community",
                 kind=_text(entry.get("tweak_kind") or entry.get("type") or "System"),
                 icon=_text(entry.get("glyph") or entry.get("icon_name") or "sliders-horizontal"),
                 line=_text(entry.get("summary") or entry.get("line")), about=_text(entry.get("about") or entry.get("description")),
                 off=_text(entry.get("off")), on_text=_text(entry.get("onTxt") or entry.get("on_text")),
                 changes=tuple(entry.get("changes") or ()), restart=bool(entry.get("restart")),
                 runs=tuple(entry.get("runs") or ()), users=int(entry.get("downloads", 0) or entry.get("n", 0) or 0))


def parse(document: dict) -> tuple[list[App], list[Look], list[Tweak], dict]:
    """Apps, Looks and Tweaks, and the editors' story if there is one. Entries that don't parse are skipped."""
    apps: list[App] = []
    looks: list[Look] = []
    tweaks: list[Tweak] = []
    items = document.get("items") or document.get("apps") or []
    for entry in items + [dict(x, kind="look") for x in document.get("looks") or [] if isinstance(x, dict)] \
            + [dict(x, kind="tweak") for x in document.get("tweaks") or [] if isinstance(x, dict)]:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        for extra in ("look", "tweak"):  # a Look's or a Tweak's own fields ride in the listing (§13.2, §13.3)
            if isinstance(entry.get(extra), dict):
                entry = {**entry, **entry[extra]}
        try:
            kind = entry.get("kind", "app")
            if kind == "look":
                looks.append(_look(entry))
            elif kind == "tweak":
                tweaks.append(_tweak(entry))
            else:
                apps.append(_app(entry))
        except (KeyError, TypeError, ValueError):
            continue
    today = document.get("today") or {}
    story = today.get("story") if isinstance(today, dict) and isinstance(today.get("story"), dict) else {}
    return apps, looks, tweaks, story


def curated(document: dict) -> tuple[str, ...]:
    """The Hub's own order of apps, for when no install counts rank them: featured listings first, then each
    collection's members (named by listing id or slug), each once."""
    entries = [e for e in document.get("items") or document.get("apps") or []
               if isinstance(e, dict) and isinstance(e.get("id"), str) and e.get("kind", "app") == "app"]
    by_name = {}
    for entry in entries:
        by_name.setdefault(entry["id"], entry["id"])
        if isinstance(entry.get("slug"), str) and entry["slug"]:
            by_name.setdefault(entry["slug"], entry["id"])
    order = [e["id"] for e in entries if e.get("featured") is True]
    for collection in document.get("collections") or []:
        if not isinstance(collection, dict):
            continue
        members = collection.get("applications") or collection.get("apps") or []
        order += [by_name[m] for m in members if isinstance(m, str) and m in by_name]
    return tuple(dict.fromkeys(order))
