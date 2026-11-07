# SPDX-License-Identifier: Apache-2.0
"""Everything Depot shows, in one shape: apps, Looks and Tweaks, and the person's world.

The world is the person's devices on their Luma account (Luma Connect), what
is installed on each, what waits to update, what changed lately, and Luma's own
update. Pages ask the world; they never read providers directly, so the same
page draws the approved fixture (tests/fixtures/depot-redesign) and the real
catalogue.

Missing data stays missing (handoff rule 8): a field nobody supplied is empty,
and the page leaves its section out.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from pathlib import Path
from typing import Iterable

from ..providers import App, Release, Screenshot

__all__ = [
    "CategoryInfo", "Device", "Done", "Form", "FORMS", "Item", "Look", "PendingUpdate", "Story", "SystemUpdate",
    "Tweak", "World", "load_fixture",
]


@dataclass(frozen=True)
class Form:
    id: str
    name: str
    icon: str


#: The five device forms, in the one order (desktop, tablet, phone, TV, home).
FORMS: tuple[Form, ...] = (
    Form("desktop", "Desktop", "monitor"), Form("tablet", "Tablet", "tablet"), Form("phone", "Phone", "smartphone"),
    Form("tv", "TV", "tv"), Form("home", "Home", "house"),
)
_FORM = {form.id: form for form in FORMS}


def form(form_id: str) -> Form:
    return _FORM[form_id]


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    form: str
    #: What the device row says when it is neither installed nor unable: "TV · off, installs when it wakes".
    note: str = ""

    @property
    def form_name(self) -> str:
        return _FORM[self.form].name

    @property
    def icon(self) -> str:
        return _FORM[self.form].icon


@dataclass(frozen=True)
class CategoryInfo:
    key: str
    name: str
    about: str
    icon: str
    hue: int
    photo: str


@dataclass(frozen=True)
class Look:
    id: str
    name: str
    by: str
    seal: str
    kind: str          # Theme · Wallpaper · Icons · Sounds · Font · Cursor · Appearance
    line: str
    about: str = ""
    users: int = 0
    runs: tuple[str, ...] = ()
    hue: int = 0
    art: str = ""      # a picture (a wallpaper, or a theme that has one)
    theme: str = ""    # luma · cupertino · redmond · exile · 95
    swatch: tuple[str, ...] = ()
    ink: bool = False


@dataclass(frozen=True)
class Tweak:
    id: str
    name: str
    by: str
    seal: str
    kind: str
    icon: str
    line: str
    about: str = ""
    off: str = ""
    on_text: str = ""
    changes: tuple[str, ...] = ()
    restart: bool = False
    runs: tuple[str, ...] = ()
    users: int = 0
    hue: int = 0
    on: tuple[str, ...] = ()


@dataclass(frozen=True)
class PendingUpdate:
    app: str
    devices: tuple[str, ...]
    from_version: str
    to_version: str
    size: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Done:
    app: str           # an app id, or "luma"
    what: str
    when: str
    where: str


@dataclass(frozen=True)
class SystemUpdate:
    name: str
    size: str
    channel: str
    notes: tuple = ()
    running: str = ""
    checked: str = ""
    up_to_date: bool = False


@dataclass(frozen=True)
class Story:
    kick: str
    title: str
    blurb: str
    app: str
    art: str
    hue: int = 0


def _hash(text: str) -> int:
    """The mockup's hue hash ((h * 31 + c) % 360 from 7), so monograms and rooms land where it put them."""
    h = 7
    for ch in str(text):
        h = (h * 31 + ord(ch)) % 360
    return h


@dataclass(frozen=True)
class Item:
    """An app, a Look or a Tweak, as every page sees it (the mockup's `item()`)."""

    kind: str          # app · look · tweak
    id: str
    name: str
    by: str
    seal: str          # luma · verified · community
    line: str
    runs: tuple[str, ...]
    hue: int
    app: App | None = None
    look: Look | None = None
    tweak: Tweak | None = None

    @property
    def key(self) -> tuple[str, str]:
        return self.kind, self.id

    @property
    def users(self) -> int:
        if self.look is not None:
            return self.look.users
        if self.tweak is not None:
            return self.tweak.users
        return self.app.rating_count if self.app is not None else 0

    @property
    def is_system(self) -> bool:
        """A part of Luma itself (Filer, Viola…): Open and Share only, never uninstalled."""
        return self.app is not None and self.app.package_kind == "system"

    @property
    def category(self) -> str:
        return self.app.category_key if self.app is not None else ""


