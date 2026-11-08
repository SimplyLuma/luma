# SPDX-License-Identifier: Apache-2.0
"""The rules Depot's pages follow, without GTK (handoff 15.5: each is unit tested).

- `bar_for(item, world)`: an item page's action bar, every row of section 8.7.
- `today_popular` / `today_fresh`: Today's two lists (4.2).
- `narrowed`, `narrowed_line`, `filter_count`: the Apps page's category and filters (5.5).
- `short_date`, `rating_line`, `fmt_count`: the words rows use.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .world import Item, World, form

__all__ = ["BarButton", "BarPlan", "Narrowing", "bar_for", "category_name", "filter_count", "fmt_count", "listed_apps",
           "narrowed", "narrowed_line", "popular_title", "rating_line", "recently_updated", "row_action", "short_date",
           "sort_date", "today_fresh", "today_popular", "top_apps"]


# ── words ─────────────────────────────────────────────────────────────────

def fmt_count(n: int) -> str:
    """1.2k, 18k, 940 (the mockup's fmtN)."""
    if n >= 1000:
        text = f"{n / 1000:.0f}" if n >= 10000 else f"{n / 1000:.1f}"
        return text.removesuffix(".0") + "k"
    return str(n)


def sort_date(text: str) -> datetime:
    for pattern in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%b %d"):
        try:
            return datetime.strptime(text, pattern)
        except (TypeError, ValueError):
            continue
    return datetime.min


def short_date(text: str) -> str:
    """"Oct 20, 2026" → "Oct 20" (rows say the day, not the year)."""
    return text.split(",")[0].strip() if text else ""


def rating_line(item: Item) -> str:
    app = item.app
    return f"{app.rating:g} ★" if app is not None and app.rating_count else ""


# ── the bar on an item page (8.7) ─────────────────────────────────────────

@dataclass(frozen=True)
class BarButton:
    key: str             # open · install · update · update-on · try · install-on · share · uninstall · none
    label: str = ""
    icon: str | None = None
    variant: str = "fill"   # key · fill · quiet (a label, no action)
    tooltip: str = ""


@dataclass(frozen=True)
class BarPlan:
    buttons: tuple[BarButton, ...]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(button.key for button in self.buttons)

    @property
    def key_button(self) -> BarButton | None:
        return next((b for b in self.buttons if b.variant == "key"), None)


def bar_for(item: Item, world: World, *, trying: bool = False) -> BarPlan:
    """Main action (the one key), the secondary ones, Install on…, Share, then Uninstall (the mockup's itemActs)."""
    me = world.here
    on = world.on_devices(item)
    mine = me in on
    system = item.is_system
    can_here = world.can_run(item, me)
    others = any(d.id != me and d.form in item.runs for d in world.devices)
    install_on = (BarButton("install-on", "Install on…", "monitor-smartphone"),) if others and not system else ()
    share = BarButton("share", "", "share-2", tooltip="Share")
    uninstall = ((BarButton("uninstall", "", "trash-2", tooltip=f"Uninstall on {world.this_device.name}"),)
                 if mine and not system else ())
    if system:
        return BarPlan((BarButton("open", "Open", None, "key"), share))
    pending = world.pending_for(item.id) if item.kind == "app" else None
    if pending is not None and me in pending.devices:
        return BarPlan((BarButton("update", "Update", "refresh-cw", "key",
                                  f"{pending.from_version} → {pending.to_version} · {pending.size}"),
                        BarButton("open", "Open"), *install_on, share, *uninstall))
    away = tuple(d for d in pending.devices if d != me) if pending is not None else ()
    update_on = ((BarButton("update-on", f"Update on {len(away)}", "refresh-cw",
                            tooltip=", ".join(world.device(d).name for d in away)),) if away else ())
    if mine:
        word = {"tweak": "Installed", "look": "In use"}.get(item.kind, "Open")
        key = BarButton("open", word, None, "key")
    elif not can_here:
        key = BarButton("none", f"No {form(world.this_device.form).name.lower()} version", variant="quiet")
    elif item.app is not None and not item.app.installable:
        key = BarButton("none", "Not available yet", variant="quiet")
    else:
        key = BarButton("install", "Install", "download" if item.kind == "app" else None, "key")
    tries = ()
    if item.kind == "look" and item.look is not None and (item.look.art or item.look.ink or item.look.kind == "Theme"):
        tries = (BarButton("try", "Try", "eye"),)
    return BarPlan((key, *tries, *update_on, *install_on, share, *uninstall))


def row_action(item: Item, world: World) -> tuple[str, str]:
    """A list row's or card's button: ("install", "Install") as Fill, or ("open", word) as Plain (the mockup's act())."""
    if item.is_system:
        return "open", "Open"
    if not world.can_run(item, world.here):
        return "none", f"Not for {form(world.this_device.form).name}"
    if world.here in world.on_devices(item):
        return "open", {"tweak": "Installed", "look": "In use"}.get(item.kind, "Open")
    if item.app is not None and not item.app.installable:
        return "none", "Not available yet"
    return "install", "Install"


# ── Today (4.2) ───────────────────────────────────────────────────────────

def listed_apps(world: World) -> list[Item]:
    """Depot's listings only: never a local app (Android, another package) or a launcher."""
    return [it for it in world.items("app") if world.listed(it.id)]


def _store_apps(world: World) -> list[Item]:
    here = world.this_device.form
    return [it for it in listed_apps(world) if not it.is_system and here in it.runs]


def today_popular(world: World, count: int = 6) -> list[Item]:
    """The top of the catalogue's popularity order that runs here (or its curated order; see `popular_title`)."""
    apps = {it.id: it for it in _store_apps(world)}
    return [apps[i] for i in world.popular if i in apps][:count]


def popular_title(world: World, measured: str) -> str:
    """`measured` ("Popular", "Top apps") only when real install counts rank the list; else "Explore apps"."""
    return measured if world.popular_measured else "Explore apps"


def category_name(world: World, item: Item) -> str:
    category = world.category(item.category)
    return category.name if category is not None else ""


def today_fresh(world: World, count: int = 6) -> list[Item]:
    """New and updated: other developers' apps only, none already in Popular, most recent first."""
    popular = set(world.popular[:count])
    fresh = [it for it in _store_apps(world)
             if it.app.updated and it.seal != "luma" and it.id not in popular]
    return sorted(fresh, key=lambda it: sort_date(it.app.updated), reverse=True)[:count]


def recently_updated(world: World, count: int = 6) -> list[Item]:
    """Apps' Recently added and updated: third-party, most recent first (Popular is not excluded here)."""
    fresh = [it for it in _store_apps(world) if it.app.updated and it.seal != "luma"]
    return sorted(fresh, key=lambda it: sort_date(it.app.updated), reverse=True)[:count]


def top_apps(world: World, count: int = 10) -> list[Item]:
    return today_popular(world, count)


# ── Apps: one category and the filters (5.5) ─────────────────────────────

@dataclass
class Narrowing:
    category: str = ""
    works: list[str] = field(default_factory=list)    # forms the app must ALSO run on (all of them)
    seals: list[str] = field(default_factory=list)    # luma · verified · community

    @property
    def active(self) -> bool:
        return bool(self.category or self.works or self.seals)

    def reset(self) -> None:
        self.category, self.works, self.seals = "", [], []


def filter_count(narrowing: Narrowing) -> int:
    return len(narrowing.works) + len(narrowing.seals)


def narrowed(world: World, narrowing: Narrowing) -> list[Item]:
    """Every app in the category that passes the filters, most popular (most ratings) first."""
    apps = [it for it in _store_apps(world)
            if (not narrowing.category or it.category == narrowing.category)
            and all(f in it.runs for f in narrowing.works)
            and (not narrowing.seals or it.seal in narrowing.seals)]
    return sorted(apps, key=lambda it: it.app.rating_count, reverse=True)


def _and_list(words: list[str]) -> str:
    return words[0] if len(words) < 2 else ", ".join(words[:-1]) + " and " + words[-1]


def narrowed_line(world: World, narrowing: Narrowing) -> tuple[str, str]:
    """The narrowed page's title and line: "That also work on phone and TV", the category's about, or
    "Narrowed by your filters" (only Made by)."""
    category = world.category(narrowing.category) if narrowing.category else None
    title = category.name if category else "Apps"
    if narrowing.works:
        names = [form(f).name for f in narrowing.works]
        return title, "That also work on " + _and_list([n if n == "TV" else n.lower() for n in names])
    if category is not None:
        return title, category.about
    return title, "Narrowed by your filters"


# ── Updates (11) ──────────────────────────────────────────────────────────

#: (key, name, plain fact), the release channel menu's rows (11.3).
CHANNELS = (("stable", "Stable", "Major releases"), ("beta", "Beta", "About once a week"),
            ("nightly", "Nightly", "Every night"))
_SPEED = {"stable": 0, "beta": 1, "nightly": 2}


def _megabytes(size: str) -> float:
    try:
        number = float(size.split()[0])
    except (ValueError, IndexError):
        return 0.0
    return number * (1000 if "GB" in size else 1)


def size_words(megabytes: float) -> str:
    return f"{megabytes / 1000:.1f} GB" if megabytes >= 1000 else f"{round(megabytes)} MB"


def updates_lead(world: World, *, system_waiting: bool, checked: str) -> str:
    """"5 updates · 1.0 GB · checked 6 hours ago", or "Everything is up to date · checked …"."""
    count = len(world.pending) + (1 if system_waiting else 0)
    total = sum(_megabytes(p.size) * len(p.devices) for p in world.pending)
    if system_waiting and world.system is not None:
        total += _megabytes(world.system.size)
    if not count:
        return f"Everything is up to date · checked {checked}"
    return f"{count} update{'s' if count != 1 else ''} · {size_words(total)} · checked {checked}"


@dataclass(frozen=True)
class ChannelDecision:
    title: str
    body: str
    action: str
    toast: str


def channel_decision(current: str, target: str, running: str) -> ChannelDecision:
    """The words that ask first (11.3): joining a faster channel, or leaving for a slower one."""
    name = dict((k, n) for k, n, _f in CHANNELS)
    if _SPEED[target] > _SPEED[current]:
        body = ("This computer will get Beta builds about once a week, before they reach Stable." if target == "beta"
                else "This computer will get a new build of Luma every night. Nightly builds get less testing, "
                     "so things can break.")
        return ChannelDecision(f"Join {name[target]}?", body, f"Join {name[target]}", f"You joined {name[target]}")
    body = (f"This computer stays on {running} until {name[target]} catches up, then gets {name[target]} "
            "releases only.")
    return ChannelDecision(f"Leave {name[current]}?", body, f"Switch to {name[target]}",
                           f"This computer now gets {name[target]} updates")


def devices_line(world: World, devices: tuple[str, ...]) -> str:
    """"On this computer, ThinkPad X1, Fairphone 6, Living room and Kitchen"."""
    names = ["this computer" if d == world.here else world.device(d).name for d in devices]
    return "On " + _and_list(names)
