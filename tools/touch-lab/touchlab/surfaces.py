# SPDX-License-Identifier: Apache-2.0
"""Touch surface classes and the capability probe.

A surface is classed by what its hardware reports, never by vendor or
model: the same model family runs everywhere and a feature mask tells it
which signals are real. See docs/surfaces.md for what each class reports
and how the model makes up for what is missing.

Capabilities come from one of:
  - a live device (sysfs bitmaps, absinfo by ioctl when readable, and the
    HID report descriptor when the device is HID),
  - a recorder header (session.json "evdev_description"),
  - a libinput test device (touchlab/data/litest-surfaces.json, mined from
    libinput's test/litest-device-*.c by `touchlab mine-litest`).
Quirks (libinput's *.quirks AttrInputProp lines) are applied on top, the
way libinput applies them.
"""

from __future__ import annotations

import fnmatch
import glob
import json
import os
import re
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
LITEST_JSON = os.path.join(HERE, "data", "litest-surfaces.json")

# ── evdev names ─────────────────────────────────────────────────────────────

ABS = {
    "ABS_X": 0x00, "ABS_Y": 0x01, "ABS_PRESSURE": 0x18, "ABS_DISTANCE": 0x19, "ABS_TOOL_WIDTH": 0x1C,
    "ABS_MT_SLOT": 0x2F, "ABS_MT_TOUCH_MAJOR": 0x30, "ABS_MT_TOUCH_MINOR": 0x31,
    "ABS_MT_WIDTH_MAJOR": 0x32, "ABS_MT_WIDTH_MINOR": 0x33, "ABS_MT_ORIENTATION": 0x34,
    "ABS_MT_POSITION_X": 0x35, "ABS_MT_POSITION_Y": 0x36, "ABS_MT_TOOL_TYPE": 0x37,
    "ABS_MT_BLOB_ID": 0x38, "ABS_MT_TRACKING_ID": 0x39, "ABS_MT_PRESSURE": 0x3A, "ABS_MT_DISTANCE": 0x3B,
}
ABS_NAME = {v: k for k, v in ABS.items()}
PROPS = {"INPUT_PROP_POINTER": 0, "INPUT_PROP_DIRECT": 1, "INPUT_PROP_BUTTONPAD": 2, "INPUT_PROP_SEMI_MT": 3,
         "INPUT_PROP_TOPBUTTONPAD": 4, "INPUT_PROP_POINTING_STICK": 5, "INPUT_PROP_ACCELEROMETER": 6,
         "INPUT_PROP_PRESSUREPAD": 7}
PROP_NAME = {v: k for k, v in PROPS.items()}
KEYS = {"BTN_LEFT": 0x110, "BTN_RIGHT": 0x111, "BTN_MIDDLE": 0x112, "BTN_TOOL_FINGER": 0x145,
        "BTN_TOOL_QUINTTAP": 0x148, "BTN_TOUCH": 0x14A, "BTN_TOOL_DOUBLETAP": 0x14D,
        "BTN_TOOL_TRIPLETAP": 0x14E, "BTN_TOOL_QUADTAP": 0x14F, "BTN_TOOL_PEN": 0x140}
KEY_NAME = {v: k for k, v in KEYS.items()}
MSC_TIMESTAMP = 0x05
BUS = {0x01: "pci", 0x03: "usb", 0x05: "bluetooth", 0x06: "virtual", 0x11: "i8042", 0x18: "i2c",
       0x19: "host", 0x1C: "spi", 0x1D: "rmi"}
BUS_ID = {v: k for k, v in BUS.items()}

# ── Classes and the feature mask ────────────────────────────────────────────