def seal_of(app: App) -> str:
    if app.developer == "Luma":
        return "luma"
    return "verified" if app.developer_verified else "community"


PACKAGE_WORDS = {"system": "Part of Luma", "luma": "Luma app", "flatpak": "Flatpak", "deb": "Universal .deb",
                 "rpm": "Universal .rpm", "snap": "Snap", "layered": "Layered package"}


@dataclass
class World:
    """The catalogue and the person's devices. `here` is the device Depot runs on."""

    forms: tuple[Form, ...]
    devices: tuple[Device, ...]
    here: str
    categories: tuple[CategoryInfo, ...]
    apps: tuple[App, ...]
    looks: tuple[Look, ...] = ()
    tweaks: tuple[Tweak, ...] = ()
    popular: tuple[str, ...] = ()
    installed: dict[str, tuple[str, ...]] = field(default_factory=dict)
    matched: tuple[str, ...] = ()
    pending: tuple[PendingUpdate, ...] = ()
    done: tuple[Done, ...] = ()
    auto: dict[str, object] = field(default_factory=dict)
    system: SystemUpdate | None = None
    story: Story | None = None
    #: Which Looks are applied on which device (the one in use).
    looks_in_use: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Where files the fixture names live (icons, marks, shots, art).
    assets: Path | None = None
    #: Luma's own apps a person removed from a device (they otherwise come with Luma).
    removed: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Luma's own apps the Library lists (all of Luma's apps come with Luma; these are the ones kept in view).
    kept: tuple[str, ...] = ()
    #: Where a catalogue's media URL is on disk once fetched (None: not yet, so not drawn).
    media: object = None
    #: Names people know that aren't on Luma, and the apps that do the same job (search, 12).
    switching: tuple[tuple[str, tuple[str, ...]], ...] = ()

    # ── lookups ───────────────────────────────────────────────────────────

    def media_path(self, url: str) -> str | None:
        if not url:
            return None
        if self.media is not None:
            return self.media(url)
        return url if Path(url).is_file() else None

    def device(self, device_id: str) -> Device:
        return next(device for device in self.devices if device.id == device_id)

    @property
    def this_device(self) -> Device:
        return self.device(self.here)

    def category(self, key: str) -> CategoryInfo | None:
        return next((c for c in self.categories if c.key == key), None)

    def app(self, app_id: str) -> App | None:
        return next((app for app in self.apps if app.app_id == app_id), None)

    def item(self, kind: str, item_id: str) -> Item | None:
        if kind == "app":
            app = self.app(item_id)
            if app is None:
                return None
            category = self.category(app.category_key)
            line = app.summary or f"{category.name if category else 'An'} app · {PACKAGE_WORDS.get(app.package_kind, '')}"
            return Item("app", app.app_id, app.name, app.developer or "Unknown developer", seal_of(app), line,
                        app.runs or ("desktop", "tablet"), _hash(app.app_id), app=app)
        if kind == "look":
            look = next((x for x in self.looks if x.id == item_id), None)
            return None if look is None else Item("look", look.id, look.name, look.by, look.seal, look.line,
                                                  look.runs, look.hue, look=look)
        tweak = next((x for x in self.tweaks if x.id == item_id), None)
        return None if tweak is None else Item("tweak", tweak.id, tweak.name, tweak.by, tweak.seal, tweak.line,
                                               tweak.runs, tweak.hue, tweak=tweak)

    def items(self, kind: str) -> list[Item]:
        source: Iterable = {"app": self.apps, "look": self.looks, "tweak": self.tweaks}[kind]
        ids = [x.app_id if kind == "app" else x.id for x in source]
        return [item for item in (self.item(kind, i) for i in ids) if item is not None]

    # ── where things are ──────────────────────────────────────────────────

    def on_devices(self, item: Item) -> tuple[str, ...]:
        """The devices it is installed on (or, for a Look, in use on; a Tweak, on)."""
        if item.kind == "app":
            if item.app is not None and item.app.developer == "Luma":
                # Luma's own apps come with Luma: on every device that can run them, unless removed.
                gone = self.removed.get(item.id, ())
                return tuple(d.id for d in self.devices if d.form in item.runs and d.id not in gone)
            return tuple(d.id for d in self.devices if item.id in self.installed.get(d.id, ()))
        if item.kind == "look":
            return self.looks_in_use.get(item.id, ())
        return item.tweak.on if item.tweak is not None else ()

    def can_run(self, item: Item, device_id: str) -> bool:
        return self.device(device_id).form in item.runs

    def pending_for(self, app_id: str) -> PendingUpdate | None:
        return next((p for p in self.pending if p.app == app_id and p.devices), None)

    def update_count(self) -> int:
        """The sidebar's Updates badge: pending updates across all devices, plus one for Luma itself."""
        apps = sum(len(p.devices) for p in self.pending)
        return apps + (1 if self.system is not None and not self.system.up_to_date else 0)

    def with_on(self, item: Item, device_id: str, on: bool) -> "World":
        """The world after installing (or uninstalling) `item` on a device."""
        if item.kind == "app":
            if item.app is not None and item.app.developer == "Luma":
                # Luma's own apps follow `runs`; removing one records it as gone from that device.
                gone = dict(self.removed)
                gone[item.id] = tuple(d for d in gone.get(item.id, ()) if d != device_id) + (() if on else (device_id,))
                return replace(self, removed=gone)
            return self.with_installed(device_id, item.id, on)
        if item.kind == "look":
            uses = dict(self.looks_in_use)
            if on:  # one Look of a kind is in use on a device at a time
                same = {x.id for x in self.looks if item.look is not None and x.kind == item.look.kind}
                for look_id in same:
                    uses[look_id] = tuple(d for d in uses.get(look_id, ()) if d != device_id)
            uses[item.id] = tuple(d for d in uses.get(item.id, ()) if d != device_id) + ((device_id,) if on else ())
            return replace(self, looks_in_use=uses)
        tweaks = tuple(replace(t, on=tuple(d for d in t.on if d != device_id) + ((device_id,) if on else ()))
                       if t.id == item.id else t for t in self.tweaks)
        return replace(self, tweaks=tweaks)

    def with_updated(self, app_id: str, devices: tuple[str, ...]) -> "World":
        pending = tuple(replace(p, devices=tuple(d for d in p.devices if d not in devices)) if p.app == app_id else p
                        for p in self.pending)
        return replace(self, pending=tuple(p for p in pending if p.devices))

    def with_matched(self, app_id: str, keep: bool) -> "World":
        matched = tuple(x for x in self.matched if x != app_id) + ((app_id,) if keep else ())
        return replace(self, matched=matched)

    def with_installed(self, device_id: str, app_id: str, on: bool) -> "World":
        installed = dict(self.installed)
        have = tuple(x for x in installed.get(device_id, ()) if x != app_id)
        installed[device_id] = have + ((app_id,) if on else ())
        return replace(self, installed=installed)


