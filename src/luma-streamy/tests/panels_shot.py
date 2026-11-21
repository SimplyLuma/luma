# SPDX-License-Identifier: Apache-2.0
"""Check runs for the Destinations, Stats and Chat panels, in a LumaUI window of their own with
fixture rows, live states, stats and messages (no network, no encoder).

    PANELS_SHOT=out.png PANELS_WIDTH=1280|360 [PANELS_OPEN=destinations|stats|chat|add|edit-twitch|edit-custom]
        python3 tests/panels_shot.py

Writes out.png and out.json (the boxes tests/panels_conform.py checks).
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("LUMA_SECRETS", "memory")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from luma_appkit import (  # noqa: E402
    ActionCenter, AppWindow, CommandRegistry, Dock, DockTile, Island, ToastHost,
)

from luma_streamy.destinations import DestinationStore  # noqa: E402
from luma_streamy.destinations_panel import DestinationsPanel  # noqa: E402
from luma_streamy.shots import _box, render  # noqa: E402
from luma_streamy.output_graph import RowStats  # noqa: E402

OUT = os.environ.get("PANELS_SHOT", "/tmp/panels.png")
WIDTH = int(os.environ.get("PANELS_WIDTH", "1280"))
HEIGHT = int(os.environ.get("PANELS_HEIGHT", "800" if WIDTH > 560 else "760"))
OPEN = os.environ.get("PANELS_OPEN", "")


class FakeOutputs:
    """What the panels read from Outputs, with fixed numbers."""

    def __init__(self):
        from gi.repository import GObject

        class Signals(GObject.Object):
            __gsignals__ = {"row-state": (GObject.SignalFlags.RUN_FIRST, None, (str, str, str)),
                            "tick": (GObject.SignalFlags.RUN_FIRST, None, ()),
                            "problem": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

        self.signals = Signals()
        self.stats_of = {}

    def connect(self, *args):
        return self.signals.connect(*args)

    def emit(self, *args):
        self.signals.emit(*args)

    def stats(self, row_id):
        return self.stats_of.get(row_id)

    def live(self):
        return bool(self.stats_of)

    def stop(self, row_id):
        pass


def fixture(data: Path, panel_secrets) -> DestinationStore:
    store = DestinationStore.load(data / "destinations.json")
    twitch = store.add("twitch")
    twitch.account = "NovaPlays"
    youtube = store.add("youtube")
    kick = store.add("kick", "vertical")
    kick.enabled = False
    custom = store.add("custom")
    custom.name, custom.server = "Studio relay", "srt://relay.example.org:9000?streamid=publish:studio"
    tiktok = store.add("tiktok", "vertical")
    tiktok.server = "rtmp://push.example.org/live"
    store.rows[-1].enabled = True
    for row in store.rows:
        store.update(row)
    panel_secrets.store(twitch.id, "stream-key", "live_1")
    panel_secrets.store(tiktok.id, "stream-key", "k")
    return store


def main() -> None:
    data = Path(tempfile.mkdtemp())
    app = Adw.Application(application_id="org.projectluma.StreamyPanels")

    def activate(_app):
        from luma_appkit.platform import secret_store

        secrets = secret_store("org.projectluma.Streamy.Destination", memory=True)
        store = fixture(data, secrets)
        outputs = FakeOutputs()
        window = AppWindow(application=app, app_id="org.projectluma.Streamy", title="Streamy",
                           icon_name="org.projectluma.Streamy", commands=CommandRegistry(), default_width=WIDTH,
                           default_height=HEIGHT, minimum_width=360, minimum_height=480)
        island = Island()
        host = ToastHost(island)
        center = ActionCenter().attach(host)
        from luma_appkit import BarAction

        center.show_bar([BarAction("radio-tower", "Go live", primary=True, on_activate=lambda: None)])
        destinations = DestinationsPanel(store, center=center, host=host, outputs=outputs, secrets=secrets)
        rows = {r.platform: r for r in store.rows}
        live = RowStats(state="live", bitrate_kbps=6012, started=time.monotonic() - 3725,
                        history=[5800 + (i * 37) % 400 for i in range(60)])
        outputs.stats_of = {rows["twitch"].id: live,
                            rows["custom"].id: RowStats(state="reconnecting", reconnects=3,
                                                        words="The server refused the connection"),
                            rows["record"].id: RowStats(state="live", bitrate_kbps=12000,
                                                        started=time.monotonic() - 3725)}
        outputs.emit("row-state", rows["twitch"].id, "live", "Live · 6.0 Mb/s")
        outputs.emit("row-state", rows["custom"].id, "reconnecting", "Reconnecting… The server refused the connection")
        outputs.emit("row-state", rows["record"].id, "live", "")
        tiles = [DockTile("preview", "Preview", "monitor", Gtk.Box(hexpand=True, vexpand=True), main=True),
                 DockTile("destinations", "Destinations", "radio-tower", destinations, phone="drawer",
                          actions=destinations.actions)]
        panels = {"destinations": destinations}
        try:
            from luma_streamy.stats_panel import StatsPanel, fixture_sampler

            stats = StatsPanel(outputs=outputs, store=store, sampler=fixture_sampler())
            tiles.append(DockTile("stats", "Stats", "activity", stats, phone="drawer", actions=stats.actions))
            panels["stats"] = stats
        except ImportError:
            pass
        try:
            from luma_streamy.chat_panel import ChatPanel, fixture_feed

            chat = ChatPanel(feed=fixture_feed(), center=center, host=host)
            tiles.append(DockTile("chat", "Chat", "message-circle", chat, phone="drawer", actions=chat.actions))
            panels["chat"] = chat
        except ImportError:
            pass
        right = "destinations"
        if "stats" in panels and "chat" in panels:
            right = {"split": "v", "ratio": 0.5, "start": "destinations",
                     "end": {"split": "h", "ratio": 0.5, "start": "stats", "end": "chat"}}
        dock = Dock(tiles, layout={"split": "h", "ratio": 0.34, "start": "preview", "end": right})
        island.append(dock)
        window.set_body(host)
        window.present()

        def exact() -> bool:
            w, h = window.get_width(), window.get_height()
            if (w, h) != (WIDTH, HEIGHT) and w and h:
                dw, dh = window.get_default_size()
                window.set_default_size(dw + WIDTH - w, dh + HEIGHT - h)
            return False

        for when in (300, 1200, 2200):
            GLib.timeout_add(when, exact)

        def act() -> bool:
            if OPEN in ("destinations", "stats", "chat") and WIDTH < 560:
                dock.open_drawer(OPEN)
            elif OPEN == "add":
                destinations.add_menu()
            elif OPEN.startswith("edit-"):
                platform = OPEN.split("-", 1)[1]
                destinations.edit(rows[platform].id)
            return False

        GLib.timeout_add(2600, act)
        tries = [0]

        def shoot() -> bool:
            tries[0] += 1
            if tries[0] == 1:
                try:
                    json.dump(measure(window, panels, destinations), open(OUT.rsplit(".", 1)[0] + ".json", "w"),
                              indent=1)
                except Exception as error:  # a check run: say what broke, keep the picture
                    print(f"measure failed: {error!r}")
            if render(window, OUT) or tries[0] > 40:
                window.close()
                app.quit()
                return False
            return True

        GLib.timeout_add(int(os.environ.get("PANELS_DELAY", "4500")), shoot)

    app.connect("activate", activate)
    app.run([])


def _labels(widget):
    found = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label) and child.get_mapped():
            found.append(child)
        found += _labels(child)
        child = child.get_next_sibling()
    return found


def measure(window, panels, destinations) -> dict:
    rows = []
    for row_id, widget in destinations._rows.items():
        if not widget.get_mapped():
            continue
        labels = [lab for lab in _labels(widget) if lab.get_label()]
        rows.append({"id": row_id, "row": _box(widget, window), "lead": _box(widget.get_child().get_first_child()
                                                                              if widget.get_child() else None, window),
                     "labels": [{"text": lab.get_label(), "box": _box(lab, window),
                                 "ellipsized": lab.get_layout().is_ellipsized()} for lab in labels],
                     "chip": _box(widget.chip, window), "switch": _box(widget.switch, window)})
    return {"window": [window.get_width(), window.get_height()], "requested_width": WIDTH,
            "panels": {k: _box(p, window) for k, p in panels.items()}, "destination_rows": rows,
            "stats": panels["stats"].measure(window) if "stats" in panels and hasattr(panels["stats"], "measure") else None,
            "chat": panels["chat"].measure(window) if "chat" in panels and hasattr(panels["chat"], "measure") else None,
            "bar": _box(destinations.center, window), "grown": destinations.center.grown}


if __name__ == "__main__":
    main()
