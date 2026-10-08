# SPDX-License-Identifier: Apache-2.0
"""Displays as a person thinks of them, and as Mutter needs them.

Pure Python: it reads Mutter's DisplayConfig state and monitors.xml, lays
displays out, and builds the configuration to apply. The window and the D-Bus
calls live elsewhere, so every rule here is testable without a session.
"""
from __future__ import annotations

import re
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from pathlib import Path

# Mutter's transforms: 0 normal, 1 rotated left, 2 upside down, 3 rotated right.
TRANSFORMS = {0: "Landscape", 1: "Portrait, Left", 3: "Portrait, Right", 2: "Landscape, Upside Down"}
_XML_ROTATION = {"normal": 0, "left": 1, "upside_down": 2, "right": 3}
VENDOR_NOISE = re.compile(
    r"\b(electronics?|electric|company|corporation|corp|inc|co|ltd|limited|technology|technologies|"
    r"computer|display|displays|international|group|gmbh|ag|s\.?a)\b\.?,?", re.I)


@dataclass(frozen=True, order=True)
class Spec:
    connector: str
    vendor: str
    product: str
    serial: str


def key_for(specs) -> tuple[Spec, ...]:
    return tuple(sorted(set(specs)))


def key_text(key: tuple[Spec, ...]) -> str:
    return "|".join(f"{s.vendor}:{s.product}:{s.serial}@{s.connector}" for s in key)


@dataclass(frozen=True)
class Mode:
    id: str
    width: int
    height: int
    rate: float
    preferred_scale: float
    scales: tuple[float, ...]
    current: bool = False
    preferred: bool = False
    variable: bool = False


@dataclass(frozen=True)
class Monitor:
    spec: Spec
    modes: tuple[Mode, ...]
    builtin: bool
    display_name: str

    @property
    def current_mode(self) -> Mode | None:
        return next((m for m in self.modes if m.current), None)

    @property
    def preferred_mode(self) -> Mode:
        return next((m for m in self.modes if m.preferred and not m.variable), None) or \
            next((m for m in self.modes if not m.variable), self.modes[0])

    def mode(self, mode_id: str) -> Mode | None:
        return next((m for m in self.modes if m.id == mode_id), None)


@dataclass
class Placement:
    """One display in a layout: where it sits, how it draws, whether it leads."""
    connector: str
    mode_id: str
    x: int
    y: int
    scale: float
    transform: int = 0
    primary: bool = False


@dataclass
class Layout:
    placements: list[Placement]
    mirror: bool = False  # every placement shares one logical monitor

    def copy(self) -> "Layout":
        return Layout([replace(p) for p in self.placements], self.mirror)


@dataclass
class State:
    serial: int
    monitors: list[Monitor]
    layout: Layout
    layout_mode: int = 1  # 1 logical, 2 physical
    supports_mirroring: bool = True
    can_change_layout_mode: bool = False

    @property
    def key(self) -> tuple[Spec, ...]:
        return key_for(m.spec for m in self.monitors)

    def monitor(self, connector: str) -> Monitor:
        return next(m for m in self.monitors if m.spec.connector == connector)


@dataclass
class Arrangement:
    """A layout Mutter has remembered for one set of displays."""
    key: tuple[Spec, ...]
    placements: list[tuple[Spec, int, int, int, int, float, int, bool]] = field(default_factory=list)
    # (spec, x, y, width, height, scale, transform, primary) per enabled display
    disabled: list[Spec] = field(default_factory=list)


# ── Reading ──────────────────────────────────────────────────────────────

def parse_state(value) -> State:
    """GetCurrentState's unpacked result."""
    serial, monitors_raw, logical_raw, properties = value
    monitors = []
    for (connector, vendor, product, serial_number), modes_raw, props in monitors_raw:
        modes = tuple(
            Mode(id=mid, width=w, height=h, rate=rate, preferred_scale=pscale, scales=tuple(scales),
                 current=bool(mprops.get("is-current", False)), preferred=bool(mprops.get("is-preferred", False)),
                 variable=mprops.get("refresh-rate-mode") == "variable")
            for mid, w, h, rate, pscale, scales, mprops in modes_raw)
        monitors.append(Monitor(Spec(connector, vendor, product, serial_number), modes,
                                bool(props.get("is-builtin", False)), props.get("display-name", connector)))
    placements, mirror = [], False
    by_connector = {m.spec.connector: m for m in monitors}
    for x, y, scale, transform, primary, members, _props in logical_raw:
        if len(members) > 1:
            mirror = True
        for connector, *_ in members:
            monitor = by_connector.get(connector)
            mode = monitor.current_mode if monitor else None
            if mode is None:
                continue
            placements.append(Placement(connector, mode.id, x, y, scale, transform, primary))
    layout = Layout(placements, mirror)
    if not mirror:
        single_primary(layout)
    return State(serial, monitors, layout,
                 layout_mode=int(properties.get("layout-mode", 1)),
                 supports_mirroring=bool(properties.get("supports-mirroring", True)),
                 can_change_layout_mode=bool(properties.get("supports-changing-layout-mode", False)))