# ── the fixture: the approved mockup's data ───────────────────────────────

_CATEGORY_PHOTOS = {"create": "luma-creatives-painter", "work": "life-sync-terrace", "media": "luma-hero-move",
                    "play": "luma-gamers-evening", "tools": "life-footer"}


def _app(raw: dict, runs: dict) -> App:
    rating = raw.get("rating") or {}
    news = raw.get("news")
    releases = ()
    if raw.get("version") or news:
        version = (news or {}).get("v") or raw.get("version", "")
        releases = (Release(version=version, date=raw.get("updated", "") or (news or {}).get("date", ""),
                            notes=tuple((news or {}).get("items", ()))),)
    mark = raw.get("mark") or {}
    shots = tuple(raw.get("shots", ()))
    return App(
        app_id=raw["id"], name=raw["name"], summary=raw.get("summary", ""),
        description="\n\n".join(raw.get("desc", ())), developer=raw.get("dev", ""),
        categories=(raw.get("cat", ""),) if raw.get("cat") else (), category_key=raw.get("cat", "tools"),
        icon_name=raw.get("icon", ""), licence=raw.get("license", ""),
        rating=float(rating.get("avg", 0) or 0), rating_count=int(rating.get("n", 0) or 0),
        rating_distribution=tuple(rating.get("dist", ())),
        screenshots=tuple(Screenshot(url=key) for key in shots), shot_keys=shots, releases=releases,
        homepage=raw.get("site", ""), developer_verified="organization" if raw.get("verified") else "",
        tier="luma" if raw.get("dev") == "Luma" else ("verified" if raw.get("verified") else "listed"),
        runs=tuple(runs.get(raw["id"], ())), mark=mark.get("file", ""), mark_colour=mark.get("bg", ""),
        package_kind=raw.get("pk", ""), size_label=raw.get("size", ""),
        reviews=tuple(tuple(r) for r in raw.get("reviews", ())), reach=tuple(tuple(r) for r in raw.get("reach", ())),
        alternatives=tuple(raw.get("alt", ())), installable=raw.get("pk") != "system",
        removable=raw.get("pk") != "system",
    )


