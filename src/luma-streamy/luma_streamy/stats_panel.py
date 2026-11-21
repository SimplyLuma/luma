# SPDX-License-Identifier: Apache-2.0
"""The Stats panel: the session, every live destination, each canvas, and the computer.

Sampled at most once a second, and only while the panel is shown or something is live (the
outputs' tick drives it then). Destinations carry a bitrate sparkline; the computer's numbers are
meters.

    panel = StatsPanel(outputs=outputs, store=dest_store)
    DockTile("stats", "Stats", "activity", panel, phone="drawer", actions=panel.actions)
    panel.set_canvas("landscape", fps=30.0, frame_ms=12.4)   # from the engine, per canvas
"""
from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from luma_appkit import NavigationList, ProgressLine, RowLead, SectionLabel, SidebarRow, Sparkline  # noqa: E402

from . import output_graph as og  # noqa: E402
from .destinations import DestinationStore  # noqa: E402
from .destinations_panel import canvas_label, lead_for, recordings_dir  # noqa: E402
from .stats import Sampler, System, format_bytes, percent  # noqa: E402

SYSTEM_TITLES = {"cpu": "CPU", "gpu": "GPU", "encoder": "Video encoder", "ram": "Memory", "disk": "Disk"}

#: A meter turns amber, then red, past these.
WARN, DANGER = 0.75, 0.9


def tone(value: float | None) -> str:
    if value is None or value < WARN:
        return "accent"
    return "warning" if value < DANGER else "danger"


class _Section:
    def __init__(self, panel: "StatsPanel", title: str) -> None:
        self.label = SectionLabel(title)
        self.nav = NavigationList("files")
        self.nav.list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.rows: dict[str, SidebarRow] = {}
        panel.box.append(self.label)
        panel.box.append(self.nav)

    def row(self, key: str, title: str, *, lead: Gtk.Widget, trail: Gtk.Widget | None = None) -> SidebarRow:
        row = self.rows.get(key)
        if row is None:
            row = SidebarRow(title, lead=lead, subtitle=" ", trail=trail)
            row.set_activatable(False)
            row.trail_widget = trail
            self.rows[key] = row
            self.nav.list.append(row)
        return row

    def keep(self, keys: set[str]) -> None:
        for key in list(self.rows):
            if key not in keys:
                self.nav.list.remove(self.rows.pop(key))

    def set_visible(self, visible: bool) -> None:
        self.label.set_visible(visible)
        self.nav.set_visible(visible)


def meter() -> ProgressLine:
    line = ProgressLine(0.0, size="row", tone="accent")
    line.set_size_request(96, -1)
    line.set_valign(Gtk.Align.CENTER)
    return line