CLASSES = {
    "legacy-clickpad": "PS/2 or SMBus/RMI4 clickpad (Synaptics, Elan, ALPS): positions and a pressure that is "
                       "really contact area; often no per-contact confidence; some are semi-MT",
    "ptp-clickpad": "I2C/USB HID Precision Touchpad with a mechanical click: firmware Confidence (MT_TOOL_PALM), "
                    "touch size on some, pressure-as-area on some",
    "force-pad": "Force or haptic pad (Sensel, Cirque, Goodix, Apple Force Touch, haptic Chromebook pads): "
                 "per-contact force in real units and a haptic click; often no contact size",
    "touchscreen": "Direct-touch screen (phones, tablets, laptop screens): size on most, pressure on some, "
                   "no button, no keyboard next to it",
    "unknown": "Not a multitouch surface the model covers",
}

# Mask bits, in a fixed order: they are model inputs ("m_<name>") and the
# Lua plugin computes the same bits from the device at start.
MASK_BITS = (
    "mt",              # per-contact slots
    "direct",          # touchscreen (INPUT_PROP_DIRECT)
    "pressure",        # a per-contact pressure axis exists (and libinput leaves it visible)
    "force",           # that pressure is real force (pressure pad), not capacitance/area
    "size",            # ABS_MT_TOUCH_MAJOR (or WIDTH_MAJOR) per contact
    "minor",           # ABS_MT_TOUCH_MINOR: an ellipse, not just a diameter
    "orientation",     # ABS_MT_ORIENTATION
    "confidence",      # firmware palm flag (ABS_MT_TOOL_TYPE, HID Confidence)
    "button",          # a click button under the surface (mechanical or haptic)
    "haptic",          # the click is synthesised from force (no moving parts)
    "semi_mt",         # positions are a bounding box, not per contact
    "resolution",      # positions convert to mm (resolution known)
    "keyboard",        # a built-in keyboard sits next to the surface
    "hover",           # proximity before contact (ABS_MT_DISTANCE)
)


@dataclass
class Caps:
    """What one input device reports. Ranges are (minimum, maximum, resolution)."""

    name: str = ""
    bus: int = 0
    vendor: int = 0
    product: int = 0
    props: set = field(default_factory=set)        # INPUT_PROP_* names
    abs: dict = field(default_factory=dict)        # ABS_* name -> (min, max, resolution)
    keys: set = field(default_factory=set)         # BTN_* names we care about
    msc: set = field(default_factory=set)          # MSC_* names
    ff: bool = False                               # force feedback (haptic actuator)
    hid_usages: set = field(default_factory=set)   # digitizer usages present: confidence, width, height, pressure
    integration: str = ""                          # udev ID_INTEGRATION: internal / external
    udev_type: str = ""                            # touchpad / touchscreen
    source: str = ""
    quirks_applied: list = field(default_factory=list)

    def has(self, axis: str) -> bool:
        return axis in self.abs

    def range(self, axis: str) -> tuple[int, int, int] | None:
        return self.abs.get(axis)

    def size_mm(self) -> tuple[float | None, float | None]:
        out = []
        for axis in ("ABS_MT_POSITION_X", "ABS_MT_POSITION_Y"):
            r = self.abs.get(axis) or self.abs.get(axis.replace("MT_POSITION_", ""))
            out.append((r[1] - r[0]) / r[2] if r and r[2] else None)
        return out[0], out[1]

    def to_json(self) -> dict:
        return {"name": self.name, "bus": self.bus, "vendor": self.vendor, "product": self.product,
                "props": sorted(self.props), "abs": {k: list(v) for k, v in sorted(self.abs.items())},
                "keys": sorted(self.keys), "msc": sorted(self.msc), "ff": self.ff,
                "hid_usages": sorted(self.hid_usages), "integration": self.integration,
                "udev_type": self.udev_type, "source": self.source}

    @classmethod
    def from_json(cls, d: dict) -> "Caps":
        return cls(name=d.get("name", ""), bus=d.get("bus", 0), vendor=d.get("vendor", 0),
                   product=d.get("product", 0), props=set(d.get("props", ())),
                   abs={k: tuple(v) for k, v in d.get("abs", {}).items()}, keys=set(d.get("keys", ())),
                   msc=set(d.get("msc", ())), ff=bool(d.get("ff")), hid_usages=set(d.get("hid_usages", ())),
                   integration=d.get("integration", ""), udev_type=d.get("udev_type", ""),
                   source=d.get("source", ""))