def _spec(element: ET.Element) -> Spec:
    return Spec(*(element.findtext(tag, "") or "" for tag in ("connector", "vendor", "product", "serial")))


def parse_monitors_xml(text: str) -> list[Arrangement]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    arrangements = []
    for configuration in root.findall("configuration"):
        specs, placements, disabled = [], [], []
        for logical in configuration.findall("logicalmonitor"):
            x = int(float(logical.findtext("x", "0")))
            y = int(float(logical.findtext("y", "0")))
            scale = float(logical.findtext("scale", "1"))
            rotation = logical.find("transform/rotation")
            transform = _XML_ROTATION.get(rotation.text if rotation is not None else "normal", 0)
            primary = (logical.findtext("primary", "no") or "no").strip() == "yes"
            for monitor in logical.findall("monitor"):
                element = monitor.find("monitorspec")
                if element is None:
                    continue
                spec = _spec(element)
                specs.append(spec)
                width = int(monitor.findtext("mode/width", "0") or 0)
                height = int(monitor.findtext("mode/height", "0") or 0)
                placements.append((spec, x, y, width, height, scale, transform, primary))
        for element in configuration.findall("disabled/monitorspec"):
            spec = _spec(element)
            specs.append(spec)
            disabled.append(spec)
        if specs:
            arrangements.append(Arrangement(key_for(specs), placements, disabled))
    return arrangements


def read_arrangements(path: Path | None = None) -> list[Arrangement]:
    if path is None:
        # Mutter owns this file. Flatpak grants only this one read-only host
        # configuration, while arrangement labels remain private app state.
        key = "HOST_XDG_CONFIG_HOME" if os.environ.get("FLATPAK_ID") else "XDG_CONFIG_HOME"
        base = Path(os.environ.get(key, Path.home() / ".config"))
        path = base / "monitors.xml"
    try:
        return parse_monitors_xml(path.read_text(encoding="utf-8"))
    except OSError:
        return []


def is_familiar(state: State, arrangements: list[Arrangement]) -> bool:
    known = {a.key for a in arrangements}
    if state.key in known:
        return True
    # With the lid closed Mutter remembers the arrangement without the
    # laptop's own panel.
    without_builtin = key_for(m.spec for m in state.monitors if not m.builtin)
    return len(without_builtin) != len(state.key) and without_builtin in known


# ── Names ────────────────────────────────────────────────────────────────

def builtin_name(dmi: Path = Path("/sys/class/dmi/id")) -> str:
    for key in ("product_family", "product_version", "product_name"):
        try:
            word = (dmi / key).read_text().strip().split()[0]
        except (OSError, IndexError):
            continue
        if re.fullmatch(r"[A-Za-z][A-Za-z-]{2,}", word) and word.lower() not in {"to", "default", "system", "none", "not"}:
            return word
    return "Built-in Display"


# Makers whose EDID name is not the name on the box.
VENDOR_ALIASES = {"asustek": "ASUS", "hewlett packard": "HP", "hewlett-packard": "HP", "lg": "LG",
                  "samsung": "Samsung", "dell": "Dell", "lenovo": "Lenovo", "benq": "BenQ", "aoc": "AOC"}


def _brand(name: str) -> str:
    lowered = name.lower()
    for maker, brand in VENDOR_ALIASES.items():
        if lowered == maker or lowered.startswith(maker + " "):
            return brand + name[len(maker):]
    return name


