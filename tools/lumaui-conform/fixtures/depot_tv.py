# SPDX-License-Identifier: Apache-2.0
"""lumaui-conform fixture: Depot (src/luma-depot) in TV posture with synthetic state, no services.

Run by the conform harness as `bin/run --module depot_tv` (scenario tv-depot) with
LUMA_POSTURE=tv; LUMA_CONFORM_DEPOT_STATE picks what the Design Center's TV mockup
(mockups/luma-tv-depot.html) shows: discover, apps, games, updates, search, app (Tide).

The apps mirror the mockup's TV catalogue (apps/depot/catalogue.js): the same names,
developers, summaries, versions and sizes, every one installed (Depot on a TV lists the
installed apps for now), Kodi with an update waiting. Their TV facts are the mockup's.
Nothing here reads this machine's catalogue, installation or update agent.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("LUMA_POSTURE", "tv")
os.environ.setdefault("LUMA_DEPOT_PREVIEW", "1")
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib  # noqa: E402

from luma_depot import preview, simulated_install, tv_facts, window as W  # noqa: E402
from luma_depot.providers import App, Catalogue, InstalledApp, Release, Result  # noqa: E402

#  id, name, developer, shelf, summary, version, size MB, rating, count, input
APPS = (
    ("tide", "Tide", "Luma", "Media", "Your music, from everywhere you keep it", "1.0.4", 38, 4.9, 1204, "remote"),
    ("weather", "Weather", "Luma", "Tools", "The sky, at a glance", "1.0", 6, 4.8, 311, "remote"),
    ("photos", "Photos", "Luma", "Media", "Every photo you’ve taken, beautifully kept", "1.0.3", 44, 4.8, 902, "remote"),
    ("youtube", "YouTube", "Luma", "Media", "Watch YouTube, made for Luma", "0.9", 27, 0, 0, "remote"),
    ("kodi", "Kodi", "Team Kodi", "Media", "Your media centre", "21.2", 96, 4.5, 288, "remote"),
    ("vlc", "VLC", "VideoLAN", "Media", "Plays everything", "3.0.21", 53, 4.7, 3310, "remote"),
    ("spotify", "Spotify", "Spotify", "Media", "Music and podcasts, streamed", "1.2.48", 180, 4.4, 5412, "remote"),
    ("steam", "Steam", "Valve", "Play", "Your games library", "1.0.0.82", 4, 4.6, 5120, "controller"),
    ("heroic", "Heroic", "Heroic Games Launcher", "Play", "Your Epic, GOG and Amazon games", "2.18", 326, 4.5, 311,
     "controller"),
    ("moonlight", "Moonlight", "Moonlight Game Streaming", "Play", "Stream games from your PC", "6.1", 8, 4.6, 190,
     "controller"),
    ("supertux", "SuperTux", "SuperTux Team", "Play", "A classic side-scroller", "0.6.3", 160, 0, 0, "controller"),
    ("ari", "Ari", "Luma", "Tools", "Ask anything, on this computer", "0.8", 1200, 0, 0, "keyboard"),
    ("discord", "Discord", "Discord", "Work", "Talk, play, hang out", "1.0.159", 92, 4.3, 2018, "keyboard"),
)
UPDATES = {"kodi": "21.3"}
KEYWORDS = {"tide": ("iTunes",), "spotify": ("iTunes",)}


def _apps():
    for key, name, dev, shelf, summary, version, size, rating, count, _input in APPS:
        yield App("catalog:" + key, name, summary, developer=dev, categories=(shelf,), slug=key,
                  tier="luma" if dev == "Luma" else "listed", developer_verified="organization" if dev == "Luma" else "",
                  installed_bytes=size * 1_000_000, rating=rating, rating_count=count,
                  releases=(Release(version),), source_title="Luma" if dev == "Luma" else "Flathub")


CATALOGUE = Catalogue(tuple(_apps()), (), next(a for a in _apps() if a.slug == "tide"))
INSTALLED = tuple(InstalledApp(app.app_id, app.version, app.installed_bytes, update_version=UPDATES.get(app.slug, ""),
                               update_commit="b" if app.slug in UPDATES else "", commit="a", app=app, managed=True)
                  for app in CATALOGUE.apps)
FACTS = {"catalog:" + entry[0]: (entry[-1], entry[3] == "Play") for entry in APPS}

preview.PreviewCatalogue.load_catalogue = lambda self, callback, cancellable=None: GLib.idle_add(
    lambda: callback(Result(CATALOGUE)) and False)
simulated_install.SimulatedInstallationProvider.installed = lambda self, callback, cancellable=None, **_k: \
    GLib.idle_add(lambda: callback(Result(INSTALLED)) and False)


def _tv_app(app, *, record=None):
    found = FACTS.get(app.app_id)
    if found is None:
        return None
    given, game = found
    return tv_facts.TvApp(app, tv_facts.TvFacts(True, given, "catalogue"), None, KEYWORDS.get(app.slug, ()), "",
                          game, None)


tv_facts.tv_app = _tv_app
tv_facts.catalogue_entry = lambda app: None


def build(app: Adw.Application) -> None:
    from luma_appkit import install_appkit, install_lumaui
    install_appkit()
    install_lumaui()
    W._install_depot_style()
    window = W.DepotWindow(app)
    window.set_default_size(1920, 1080)
    window.present()
    state = os.environ.get("LUMA_CONFORM_DEPOT_STATE", "discover")

    def go() -> bool:
        tv = window.tv
        if tv is None:
            return GLib.SOURCE_CONTINUE
        if state == "app":
            tv.open_app(next(i for i in tv.items if i.app.slug == "tide"))
        elif state == "search":
            tv._enter_page("search")
            tv._field.set_value("itunes")
        elif state != "discover":
            tv.focus.focus(next(t for t in tv.tabs if t.tv_key == "tab:" + state))
        return GLib.SOURCE_REMOVE
    GLib.timeout_add(600, go)


def main() -> int:
    app = Adw.Application(application_id="org.projectluma.Depot", flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.connect("activate", build)
    return app.run(sys.argv[:1])


if __name__ == "__main__":
    raise SystemExit(main())