@dataclass
class Surface:
    cls: str
    mask: dict                                   # MASK_BITS -> 0/1
    caps: Caps
    reasons: list
    pad_mm: tuple[float | None, float | None]

    def mask_bits(self) -> int:
        return sum(1 << i for i, b in enumerate(MASK_BITS) if self.mask.get(b))

    def missing(self) -> list[str]:
        return [b for b in ("pressure", "size", "confidence", "button", "keyboard") if not self.mask.get(b)]

    def to_json(self) -> dict:
        return {"class": self.cls, "mask": self.mask, "mask_bits": self.mask_bits(), "pad_mm": list(self.pad_mm),
                "reasons": self.reasons, "caps": self.caps.to_json()}


def classify(caps: Caps) -> Surface:
    """One class and a feature mask, from capabilities alone."""
    p, a = caps.props, caps.abs
    why: list[str] = []
    mt = "ABS_MT_POSITION_X" in a and "ABS_MT_POSITION_Y" in a
    direct = "INPUT_PROP_DIRECT" in p or caps.udev_type == "touchscreen"
    buttonpad = "INPUT_PROP_BUTTONPAD" in p
    pressure_axis = "ABS_MT_PRESSURE" in a or (not mt and "ABS_PRESSURE" in a)
    pr = a.get("ABS_MT_PRESSURE") or a.get("ABS_PRESSURE")
    # libinput's own test: INPUT_PROP_PRESSUREPAD, or a pressure resolution (real units).
    force = "INPUT_PROP_PRESSUREPAD" in p or bool(pr and pr[2])
    size = "ABS_MT_TOUCH_MAJOR" in a or "ABS_MT_WIDTH_MAJOR" in a
    minor = "ABS_MT_TOUCH_MINOR" in a or "ABS_MT_WIDTH_MINOR" in a
    conf = "ABS_MT_TOOL_TYPE" in a or "confidence" in caps.hid_usages
    button = "BTN_LEFT" in caps.keys
    res = all((a.get(k) or (0, 0, 0))[2] for k in ("ABS_MT_POSITION_X", "ABS_MT_POSITION_Y")) if mt else \
        all((a.get(k) or (0, 0, 0))[2] for k in ("ABS_X", "ABS_Y"))
    semi = "INPUT_PROP_SEMI_MT" in p
    bus = BUS.get(caps.bus, "")
    external = caps.integration == "external" or (bus in ("usb", "bluetooth") and caps.integration != "internal")
    if not mt and not (("ABS_X" in a) and ("BTN_TOOL_FINGER" in caps.keys or direct)):
        cls = "unknown"
        why.append("no multitouch position axes")
    elif direct:
        cls = "touchscreen"
        why.append("INPUT_PROP_DIRECT")
    elif force:
        cls = "force-pad"
        why.append("INPUT_PROP_PRESSUREPAD" if "INPUT_PROP_PRESSUREPAD" in p else "pressure axis has a resolution")
    elif bus in ("i8042", "rmi") or (not conf and bus not in ("i2c", "usb", "bluetooth", "spi")):
        cls = "legacy-clickpad"
        why.append(f"bus {bus or hex(caps.bus)}" + ("" if conf else ", no firmware confidence"))
    elif bus in ("i2c", "usb", "bluetooth", "spi", "host"):
        cls = "ptp-clickpad"
        why.append(f"HID-style bus {bus}" + (", firmware confidence" if conf else ", no confidence axis"))
    else:
        cls = "legacy-clickpad"
        why.append("touchpad on an unrecognised bus")
    mask = {
        "mt": int(mt), "direct": int(direct), "pressure": int(pressure_axis), "force": int(force and pressure_axis),
        "size": int(size), "minor": int(minor), "orientation": int("ABS_MT_ORIENTATION" in a),
        "confidence": int(conf), "button": int(button and not direct),
        "haptic": int(force and button and not direct and (caps.ff or "INPUT_PROP_PRESSUREPAD" in p)),
        "semi_mt": int(semi), "resolution": int(bool(res)),
        "keyboard": int(not direct and not external), "hover": int("ABS_MT_DISTANCE" in a),
    }
    if buttonpad and not button:
        why.append("buttonpad without BTN_LEFT")
    return Surface(cls, mask, caps, why, caps.size_mm())


