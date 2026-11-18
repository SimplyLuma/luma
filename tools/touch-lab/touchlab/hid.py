# SPDX-License-Identifier: Apache-2.0
"""HID report descriptor parser and Precision Touchpad report decoder.

Recordings keep the raw descriptor and raw reports; everything here is
derived offline, so a decoding fix applies to every recording already made.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PAGE_GENERIC = 0x01
PAGE_BUTTON = 0x09
PAGE_DIGITIZER = 0x0D
PAGE_HAPTICS = 0x0E
PAGE_VENDOR = 0xFF00

# Digitizer page usages (HUT 1.5, chapter 16).
DG_FINGER = 0x22
DG_TIP_PRESSURE = 0x30
DG_TIP_SWITCH = 0x42
DG_CONFIDENCE = 0x47
DG_WIDTH = 0x48
DG_HEIGHT = 0x49
DG_CONTACT_ID = 0x51
DG_INPUT_MODE = 0x52
DG_CONTACT_COUNT = 0x54
DG_CONTACT_COUNT_MAX = 0x55
DG_SCAN_TIME = 0x56
DG_SURFACE_SWITCH = 0x57
DG_BUTTON_SWITCH = 0x58
DG_PAD_TYPE = 0x59
DG_AZIMUTH = 0x3F
DG_LATENCY_MODE = 0x60
GD_X = 0x30
GD_Y = 0x31

USAGE_NAMES = {
    (PAGE_DIGITIZER, 0x05): "touch pad",
    (PAGE_DIGITIZER, 0x0E): "device configuration",
    (PAGE_DIGITIZER, DG_FINGER): "finger",
    (PAGE_DIGITIZER, DG_TIP_PRESSURE): "tip pressure",
    (PAGE_DIGITIZER, DG_TIP_SWITCH): "tip switch",
    (PAGE_DIGITIZER, DG_CONFIDENCE): "confidence",
    (PAGE_DIGITIZER, DG_WIDTH): "width",
    (PAGE_DIGITIZER, DG_HEIGHT): "height",
    (PAGE_DIGITIZER, DG_CONTACT_ID): "contact id",
    (PAGE_DIGITIZER, DG_INPUT_MODE): "input mode",
    (PAGE_DIGITIZER, DG_CONTACT_COUNT): "contact count",
    (PAGE_DIGITIZER, DG_CONTACT_COUNT_MAX): "contact count maximum",
    (PAGE_DIGITIZER, DG_SCAN_TIME): "scan time",
    (PAGE_DIGITIZER, DG_SURFACE_SWITCH): "surface switch",
    (PAGE_DIGITIZER, DG_BUTTON_SWITCH): "button switch",
    (PAGE_DIGITIZER, DG_PAD_TYPE): "pad type",
    (PAGE_DIGITIZER, DG_LATENCY_MODE): "latency mode",
    (PAGE_GENERIC, 0x01): "pointer",
    (PAGE_GENERIC, 0x02): "mouse",
    (PAGE_GENERIC, GD_X): "x",
    (PAGE_GENERIC, GD_Y): "y",
    (PAGE_HAPTICS, 0x01): "simple haptic controller",
    (PAGE_HAPTICS, 0x23): "haptic intensity",
    (PAGE_VENDOR, 0xC5): "vendor 0xc5 (PTP certification blob)",
}


def usage_name(page: int, usage: int) -> str:
    name = USAGE_NAMES.get((page, usage))
    if name:
        return name
    if page == PAGE_BUTTON:
        return f"button {usage}"
    return f"0x{page:04x}:0x{usage:02x}"


@dataclass
class Field:
    kind: str                 # "input", "output" or "feature"
    report_id: int
    bit_offset: int           # from the first data byte, after the report ID
    bit_size: int
    page: int
    usage: int
    logical_min: int
    logical_max: int
    physical_min: int
    physical_max: int
    unit: int
    unit_exponent: int
    flags: int
    collection: int           # index into Descriptor.collections, -1 at top level
    variable: bool = True
    usages: tuple[tuple[int, int], ...] = ()   # array fields: the usage list

    @property
    def constant(self) -> bool:
        return bool(self.flags & 0x01)

    @property
    def signed(self) -> bool:
        return self.logical_min < 0

    def read(self, data: bytes) -> int:
        value = int.from_bytes(data, "little") >> self.bit_offset
        value &= (1 << self.bit_size) - 1
        if self.signed and value & (1 << (self.bit_size - 1)):
            value -= 1 << self.bit_size
        return value

    def describe(self) -> dict:
        return {
            "kind": self.kind, "report_id": self.report_id, "bit_offset": self.bit_offset,
            "bit_size": self.bit_size, "usage": usage_name(self.page, self.usage),
            "page": self.page, "usage_id": self.usage, "logical": [self.logical_min, self.logical_max],
            "physical": [self.physical_min, self.physical_max], "unit": self.unit,
            "unit_exponent": self.unit_exponent, "constant": self.constant,
            "variable": self.variable, "collection": self.collection,
        }


@dataclass
class Collection:
    page: int
    usage: int
    kind: int                 # 0 physical, 1 application, 2 logical, ...
    parent: int


@dataclass
class Descriptor:
    fields: list[Field] = field(default_factory=list)
    collections: list[Collection] = field(default_factory=list)
    uses_report_ids: bool = False

    def reports(self, kind: str) -> dict[int, list[Field]]:
        out: dict[int, list[Field]] = {}
        for f in self.fields:
            if f.kind == kind:
                out.setdefault(f.report_id, []).append(f)
        return out

    def report_bytes(self, kind: str, report_id: int) -> int:
        bits = max((f.bit_offset + f.bit_size for f in self.fields
                    if f.kind == kind and f.report_id == report_id), default=0)
        return (bits + 7) // 8 + (1 if self.uses_report_ids else 0)

    def ancestors(self, index: int):
        while index >= 0:
            yield index, self.collections[index]
            index = self.collections[index].parent

    def in_collection(self, f: Field, page: int, usage: int) -> int | None:
        for index, c in self.ancestors(f.collection):
            if c.page == page and c.usage == usage:
                return index
        return None

    def summary(self) -> list[dict]:
        rows = []
        for kind in ("input", "output", "feature"):
            for rid, fields in sorted(self.reports(kind).items()):
                app = None
                for f in fields:
                    for _i, c in self.ancestors(f.collection):
                        if c.kind == 1:
                            app = usage_name(c.page, c.usage)
                rows.append({
                    "kind": kind, "report_id": rid, "bytes": self.report_bytes(kind, rid),
                    "application": app,
                    "usages": sorted({usage_name(f.page, f.usage) for f in fields if not f.constant}),
                })
        return rows


def _signed(raw: bytes) -> int:
    return int.from_bytes(raw, "little", signed=True) if raw else 0


def _unsigned(raw: bytes) -> int:
    return int.from_bytes(raw, "little") if raw else 0


def parse_descriptor(data: bytes) -> Descriptor:
    desc = Descriptor()
    glob = {"page": 0, "lmin": 0, "lmax": 0, "pmin": 0, "pmax": 0, "unit": 0, "uexp": 0,
            "size": 0, "count": 0, "id": 0}
    lmax_raw = b""
    stack: list[tuple[dict, bytes]] = []
    usages: list[tuple[int, int]] = []
    umin: int | None = None
    offsets: dict[tuple[str, int], int] = {}
    current = -1
    i = 0

    def full(u: int, size: int) -> tuple[int, int]:
        return ((u >> 16) & 0xFFFF, u & 0xFFFF) if size == 4 else (glob["page"], u)

    while i < len(data):
        prefix = data[i]
        if prefix == 0xFE:                        # long item: skip
            i += 3 + (data[i + 1] if i + 1 < len(data) else 0)
            continue
        size = (0, 1, 2, 4)[prefix & 3]
        kind = (prefix >> 2) & 3
        tag = prefix >> 4
        raw = data[i + 1:i + 1 + size]
        i += 1 + size
        if kind == 1:                              # global
            if tag == 0:
                glob["page"] = _unsigned(raw)
            elif tag == 1:
                glob["lmin"] = _signed(raw)
            elif tag == 2:
                lmax_raw = raw
                glob["lmax"] = _signed(raw)
            elif tag == 3:
                glob["pmin"] = _signed(raw)
            elif tag == 4:
                glob["pmax"] = _signed(raw)
            elif tag == 5:
                e = _unsigned(raw) & 0xF
                glob["uexp"] = e - 16 if e > 7 else e
            elif tag == 6:
                glob["unit"] = _unsigned(raw)
            elif tag == 7:
                glob["size"] = _unsigned(raw)
            elif tag == 8:
                glob["id"] = _unsigned(raw)
                desc.uses_report_ids = True
            elif tag == 9:
                glob["count"] = _unsigned(raw)
            elif tag == 10:
                stack.append((dict(glob), lmax_raw))
            elif tag == 11 and stack:
                glob, lmax_raw = stack.pop()
            continue
        if kind == 2:                              # local
            if tag == 0:
                usages.append(full(_unsigned(raw), size))
            elif tag == 1:
                umin = _unsigned(raw)
            elif tag == 2 and umin is not None:
                page, top = full(_unsigned(raw), size)
                page_min, low = full(umin, size)
                usages.extend((page, u) for u in range(low, top + 1))
                umin = None
            continue
        if kind != 0:
            continue
        # main items
        if tag == 0xA:                             # collection
            page, usage = usages[0] if usages else (glob["page"], 0)
            desc.collections.append(Collection(page, usage, _unsigned(raw), current))
            current = len(desc.collections) - 1
        elif tag == 0xC:                           # end collection
            if current >= 0:
                current = desc.collections[current].parent
        elif tag in (0x8, 0x9, 0xB):
            report_kind = {0x8: "input", 0x9: "output", 0xB: "feature"}[tag]
            flags = _unsigned(raw)
            lmin, lmax = glob["lmin"], glob["lmax"]
            if lmax < lmin:                        # an unsigned maximum written in too few bytes
                lmax = _unsigned(lmax_raw)
            key = (report_kind, glob["id"])
            offset = offsets.get(key, 0)
            variable = bool(flags & 0x02)
            common = dict(kind=report_kind, report_id=glob["id"], bit_size=glob["size"],
                          logical_min=lmin, logical_max=lmax, physical_min=glob["pmin"],
                          physical_max=glob["pmax"], unit=glob["unit"],
                          unit_exponent=glob["uexp"], flags=flags, collection=current)
            if variable:
                for n in range(glob["count"]):
                    page, usage = (usages[min(n, len(usages) - 1)] if usages else (glob["page"], 0))
                    desc.fields.append(Field(bit_offset=offset, page=page, usage=usage, **common))
                    offset += glob["size"]
            else:
                page, usage = usages[0] if usages else (glob["page"], 0)
                for _n in range(glob["count"]):
                    desc.fields.append(Field(bit_offset=offset, page=page, usage=usage, variable=False,
                                             usages=tuple(usages), **common))
                    offset += glob["size"]
            offsets[key] = offset
        usages = []
        umin = None
    return desc


@dataclass
class Contact:
    contact_id: int
    tip: bool
    confidence: bool | None
    x: int
    y: int
    pressure: int | None
    width: int | None = None
    height: int | None = None


@dataclass
class TouchReport:
    report_id: int
    scan_time: int | None
    contact_count: int | None
    buttons: int
    contacts: list[Contact]


class TouchpadLayout:
    """Where a Precision Touchpad's fields sit in its input report."""

    def __init__(self, desc: Descriptor) -> None:
        self.desc = desc
        self.report_id: int | None = None
        self.slots: list[dict[str, Field]] = []
        self.scan_time: Field | None = None
        self.contact_count: Field | None = None
        self.buttons: list[Field] = []
        self.x_range = (0, 1)
        self.y_range = (0, 1)
        self.pressure_range = (0, 1)
        self.x_mm = self.y_mm = None
        for rid, fields in desc.reports("input").items():
            slots: dict[int, dict[str, Field]] = {}
            for f in fields:
                finger = desc.in_collection(f, PAGE_DIGITIZER, DG_FINGER)
                if finger is None:
                    continue
                name = {
                    (PAGE_DIGITIZER, DG_TIP_SWITCH): "tip",
                    (PAGE_DIGITIZER, DG_CONFIDENCE): "confidence",
                    (PAGE_DIGITIZER, DG_CONTACT_ID): "contact_id",
                    (PAGE_DIGITIZER, DG_TIP_PRESSURE): "pressure",
                    (PAGE_DIGITIZER, DG_WIDTH): "width",
                    (PAGE_DIGITIZER, DG_HEIGHT): "height",
                    (PAGE_GENERIC, GD_X): "x",
                    (PAGE_GENERIC, GD_Y): "y",
                }.get((f.page, f.usage))
                if name and not f.constant:
                    slots.setdefault(finger, {})[name] = f
            if not slots:
                continue
            self.report_id = rid
            self.slots = [slots[k] for k in sorted(slots)]
            for f in fields:
                if (f.page, f.usage) == (PAGE_DIGITIZER, DG_SCAN_TIME):
                    self.scan_time = f
                elif (f.page, f.usage) == (PAGE_DIGITIZER, DG_CONTACT_COUNT):
                    self.contact_count = f
                elif f.page == PAGE_BUTTON and not f.constant:
                    self.buttons.append(f)
            first = self.slots[0]
            if "x" in first:
                self.x_range = (first["x"].logical_min, first["x"].logical_max)
                self.x_mm = _physical_mm(first["x"])
            if "y" in first:
                self.y_range = (first["y"].logical_min, first["y"].logical_max)
                self.y_mm = _physical_mm(first["y"])
            if "pressure" in first:
                self.pressure_range = (first["pressure"].logical_min, first["pressure"].logical_max)
            break

    @property
    def ok(self) -> bool:
        return self.report_id is not None

    def decode(self, report: bytes) -> TouchReport | None:
        if not report:
            return None
        if self.desc.uses_report_ids:
            if report[0] != self.report_id:
                return None
            data = report[1:]
        else:
            data = report
        count = self.contact_count.read(data) if self.contact_count else None
        contacts = []
        # Hybrid mode sends contacts across several reports; contact count is
        # non-zero only in the first. Read every slot and keep the live ones.
        limit = len(self.slots) if not count else min(count, len(self.slots))
        for slot in self.slots[:limit]:
            def get(name: str) -> int | None:
                f = slot.get(name)
                return f.read(data) if f else None
            tip = get("tip")
            contacts.append(Contact(
                contact_id=get("contact_id") or 0, tip=bool(tip),
                confidence=None if "confidence" not in slot else bool(get("confidence")),
                x=get("x") or 0, y=get("y") or 0, pressure=get("pressure"),
                width=get("width"), height=get("height")))
        buttons = 0
        for n, f in enumerate(self.buttons):
            if f.read(data):
                buttons |= 1 << n
        return TouchReport(self.report_id, self.scan_time.read(data) if self.scan_time else None,
                           count, buttons, contacts)

    def encode(self, scan_time: int, contacts: list[Contact], buttons: int = 0) -> bytes:
        """Build a raw input report (synthetic recordings and kernel replay tests)."""
        size = self.desc.report_bytes("input", self.report_id) - (1 if self.desc.uses_report_ids else 0)
        value = 0

        def put(f: Field | None, v: int) -> None:
            nonlocal value
            if f is None:
                return
            v = max(f.logical_min, min(f.logical_max, int(v)))
            v &= (1 << f.bit_size) - 1
            value &= ~(((1 << f.bit_size) - 1) << f.bit_offset)
            value |= v << f.bit_offset

        for slot, c in zip(self.slots, contacts):
            put(slot.get("tip"), int(c.tip))
            put(slot.get("confidence"), int(bool(c.confidence)))
            put(slot.get("contact_id"), c.contact_id)
            put(slot.get("x"), c.x)
            put(slot.get("y"), c.y)
            put(slot.get("pressure"), c.pressure or 0)
        put(self.scan_time, scan_time & 0xFFFF)
        put(self.contact_count, len(contacts))
        for n, f in enumerate(self.buttons):
            put(f, (buttons >> n) & 1)
        body = value.to_bytes(size, "little")
        return (bytes([self.report_id]) + body) if self.desc.uses_report_ids else body

    def describe(self) -> dict:
        return {
            "report_id": self.report_id, "slots": len(self.slots),
            "slot_fields": sorted(self.slots[0]) if self.slots else [],
            "x_range": self.x_range, "y_range": self.y_range, "x_mm": self.x_mm, "y_mm": self.y_mm,
            "pressure_range": self.pressure_range,
            "scan_time_unit_s": _scan_unit(self.scan_time) if self.scan_time else None,
        }

    def scan_time_unit(self) -> float:
        return _scan_unit(self.scan_time) if self.scan_time else 1e-4


def _physical_mm(f: Field) -> float | None:
    if f.physical_max <= f.physical_min:
        return None
    span = f.physical_max - f.physical_min
    system, length = f.unit & 0xF, (f.unit >> 4) & 0xF
    if length != 1:
        return None
    scale = 10.0 if system == 1 else 25.4 if system == 3 else None   # cm or inch to mm
    if scale is None:
        return None
    return span * (10.0 ** f.unit_exponent) * scale


def _scan_unit(f: Field) -> float:
    # Precision Touchpad scan time is in 100 microsecond units (unit 0x1001, exponent -4).
    return 10.0 ** f.unit_exponent if (f.unit & 0xF000) == 0x1000 else 1e-4