def short_name(monitor: Monitor) -> str:
    """"ThinkPad" for the laptop's panel, "ASUS 27″" for the one beside it."""
    if monitor.builtin:
        return builtin_name()
    name = VENDOR_NOISE.sub("", monitor.display_name or "").replace('"', "″")
    return _brand(re.sub(r"\s+", " ", name).strip()) or monitor.display_name or monitor.spec.connector


def spec_short_name(spec: Spec) -> str:
    if spec.connector.startswith(("eDP", "LVDS", "DSI")):
        return builtin_name()
    product = VENDOR_NOISE.sub("", spec.product).strip()
    return product or spec.vendor or spec.connector


def join_names(names: list[str]) -> str:
    names = [n for n in names if n]
    if len(names) <= 1:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + " and " + names[-1]


# ── Geometry ─────────────────────────────────────────────────────────────

def logical_size(mode: Mode, scale: float, transform: int, layout_mode: int = 1) -> tuple[int, int]:
    if layout_mode == 1:
        width, height = round(mode.width / scale), round(mode.height / scale)
    else:
        width, height = mode.width, mode.height
    return (height, width) if transform % 2 else (width, height)


def rects(state: State, layout: Layout) -> dict[str, tuple[int, int, int, int]]:
    result = {}
    for p in layout.placements:
        mode = state.monitor(p.connector).mode(p.mode_id)
        w, h = logical_size(mode, p.scale, p.transform, state.layout_mode)
        result[p.connector] = (p.x, p.y, w, h)
    return result


def _overlaps(a, b) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _touches(a, b) -> bool:
    horizontal = (a[0] + a[2] == b[0] or b[0] + b[2] == a[0]) and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]
    vertical = (a[1] + a[3] == b[1] or b[1] + b[3] == a[1]) and a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
    return horizontal or vertical


def connected(boxes: list[tuple[int, int, int, int]]) -> bool:
    if len(boxes) <= 1:
        return True
    seen, frontier = {0}, [0]
    while frontier:
        i = frontier.pop()
        for j, other in enumerate(boxes):
            if j not in seen and _touches(boxes[i], other):
                seen.add(j)
                frontier.append(j)
    return len(seen) == len(boxes) and not any(
        _overlaps(a, b) for i, a in enumerate(boxes) for b in boxes[i + 1:])


def normalize(layout: Layout) -> None:
    if not layout.placements:
        return
    dx = min(p.x for p in layout.placements)
    dy = min(p.y for p in layout.placements)
    for p in layout.placements:
        p.x -= dx
        p.y -= dy


def snap(state: State, layout: Layout, connector: str, x: float, y: float) -> None:
    """Put a dragged display against the nearest edge of the others."""
    boxes = rects(state, layout)
    moving = boxes.pop(connector)
    w, h = moving[2], moving[3]
    others = list(boxes.values())
    if not others:
        return
    candidates = []
    for ox, oy, ow, oh in others:
        cy = min(max(y, oy - h + 1), oy + oh - 1)
        cx = min(max(x, ox - w + 1), ox + ow - 1)
        candidates += [(ox - w, cy), (ox + ow, cy), (cx, oy - h), (cx, oy + oh)]
    valid = [(round(cx), round(cy)) for cx, cy in candidates
             if not any(_overlaps((cx, cy, w, h), o) for o in others)
             and connected(others + [(cx, cy, w, h)])]
    if not valid:
        return
    best = min(valid, key=lambda c: (c[0] - x) ** 2 + (c[1] - y) ** 2)
    # Aligning edges is what people usually mean when they get close.
    bx, by = best
    for ox, oy, ow, oh in others:
        for edge in (ox, ox + ow - w):
            if abs(bx - edge) < max(w, h) * 0.06 and not any(_overlaps((edge, by, w, h), o) for o in others):
                bx = edge
        for edge in (oy, oy + oh - h):
            if abs(by - edge) < max(w, h) * 0.06 and not any(_overlaps((bx, edge, w, h), o) for o in others):
                by = edge
    if not connected(others + [(bx, by, w, h)]):
        bx, by = best
    placement = next(p for p in layout.placements if p.connector == connector)
    placement.x, placement.y = bx, by
    normalize(layout)


def reflow_after_resize(state: State, layout: Layout, connector: str) -> None:
    """After a display changes size, keep every display touching another."""
    boxes = rects(state, layout)
    if connected(list(boxes.values())):
        return
    placement = next(p for p in layout.placements if p.connector == connector)
    snap(state, layout, connector, placement.x, placement.y)
    if not connected(list(rects(state, layout).values())):
        extended_row(state, layout)