# ── Quirks ──────────────────────────────────────────────────────────────────

@dataclass
class Quirk:
    section: str
    match: dict
    attrs: dict


def parse_quirks(text: str) -> list[Quirk]:
    out, cur = [], None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            cur = Quirk(line[1:-1], {}, {})
            out.append(cur)
        elif cur is not None and "=" in line:
            k, v = line.split("=", 1)
            (cur.match if k.startswith("Match") else cur.attrs)[k.strip()] = v.strip()
    return out


def load_quirks(paths: list[str]) -> list[Quirk]:
    out = []
    for path in paths:
        for f in sorted(glob.glob(os.path.join(path, "*.quirks")) if os.path.isdir(path) else [path]):
            with open(f, encoding="utf-8", errors="replace") as fh:
                out.extend(parse_quirks(fh.read()))
    return out


def _quirk_matches(q: Quirk, caps: Caps) -> bool:
    if not q.match:
        return False
    for k, v in q.match.items():
        if k == "MatchName" and not fnmatch.fnmatchcase(caps.name, v):
            return False
        if k == "MatchBus" and BUS.get(caps.bus) != v and not (v == "ps2" and caps.bus == 0x11):
            return False
        if k == "MatchVendor" and caps.vendor != int(v, 16):
            return False
        if k == "MatchProduct" and caps.product not in {int(x, 16) for x in v.split(";") if x}:
            return False
        if k == "MatchUdevType" and caps.udev_type and v != caps.udev_type:
            return False
        if k in ("MatchDMIModalias", "MatchDeviceTree"):
            return False                 # machine-specific: not decidable from the device alone
    return True


def apply_quirks(caps: Caps, quirks: list[Quirk]) -> Caps:
    for q in quirks:
        if not _quirk_matches(q, caps):
            continue
        props = q.attrs.get("AttrInputProp")
        if props:
            for item in props.split(";"):
                item = item.strip()
                if not item:
                    continue
                sign, name = (item[0], item[1:]) if item[0] in "+-" else ("+", item)
                name = name if name.startswith("INPUT_PROP_") else PROP_NAME.get(int(name, 0), name)
                (caps.props.add if sign == "+" else caps.props.discard)(name)
            caps.quirks_applied.append(q.section)
        for attr in ("AttrPressureRange", "AttrPalmPressureThreshold", "AttrThumbPressureThreshold",
                     "AttrTouchSizeRange", "AttrPalmSizeThreshold"):
            if attr in q.attrs and q.section not in caps.quirks_applied:
                caps.quirks_applied.append(q.section)
    return caps


# ── Sources ─────────────────────────────────────────────────────────────────

def _bitmap(text: str) -> set[int]:
    """sysfs capability bitmap ("1 0 7fff" words, most significant first) -> bit numbers."""
    import struct
    bits, words = set(), text.split()
    width = 8 * struct.calcsize("l")             # the kernel prints one unsigned long per word
    for wi, word in enumerate(reversed(words)):
        v = int(word, 16)
        for b in range(width):
            if v >> b & 1:
                bits.add(wi * width + b)
    return bits