def _updated(app: App) -> str:
    return app.releases[0].date if app.releases else ""


def load_fixture(path: str | Path, *, here: str = "xps") -> World:
    """The approved mockup's catalogue and world (tests/fixtures/depot-redesign/world.json)."""
    path = Path(path)
    data = json.loads(path.read_text())
    runs = data.get("runs", {})
    apps = tuple(_app(raw, runs) for raw in data["apps"])
    # An app with an update date and no release still has a date (the mockup keeps them apart).
    apps = tuple(app if app.releases or not raw.get("updated")
                 else replace(app, releases=(Release(version="", date=raw["updated"]),))
                 for app, raw in zip(apps, data["apps"]))
    categories = tuple(CategoryInfo(key, c["name"], c.get("about", ""), c.get("icon", ""), int(c.get("hue", 0)),
                                    _CATEGORY_PHOTOS.get(key, "")) for key, c in data["cats"].items())
    looks = tuple(Look(id=x["id"], name=x["name"], by=x.get("by", ""), seal=x.get("seal", "community"),
                       kind=x.get("kind", ""), line=x.get("line", ""), about=x.get("about", ""),
                       users=int(x.get("n", 0)), runs=tuple(x.get("runs", ())), hue=int(x.get("hue", 0)),
                       art=Path(x["art"]).stem if x.get("art") else "", theme=x.get("theme", ""),
                       swatch=tuple(x.get("swatch", ())), ink=bool(x.get("ink")))
                  for x in data.get("looks", ()))
    tweaks = tuple(Tweak(id=x["id"], name=x["name"], by=x.get("by", ""), seal=x.get("seal", "community"),
                         kind=x.get("kind", ""), icon=x.get("icon", "sliders-horizontal"), line=x.get("line", ""),
                         about=x.get("about", ""), off=x.get("off", ""), on_text=x.get("onTxt", ""),
                         changes=tuple(x.get("changes", ())), restart=bool(x.get("restart")),
                         runs=tuple(x.get("runs", ())), users=int(x.get("n", 0)), hue=int(x.get("hue", 0)),
                         on=tuple(x.get("on", ())))
                   for x in data.get("tweaks", ()))
    devices = tuple(Device(d["id"], d["name"], d["form"], d.get("note", "")) for d in data["devices"])
    pending = tuple(PendingUpdate(p["app"], tuple(p["devs"]), p.get("from", ""), p.get("to", ""), p.get("size", ""),
                                  tuple(p.get("notes", ()))) for p in data.get("pending", ()))
    done = tuple(Done(*row[:4]) for row in data.get("done", ()))
    sysd = data.get("system")
    system = None if not sysd else SystemUpdate(sysd["name"], sysd.get("size", ""), sysd.get("channel", ""),
                                                tuple(tuple(n) for n in sysd.get("notes", ())),
                                                sysd.get("running", ""), sysd.get("checked", ""))
    raw_story = next((s for s in data.get("today", ()) if s.get("kind") == "everywhere"), None)
    story = None if raw_story is None else Story(raw_story["kick"], raw_story["title"], raw_story["blurb"],
                                                 raw_story["app"], Path(raw_story.get("art", "")).stem,
                                                 int(raw_story.get("hue", 0)))
    # The mockup applies Prism and the Luma theme everywhere they run (its onDevices for Looks).
    in_use = {look.id: tuple(d.id for d in devices if d.form in look.runs)
              for look in looks if look.id in ("prism", "theme-luma")}
    return World(forms=FORMS, devices=devices, here=here, categories=categories, apps=apps, looks=looks,
                 tweaks=tweaks, popular=tuple(data.get("popular", ())),
                 installed={k: tuple(v) for k, v in data.get("installed", {}).items()},
                 matched=tuple(data.get("matched", ())), pending=pending, done=done, auto=dict(data.get("auto", {})),
                 system=system, story=story, looks_in_use=in_use, assets=path.parent,
                 switching=tuple((name, tuple(ids)) for name, ids in data.get("switching", ())),
                 kept=tuple(data.get("kept", ("tide", "photos", "messages", "write", "notes"))))