class StatsPanel(Gtk.Box):
    __gtype_name__ = "StreamyStatsPanel"

    def __init__(self, *, outputs=None, store: DestinationStore | None = None, sampler=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.add_css_class("streamy-stats")
        self.outputs, self.store = outputs, store
        self.sampler = sampler or Sampler(recordings_dir())
        self.actions: list[Gtk.Widget] = []
        self.canvases: dict[str, tuple[float, float]] = {}
        self.session_started: float | None = None
        self._timer = 0
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(self.box)
        self.append(scroll)
        self.session = _Section(self, "Session")
        self.destinations = _Section(self, "Destinations")
        self.canvas_section = _Section(self, "Canvases")
        self.system = _Section(self, "Computer")
        self._meters = {}
        for key, icon in (("cpu", "cpu"), ("gpu", "gauge"), ("encoder", "film"), ("ram", "memory-stick"),
                          ("disk", "hard-drive")):
            title = SYSTEM_TITLES[key]
            line = meter()
            self._meters[key] = line
            self.system.row(key, title, lead=RowLead.icon(icon), trail=line)
        self.connect("map", lambda *_a: self._schedule())
        self.connect("unmap", lambda *_a: self._schedule())
        if outputs is not None:
            outputs.connect("tick", lambda *_a: self.refresh())
            outputs.connect("row-state", lambda *_a: self._schedule())
        self.refresh()

    # ── Sampling: ≤1 Hz, only while shown or live ──

    def _live(self) -> bool:
        return self.outputs is not None and self.outputs.live()

    def _schedule(self) -> None:
        wanted = self.get_mapped() and not self._live()  # while live, the outputs' tick drives it
        if wanted and not self._timer:
            self._timer = GLib.timeout_add_seconds(1, self._on_timer)
            self.refresh()
        elif not wanted and self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _on_timer(self) -> bool:
        if not self.get_mapped() or self._live():
            self._timer = 0
            return False
        self.refresh()
        return True

    def set_canvas(self, canvas: str, *, fps: float | None, frame_ms: float | None) -> None:
        """The engine's numbers for one canvas (None: not composited)."""
        if fps is None:
            self.canvases.pop(canvas, None)
        else:
            self.canvases[canvas] = (fps, frame_ms or 0.0)

    # ── Drawing ──

    def refresh(self) -> None:
        self._session()
        self._destinations()
        self._canvases()
        self._system(self.sampler.sample())

    def _session(self) -> None:
        live = rec = None
        if self.outputs is not None and self.store is not None:
            for row in self.store.rows:
                stats = self.outputs.stats(row.id)
                if stats is None or stats.state == "off" or stats.started is None:
                    continue
                if row.is_stream:
                    live = min(live or stats.started, stats.started)
                else:
                    rec = stats.started
        now = time.monotonic()
        keys = set()
        for key, title, icon, since in (("live", "Live", "radio-tower", live), ("rec", "Recording", "circle-dot", rec)):
            if since is None:
                continue
            keys.add(key)
            self.session.row(key, title, lead=RowLead.icon(icon)).set_subtitle(_clock(now - since))
        if not keys:
            keys.add("idle")
            self.session.row("idle", "Not live", lead=RowLead.icon("radio-tower")).set_subtitle(
                "Go live or record to see each destination here")
        self.session.keep(keys)

    def _destinations(self) -> None:
        keys = set()
        if self.outputs is not None and self.store is not None:
            for row in self.store.rows:
                stats = self.outputs.stats(row.id)
                if stats is None or stats.state == "off":
                    continue
                keys.add(row.id)
                spark = self.destinations.rows[row.id].trail_widget if row.id in self.destinations.rows else \
                    Sparkline(capacity=og.HISTORY, label=f"{row.label} bitrate")
                widget = self.destinations.row(row.id, row.account or row.label, lead=lead_for(row), trail=spark)
                widget.set_subtitle(destination_words(stats))
                spark.set_values(stats.history[-og.HISTORY:])
                spark.set_tone("danger" if stats.state in ("reconnecting", "error") else
                               "warning" if stats.dropped_network or stats.dropped_encoder else "accent")
        self.destinations.keep(keys)
        self.destinations.set_visible(bool(keys))

    def _canvases(self) -> None:
        keys = set()
        for canvas, (fps, frame_ms) in self.canvases.items():
            keys.add(canvas)
            row = self.canvas_section.row(canvas, canvas_label(canvas), lead=RowLead.icon(
                "rectangle-horizontal" if canvas == "landscape" else "smartphone"))
            row.set_subtitle(f"{fps:.0f} fps · {frame_ms:.1f} ms a frame")
        self.canvas_section.keep(keys)
        self.canvas_section.set_visible(bool(keys))

    def _system(self, system: System) -> None:
        ram = system.ram_used / system.ram_total if system.ram_total else None
        disk_used = 1 - system.disk_free / system.disk_total if system.disk_total else None
        values = {
            "cpu": (system.cpu, percent(system.cpu)),
            "gpu": (system.gpu, percent(system.gpu) if system.gpu is not None else "Not reported by this GPU"),
            "encoder": (system.encoder, percent(system.encoder) if system.encoder is not None else
                        ("Idle" if system.gpu is not None else "Not reported by this GPU")),
            "ram": (ram, f"{format_bytes(system.ram_used)} of {format_bytes(system.ram_total)}"
                    if system.ram_total else "—"),
            "disk": (disk_used, " · ".join(part for part in (
                f"Writing {format_bytes(system.disk_write)}/s" if system.disk_write else "",
                f"{format_bytes(system.disk_free)} free" if system.disk_free is not None else "") if part) or "—"),
        }
        for key, (value, words) in values.items():
            row = self.system.rows[key]
            row.set_subtitle(words)
            line = self._meters[key]
            line.set_visible(value is not None)
            line.set_fraction(max(0.0, min(1.0, value or 0.0)))
            line.set_tone(tone(value) if key != "disk" else tone(value if value and value > 0.9 else 0))
            line.set_label(f"{SYSTEM_TITLES[key]}: {words}")

    def measure(self, root: Gtk.Widget) -> dict:
        from .shots import _box

        out = {}
        for name, section in (("session", self.session), ("destinations", self.destinations),
                              ("canvases", self.canvas_section), ("system", self.system)):
            out[name] = [{"key": k, "row": _box(r, root), "trail": _box(r.trail_widget, root)
                          if getattr(r, "trail_widget", None) is not None else None} for k, r in section.rows.items()]
        return out


def destination_words(stats: og.RowStats) -> str:
    if stats.state != "live":
        return og.state_words(stats) + (f" · {stats.reconnects} reconnects" if stats.reconnects else "")
    parts = [og.format_rate(stats.bitrate_kbps)]
    dropped = stats.dropped_network + stats.dropped_encoder
    if dropped:
        parts.append(f"{dropped} dropped ({stats.dropped_network} network, {stats.dropped_encoder} encoder)")
    if stats.rtt_ms is not None:
        parts.append(f"RTT {stats.rtt_ms:.0f} ms")
    if stats.reconnects:
        parts.append(f"{stats.reconnects} reconnect{'s' if stats.reconnects > 1 else ''}")
    return " · ".join(parts)


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


class _FixedSampler:
    def __init__(self, system: System) -> None:
        self.system = system

    def sample(self) -> System:
        return self.system


def fixture_sampler() -> _FixedSampler:
    """Fixed numbers for check runs."""
    return _FixedSampler(System(cpu=0.31, gpu=0.58, encoder=0.42, engines={"render": 0.58, "video": 0.42},
                                ram_used=9_100_000_000, ram_total=16_000_000_000, disk_write=1_600_000,
                                disk_free=212_000_000_000, disk_total=512_000_000_000))