def _read(path: str) -> str:
    try:
        with open(path, encoding="ascii", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _absinfo_ioctl(dev: str, codes: list[int]) -> dict[int, tuple[int, int, int]]:
    """EVIOCGABS for each code; empty if the node isn't readable (it's root-only on most systems)."""
    import fcntl
    import struct
    out = {}
    try:
        fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return out
    try:
        for code in codes:
            buf = bytearray(24)
            try:
                fcntl.ioctl(fd, 0x80184540 + code, buf)        # EVIOCGABS(code), 6 x s32
            except OSError:
                continue
            _val, lo, hi, _fuzz, _flat, res = struct.unpack("6i", bytes(buf))
            out[code] = (lo, hi, res)
    finally:
        os.close(fd)
    return out


def from_sysfs(event: str, sys_root: str = "/sys") -> Caps:
    """A live device by its event node name (event6). Needs no privileges for the bitmaps; ranges
    come from EVIOCGABS when the node is readable, else from the HID descriptor when there is one."""
    from . import hid
    base = os.path.join(sys_root, "class", "input", event, "device")
    caps = Caps(name=_read(os.path.join(base, "name")), source=f"sysfs:{event}")
    for k in ("bustype", "vendor", "product"):
        v = _read(os.path.join(base, "id", k))
        setattr(caps, "bus" if k == "bustype" else k, int(v, 16) if v else 0)
    caps.props = {PROP_NAME.get(b, f"prop{b}") for b in _bitmap(_read(os.path.join(base, "properties")) or "0")}
    abs_bits = _bitmap(_read(os.path.join(base, "capabilities", "abs")) or "0")
    key_bits = _bitmap(_read(os.path.join(base, "capabilities", "key")) or "0")
    msc_bits = _bitmap(_read(os.path.join(base, "capabilities", "msc")) or "0")
    caps.ff = bool(_bitmap(_read(os.path.join(base, "capabilities", "ff")) or "0"))
    caps.keys = {KEY_NAME[b] for b in key_bits if b in KEY_NAME}
    caps.msc = {"MSC_TIMESTAMP"} if MSC_TIMESTAMP in msc_bits else set()
    ranges = _absinfo_ioctl(os.path.join("/dev/input", event), sorted(abs_bits))
    for b in abs_bits:
        if b in ABS_NAME:
            caps.abs[ABS_NAME[b]] = ranges.get(b, (0, 0, 0))
    # HID: the descriptor is world-readable and carries ranges, units and the Confidence usage.
    desc_path = os.path.realpath(os.path.join(base, "device", "report_descriptor"))
    raw = b""
    try:
        with open(desc_path, "rb") as fh:
            raw = fh.read()
    except OSError:
        pass
    if raw:
        layout = hid.TouchpadLayout(hid.parse_descriptor(raw))
        if layout.ok and layout.slots:
            first = layout.slots[0]
            caps.hid_usages = {k for k in ("confidence", "width", "height", "pressure") if k in first}
            for axis, key, mm in (("ABS_MT_POSITION_X", "x", layout.x_mm), ("ABS_MT_POSITION_Y", "y", layout.y_mm)):
                if axis in caps.abs and caps.abs[axis] == (0, 0, 0) and key in first:
                    lo, hi = first[key].logical_min, first[key].logical_max
                    caps.abs[axis] = (lo, hi, round((hi - lo) / mm) if mm else 0)
            if "ABS_MT_PRESSURE" in caps.abs and caps.abs["ABS_MT_PRESSURE"] == (0, 0, 0) and "pressure" in first:
                caps.abs["ABS_MT_PRESSURE"] = (first["pressure"].logical_min, first["pressure"].logical_max, 0)
    props = _udev_props(event)
    caps.integration = props.get("ID_INTEGRATION", "")
    caps.udev_type = ("touchscreen" if props.get("ID_INPUT_TOUCHSCREEN") == "1" else
                      "touchpad" if props.get("ID_INPUT_TOUCHPAD") == "1" else "")
    return caps


def _udev_props(event: str) -> dict:
    """udev properties from the udev database (readable by users), best effort."""
    out = {}
    try:
        st = os.stat(os.path.join("/dev/input", event))
    except OSError:
        return out
    path = f"/run/udev/data/c{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}"
    for line in _read(path).splitlines():
        if line.startswith("E:") and "=" in line:
            k, v = line[2:].split("=", 1)
            out[k] = v
    return out


def from_description(desc: dict, source: str = "recording") -> Caps:
    """The recorder's evdev_description (numeric codes, absinfo by code)."""
    ident = desc.get("id", {})
    caps = Caps(name=desc.get("name", ""), bus=ident.get("bustype", 0), vendor=ident.get("vendor", 0),
                product=ident.get("product", 0), source=source)
    caps.props = {PROP_NAME.get(int(p), f"prop{p}") for p in desc.get("properties", [])}
    codes = desc.get("codes", {})
    caps.keys = {KEY_NAME[c] for c in codes.get("1", []) if c in KEY_NAME}
    caps.msc = {"MSC_TIMESTAMP"} if MSC_TIMESTAMP in codes.get("4", []) else set()
    caps.ff = bool(codes.get("21"))
    for code, info in desc.get("absinfo", {}).items():
        name = ABS_NAME.get(int(code))
        if name:
            caps.abs[name] = (info.get("minimum", 0), info.get("maximum", 0), info.get("resolution", 0))
    return caps


def probe_all(sys_root: str = "/sys", quirk_dirs=("/usr/share/libinput", "/etc/libinput")) -> list[Surface]:
    """Every touch surface on this machine, classed."""
    quirks = load_quirks([d for d in quirk_dirs if os.path.exists(d)])
    out = []
    for path in sorted(glob.glob(os.path.join(sys_root, "class", "input", "event*"))):
        caps = from_sysfs(os.path.basename(path), sys_root)
        if "ABS_MT_POSITION_X" not in caps.abs and "BTN_TOOL_FINGER" not in caps.keys:
            continue
        if "BTN_TOOL_PEN" in caps.keys and "ABS_MT_POSITION_X" not in caps.abs:
            continue
        s = classify(apply_quirks(caps, quirks))
        if s.cls != "unknown":
            out.append(s)
    return out


# ── Mining libinput's test devices ──────────────────────────────────────────

_ABSROW = re.compile(r"\{\s*(ABS_[A-Z_]+)\s*,\s*(-?\w+)\s*,\s*(-?\w+)\s*,\s*(-?\w+)\s*,\s*(-?\w+)\s*,\s*(-?\w+)\s*\}")
_EVENT = re.compile(r"(EV_KEY|EV_MSC|INPUT_PROP_MAX|EV_FF)\s*,\s*([A-Z0-9_]+)")


def _int(s: str) -> int:
    return int(s, 0)


def mine_litest(src_dir: str) -> list[dict]:
    """Touch devices from libinput's test/litest-device-*.c: name, id, props, keys and real absinfo."""
    out = []
    for path in sorted(glob.glob(os.path.join(src_dir, "test", "litest-device-*.c"))):
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        feats = re.search(r"\.features\s*=\s*([^,]+(?:\|[^,]+)*),", text)
        feats = feats.group(1) if feats else ""
        if "LITEST_TOUCHPAD" not in feats and "LITEST_TOUCH" not in re.sub(r"LITEST_TOUCHPAD", "", feats):
            continue
        if "LITEST_TABLET" in feats:
            continue
        name = re.search(r'\.name\s*=\s*"([^"]+)"', text)
        ident = {k: re.search(rf"\.{k}\s*=\s*(0x[0-9a-fA-F]+|\d+)", text) for k in ("bustype", "vendor", "product")}
        absinfo = {}
        for m in _ABSROW.finditer(text):
            try:
                absinfo[m.group(1)] = [_int(m.group(2)), _int(m.group(3)), _int(m.group(6))]
            except ValueError:
                continue
        keys, props, msc, ff = set(), set(), set(), False
        for m in _EVENT.finditer(text):
            kind, code = m.groups()
            if kind == "EV_KEY" and code in KEYS:
                keys.add(code)
            elif kind == "INPUT_PROP_MAX":
                props.add(code)
            elif kind == "EV_MSC":
                msc.add(code)
            elif kind == "EV_FF":
                ff = True
        quirk = re.search(r"quirk_file\[\]\s*=\s*((?:\s*\"[^\"]*\"\s*)+);", text)
        quirk_text = "".join(re.findall(r'"([^"]*)"', quirk.group(1))).replace("\\n", "\n") if quirk else ""
        integration = re.search(r'\{\s*"ID_INTEGRATION"\s*,\s*"(\w+)"\s*\}', text)
        out.append({
            "file": os.path.basename(path), "name": name.group(1) if name else "", "features": feats.strip(),
            "bus": _int(ident["bustype"].group(1)) if ident["bustype"] else 0,
            "vendor": _int(ident["vendor"].group(1)) if ident["vendor"] else 0,
            "product": _int(ident["product"].group(1)) if ident["product"] else 0,
            "props": sorted(props), "keys": sorted(keys), "msc": sorted(msc), "ff": ff, "abs": absinfo,
            "integration": integration.group(1) if integration else "",
            "udev_type": "touchscreen" if "LITEST_TOUCH" in re.sub("LITEST_TOUCHPAD", "", feats) else "touchpad",
            "quirk": quirk_text,
        })
    return out


def litest_caps(entry: dict) -> Caps:
    """A mined test device with the quirks libinput would apply to it (recorded at mining time)."""
    caps = Caps.from_json({**entry, "source": "litest:" + entry.get("file", "")})
    for item in entry.get("quirk_props", []):
        (caps.props.add if item[0] == "+" else caps.props.discard)(item[1:])
    caps.quirks_applied = list(entry.get("quirk_sections", []))
    return caps


def load_litest() -> list[dict]:
    try:
        with open(LITEST_JSON, encoding="utf-8") as fh:
            return json.load(fh)["devices"]
    except (OSError, ValueError, KeyError):
        return []


def survey(entries: list[dict], quirks: list[Quirk] | None = None) -> list[dict]:
    """Each mined device classed (with quirks applied), for the design doc and tests."""
    rows = []
    for e in entries:
        caps = litest_caps(e)
        if quirks:
            apply_quirks(caps, quirks)
        s = classify(caps)
        w, h = s.pad_mm
        rows.append({"file": e["file"], "name": e["name"], "class": s.cls, "missing": s.missing(),
                     "size_mm": [round(w, 1) if w else None, round(h, 1) if h else None],
                     "mask_bits": s.mask_bits(), "reasons": s.reasons})
    return rows


def quirk_stats(quirks: list[Quirk]) -> dict:
    """How libinput tunes surfaces by hand today: the thresholds a learned model replaces."""
    stats: dict[str, int] = {}
    for q in quirks:
        for k, v in q.attrs.items():
            if k.startswith("AttrPalm") or k.startswith("AttrThumb") or k in ("AttrPressureRange",
                                                                               "AttrTouchSizeRange"):
                stats[k] = stats.get(k, 0) + 1
            if k == "AttrInputProp" and "PRESSUREPAD" in v:
                stats["AttrInputProp=+PRESSUREPAD"] = stats.get("AttrInputProp=+PRESSUREPAD", 0) + 1
    return dict(sorted(stats.items()))


def write_litest(src_dir: str, out: str = LITEST_JSON) -> dict:
    entries = mine_litest(src_dir)
    quirks = load_quirks([os.path.join(src_dir, "quirks")])
    for e in entries:
        caps = Caps.from_json(e)
        own = [q for q in parse_quirks(e.get("quirk", "")) if q.attrs]
        before = set(caps.props)
        caps.name = "litest " + caps.name             # litest prefixes device names; its quirks match that
        apply_quirks(caps, own)
        caps.name = e["name"]
        apply_quirks(caps, quirks)
        e["quirk_props"] = sorted(["+" + p for p in caps.props - before] + ["-" + p for p in before - caps.props])
        e["quirk_sections"] = caps.quirks_applied
    head = ""
    try:
        import subprocess
        head = subprocess.run(["git", "-C", src_dir, "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except (OSError, ValueError):
        pass
    doc = {"source": "libinput test/litest-device-*.c and quirks/*.quirks", "libinput_commit": head,
           "devices": entries, "quirk_stats": quirk_stats(quirks)}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")
    return doc