def extended_row(state: State, layout: Layout) -> None:
    """Every display side by side, tops aligned, in their current order."""
    x = 0
    for p in sorted(layout.placements, key=lambda p: (p.x, p.y)):
        mode = state.monitor(p.connector).mode(p.mode_id)
        w, _h = logical_size(mode, p.scale, p.transform, state.layout_mode)
        p.x, p.y = x, 0
        x += w


# ── Choices ──────────────────────────────────────────────────────────────

def refresh_modes(monitor: Monitor, mode: Mode) -> list[Mode]:
    same = [m for m in monitor.modes if (m.width, m.height) == (mode.width, mode.height) and not m.variable]
    by_rate: dict[int, Mode] = {}
    for m in sorted(same, key=lambda m: -m.rate):
        by_rate.setdefault(round(m.rate), m)
    return sorted(by_rate.values(), key=lambda m: -m.rate)


def resolutions(monitor: Monitor) -> list[Mode]:
    best: dict[tuple[int, int], Mode] = {}
    for m in monitor.modes:
        if m.variable:
            continue
        current = best.get((m.width, m.height))
        if current is None or m.rate > current.rate:
            best[(m.width, m.height)] = m
    return sorted(best.values(), key=lambda m: (-m.width * m.height, -m.width))


def extend_layout(state: State) -> Layout:
    if not state.layout.mirror and len(state.layout.placements) == len(state.monitors):
        return state.layout.copy()
    placements = []
    for monitor in sorted(state.monitors, key=lambda m: (not m.builtin, m.spec.connector)):
        mode = monitor.current_mode or monitor.preferred_mode
        if mode.variable:
            mode = monitor.preferred_mode
        placements.append(Placement(monitor.spec.connector, mode.id, 0, 0, mode.preferred_scale, 0, False))
    layout = Layout(placements)
    extended_row(state, layout)
    externals = [p for p in placements if not state.monitor(p.connector).builtin]
    (externals[0] if externals else placements[0]).primary = True
    return layout


def mirror_layout(state: State) -> Layout | None:
    sizes = None
    for monitor in state.monitors:
        mine = {(m.width, m.height) for m in monitor.modes if not m.variable}
        sizes = mine if sizes is None else sizes & mine
    if not sizes or not state.supports_mirroring:
        return None
    width, height = max(sizes, key=lambda s: s[0] * s[1])
    placements, scales = [], None
    for monitor in state.monitors:
        mode = max((m for m in monitor.modes if (m.width, m.height) == (width, height) and not m.variable),
                   key=lambda m: m.rate)
        scales = set(mode.scales) if scales is None else scales & set(mode.scales)
        placements.append(Placement(monitor.spec.connector, mode.id, 0, 0, 1.0, 0, True))
    scale = 1.0 if not scales or 1.0 in scales else min(scales)
    for p in placements:
        p.scale = scale
    return Layout(placements, mirror=True)


def single_layout(state: State, connector: str) -> Layout:
    monitor = state.monitor(connector)
    current = next((p for p in state.layout.placements if p.connector == connector and not state.layout.mirror), None)
    if current:
        return Layout([Placement(connector, current.mode_id, 0, 0, current.scale, current.transform, True)])
    mode = monitor.preferred_mode
    return Layout([Placement(connector, mode.id, 0, 0, mode.preferred_scale, 0, True)])


def single_primary(layout: Layout) -> None:
    """Leave exactly one primary display.

    Mutter can report two logical monitors as primary after a dock or lid
    change, then refuses that same configuration when it is applied back.
    """
    leader = next((p for p in layout.placements if p.primary), layout.placements[0] if layout.placements else None)
    for placement in layout.placements:
        placement.primary = placement is leader


def apply_arguments(state: State, layout: Layout) -> list:
    """Logical monitors in ApplyMonitorsConfig's a(iiduba(ssa{sv})) shape."""
    if layout.mirror:
        first = layout.placements[0]
        return [(0, 0, first.scale, first.transform, True, [(p.connector, p.mode_id, {}) for p in layout.placements])]
    normalize(layout)
    single_primary(layout)
    return [(p.x, p.y, p.scale, p.transform, p.primary, [(p.connector, p.mode_id, {})]) for p in layout.placements]
